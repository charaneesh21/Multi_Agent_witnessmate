"""agents/manager.py
Manager Agent – orchestrates all specialist agents.
Public API (called by FastAPI routers):
  manager = ManagerAgent()
  report  = await manager.run_daily_pipeline(db, company_id, company_name)
  verdict = await manager.handle_employee_submission(db, submission_data, company_id)
"""
import asyncio
from dataclasses import dataclass
from datetime import date, datetime

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agents.analyst_agent import AnalystAgent, KPISummary
from agents.research_agent import ResearchAgent
from agents.watchdog_agent import AlertItem, WatchdogAgent, WatchdogVerdict
from agents.writer_agent import ReportContext, WriterAgent
from config.settings import get_settings

settings = get_settings()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_template() -> dict:
    with open(settings.active_template_path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


async def _persist_report(
    db: AsyncSession,
    content: str,
    company_id: int,
    report_type: str = "daily_brief",
    generated_by: str = "manager",
) -> int:
    result = await db.execute(
        text(
            "INSERT INTO agent_reports "
            "(company_id, report_type, content, generated_by, created_at) "
            "VALUES (:cid, :rtype, :content, :by, :now)"
        ),
        {
            "cid": company_id,
            "rtype": report_type,
            "content": content,
            "by": generated_by,
            "now": datetime.utcnow().isoformat(),
        },
    )
    await db.flush()
    return result.lastrowid


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------
@dataclass
class PipelineResult:
    report_id: int
    report_markdown: str
    kpis: KPISummary
    alert_count: int
    generated_at: datetime


@dataclass
class SubmissionResult:
    accepted: bool
    verdict: WatchdogVerdict
    message: str


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------
class ManagerAgent:
    def __init__(self) -> None:
        self._analyst  = AnalystAgent()
        self._research = ResearchAgent()
        self._watchdog = WatchdogAgent()
        self._writer   = WriterAgent()

    # ------------------------------------------------------------------
    # 1. Daily pipeline
    # ------------------------------------------------------------------
    async def run_daily_pipeline(
        self,
        db: AsyncSession,
        company_id: int,
        company_name: str | None = None,
        target_date: date | None = None,
    ) -> PipelineResult:
        if target_date is None:
            target_date = date.today()

        config = _load_template()
        report_sections: list[str] = (
            config.get("ceo_dashboard", {}).get("report_sections", [])
        )

        if not company_name:
            company_name = config.get("company", {}).get("name", "Your Company")

        research_topic = (
            f"{company_name} industry trends, competitor news, and market updates"
        )

        # Step 1: Direct SQL KPIs + Research in parallel
        date_str = target_date.isoformat()

        emp_row = await db.execute(
            text("SELECT COUNT(*) FROM employees WHERE company_id = :cid"),
            {"cid": company_id}
        )
        total_employees = emp_row.scalar() or 0

        sub_row = await db.execute(
            text("SELECT COUNT(*) FROM daily_submissions ds "
                 "JOIN employees e ON ds.employee_id = e.id "
                 "WHERE e.company_id = :cid AND DATE(ds.submitted_at) = :d"),
            {"cid": company_id, "d": date_str}
        )
        submissions_today = sub_row.scalar() or 0

        mood_row = await db.execute(
            text("SELECT AVG(ds.mood) FROM daily_submissions ds "
                 "JOIN employees e ON ds.employee_id = e.id "
                 "WHERE e.company_id = :cid AND DATE(ds.submitted_at) = :d"),
            {"cid": company_id, "d": date_str}
        )
        avg_morale = round(mood_row.scalar() or 0.0, 2)

        kpis = KPISummary(
            date=date_str,
            total_employees=total_employees,
            submissions_today=submissions_today,
            missed_submissions=total_employees - submissions_today,
            average_morale=avg_morale,
            open_alerts=0,
        )

        # Fetch actual submission text so the writer can analyse content, not just numbers
        sub_text_rows = await db.execute(
            text(
                "SELECT e.name, e.department, ds.tasks_completed, ds.blockers, ds.next_day_plan "
                "FROM daily_submissions ds "
                "JOIN employees e ON ds.employee_id = e.id "
                "WHERE e.company_id = :cid AND DATE(ds.submitted_at) = :d"
            ),
            {"cid": company_id, "d": date_str},
        )
        submission_summaries = [
            {
                "name": row.name,
                "department": row.department,
                "tasks_completed": row.tasks_completed,
                "blockers": row.blockers,
                "next_day_plan": row.next_day_plan,
            }
            for row in sub_text_rows
        ]

        market_brief = await self._research.gather_intelligence(research_topic)

        # Step 2: Watchdog daily scan (scoped to company)
        alerts: list[AlertItem] = await self._watchdog.check_for_alerts(
            db, company_id=company_id, company_name=company_name, target_date=target_date
        )
        kpis.open_alerts = len(alerts)

        # Step 3: Writer composes report
        ctx = ReportContext(
            kpis=kpis,
            market_brief=market_brief,
            alerts=alerts,
            report_sections=report_sections or None,
            company_name=company_name,
            submission_summaries=submission_summaries,
        )
        report_markdown: str = await self._writer.compose_report(ctx)

        # Step 4: Persist report scoped to company
        report_id = await _persist_report(
            db, report_markdown, company_id=company_id
        )

        return PipelineResult(
            report_id=report_id,
            report_markdown=report_markdown,
            kpis=kpis,
            alert_count=len(alerts),
            generated_at=datetime.utcnow(),
        )

    # ------------------------------------------------------------------
    # 2. Employee submission handler
    # ------------------------------------------------------------------
    async def handle_employee_submission(
        self,
        db: AsyncSession,
        submission_data: dict,
        company_id: int,
    ) -> SubmissionResult:
        verdict: WatchdogVerdict = await self._watchdog.inspect_submission(
            submission_data
        )

        if verdict.action == "block":
            await db.execute(
                text(
                    "INSERT INTO alerts "
                    "(company_id, severity, message, resolved, created_at) "
                    "VALUES (:cid, 'critical', :msg, 0, :now)"
                ),
                {
                    "cid": company_id,
                    "msg": f"Submission blocked: {verdict.reason}",
                    "now": datetime.utcnow().isoformat(),
                },
            )
            await db.flush()
            return SubmissionResult(
                accepted=False,
                verdict=verdict,
                message=(
                    "Your submission could not be accepted due to a policy violation. "
                    "Please contact HR if you have questions."
                ),
            )

        if verdict.action == "flag":
            await db.execute(
                text(
                    "INSERT INTO alerts "
                    "(company_id, severity, message, resolved, created_at) "
                    "VALUES (:cid, 'medium', :msg, 0, :now)"
                ),
                {
                    "cid": company_id,
                    "msg": f"Submission flagged for review: {verdict.reason}",
                    "now": datetime.utcnow().isoformat(),
                },
            )
            await db.flush()
            return SubmissionResult(
                accepted=True,
                verdict=verdict,
                message=(
                    "Your submission has been received. "
                    "A note has been sent to your manager for follow-up."
                ),
            )

        return SubmissionResult(
            accepted=True,
            verdict=verdict,
            message="Daily check-in submitted successfully. Thank you!",
        )