"""agents/research_agent.py
Research Agent – gathers external market and industry intelligence via a
Groq tool-use loop backed by the DuckDuckGo Instant Answer API (no key needed).

Swap _web_search_impl() for a paid search API (Tavily, Serper, Brave, etc.)
to get richer results in production.
"""

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass

from groq import AsyncGroq

from config.settings import get_settings

settings = get_settings()

MODEL = "llama-3.3-70b-versatile"

# ---------------------------------------------------------------------------
# Tool spec
# ---------------------------------------------------------------------------
_SEARCH_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web for current information. "
            "Returns a list of result snippets relevant to the query."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query string.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default 5).",
                },
            },
            "required": ["query"],
        },
    },
}


# ---------------------------------------------------------------------------
# Tool implementation  (DuckDuckGo Instant Answer – free, no API key)
# ---------------------------------------------------------------------------
def _web_search_impl(query: str, max_results: int = 5) -> list[dict]:
    """
    Calls the DuckDuckGo Instant Answer API (synchronous, runs in thread).
    Returns a list of result dicts: {title, snippet, url}.

    In production, replace the body with a call to Tavily / Serper / Brave.
    """
    encoded = urllib.parse.quote_plus(query)
    url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_redirect=1&no_html=1"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MultiAgentSystem/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        return [{"title": "Search unavailable", "snippet": str(exc), "url": ""}]

    results: list[dict] = []

    # Abstract (top answer)
    if data.get("Abstract"):
        results.append(
            {
                "title": data.get("Heading", query),
                "snippet": data["Abstract"],
                "url": data.get("AbstractURL", ""),
            }
        )

    # Related topics
    for topic in data.get("RelatedTopics", [])[:max_results]:
        if "Text" in topic:
            results.append(
                {
                    "title": topic.get("FirstURL", "").split("/")[-1].replace("_", " "),
                    "snippet": topic["Text"],
                    "url": topic.get("FirstURL", ""),
                }
            )
        elif "Topics" in topic:
            # nested topic group
            for sub in topic["Topics"][:2]:
                results.append(
                    {
                        "title": sub.get("FirstURL", "").split("/")[-1].replace("_", " "),
                        "snippet": sub.get("Text", ""),
                        "url": sub.get("FirstURL", ""),
                    }
                )

    return results[:max_results] if results else [
        {"title": "No results", "snippet": f"No instant answer found for: {query}", "url": ""}
    ]


# ---------------------------------------------------------------------------
# Helpers (shared with analyst; duplicated here to keep agents self-contained)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------
@dataclass
class IntelligenceBrief:
    topic: str
    content: str          # markdown-formatted brief


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------
class ResearchAgent:
    def __init__(self) -> None:
        self._client = AsyncGroq(api_key=settings.groq_api_key)

    async def gather_intelligence(self, topic: str | None = None) -> IntelligenceBrief:
        """
        Run an agentic loop: the LLM issues web_search tool calls to gather
        information, then composes a concise markdown market brief.
        """
        if topic is None:
            topic = "software and SaaS industry trends, competitor news, and tech market updates"

        system = (
            "You are a market research specialist for a tech company. "
            "Use the web_search tool to gather relevant, current information. "
            "After searching, write a concise markdown brief (3-5 bullet points per section) "
            "covering: market trends, competitor activity, and technology developments. "
            "Cite the source URL for each key point. "
            "Be factual and concise. Do not invent information."
        )

        messages: list[dict] = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"Research the following topic for today's CEO briefing: {topic}. "
                    "Use 2-3 targeted searches to gather diverse, current information."
                ),
            },
        ]

        while True:
            response = await self._client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=[_SEARCH_TOOL],
                tool_choice="auto",
                temperature=0.3,
                max_tokens=2048,
            )
            choice = response.choices[0]
            msg = choice.message

            if choice.finish_reason == "tool_calls" and msg.tool_calls:
                messages.append(_build_tool_call_message(msg))

                for tc in msg.tool_calls:
                    args = json.loads(tc.function.arguments)
                    # _web_search_impl is synchronous but fast; acceptable here.
                    # In production, wrap with asyncio.to_thread().
                    results = _web_search_impl(
                        args["query"], int(args.get("max_results", 5))
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(results),
                        }
                    )
            else:
                return IntelligenceBrief(
                    topic=topic,
                    content=msg.content or "_No intelligence gathered._",
                )
