"""backend/schemas/employee.py
Pydantic v2 request / response schemas for the Employee Portal.
"""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------
class EmployeeRegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6)
    name: str | None = None
    department: str | None = None


class DailySubmissionRequest(BaseModel):
    tasks_completed: str = Field(..., min_length=10)
    blockers: str | None = None
    next_day_plan: str = Field(..., min_length=10)
    mood: int = Field(..., ge=1, le=5)


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------
class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class EmployeeRegisterResponse(BaseModel):
    id: int
    email: str
    name: str | None
    department: str | None
    message: str


class EmployeeProfile(BaseModel):
    id: int
    email: str
    name: str | None
    department: str | None
    company_id: int


class SubmissionStatus(BaseModel):
    submitted_today: bool
    submission_id: int | None = None
    submitted_at: datetime | None = None


class SubmissionResponse(BaseModel):
    submission_id: int
    message: str
    flagged: bool


class UploadResponse(BaseModel):
    file_id: int
    original_name: str
    size_bytes: int