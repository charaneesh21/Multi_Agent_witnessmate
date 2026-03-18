"""backend/schemas/ceo.py
Pydantic v2 request / response schemas for the CEO Dashboard.
"""

from datetime import datetime

from pydantic import BaseModel, EmailStr


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------
class CEOLoginRequest(BaseModel):
    email: EmailStr
    password: str


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------
class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class KPISummaryResponse(BaseModel):
    date: str
    total_employees: int
    submissions_today: int
    missed_submissions: int
    average_morale: float
    open_alerts: int


class AlertItemResponse(BaseModel):
    id: int
    severity: str
    message: str
    created_at: datetime
    resolved: bool


class TeamMemberStatus(BaseModel):
    employee_id: int
    name: str | None
    department: str | None
    submitted: bool
    submitted_at: datetime | None = None


class DailyReportResponse(BaseModel):
    report_id: int
    generated_at: datetime
    content: str          # markdown
    kpis: KPISummaryResponse
    alert_count: int


class ReportHistoryItem(BaseModel):
    id: int
    report_type: str
    generated_by: str | None
    created_at: datetime
