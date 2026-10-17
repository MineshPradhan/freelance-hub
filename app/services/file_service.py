"""Resume upload: validation (extension, MIME, magic bytes, size) and private storage."""
import hashlib
import io
import os
import re
import secrets
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

from app.config import get_settings

ALLOWED_TYPES = {
    ".pdf": {"application/pdf"},
    ".doc": {"application/msword"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
}
MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
# Browsers/OSes sometimes label documents with a generic type; the magic bytes decide.
GENERIC_MIME = {"application/octet-stream", "binary/octet-stream", ""}
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

STORED_NAME_RE = re.compile(r"^[0-9a-f]{24}_[0-9a-f]{16}\.(pdf|doc|docx)$")


def ensure_upload_dir() -> Path:
    path = get_settings().upload_path
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ext(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower()


def _safe_filename(original: str, user_id: str = "") -> str:
    """Server-generated storage name. No user-controlled text ends up in it except the
    (whitelisted) extension: sha256(user, time-salt, name) + random token."""
    ext = _ext(original)
    if ext not in ALLOWED_TYPES:
        raise HTTPException(status_code=422, detail="Resume must be a PDF, DOC or DOCX file")
    digest = hashlib.sha256(f"{user_id}:{original}:{secrets.token_hex(8)}".encode()).hexdigest()[:24]
    return f"{digest}_{secrets.token_hex(8)}{ext}"


def display_name(original: str) -> str:
    """Sanitised original filename, only ever used for display / download name."""
    base = os.path.basename((original or "").replace("\\", "/"))
    base = re.sub(r"[^A-Za-z0-9._ \-]", "_", base).strip(" .")
    return base[:100] or "resume"


def validate_resume(content: bytes, filename: str, content_type: Optional[str]) -> str:
    """Validate an upload and return its extension. Raises HTTPException(422/413)."""
    s = get_settings()
    ext = _ext(filename)
    if ext not in ALLOWED_TYPES:
        raise HTTPException(status_code=422, detail="Resume must be a PDF, DOC or DOCX file")
    if not content:
        raise HTTPException(status_code=422, detail="The uploaded resume is empty")
    if len(content) > s.max_resume_size_bytes:
        mb = s.max_resume_size_bytes // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"Resume must be {mb} MB or smaller")

    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype not in ALLOWED_TYPES[ext] and ctype not in GENERIC_MIME:
        raise HTTPException(status_code=422, detail="File type does not match its extension")

    if ext == ".pdf":
        ok = content.startswith(b"%PDF-")
    elif ext == ".doc":
        ok = content.startswith(OLE_MAGIC)
    else:  # .docx is a zip archive containing word/document.xml
        ok = False
        if content.startswith(b"PK\x03\x04"):
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as z:
                    ok = "word/document.xml" in z.namelist()
            except zipfile.BadZipFile:
                ok = False
    if not ok:
        raise HTTPException(status_code=422, detail="File content does not match a valid PDF/DOC/DOCX document")
    return ext


# ── storage backends ─────────────────────────────────────────────────────────
def _local_path(stored_name: str) -> Path:
    if not STORED_NAME_RE.fullmatch(stored_name or ""):
        raise HTTPException(status_code=404, detail="File not found")
    base = ensure_upload_dir().resolve()
    path = (base / stored_name).resolve()
    if base not in path.parents:  # defence in depth against path traversal
        raise HTTPException(status_code=404, detail="File not found")
    return path


def save_resume(content: bytes, original_name: str, content_type: Optional[str], user_id: str) -> str:
    """Validate and store a resume. Returns the generated storage filename."""
    ext = validate_resume(content, original_name, content_type)
    stored = _safe_filename(f"x{ext}", user_id)
    s = get_settings()
    if s.storage_backend == "supabase":
        from app import database

        database.get_db().storage.from_(s.storage_bucket).upload(
            path=stored, file=content, file_options={"content-type": MEDIA_TYPES[ext]}
        )
    else:
        with open(_local_path(stored), "xb") as fh:
            fh.write(content)
    return stored


def read_resume(stored_name: str) -> bytes:
    s = get_settings()
    if s.storage_backend == "supabase":
        from app import database

        if not STORED_NAME_RE.fullmatch(stored_name or ""):
            raise HTTPException(status_code=404, detail="File not found")
        try:
            return database.get_db().storage.from_(s.storage_bucket).download(stored_name)
        except Exception:
            raise HTTPException(status_code=404, detail="File not found")
    path = _local_path(stored_name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return path.read_bytes()


def delete_resume(stored_name: Optional[str]) -> None:
    if not stored_name:
        return
    s = get_settings()
    try:
        if s.storage_backend == "supabase":
            from app import database

            database.get_db().storage.from_(s.storage_bucket).remove([stored_name])
        else:
            _local_path(stored_name).unlink(missing_ok=True)
    except Exception:
        pass  # best effort cleanup


def media_type_for(stored_name: str) -> str:
    return MEDIA_TYPES.get(_ext(stored_name), "application/octet-stream")
