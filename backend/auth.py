"""backend/auth.py
JWT creation and FastAPI dependency helpers shared by both routers.
"""

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from config.settings import get_settings

settings = get_settings()

_bearer = HTTPBearer()


# ---------------------------------------------------------------------------
# Token creation
# ---------------------------------------------------------------------------
def create_token(data: dict) -> str:
    """Encode a JWT with an expiry set by JWT_EXPIRE_HOURS."""
    payload = data.copy()
    payload["exp"] = datetime.now(tz=timezone.utc) + timedelta(
        hours=settings.jwt_expire_hours
    )
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------
async def require_employee(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    """Dependency – validates JWT and asserts role == 'employee'."""
    payload = _decode_token(creds.credentials)
    if payload.get("role") != "employee":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Employee access required")
    return payload


async def require_admin(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    """Dependency – validates JWT and asserts role == "admin"."""
    payload = _decode_token(creds.credentials)
    if payload.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CEO access required")
    return payload
