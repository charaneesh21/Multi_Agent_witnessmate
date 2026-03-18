"""agents/analyst_agent.py
Analyst Agent – queries the database and computes KPIs via a Groq tool-use loop.
Now scoped to a specific company via company_id.
"""

import json
from dataclasses import dataclass, field
from datetime import date

from groq import AsyncGroq
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings

settings = get_settings()

MODEL = "llama-3.3-70b-versatile"

# ---------------------------------------------------------------------------
# Tool spec
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
                "sql": {
                    "type": "string",
                    "description": "A SQL SELECT statement to execute.",
                },
                "params": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of positional bind parameters.",
                },
            },
            "required": ["sql"],
        },
    },
}


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------
@dataclass
class KPISummary:
    date: str
    total_employees: int
    submissions_today: int
    missed_submissions: int
    average_morale: float
    blocker_themes: list[str] = field(default_factory=list)
    open_alerts: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _run_db_query(
    db: AsyncSession, sql: str, params: list | None = None
) -> list[dict]:
    if not sql.strip().upper().startswith("SELECT"):
        raise ValueError("Only SELECT statements are permitted.")
    result = await db.execute(text(sql), params or [])
    cols = list(result.keys())
    return [dict(zip(cols, row)) for row in result.fetchall()]


def _build_tool_call_message(msg) -> dict:
    return {
        "role": "assistant",
        "content": msg.content,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
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
class AnalystAgent:
    def __init__(self) -> None:
        self._client = AsyncGroq(api_key=settings.groq_api_key)

    async def analyze_submissions(
        self,
        db: AsyncSession,
        company_id: int,
        target_date: date | None = None,
    ) -> KPISummary:
        """
        Run an agentic loop scoped to company_id.
        The LLM queries the DB via tool calls, then returns KPIs as JSON.
        """
        if target_date is None:
            target_date = date.today()
        date_str = target_date.isoformat()

        system = (
            "You are a data analyst. "
            "Use the database_query tool to retrieve data, then "
            "return your final answer as a single JSON object with EXACTLY these keys:\n"
            "  total_employees       (integer)\n"
            "  submissions_today     (integer)\n"
            "  missed_submissions    (integer)\n"
            "  average_morale        (float, 1-5, 0 if no data)\n"
            "  blocker_themes        (list of up to 3 short strings summarising blockers)\n"
            "Return ONLY the raw JSON — no markdown, no commentary."
        )

        # Give the LLM the exact schema + company_id so it never guesses
        messages: list[dict] = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"Analyse daily submissions for date {date_str} "
                    f"and company_id {company_id}.\n\n"
                    "Database schema (use ONLY these exact column names):\n"
                    "  employees: id, company_id, email, name, department, created_at\n"
                    "  daily_submissions: id, employee_id, tasks_completed, blockers, "
                    "next_day_plan, mood, submitted_at\n\n"
                    "Rules:\n"
                    f"  - Always filter employees by company_id = {company_id}\n"
                    f"  - Filter submissions by DATE(submitted_at) = '{date_str}'\n"
                    "  - Join submissions to employees on employee_id = employees.id\n"
                    "  - The mood column is an integer 1-5\n"
                    "  - The blockers column is free text (may be NULL)\n"
                    "  - Use AVG(ds.mood) for average morale\n"
                    "  - missed_submissions = total_employees - submissions_today\n"
                    "Run your queries then return the JSON."
                ),
            },
        ]

        max_iterations = 5
        iteration = 0

        while True:
            iteration += 1
            if iteration > max_iterations:
                break

            response = await self._client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=[_DB_TOOL],
                tool_choice="auto",
                temperature=0.1,
                max_tokens=1024,
            )
            choice = response.choices[0]
            msg = choice.message

            if choice.finish_reason == "tool_calls" and msg.tool_calls:
                messages.append(_build_tool_call_message(msg))

                for tc in msg.tool_calls:
                    args = json.loads(tc.function.arguments)
                    try:
                        rows = await _run_db_query(db, args["sql"], args.get("params"))
                        content = json.dumps(rows)
                    except Exception as exc:
                        content = json.dumps({"error": str(exc)})

                    messages.append(
                        {"role": "tool", "tool_call_id": tc.id, "content": content}
                    )
            else:
                raw = _strip_code_fence(msg.content or "{}")
                try:
                    data: dict = json.loads(raw)
                except json.JSONDecodeError:
                    data = {}

                return KPISummary(
                    date=date_str,
                    total_employees=int(data.get("total_employees", 0)),
                    submissions_today=int(data.get("submissions_today", 0)),
                    missed_submissions=int(data.get("missed_submissions", 0)),
                    average_morale=float(data.get("average_morale", 0.0)),
                    blocker_themes=list(data.get("blocker_themes", [])),
                )

        # Fallback if max iterations hit
        return KPISummary(
            date=date_str,
            total_employees=0,
            submissions_today=0,
            missed_submissions=0,
            average_morale=0.0,
        )