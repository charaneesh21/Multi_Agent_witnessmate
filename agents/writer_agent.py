"""agents/writer_agent.py
Writer Agent – converts raw agent outputs into polished executive prose.
No tools; a single LLM call per method.
"""

from dataclasses import dataclass, field

from groq import AsyncGroq

from agents.analyst_agent import KPISummary
from agents.research_agent import IntelligenceBrief
from agents.watchdog_agent import AlertItem
from config.settings import get_settings

settings = get_settings()

MODEL = "llama-3.3-70b-versatile"

# Default section order – overridden by tech_company.yaml at runtime
DEFAULT_SECTIONS = [
    "Executive Summary",
    "Team Highlights",
    "Risk & Alerts",
    "Market Intelligence",
]


# ---------------------------------------------------------------------------
# Data container passed in from the manager
# ---------------------------------------------------------------------------
@dataclass
class ReportContext:
    kpis: KPISummary
    market_brief: IntelligenceBrief
    alerts: list[AlertItem]
    report_sections: list[str] = field(default_factory=lambda: list(DEFAULT_SECTIONS))
    company_name: str = "Acme Tech Inc."
    submission_summaries: list[dict] = field(default_factory=list)  # [{name, dept, tasks, blockers, next_plan}]


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------
class WriterAgent:
    def __init__(self) -> None:
        self._client = AsyncGroq(api_key=settings.groq_api_key)

    async def compose_report(self, ctx: ReportContext) -> str:
        """
        Write the full daily executive brief as a markdown string.
        Sections follow the order defined in ctx.report_sections.
        """
        # Summarise inputs so the LLM has structured context
        kpi_block = (
            f"- Date: {ctx.kpis.date}\n"
            f"- Total employees: {ctx.kpis.total_employees}\n"
            f"- Submitted today: {ctx.kpis.submissions_today}\n"
            f"- Missed: {ctx.kpis.missed_submissions}\n"
            f"- Average morale: {ctx.kpis.average_morale:.1f}/5\n"
            f"- Top blockers: {', '.join(ctx.kpis.blocker_themes) or 'None identified'}\n"
            f"- Open alerts: {ctx.kpis.open_alerts}"
        )

        alert_block = (
            "\n".join(
                f"  [{a.severity.upper()}] {a.message}"
                for a in ctx.alerts
            )
            or "  None"
        )

        sections_str = "\n".join(f"{i+1}. {s}" for i, s in enumerate(ctx.report_sections))

        # Build submission content block
        if ctx.submission_summaries:
            submission_lines = []
            for s in ctx.submission_summaries:
                submission_lines.append(
                    f"- **{s.get('name', 'Unknown')}** ({s.get('department', '—')}): "
                    f"Tasks: {s.get('tasks_completed', '—')} | "
                    f"Blockers: {s.get('blockers') or 'None'} | "
                    f"Tomorrow: {s.get('next_day_plan', '—')}"
                )
            submission_block = "\n".join(submission_lines)
        else:
            submission_block = "No submissions available."

        system = (
            f"You are an executive communications writer for {ctx.company_name}. "
            "Write a professional CEO daily brief in clean markdown. "
            f"Use exactly these sections in this order:\n{sections_str}\n\n"
            "Guidelines:\n"
            "  • Executive Summary: 2-3 sentences, key headline number.\n"
            "  • Team Highlights: 3-5 bullet points grounded in the actual submission content — "
            "reference specific tasks completed, recurring themes, or notable wins.\n"
            "  • Risk & Alerts: list each alert with severity, message, recommended action.\n"
            "  • Market Intelligence: synthesise the research brief into 4-6 bullets.\n"
            "Tone: concise, data-driven, executive-level. No fluff. Do NOT invent data. "
            "Use markdown headers (##)."
        )

        user = (
            f"## KPI Data\n{kpi_block}\n\n"
            f"## Employee Submissions\n{submission_block}\n\n"
            f"## Active Alerts\n{alert_block}\n\n"
            f"## Market Intelligence (raw)\n{ctx.market_brief.content}"
        )

        response = await self._client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.4,
            max_tokens=2048,
        )

        return response.choices[0].message.content or "_Report generation failed._"

    async def compose_alert_summary(self, alerts: list[AlertItem]) -> str:
        """
        Produce a short (≤ 5 bullet) plain-English alert summary for the
        dashboard notification panel.  Returns an empty string if no alerts.
        """
        if not alerts:
            return ""

        alert_lines = "\n".join(
            f"[{a.severity.upper()}] {a.message}" for a in alerts[:10]
        )

        response = await self._client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a concise executive assistant. "
                        "Summarise the following alerts in at most 5 plain-English bullet points. "
                        "Each bullet: one sentence, most critical first. No markdown headers."
                    ),
                },
                {"role": "user", "content": alert_lines},
            ],
            temperature=0.2,
            max_tokens=256,
        )

        return response.choices[0].message.content or ""
