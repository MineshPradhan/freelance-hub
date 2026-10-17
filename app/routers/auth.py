"""/api/auth/* – registration, email verification, login/logout, password reset."""
import logging
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response

from app import auth as auth_utils
from app import database
from app.config import get_settings
from app.limiter import limiter
from app.schemas import (
    EmailRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenRequest,
)
from app.services import email_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

VERIFY_TTL = timedelta(hours=24)
RESET_TTL = timedelta(hours=1)
GENERIC_RESET_MSG = "If an account exists for that email, we have sent further instructions."


def _issue_verification(db, user_id: str) -> str:
    token = auth_utils.new_token()
    db.table("users").update(
        {
            "verification_token_hash": auth_utils.hash_token(token),
            "verification_expires": (auth_utils.utcnow() + VERIFY_TTL).isoformat(),
        }
    ).eq("id", user_id).execute()
    return token


@router.post("/register", status_code=201)
@limiter.limit("10/minute")
def register(request: Request, data: RegisterRequest, background: BackgroundTasks):
    db = database.get_db()
    existing = db.table("users").select("id").eq("email", data.email).limit(1).execute().data
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    try:
        created = (
            db.table("users")
            .insert(
                {
                    "email": data.email,
                    "full_name": data.full_name,
                    "password_hash": auth_utils.hash_password(data.password),
                    "role": "applicant",
                    "email_verified": False,
                }
            )
            .execute()
            .data
        )
    except Exception as exc:
        if database.is_duplicate_error(exc):
            raise HTTPException(status_code=409, detail="An account with this email already exists")
        raise
    user = created[0]
    token = _issue_verification(db, user["id"])
    background.add_task(email_service.send_verification_email, data.email, data.full_name, token)
    return {"message": "Account created. Please check your email to verify your address."}


@router.post("/verify-email")
@limiter.limit("20/minute")
def verify_email(request: Request, data: TokenRequest):
    db = database.get_db()
    rows = (
        db.table("users")
        .select("id,verification_expires,email_verified")
        .eq("verification_token_hash", auth_utils.hash_token(data.token))
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        raise HTTPException(status_code=400, detail="This verification link is invalid or has already been used")
    user = rows[0]
    expires = auth_utils.parse_ts(user.get("verification_expires"))
    if not expires or expires < auth_utils.utcnow():
        raise HTTPException(status_code=400, detail="This verification link has expired. Please request a new one.")
    db.table("users").update(
        {"email_verified": True, "verification_token_hash": None, "verification_expires": None}
    ).eq("id", user["id"]).execute()
    return {"message": "Email verified. You can now log in."}


@router.post("/resend-verification")
@limiter.limit("5/minute")
def resend_verification(request: Request, data: EmailRequest, background: BackgroundTasks):
    db = database.get_db()
    rows = (
        db.table("users").select("id,full_name,email_verified,is_active").eq("email", data.email).limit(1).execute().data
    )
    if rows and not rows[0]["email_verified"] and rows[0].get("is_active", True):
        user = rows[0]
        token = _issue_verification(db, user["id"])
        background.add_task(email_service.send_verification_email, data.email, user["full_name"], token)
    return {"message": "If that account exists and is not yet verified, we have sent a new link."}


@router.post("/login")
@limiter.limit("10/minute")
def login(request: Request, response: Response, data: LoginRequest):
    db = database.get_db()
    rows = (
        db.table("users")
        .select("id,email,full_name,role,password_hash,email_verified,is_active")
        .eq("email", data.email)
        .limit(1)
        .execute()
        .data
    )
    user = rows[0] if rows else None
    # Always run a bcrypt verification so response time does not reveal whether the email exists.
    valid = auth_utils.verify_password(data.password, user["password_hash"] if user else None)
    if not user or not valid:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="This account has been disabled")
    if not user.get("email_verified"):
        raise HTTPException(status_code=403, detail="Please verify your email address before logging in")

    token = auth_utils.create_access_token({"sub": user["id"], "role": user["role"]})
    auth_utils.set_session_cookie(response, token)
    return {
        "message": "Login successful",
        "role": user["role"],
        "redirect": "/admin" if user["role"] == "admin" else "/dashboard",
    }


@router.post("/logout")
def logout(response: Response):
    auth_utils.clear_session_cookie(response)
    return {"message": "Logged out"}


@router.get("/session")
def session(request: Request):
    """Like /me but never errors: lets pages check login state without a 401 in the console."""
    try:
        user = auth_utils.get_current_user(request)
    except HTTPException:
        return {"authenticated": False, "user": None}
    return {
        "authenticated": True,
        "user": {"id": user["id"], "email": user["email"], "full_name": user["full_name"], "role": user["role"]},
    }


@router.get("/me")
def me(user: dict = Depends(auth_utils.get_current_user)):
    return {
        "id": user["id"],
        "email": user["email"],
        "full_name": user["full_name"],
        "role": user["role"],
    }


@router.post("/forgot-password")
@limiter.limit("5/minute")
def forgot_password(request: Request, data: EmailRequest, background: BackgroundTasks):
    db = database.get_db()
    rows = db.table("users").select("id,full_name,is_active").eq("email", data.email).limit(1).execute().data
    if rows and rows[0].get("is_active", True):
        user = rows[0]
        token = auth_utils.new_token()
        db.table("users").update(
            {
                "reset_token_hash": auth_utils.hash_token(token),
                "reset_expires": (auth_utils.utcnow() + RESET_TTL).isoformat(),
            }
        ).eq("id", user["id"]).execute()
        background.add_task(email_service.send_password_reset_email, data.email, user["full_name"], token)
    return {"message": GENERIC_RESET_MSG}


@router.post("/reset-password")
@limiter.limit("5/minute")
def reset_password(request: Request, data: ResetPasswordRequest):
    db = database.get_db()
    rows = (
        db.table("users")
        .select("id,reset_expires")
        .eq("reset_token_hash", auth_utils.hash_token(data.token))
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has already been used")
    expires = auth_utils.parse_ts(rows[0].get("reset_expires"))
    if not expires or expires < auth_utils.utcnow():
        raise HTTPException(status_code=400, detail="This reset link has expired. Please request a new one.")
    db.table("users").update(
        {
            "password_hash": auth_utils.hash_password(data.password),
            "reset_token_hash": None,
            "reset_expires": None,
            "email_verified": True,  # receiving the email proves ownership of the address
        }
    ).eq("id", rows[0]["id"]).execute()
    return {"message": "Password updated. You can now log in."}
