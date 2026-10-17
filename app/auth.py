"""Password hashing, JWT sessions and FastAPI auth dependencies."""
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, Response
from jose import JWTError, jwt
from passlib.context import CryptContext

from app import database
from app.config import get_settings

ALGORITHM = "HS256"
pwd_context = CryptContext(schemes=["bcrypt"], bcrypt__rounds=12, deprecated="auto")
# Used to burn the same CPU time when a login email does not exist (timing side-channel).
_DUMMY_HASH = pwd_context.hash("not-a-real-password")


# ── time / tokens ────────────────────────────────────────────────────────────
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


_FRACTION_RE = re.compile(r"\.(\d{1,9})")


def parse_ts(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO timestamp. Works on Python 3.10 too, whose fromisoformat() only accepts
    exactly 3 or 6 fractional digits (Postgres may return 1-6)."""
    if not value:
        return None
    value = value.strip().replace("Z", "+00:00")
    value = _FRACTION_RE.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), value, count=1)
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def new_token() -> str:
    """Random URL-safe token for email verification / password reset."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Only the SHA-256 of a token is stored, never the token itself."""
    return hashlib.sha256(token.encode()).hexdigest()


# ── passwords ────────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: Optional[str]) -> bool:
    if not hashed:
        pwd_context.verify(plain, _DUMMY_HASH)
        return False
    try:
        return pwd_context.verify(plain, hashed)
    except Exception:
        return False


# ── JWT ──────────────────────────────────────────────────────────────────────
def create_access_token(data: dict, expires_minutes: Optional[int] = None) -> str:
    s = get_settings()
    minutes = expires_minutes if expires_minutes is not None else s.access_token_expire_minutes
    now = utcnow()
    payload = {**data, "iat": now, "exp": now + timedelta(minutes=minutes)}
    return jwt.encode(payload, s.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Return the token payload or raise jose.JWTError."""
    return jwt.decode(token, get_settings().secret_key, algorithms=[ALGORITHM])


def set_session_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        key=s.session_cookie_name,
        value=token,
        max_age=s.access_token_expire_minutes * 60,
        httponly=True,
        samesite="lax",
        secure=s.force_https,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(get_settings().session_cookie_name, path="/")


# ── dependencies ─────────────────────────────────────────────────────────────
USER_COLUMNS = "id,email,full_name,role,email_verified,is_active"


def get_current_user(request: Request) -> dict:
    s = get_settings()
    token = request.cookies.get(s.session_cookie_name)
    if not token:
        header = request.headers.get("Authorization", "")
        if header.lower().startswith("bearer "):
            token = header[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_access_token(token)
    except JWTError:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid session")

    rows = (
        database.get_db().table("users").select(USER_COLUMNS).eq("id", user_id).limit(1).execute().data
    )
    if not rows:
        raise HTTPException(status_code=401, detail="Account not found")
    user = rows[0]
    if not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="Account disabled")
    return user


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
