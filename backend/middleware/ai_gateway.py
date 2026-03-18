"""backend/middleware/ai_gateway.py
AI Gateway – security and routing layer applied to every API request.

Responsibilities (in order of execution):
  1. Rate limiting      – per-user, in-memory, sliding 60-second window
  2. Content filtering  – block submissions matching prohibited categories
  3. PII scrubbing      – redact emails / phones / SSNs from request bodies
  4. Request routing    – stamp request.state.target_agent for downstream use
  5. Audit logging      – print structured log line (swap for DB in production)

Configuration is loaded from the active YAML template at startup.
"""

import re
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# ---------------------------------------------------------------------------
# PII regex patterns
# ---------------------------------------------------------------------------
_PII: dict[str, re.Pattern] = {
    "email": re.compile(
        r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
    ),
    "phone": re.compile(
        r"\b(\+?1[-.\s]?)?(\(?\d{3}\)?[-.\s]?)(\d{3}[-.\s]?\d{4})\b"
    ),
    "ssn": re.compile(r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b"),
}

# ---------------------------------------------------------------------------
# Content-filter patterns  (basic keyword list – replace with an LLM-based
# classifier or a moderation API such as OpenAI Moderation in production)
# ---------------------------------------------------------------------------
_BLOCKED: list[re.Pattern] = [
    re.compile(
        r"\b(hate[\s_]?speech|self[\s_]?harm|bomb[\s_]?threat|suicide[\s_]?method)\b",
        re.IGNORECASE,
    ),
]


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
class AIGatewayMiddleware(BaseHTTPMiddleware):
    """
    Starlette BaseHTTPMiddleware that enforces security rules before any
    request reaches a router or agent.
    """

    def __init__(self, app, config: dict) -> None:
        super().__init__(app)
        gateway = config.get("ai_gateway", {})

        self._rpm_limit: int = (
            gateway.get("rate_limiting", {}).get("requests_per_minute_per_user", 20)
        )
        self._pii_enabled: bool = (
            gateway.get("pii_scrubbing", {}).get("enabled", True)
        )
        self._filter_enabled: bool = (
            gateway.get("content_filter", {}).get("enabled", True)
        )

        routing = gateway.get("routing", {})
        self._employee_agent: str = routing.get("employee_portal_agent", "watchdog")
        self._ceo_agent: str = routing.get("ceo_dashboard_agent", "manager")

        # Sliding-window rate-limit store: {identity_key: [unix_timestamps]}
        self._rate_store: dict[str, list[float]] = defaultdict(list)

    # ------------------------------------------------------------------
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Pass through health check and static assets untouched
        if path in ("/health", "/favicon.ico") or path.startswith("/static"):
            return await call_next(request)

        # ── 1. Rate limiting ──────────────────────────────────────────
        # Identify the caller by their Authorization header (preferred)
        # or by client IP as a fallback (e.g. on login endpoint).
        auth_header = request.headers.get("Authorization", "")
        rate_key = auth_header if auth_header else (
            request.client.host if request.client else "unknown"
        )

        now = time.monotonic()
        window_start = now - 60.0
        hits = self._rate_store[rate_key]
        # Purge timestamps outside the current window
        hits[:] = [t for t in hits if t > window_start]

        if len(hits) >= self._rpm_limit:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Please wait a moment and retry."},
            )
        hits.append(now)

        # ── 2 & 3. Content filtering + PII scrubbing ──────────────────
        # Only applies to requests that send a JSON body.
        content_type = request.headers.get("content-type", "")
        if (
            request.method in ("POST", "PUT", "PATCH")
            and "application/json" in content_type
        ):
            raw_bytes = await request.body()
            body_text = raw_bytes.decode("utf-8", errors="ignore")

            # Content filter – reject before touching PII
            if self._filter_enabled:
                for pattern in _BLOCKED:
                    if pattern.search(body_text):
                        return JSONResponse(
                            status_code=400,
                            content={
                                "detail": (
                                    "Submission rejected: content violates usage policy."
                                )
                            },
                        )

            # PII scrubbing – redact in-place
            if self._pii_enabled:
                for label, pattern in _PII.items():
                    body_text = pattern.sub(f"[{label.upper()}-REDACTED]", body_text)

            # Re-inject the (potentially scrubbed) body so downstream
            # route handlers receive it normally via request.body().
            scrubbed = body_text.encode("utf-8")

            async def _receive():
                return {"type": "http.request", "body": scrubbed, "more_body": False}

            request = Request(request.scope, receive=_receive)

        # ── 4. Routing annotation ─────────────────────────────────────
        if "/api/employee" in path:
            request.state.target_agent = self._employee_agent
        elif "/api/ceo" in path:
            request.state.target_agent = self._ceo_agent
        else:
            request.state.target_agent = "none"

        # ── 5. Audit log ──────────────────────────────────────────────
        # PII is never logged: we log only method, path, and target agent.
        # In production, replace this print with an async DB/queue write.
        print(
            f"[GATEWAY] {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} | "
            f"{request.method} {path} | "
            f"agent={request.state.target_agent}"
        )

        return await call_next(request)
