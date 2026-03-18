"""backend/routers/auth.py
Unified authentication router.

Endpoints
---------
POST /api/auth/register     Company self-signup (creates company + CEO account)
POST /api/auth/login/ceo    Admin login
POST /api/auth/login/employee  Employee login
"""

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import create_token
from backend.database.connection import get_db
from backend.database.models import Company, Employee
from backend.schemas.auth import (
    CompanyRegisterRequest,
    EmployeeSelfRegisterRequest,
    CompanyRegisterResponse,
    LoginRequest,
    TokenResponse,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


# ---------------------------------------------------------------------------
# Company self-signup
# ---------------------------------------------------------------------------
@router.post("/register", response_model=CompanyRegisterResponse, status_code=201)
async def register_company(
    body: CompanyRegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Any client (insurance company, startup, etc.) can register here.
    Creates a Company row + CEO account in one step.
    """
    # Check if CEO email already exists
    existing = await db.execute(
        select(Company).where(Company.ceo_email == body.ceo_email)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A company with this CEO email already exists.",
        )

    # Create company
    company = Company(
        name=body.company_name,
        industry=body.industry,
        ceo_email=body.ceo_email,
        ceo_password_hash=_hash(body.ceo_password),
    )
    db.add(company)
    await db.flush()  # get company.id

    await db.commit()

    return CompanyRegisterResponse(
        company_id=company.id,
        company_name=company.name,
        ceo_email=company.ceo_email,
        message=f"Welcome to the platform! Your company '{company.name}' has been created.",
    )


# ---------------------------------------------------------------------------
# Admin login
# ---------------------------------------------------------------------------
@router.post("/login/ceo", response_model=TokenResponse)
async def ceo_login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Company).where(Company.ceo_email == body.email)
    )
    company = result.scalar_one_or_none()

    if company is None or not _verify(body.password, company.ceo_password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid CEO credentials.",
        )

    token = create_token({
        "sub": str(company.id),
        "role": "admin",
        "company_id": company.id,
        "company_name": company.name,
    })
    return TokenResponse(access_token=token)


# ---------------------------------------------------------------------------
# Employee login
# ---------------------------------------------------------------------------
@router.post("/login/employee", response_model=TokenResponse)
async def employee_login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Employee).where(Employee.email == body.email)
    )
    emp = result.scalar_one_or_none()

    if emp is None or not _verify(body.password, emp.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    token = create_token({
        "sub": str(emp.id),
        "role": "employee",
        "company_id": emp.company_id,
    })
    return TokenResponse(access_token=token)

# ---------------------------------------------------------------------------
# Employee self-registration
# ---------------------------------------------------------------------------
@router.post("/register/employee", status_code=201)
async def register_employee_self(
    body: EmployeeSelfRegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Employees self-register by providing their company's admin email.
    Automatically linked to the correct company.
    """
    # Find company by admin email
    result = await db.execute(
        select(Company).where(Company.ceo_email == body.admin_email)
    )
    company = result.scalar_one_or_none()
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No company found with that admin email.",
        )

    # Check if employee email already exists
    existing = await db.execute(
        select(Employee).where(Employee.email == body.email)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An employee with this email already exists.",
        )

    # Create employee
    emp = Employee(
        name=body.name,
        email=body.email,
        password_hash=_hash(body.password),
        department=body.department,
        company_id=company.id,
    )
    db.add(emp)
    await db.commit()

    return {
        "message": f"Welcome! You've been registered under {company.name}.",
        "company": company.name,
        "email": body.email,
    }