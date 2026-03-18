"""backend/schemas/auth.py
Pydantic v2 schemas for the unified auth router.
"""

from pydantic import BaseModel, EmailStr, Field


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------
class CompanyRegisterRequest(BaseModel):
    company_name: str = Field(..., min_length=2, max_length=255)
    industry: str | None = None
    ceo_email: EmailStr
    ceo_password: str = Field(..., min_length=6)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------
class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class CompanyRegisterResponse(BaseModel):
    company_id: int
    company_name: str
    ceo_email: str
    message: str
    
class EmployeeSelfRegisterRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    email: EmailStr
    password: str = Field(..., min_length=6)
    department: str | None = None
    admin_email: EmailStr  # used to look up which company to join