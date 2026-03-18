"""agents/watchdog_agent.py
Watchdog Agent – real-time compliance checks and daily alert generation.
Now scoped to a specific company via company_id.
"""

import json
from dataclasses import dataclass, field
from datetime import date, datetime

from groq import AsyncGroq
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings

settings = get_settings()

FAST_MODEL = "llama-3.1-8b-instant"
SCAN_MODEL = "llama-3.3-70b-versatile"

MORALE_ALERT_BELOW = 2
MISSED_SUBMISSIONS_PCT_THRESHOLD = 20

# ---------------------------------------------------------------------------
# Tool specs
# ---------------------------------------------------------------------------
_DB_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "database_query",
        "description": (
            "Run a read-only SELECT query against the SQLite database. "
            "Only SELECT statements are accepted."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "A SQL SELECT statement."},
                "params": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional positional bind parameters.",
                },
            },
            "required": ["sql"],
        },
    },
}

_ALERT_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "alert_sender",
        "description": "Persist a new alert to the alerts table.",
        "parameters": {
            "type": "object",
            "properties": {
                "severity": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "critical"],
                },
                "message": {"type": "string"},
            },
            "required": ["severity", "message"],
        },
    },
}


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------
@dataclass
class WatchdogVerdict:
    action: str           # "pass" | "flag" | "block"
    reason: str
    urgency_score: float  # 0.0 – 1.0


@dataclass
class AlertItem:
    id: int
    severity: str
    message: str
    created_at: datetime
    resolved: bool = False
    resolved_at: datetime | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _run_db_query(
    db: AsyncSession, sql: str, params: list | None = None
) -> list[dict]:
    if not sql.strip().upper().startswith("SELECT"):
        raise ValueError("Only SELECT statements are permitted.")
    result = await db.execute(text(sql), params or {})
    cols = list(result.keys())
    return [dict(zip(cols, row)) for row in result.fetchall()]


async def _persist_alert(
    db: AsyncSession, severity: str, message: str, company_id: int
) -> int:
    # Check if same alert already exists and is unresolved today
    existing = await db.execute(
        text(
            "SELECT id FROM alerts WHERE company_id = :cid "
            "AND message = :msg AND resolved = 0 "
            "AND DATE(created_at) = DATE('now')"
        ),
        {"cid": company_id, "msg": message},
    )
    row = existing.fetchone()
    if row:
        return row[0]  # already exists, skip insert

    result = await db.execute(
        text(
            "INSERT INTO alerts (company_id, severity, message, resolved, created_at) "
            "VALUES (:cid, :sev, :msg, 0, CURRENT_TIMESTAMP)"
        ),
        {"cid": company_id, "sev": severity, "msg": message},
    )
    await db.commit()
    return result.lastrowid


def _build_tool_call_message(msg) -> dict:
    return {
        "role": "assistant",
        "content": msg.content,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in (msg.tool_calls or [])
        ],
    }


def _strip_code_fence(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return raw.strip()


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------
class WatchdogAgent:
    def __init__(self) -> None:
        self._client = AsyncGroq(api_key=settings.groq_api_key)

    # ------------------------------------------------------------------
    # 1. Inline submission inspection (fast, no tool loop)
    # ------------------------------------------------------------------
    async def inspect_submission(self, submission: dict) -> WatchdogVerdict:
        system = (
            "You are a compliance watchdog. Review the employee submission below. "
            "Respond with ONLY a JSON object with these keys:\n"
            "  action         : 'pass' | 'flag' | 'block'\n"
            "  reason         : short explanation (one sentence)\n"
            "  urgency_score  : float 0.0 to 1.0\n\n"
            "Flag if: possible confidential data leak, harassment, threats, "
            "copy-pasted/empty answers, or anything needing manager review.\n"
            "Block if: illegal content, severe harassment, or security threat.\n"
            "Return ONLY raw JSON — no markdown, no extra text."
        )

        user_content = (
            f"Tasks completed: {submission.get('tasks_completed', '')}\n"
            f"Blockers: {submission.get('blockers', '')}\n"
            f"Next-day plan: {submission.get('next_day_plan', '')}\n"
            f"Mood score: {submission.get('mood', '')}"
        )

        response = await self._client.chat.completions.create(
            model=FAST_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            temperature=0.0,
            max_tokens=256,
        )

        raw = _strip_code_fence(response.choices[0].message.content or "{}")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"action": "flag", "reason": "Could not parse watchdog response.", "urgency_score": 0.5}

        return WatchdogVerdict(
            action=data.get("action", "flag"),
            reason=data.get("reason", ""),
            urgency_score=float(data.get("urgency_score", 0.5)),
        )

    # ------------------------------------------------------------------
    # 2. Daily alert scan (direct SQL, no LLM hallucination)
    # ------------------------------------------------------------------
    async def check_for_alerts(
        self,
        db: AsyncSession,
        company_id: int,
        company_name: str = "Your Company",
        target_date: date | None = None,
    ) -> list[AlertItem]:
        if target_date is None:
            target_date = date.today()
        date_str = target_date.isoformat()

        # 1. Total employees
        emp_row = await db.execute(
            text("SELECT COUNT(*) FROM employees WHERE company_id = :cid"),
            {"cid": company_id}
        )
        total_employees = emp_row.scalar() or 0

        # 2. Submissions today
        sub_row = await db.execute(
            text("SELECT COUNT(*) FROM daily_submissions ds "
                 "JOIN employees e ON ds.employee_id = e.id "
                 "WHERE e.company_id = :cid AND DATE(ds.submitted_at) = :d"),
            {"cid": company_id, "d": date_str}
        )
        submissions_today = sub_row.scalar() or 0

        # 3. Average mood today
        mood_row = await db.execute(
            text("SELECT AVG(ds.mood) FROM daily_submissions ds "
                 "JOIN employees e ON ds.employee_id = e.id "
                 "WHERE e.company_id = :cid AND DATE(ds.submitted_at) = :d"),
            {"cid": company_id, "d": date_str}
        )
        avg_mood = mood_row.scalar() or 0.0

        # 4. Raise alerts based on thresholds
        missed_pct = (
            ((total_employees - submissions_today) / total_employees * 100)
            if total_employees > 0 else 0
        )

        if avg_mood > 0 and avg_mood < MORALE_ALERT_BELOW:
            await _persist_alert(
                db,
                "high",
                f"Average mood for today among {company_name} employees is below {MORALE_ALERT_BELOW}",
                company_id,
            )

        if missed_pct > MISSED_SUBMISSIONS_PCT_THRESHOLD:
            await _persist_alert(
                db,
                "medium",
                f"{int(missed_pct)}% of {company_name} employees have NOT submitted today",
                company_id,
            )

        # 5. Return all unresolved alerts for this company
        result = await db.execute(
            text("SELECT id, severity, message, resolved, created_at, resolved_at "
                 "FROM alerts WHERE resolved = 0 AND company_id = :cid "
                 "ORDER BY created_at DESC"),
            {"cid": company_id},
        )
        rows = result.fetchall()
        return [
            AlertItem(
                id=r[0],
                severity=r[1],
                message=r[2],
                resolved=bool(r[3]),
                created_at=datetime.fromisoformat(r[4]) if r[4] else datetime.utcnow(),
                resolved_at=datetime.fromisoformat(r[5]) if r[5] else None,
            )
            for r in rows
        ]