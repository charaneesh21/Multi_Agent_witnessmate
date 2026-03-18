"""backend/routers/employee.py
FastAPI router – Employee Portal API.

Endpoints
---------
POST /api/employee/register         CEO creates a new employee for their company
POST /api/employee/logout           Stateless logout (client drops token)
GET  /api/employee/me               Return authenticated employee's profile
GET  /api/employee/submission/today Check if already submitted today
POST /api/employee/submission       Submit daily status form
POST /api/employee/upload           Upload a file linked to today's submission
"""

import uuid
from datetime import date, datetime
from pathlib import Path

import bcrypt
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from agents.manager import ManagerAgent
from backend.auth import create_token, require_admin, require_employee
from backend.database.connection import get_db
from backend.database.models import Employee
from backend.schemas.employee import (
    DailySubmissionRequest,
    EmployeeProfile,
    EmployeeRegisterRequest,
    EmployeeRegisterResponse,
    SubmissionResponse,
    SubmissionStatus,
    TokenResponse,
    UploadResponse,
)
from config.settings import get_settings

router = APIRouter(prefix="/api/employee", tags=["employee"])

_settings = get_settings()
_manager = ManagerAgent()

_ALLOWED_EXT = {"pdf", "docx", "xlsx", "png", "jpg", "jpeg"}
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def _verify(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


# ---------------------------------------------------------------------------
# Employee Registration (CEO creates employees for their company)
# ---------------------------------------------------------------------------
@router.post("/register", response_model=EmployeeRegisterResponse, status_code=201)
async def register_employee(
    body: EmployeeRegisterRequest,
    payload: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    CEO registers a new employee under their company.
    Only a CEO token can call this endpoint.
    """
    company_id = int(payload["company_id"])

    # Check email not already taken
    existing = await db.execute(
        select(Employee).where(Employee.email == body.email)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An employee with this email already exists.",
        )

    emp = Employee(
        company_id=company_id,
        email=body.email,
        password_hash=_hash(body.password),
        name=body.name,
        department=body.department,
    )
    db.add(emp)
    await db.flush()
    await db.commit()

    return EmployeeRegisterResponse(
        id=emp.id,
        email=emp.email,
        name=emp.name,
        department=emp.department,
        message=f"Employee '{emp.name}' created successfully.",
    )


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------
@router.post("/logout")
async def logout(_payload: dict = Depends(require_employee)):
    return {"message": "Logged out successfully"}


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------
@router.get("/me", response_model=EmployeeProfile)
async def me(
    payload: dict = Depends(require_employee),
    db: AsyncSession = Depends(get_db),
):
    emp_id = int(payload["sub"])
    company_id = int(payload["company_id"])

    result = await db.execute(
        select(Employee).where(
            Employee.id == emp_id,
            Employee.company_id == company_id,   # security: scope to company
        )
    )
    emp = result.scalar_one_or_none()
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    return EmployeeProfile(
        id=emp.id,
        email=emp.email,
        name=emp.name,
        department=emp.department,
        company_id=emp.company_id,
    )


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------
@router.get("/submission/today", response_model=SubmissionStatus)
async def submission_today(
    payload: dict = Depends(require_employee),
    db: AsyncSession = Depends(get_db),
):
    emp_id = int(payload["sub"])
    today = date.today().isoformat()

    row = (
        await db.execute(
            text(
                "SELECT id, submitted_at FROM daily_submissions "
                "WHERE employee_id = :eid AND DATE(submitted_at) = :today "
                "LIMIT 1"
            ),
            {"eid": emp_id, "today": today},
        )
    ).fetchone()

    if row:
        submitted_at = (
            datetime.fromisoformat(row[1]) if isinstance(row[1], str) else row[1]
        )
        return SubmissionStatus(
            submitted_today=True,
            submission_id=row[0],
            submitted_at=submitted_at,
        )
    return SubmissionStatus(submitted_today=False)


@router.post("/submission", response_model=SubmissionResponse, status_code=201)
async def submit(
    body: DailySubmissionRequest,
    payload: dict = Depends(require_employee),
    db: AsyncSession = Depends(get_db),
):
    emp_id = int(payload["sub"])
    company_id = int(payload["company_id"])
    today = date.today().isoformat()

    # Enforce one submission per day
    existing = (
        await db.execute(
            text(
                "SELECT id FROM daily_submissions "
                "WHERE employee_id = :eid AND DATE(submitted_at) = :today LIMIT 1"
            ),
            {"eid": emp_id, "today": today},
        )
    ).fetchone()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already submitted your daily check-in today.",
        )

    # Watchdog inspection via Manager (fast, inline)
    result = await _manager.handle_employee_submission(
        db, body.model_dump(), company_id=company_id
    )

    if not result.accepted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.message
        )

    # Persist the submission
    insert = await db.execute(
        text(
            "INSERT INTO daily_submissions "
            "(employee_id, tasks_completed, blockers, next_day_plan, mood, submitted_at) "
            "VALUES (:eid, :tasks, :blockers, :plan, :mood, :now)"
        ),
        {
            "eid": emp_id,
            "tasks": body.tasks_completed,
            "blockers": body.blockers,
            "plan": body.next_day_plan,
            "mood": body.mood,
            "now": datetime.utcnow().isoformat(),
        },
    )
    await db.flush()

    return SubmissionResponse(
        submission_id=insert.lastrowid,
        message=result.message,
        flagged=(result.verdict.action == "flag"),
    )


# ---------------------------------------------------------------------------
# File upload
# ---------------------------------------------------------------------------
@router.post("/upload", response_model=UploadResponse, status_code=201)
async def upload_file(
    file: UploadFile = File(...),
    payload: dict = Depends(require_employee),
    db: AsyncSession = Depends(get_db),
):
    suffix = Path(file.filename or "").suffix.lstrip(".").lower()
    if suffix not in _ALLOWED_EXT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type '.{suffix}' is not allowed.",
        )

    content = await file.read()
    if len(content) > _MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File exceeds the 10 MB size limit.",
        )

    upload_dir = Path(_settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}_{file.filename}"
    stored_path = upload_dir / stored_name
    stored_path.write_bytes(content)

    emp_id = int(payload["sub"])
    today = date.today().isoformat()
    sub_row = (
        await db.execute(
            text(
                "SELECT id FROM daily_submissions "
                "WHERE employee_id = :eid AND DATE(submitted_at) = :today LIMIT 1"
            ),
            {"eid": emp_id, "today": today},
        )
    ).fetchone()
    submission_id = sub_row[0] if sub_row else None

    res = await db.execute(
        text(
            "INSERT INTO uploaded_files "
            "(submission_id, original_name, stored_path, file_size_bytes, uploaded_at) "
            "VALUES (:sid, :name, :path, :size, :now)"
        ),
        {
            "sid": submission_id,
            "name": file.filename,
            "path": str(stored_path),
            "size": len(content),
            "now": datetime.utcnow().isoformat(),
        },
    )
    await db.flush()

    return UploadResponse(
        file_id=res.lastrowid,
        original_name=file.filename,
        size_bytes=len(content),
    )