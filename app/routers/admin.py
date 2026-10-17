"""/api/admin/* – review applications, verify payments, download resumes, audit log."""
import logging
import re
import uuid
from collections import Counter
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response

from app import database
from app.auth import require_admin
from app.limiter import limiter
from app.schemas import AdminUpdate
from app.services import audit_service, email_service, file_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

LIST_FETCH_CAP = 1000  # rows pulled before in-memory text search / pagination
LIST_COLUMNS = (
    "id,user_id,reference_number,full_name,email,phone,payment_status,application_status,"
    "transaction_id,submitted_at,created_at"
)


def _uuid_or_404(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="Application not found")


def _emails_for(db, user_ids: list[str]) -> dict[str, dict]:
    if not user_ids:
        return {}
    rows = db.table("users").select("id,email,full_name,email_verified").in_("id", list(set(user_ids))).execute().data
    return {r["id"]: r for r in rows}


# ── stats ────────────────────────────────────────────────────────────────────
@router.get("/stats")
def stats():
    db = database.get_db()
    rows = db.table("applications").select("payment_status,application_status").execute().data
    submitted = [r for r in rows if r["application_status"] != "draft"]
    pay = Counter(r["payment_status"] for r in submitted)
    app = Counter(r["application_status"] for r in submitted)
    return {
        "total_submitted": len(submitted),
        "drafts": len(rows) - len(submitted),
        "payment": {k: pay.get(k, 0) for k in ("pending", "verified", "rejected", "unpaid")},
        "application": {k: app.get(k, 0) for k in ("under_review", "shortlisted", "accepted", "rejected")},
    }


# ── list / detail ────────────────────────────────────────────────────────────
@router.get("/applications")
def list_applications(
    status: Optional[str] = Query(None, max_length=30),
    payment_status: Optional[str] = Query(None, max_length=30),
    q: Optional[str] = Query(None, max_length=100),
    include_drafts: bool = False,
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(20, ge=1, le=100),
):
    db = database.get_db()
    query = db.table("applications").select(LIST_COLUMNS)
    if status:
        query = query.eq("application_status", status)
    elif not include_drafts:
        query = query.neq("application_status", "draft")
    if payment_status:
        query = query.eq("payment_status", payment_status)
    rows = query.order("created_at", desc=True).limit(LIST_FETCH_CAP).execute().data

    users = _emails_for(db, [r["user_id"] for r in rows])
    items = []
    for r in rows:
        u = users.get(r["user_id"], {})
        items.append({**r, "account_email": u.get("email", "")})
    if q:
        needle = q.strip().lower()
        items = [
            i
            for i in items
            if needle
            in " ".join(
                str(i.get(k) or "") for k in ("full_name", "email", "account_email", "reference_number", "transaction_id")
            ).lower()
        ]
    total = len(items)
    start = (page - 1) * page_size
    return {"items": items[start : start + page_size], "total": total, "page": page, "page_size": page_size}


def _load_application(db, application_id: str) -> dict:
    rows = db.table("applications").select("*").eq("id", _uuid_or_404(application_id)).limit(1).execute().data
    if not rows:
        raise HTTPException(status_code=404, detail="Application not found")
    return rows[0]


@router.get("/applications/{application_id}")
def application_detail(application_id: str):
    db = database.get_db()
    row = _load_application(db, application_id)
    user = _emails_for(db, [row["user_id"]]).get(row["user_id"], {})
    out = {k: v for k, v in row.items() if k != "resume_filename"}
    out["resume_url"] = f"/api/admin/resume/{row['resume_filename']}" if row.get("resume_filename") else None
    out["account_email"] = user.get("email", "")
    out["account_email_verified"] = bool(user.get("email_verified"))
    return out


@router.patch("/applications/{application_id}")
@limiter.limit("60/minute")
def update_application(
    request: Request,
    application_id: str,
    data: AdminUpdate,
    background: BackgroundTasks,
    admin: dict = Depends(require_admin),
):
    db = database.get_db()
    row = _load_application(db, application_id)

    changes = data.model_dump(exclude_none=True)
    new_payment = changes.get("payment_status", row["payment_status"])
    new_status = changes.get("application_status", row["application_status"])

    if new_status in ("shortlisted", "accepted") and new_payment != "verified":
        raise HTTPException(status_code=422, detail="Payment must be verified before shortlisting or accepting an application")
    if new_payment == "verified" and not row.get("transaction_id"):
        raise HTTPException(status_code=422, detail="No transaction ID has been submitted for this application")
    if "payment_status" in changes and changes["payment_status"] in ("unpaid", "rejected") and "application_status" not in changes:
        if row["application_status"] in ("shortlisted", "accepted"):
            raise HTTPException(status_code=422, detail="Change the application status first; it requires verified payment")

    diff = {k: {"from": row.get(k), "to": v} for k, v in changes.items() if row.get(k) != v}
    if not diff:
        return {"message": "No changes", "application_id": row["id"]}
    if "admin_notes" in diff:  # don't copy free-text notes into the audit trail
        diff["admin_notes"] = {"changed": True}

    db.table("applications").update(changes).eq("id", row["id"]).execute()
    audit_service.log_action(
        admin["id"], "application_update", "application", row["id"],
        {"reference": row["reference_number"], "changes": diff}, request,
    )

    if ("payment_status" in diff or "application_status" in diff) and row.get("email"):
        background.add_task(
            email_service.send_status_email, row["email"], row["full_name"],
            row["reference_number"], new_payment, new_status,
        )
    return {"message": "Application updated", "application_id": row["id"], **{
        "payment_status": new_payment, "application_status": new_status}}


# ── resume download ──────────────────────────────────────────────────────────
@router.get("/resume/{filename}")
def download_resume(request: Request, filename: str, admin: dict = Depends(require_admin)):
    if not file_service.STORED_NAME_RE.fullmatch(filename):
        raise HTTPException(status_code=404, detail="File not found")
    db = database.get_db()
    rows = (
        db.table("applications")
        .select("id,reference_number,resume_original_name")
        .eq("resume_filename", filename)
        .limit(1)
        .execute()
        .data
    )
    if not rows:  # only files that belong to an application can be fetched
        raise HTTPException(status_code=404, detail="File not found")
    content = file_service.read_resume(filename)
    audit_service.log_action(
        admin["id"], "resume_download", "application", rows[0]["id"],
        {"reference": rows[0]["reference_number"]}, request,
    )
    download_name = file_service.display_name(rows[0].get("resume_original_name") or filename)
    ext = re.search(r"\.[A-Za-z0-9]+$", filename).group(0)
    if not download_name.lower().endswith(ext):
        download_name += ext
    return Response(
        content=content,
        media_type=file_service.media_type_for(filename),
        headers={
            "Content-Disposition": f'attachment; filename="{download_name}"',
            "Cache-Control": "private, no-store",
        },
    )


# ── audit log ────────────────────────────────────────────────────────────────
@router.get("/audit-log")
def audit_log(limit: int = Query(100, ge=1, le=500), target_id: Optional[str] = Query(None, max_length=64)):
    db = database.get_db()
    query = db.table("audit_log").select("*")
    if target_id:
        query = query.eq("target_id", target_id)
    rows = query.order("created_at", desc=True).limit(limit).execute().data
    admins = _emails_for(db, [r["admin_id"] for r in rows if r.get("admin_id")])
    return {
        "items": [{**r, "admin_email": admins.get(r.get("admin_id"), {}).get("email", "")} for r in rows]
    }
