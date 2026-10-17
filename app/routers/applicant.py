"""/api/applicant/* – profile + resume submission, payment submission, status tracking."""
import logging
import secrets
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from app import database
from app.auth import get_current_user, utcnow
from app.config import get_settings
from app.limiter import limiter
from app.schemas import PaymentSubmit, ProfileSubmit
from app.services import email_service, file_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/applicant", tags=["applicant"])

PROFILE_FIELDS = list(ProfileSubmit.model_fields.keys())
# Fields applicants must never see.
PRIVATE_FIELDS = {"admin_notes", "user_id", "resume_filename"}
EDITABLE_PAYMENT_STATES = ("unpaid", "rejected")


# ── helpers ──────────────────────────────────────────────────────────────────
def _validation_errors(exc: ValidationError) -> list[dict]:
    out = []
    for err in exc.errors():
        msg = err["msg"].removeprefix("Value error, ")
        out.append({"loc": ["body", *[str(p) for p in err["loc"]]], "msg": msg})
    return out


def public_application(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    data = {k: v for k, v in row.items() if k not in PRIVATE_FIELDS}
    data["has_resume"] = bool(row.get("resume_filename"))
    return data


def _get_application(db, user_id: str) -> Optional[dict]:
    rows = db.table("applications").select("*").eq("user_id", user_id).limit(1).execute().data
    return rows[0] if rows else None


def _assessment_info() -> dict:
    s = get_settings()
    return {"name": s.assessment_name, "fee": s.assessment_fee, "currency": s.assessment_currency}


def _next_step(app_row: Optional[dict]) -> str:
    if not app_row or not app_row.get("resume_filename"):
        return "apply"
    if app_row["payment_status"] in EDITABLE_PAYMENT_STATES:
        return "payment"
    return "track"


# ── status / profile (read) ──────────────────────────────────────────────────
@router.get("/status")
@router.get("/profile")
def status(user: dict = Depends(get_current_user)):
    app_row = _get_application(database.get_db(), user["id"])
    return {
        "user": {"id": user["id"], "email": user["email"], "full_name": user["full_name"], "role": user["role"]},
        "application": public_application(app_row),
        "assessment": _assessment_info(),
        "next_step": _next_step(app_row),
    }


# ── profile (write) ──────────────────────────────────────────────────────────
def _new_reference() -> str:
    return f"ASM-{utcnow().year}-{secrets.token_hex(3).upper()}"


def _save_profile(user: dict, data: ProfileSubmit, upload: Optional[tuple]) -> dict:
    db = database.get_db()
    existing = _get_application(db, user["id"])
    if existing and existing["payment_status"] not in EDITABLE_PAYMENT_STATES:
        raise HTTPException(status_code=409, detail="Your application has been submitted and can no longer be edited")

    new_resume = None
    if upload:
        content, filename, ctype = upload
        new_resume = file_service.save_resume(content, filename, ctype, user["id"])  # validates first
    elif not existing or not existing.get("resume_filename"):
        raise HTTPException(
            status_code=422, detail=[{"loc": ["body", "resume"], "msg": "Please upload your resume"}]
        )

    row = data.model_dump()
    row["date_of_birth"] = row["date_of_birth"] or None
    if new_resume:
        row["resume_filename"] = new_resume
        row["resume_original_name"] = file_service.display_name(upload[1])

    try:
        if existing:
            saved = db.table("applications").update(row).eq("id", existing["id"]).execute().data[0]
        else:
            saved = None
            for _ in range(5):  # reference numbers are random; retry on the (unlikely) collision
                try:
                    saved = (
                        db.table("applications")
                        .insert({**row, "user_id": user["id"], "reference_number": _new_reference()})
                        .execute()
                        .data[0]
                    )
                    break
                except Exception as exc:
                    if not database.is_duplicate_error(exc):
                        raise
                    if _get_application(db, user["id"]):  # a concurrent request already created it
                        raise HTTPException(status_code=409, detail="Application already exists; please reload")
            if saved is None:
                raise HTTPException(status_code=500, detail="Could not allocate a reference number")
    except Exception:
        if new_resume:
            file_service.delete_resume(new_resume)
        raise

    if new_resume and existing and existing.get("resume_filename"):
        file_service.delete_resume(existing["resume_filename"])
    return saved


@router.post("/profile")
@limiter.limit("20/minute")
async def save_profile(request: Request, user: dict = Depends(get_current_user)):
    form = await request.form()
    raw = {k: str(form.get(k) or "") for k in PROFILE_FIELDS}
    if not raw["email"]:
        raw["email"] = user["email"]
    try:
        data = ProfileSubmit(**raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_errors(exc))

    upload = None
    resume = form.get("resume")
    if resume is not None and hasattr(resume, "read") and getattr(resume, "filename", ""):
        limit = get_settings().max_resume_size_bytes
        content = await resume.read(limit + 1)  # never buffer more than limit+1 bytes
        upload = (content, resume.filename, resume.content_type)

    saved = await run_in_threadpool(_save_profile, user, data, upload)
    return {
        "message": "Application saved",
        "application": public_application(saved),
        "next_step": _next_step(saved),
    }


# ── payment ──────────────────────────────────────────────────────────────────
@router.post("/payment")
@limiter.limit("10/minute")
def submit_payment(
    request: Request,
    data: PaymentSubmit,
    background: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    db = database.get_db()
    app_row = _get_application(db, user["id"])
    if not app_row or not app_row.get("resume_filename"):
        raise HTTPException(status_code=400, detail="Please complete your application before submitting payment")
    if app_row["payment_status"] == "verified":
        raise HTTPException(status_code=409, detail="Your payment has already been verified")
    if app_row["payment_status"] == "pending":
        raise HTTPException(status_code=409, detail="Your payment is already awaiting verification")

    clash = db.table("applications").select("id").eq("transaction_id", data.transaction_id).limit(1).execute().data
    if clash and clash[0]["id"] != app_row["id"]:
        raise HTTPException(status_code=409, detail="This transaction ID has already been used")

    s = get_settings()
    now = utcnow().isoformat()
    updated = (
        db.table("applications")
        .update(
            {
                "transaction_id": data.transaction_id,
                "payment_status": "pending",  # never auto-approved; an admin must verify it
                "payment_amount": s.assessment_fee,
                "payment_submitted_at": now,
                "application_status": "under_review",
                "submitted_at": app_row.get("submitted_at") or now,
            }
        )
        .eq("id", app_row["id"])
        .execute()
        .data[0]
    )
    background.add_task(
        email_service.send_submission_email, user["email"], user["full_name"], updated["reference_number"]
    )
    return {"message": "Payment details submitted for verification", "application": public_application(updated)}
