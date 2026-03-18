"""backend/routers/admin.py
FastAPI router – CEO Dashboard API.

All endpoints are scoped to the authenticated CEO's company.

Endpoints
---------
GET   /api/admin/dashboard              KPI summary (analyst agent)
GET   /api/admin/report/daily           Trigger full agent pipeline
GET   /api/admin/alerts                 List unresolved alerts for this company
PATCH /api/admin/alerts/{id}/resolve    Resolve an alert
GET   /api/admin/team                   Per-employee submission status for today
GET   /api/admin/reports/history        List past generated reports
GET   /api/admin/reports/{id}           Fetch a specific report
"""

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agents.analyst_agent import AnalystAgent
from agents.manager import ManagerAgent
from backend.auth import require_admin
from backend.database.connection import get_db
from backend.schemas.admin import (
    AlertItemResponse,
    DailyReportResponse,
    KPISummaryResponse,
    ReportHistoryItem,
    TeamMemberStatus,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Agent singletons
_manager = ManagerAgent()
_analyst = AnalystAgent()


# ---------------------------------------------------------------------------
# Dashboard KPIs
# ---------------------------------------------------------------------------
@router.get("/dashboard", response_model=KPISummaryResponse)
async def dashboard(
    payload: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Return today's KPIs for this CEO's company only."""
    company_id = int(payload["company_id"])
    today = date.today().isoformat()

    total = await db.execute(
        text("SELECT COUNT(*) FROM employees WHERE company_id = :cid"),
        {"cid": company_id}
    )
    total_employees = total.scalar() or 0

    submitted = await db.execute(
        text("SELECT COUNT(*) FROM daily_submissions ds "
             "JOIN employees e ON ds.employee_id = e.id "
             "WHERE e.company_id = :cid AND DATE(ds.submitted_at) = :today"),
        {"cid": company_id, "today": today}
    )
    submissions_today = submitted.scalar() or 0

    morale = await db.execute(
        text("SELECT AVG(ds.mood) FROM daily_submissions ds "
             "JOIN employees e ON ds.employee_id = e.id "
             "WHERE e.company_id = :cid AND DATE(ds.submitted_at) = :today"),
        {"cid": company_id, "today": today}
    )
    avg_morale = round(morale.scalar() or 0.0, 2)

    alerts = await db.execute(
        text("SELECT COUNT(*) FROM alerts WHERE resolved = 0 AND company_id = :cid"),
        {"cid": company_id}
    )
    open_alerts = alerts.scalar() or 0

    return KPISummaryResponse(
        date=today,
        total_employees=total_employees,
        submissions_today=submissions_today,
        missed_submissions=total_employees - submissions_today,
        average_morale=avg_morale,
        open_alerts=open_alerts,
    )


# ---------------------------------------------------------------------------
# Daily report (full agent pipeline)
# ---------------------------------------------------------------------------
@router.get("/report/daily", response_model=DailyReportResponse)
async def generate_daily_report(
    payload: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Run the full agent pipeline scoped to this company."""
    company_id = int(payload["company_id"])
    company_name = payload.get("company_name", "Your Company")

    result = await _manager.run_daily_pipeline(
        db,
        company_id=company_id,
        company_name=company_name,
    )

    return DailyReportResponse(
        report_id=result.report_id,
        generated_at=result.generated_at,
        content=result.report_markdown,
        kpis=KPISummaryResponse(
            date=result.kpis.date,
            total_employees=result.kpis.total_employees,
            submissions_today=result.kpis.submissions_today,
            missed_submissions=result.kpis.missed_submissions,
            average_morale=result.kpis.average_morale,
            open_alerts=result.kpis.open_alerts,
        ),
        alert_count=result.alert_count,
    )


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------
@router.get("/alerts", response_model=list[AlertItemResponse])
async def get_alerts(
    payload: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    company_id = int(payload["company_id"])

    rows = (
        await db.execute(
            text(
                "SELECT id, severity, message, resolved, created_at "
                "FROM alerts "
                "WHERE resolved = 0 AND company_id = :cid "
                "ORDER BY created_at DESC"
            ),
            {"cid": company_id},
        )
    ).fetchall()

    return [
        AlertItemResponse(
            id=r[0],
            severity=r[1],
            message=r[2],
            resolved=bool(r[3]),
            created_at=datetime.fromisoformat(r[4]) if isinstance(r[4], str) else r[4],
        )
        for r in rows
    ]


@router.patch("/alerts/{alert_id}/resolve")
async def resolve_alert(
    alert_id: int,
    payload: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    company_id = int(payload["company_id"])

    result = await db.execute(
        text(
            "SELECT id FROM alerts "
            "WHERE id = :id AND company_id = :cid"
        ),
        {"id": alert_id, "cid": company_id},
    )
    if not result.fetchone():
        raise HTTPException(status_code=404, detail="Alert not found")

    await db.execute(
        text(
            "UPDATE alerts SET resolved = 1, resolved_at = :now "
            "WHERE id = :id AND company_id = :cid"
        ),
        {
            "now": datetime.utcnow().isoformat(),
            "id": alert_id,
            "cid": company_id,
        },
    )
    return {"message": "Alert resolved", "alert_id": alert_id}


# ---------------------------------------------------------------------------
# Team status
# ---------------------------------------------------------------------------
@router.get("/team", response_model=list[TeamMemberStatus])
async def team_status(
    payload: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    company_id = int(payload["company_id"])
    today = date.today().isoformat()

    rows = (
        await db.execute(
            text(
                """
                SELECT
                    e.id,
                    e.name,
                    e.department,
                    ds.id          AS submission_id,
                    ds.submitted_at
                FROM employees e
                LEFT JOIN daily_submissions ds
                    ON e.id = ds.employee_id
                    AND DATE(ds.submitted_at) = :today
                WHERE e.company_id = :cid
                ORDER BY e.name
                """
            ),
            {"today": today, "cid": company_id},
        )
    ).fetchall()

    return [
        TeamMemberStatus(
            employee_id=r[0],
            name=r[1],
            department=r[2],
            submitted=r[3] is not None,
            submitted_at=(
                datetime.fromisoformat(r[4])
                if r[4] and isinstance(r[4], str)
                else r[4]
            ),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Report history
# ---------------------------------------------------------------------------
@router.get("/reports/history", response_model=list[ReportHistoryItem])
async def report_history(
    payload: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    company_id = int(payload["company_id"])

    rows = (
        await db.execute(
            text(
                "SELECT id, report_type, generated_by, created_at "
                "FROM agent_reports "
                "WHERE company_id = :cid "
                "ORDER BY created_at DESC LIMIT 50"
            ),
            {"cid": company_id},
        )
    ).fetchall()

    return [
        ReportHistoryItem(
            id=r[0],
            report_type=r[1],
            generated_by=r[2],
            created_at=datetime.fromisoformat(r[3]) if isinstance(r[3], str) else r[3],
        )
        for r in rows
    ]


@router.get("/reports/{report_id}")
async def get_report(
    report_id: int,
    payload: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    company_id = int(payload["company_id"])

    row = (
        await db.execute(
            text(
                "SELECT id, content, generated_by, created_at "
                "FROM agent_reports "
                "WHERE id = :id AND company_id = :cid"
            ),
            {"id": report_id, "cid": company_id},
        )
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Report not found")

    return {
        "report_id": row[0],
        "content": row[1],
        "generated_by": row[2],
        "created_at": row[3],
    }


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------
@router.post("/logout")
async def admin_logout(_payload: dict = Depends(require_admin)):
    return {"message": "Logged out successfully"}