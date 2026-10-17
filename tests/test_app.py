"""End-to-end tests: pages, auth, application flow, payment, admin review, security."""
import io
import re
import zipfile
from datetime import timedelta

import pytest

from app import auth as auth_utils
from app.services import file_service
from conftest import PASSWORD, token_from

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


def make_docx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document/>")
    return buf.getvalue()


PROFILE = {
    "full_name": "Alice Applicant", "email": "alice@example.com", "phone": "+91 98765 43210",
    "date_of_birth": "1998-04-12", "address": "12 MG Road", "city": "Indore", "state": "MP",
    "postal_code": "452001", "country": "India", "education": "Bachelor's degree",
    "institution": "NIT Raipur", "field_of_study": "IT", "graduation_year": "2020",
    "experience_level": "1-3 years", "current_employer": "Acme", "job_title": "Developer", "summary": "Builds things.",
}


def submit_profile(c, resume=("cv.pdf", PDF, "application/pdf"), **overrides):
    files = {"resume": resume} if resume else None
    return c.post("/api/applicant/profile", data={**PROFILE, **overrides}, files=files)


# ── pages ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", [
    "/", "/register", "/login", "/auth/verify-email", "/auth/verify-email?token=abc", "/auth/forgot-password",
    "/auth/reset-password?token=abc", "/apply", "/payment", "/dashboard", "/confirmation", "/admin",
    "/admin/application/123", "/static/css/main.css", "/static/js/main.js", "/static/qrcode.jpeg",
])
def test_pages_render(client, path):
    r = client.get(path)
    assert r.status_code == 200, path


def test_payment_page_shows_configured_details(client):
    html = client.get("/payment").text
    assert "testorg@upi" in html and "Test Org" in html and "500 INR" in html


def test_template_escapes_token_in_script(client):
    html = client.get('/auth/verify-email?token=</script><script>alert(1)</script>').text
    assert "<script>alert(1)</script>" not in html


def test_404_pages(client):
    r = client.get("/nope")
    assert r.status_code == 404 and "text/html" in r.headers["content-type"]
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.json() == {"detail": "Not found"}


def test_security_headers(client):
    h = client.get("/").headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"


# ── auth ─────────────────────────────────────────────────────────────────────
def test_register_validation(client, emails):
    base = {"full_name": "Bob Tester", "email": "bob@example.com", "password": PASSWORD}
    assert client.post("/api/auth/register", json={**base, "password": "short1"}).status_code == 422
    assert client.post("/api/auth/register", json={**base, "password": "allletterspassword"}).status_code == 422
    assert client.post("/api/auth/register", json={**base, "email": "not-an-email"}).status_code == 422
    assert client.post("/api/auth/register", json={**base, "full_name": "<b>x</b>"}).status_code == 422
    assert client.post("/api/auth/register", json={**base, "password": "a1" * 40}).status_code == 422  # >72 bytes
    r = client.post("/api/auth/register", json={"full_name": "Bob Tester"})
    assert r.status_code == 422 and isinstance(r.json()["detail"], list)


def test_register_duplicate_and_verification_email(client, emails, fake_db):
    base = {"full_name": "Bob Tester", "email": "Bob@Example.com", "password": PASSWORD}
    assert client.post("/api/auth/register", json=base).status_code == 201
    assert client.post("/api/auth/register", json=base).status_code == 409
    assert len(emails) == 1 and emails[0]["to"] == "bob@example.com"
    user = fake_db.tables["users"][0]
    assert user["password_hash"].startswith("$2") and PASSWORD not in str(user)
    token = token_from(emails, "bob@example.com")
    assert token not in str(user)  # only a hash of the token is stored


def test_login_requires_verification_and_sets_secure_cookie(client, emails):
    client.post("/api/auth/register", json={"full_name": "Carol Test", "email": "carol@example.com", "password": PASSWORD})
    r = client.post("/api/auth/login", json={"email": "carol@example.com", "password": PASSWORD})
    assert r.status_code == 403 and "verify" in r.json()["detail"].lower()
    assert client.post("/api/auth/verify-email", json={"token": token_from(emails, "carol@example.com")}).status_code == 200
    r = client.post("/api/auth/login", json={"email": "carol@example.com", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["redirect"] == "/dashboard"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert client.get("/api/auth/me").json()["email"] == "carol@example.com"


def test_login_failures_are_generic(client, make_user):
    make_user("dan@example.com", login=False)
    wrong = client.post("/api/auth/login", json={"email": "dan@example.com", "password": "Wrong-Pass-123"})
    missing = client.post("/api/auth/login", json={"email": "ghost@example.com", "password": "Wrong-Pass-123"})
    assert wrong.status_code == missing.status_code == 401 and wrong.json() == missing.json()


def test_verification_token_single_use_and_expiry(client, emails, fake_db):
    client.post("/api/auth/register", json={"full_name": "Eve Test", "email": "eve@example.com", "password": PASSWORD})
    token = token_from(emails, "eve@example.com")
    assert client.post("/api/auth/verify-email", json={"token": "x" * 30}).status_code == 400
    fake_db.tables["users"][0]["verification_expires"] = (auth_utils.utcnow() - timedelta(minutes=1)).isoformat()
    r = client.post("/api/auth/verify-email", json={"token": token})
    assert r.status_code == 400 and "expired" in r.json()["detail"]
    client.post("/api/auth/resend-verification", json={"email": "eve@example.com"})
    fresh = token_from(emails, "eve@example.com")
    assert fresh != token
    assert client.post("/api/auth/verify-email", json={"token": fresh}).status_code == 200
    assert client.post("/api/auth/verify-email", json={"token": fresh}).status_code == 400


def test_logout_and_protected_routes(make_user, client):
    c = make_user("frank@example.com")
    assert c.get("/api/applicant/status").status_code == 200
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/applicant/status").status_code == 401
    for path in ("/api/applicant/status", "/api/applicant/profile", "/api/admin/applications", "/api/admin/audit-log"):
        assert client.get(path).status_code == 401, path


def test_session_endpoint_never_errors(client, make_user):
    r = client.get("/api/auth/session")
    assert r.status_code == 200 and r.json() == {"authenticated": False, "user": None}
    c = make_user("sam@example.com")
    body = c.get("/api/auth/session").json()
    assert body["authenticated"] is True and body["user"]["email"] == "sam@example.com" and "password_hash" not in str(body)


def test_tampered_or_expired_jwt_rejected(client, make_user, fake_db):
    make_user("gina@example.com")
    uid = fake_db.tables["users"][0]["id"]
    client.cookies.set("assessment_session", "garbage.token.value")
    assert client.get("/api/auth/me").status_code == 401
    expired = auth_utils.create_access_token({"sub": uid, "role": "applicant"}, expires_minutes=-5)
    client.cookies.set("assessment_session", expired)
    assert client.get("/api/auth/me").status_code == 401


def test_password_reset_flow(client, make_user, emails):
    make_user("hank@example.com", login=False)
    assert client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"}).status_code == 200  # no enumeration
    assert not any(m["to"] == "nobody@example.com" for m in emails)
    assert client.post("/api/auth/forgot-password", json={"email": "hank@example.com"}).status_code == 200
    token = token_from(emails, "hank@example.com")
    assert client.post("/api/auth/reset-password", json={"token": token, "password": "weak"}).status_code == 422
    new_pw = "Brand-New-Pass-42"
    assert client.post("/api/auth/reset-password", json={"token": token, "password": new_pw}).status_code == 200
    assert client.post("/api/auth/reset-password", json={"token": token, "password": new_pw}).status_code == 400
    assert client.post("/api/auth/login", json={"email": "hank@example.com", "password": PASSWORD}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "hank@example.com", "password": new_pw}).status_code == 200


def test_rate_limiting_on_login(client):
    from app.limiter import limiter
    limiter.enabled = True
    codes = [client.post("/api/auth/login", json={"email": "x@example.com", "password": "nope-nope-1"}).status_code for _ in range(12)]
    assert 429 in codes and codes[0] == 401


# ── applicant flow ───────────────────────────────────────────────────────────
def test_full_applicant_flow(make_user, emails, fake_db):
    c = make_user("alice@example.com")
    s = c.get("/api/applicant/status").json()
    assert s["application"] is None and s["next_step"] == "apply"
    assert s["assessment"] == {"name": "Professional Skills Assessment", "fee": 500, "currency": "INR"}

    r = submit_profile(c)
    assert r.status_code == 200, r.text
    a = r.json()["application"]
    assert re.fullmatch(r"ASM-\d{4}-[0-9A-F]{6}", a["reference_number"])
    assert a["payment_status"] == "unpaid" and a["application_status"] == "draft"
    assert "resume_filename" not in a and "admin_notes" not in a and a["has_resume"] is True
    assert r.json()["next_step"] == "payment"

    # Resume stored under a generated name, outside any web-served path.
    stored = fake_db.tables["applications"][0]["resume_filename"]
    assert file_service.STORED_NAME_RE.fullmatch(stored) and "cv" not in stored
    assert (file_service.get_settings().upload_path / stored).read_bytes() == PDF

    # Editing before payment works, without re-uploading the resume.
    r = submit_profile(c, resume=None, city="Bhopal")
    assert r.status_code == 200 and r.json()["application"]["city"] == "Bhopal"
    assert fake_db.tables["applications"][0]["resume_filename"] == stored

    # Payment validation
    assert c.post("/api/applicant/payment", json={"transaction_id": "abc"}).status_code == 422
    assert c.post("/api/applicant/payment", json={"transaction_id": "bad id!;--"}).status_code == 422
    r = c.post("/api/applicant/payment", json={"transaction_id": "utr123456789"})
    assert r.status_code == 200, r.text
    a = r.json()["application"]
    assert a["payment_status"] == "pending" and a["application_status"] == "under_review"  # never auto-approved
    assert a["transaction_id"] == "UTR123456789" and a["submitted_at"]
    assert any("Application received" in m["subject"] for m in emails)

    # Locked after submission
    assert submit_profile(c).status_code == 409
    assert c.post("/api/applicant/payment", json={"transaction_id": "UTR999999999"}).status_code == 409
    assert c.get("/api/applicant/status").json()["next_step"] == "track"


def test_cannot_pay_before_applying(make_user):
    c = make_user("ivy@example.com")
    assert c.post("/api/applicant/payment", json={"transaction_id": "UTR123456789"}).status_code == 400


def test_profile_validation(make_user):
    c = make_user("jack@example.com")
    r = submit_profile(c, resume=None)
    assert r.status_code == 422 and "resume" in str(r.json())
    r = submit_profile(c, full_name="", phone="abc", date_of_birth="2030-01-01")
    assert r.status_code == 422
    fields = {e["loc"][-1] for e in r.json()["detail"]}
    assert {"full_name", "phone", "date_of_birth"} <= fields
    assert submit_profile(c, graduation_year="1800").status_code == 422


@pytest.mark.parametrize("name,content,ctype", [
    ("virus.exe", b"MZ\x90\x00", "application/octet-stream"),
    ("cv.pdf", b"not really a pdf", "application/pdf"),                  # bad magic bytes
    ("cv.docx", b"PK\x03\x04junk", "application/octet-stream"),         # zip but not docx
    ("cv.pdf", b"", "application/pdf"),                                  # empty
    ("cv.pdf", PDF, "text/html"),                                        # wrong MIME
    ("cv.pdf.exe", PDF, "application/pdf"),                              # double extension
])
def test_bad_resume_rejected(make_user, name, content, ctype):
    c = make_user("kate@example.com")
    r = submit_profile(c, resume=(name, content, ctype))
    assert r.status_code == 422, r.text


def test_resume_size_limit_and_docx_accepted(make_user):
    c = make_user("leo@example.com")
    assert submit_profile(c, resume=("big.pdf", PDF + b"0" * (5 * 1024 * 1024), "application/pdf")).status_code == 413
    assert submit_profile(c, resume=("cv.docx", make_docx(), "application/octet-stream")).status_code == 200


def test_duplicate_transaction_id_rejected(make_user):
    a, b = make_user("a@example.com"), make_user("b@example.com", name="Bee Person")
    for c, mail in ((a, "a@example.com"), (b, "b@example.com")):
        assert submit_profile(c, email=mail).status_code == 200
    assert a.post("/api/applicant/payment", json={"transaction_id": "UTR555555555"}).status_code == 200
    r = b.post("/api/applicant/payment", json={"transaction_id": "utr555555555"})
    assert r.status_code == 409 and "already been used" in r.json()["detail"]


# ── admin ────────────────────────────────────────────────────────────────────
@pytest.fixture
def submitted(make_user):
    """An applicant with a submitted application + payment, plus a logged-in admin client."""
    applicant = make_user("alice@example.com")
    submit_profile(applicant)
    applicant.post("/api/applicant/payment", json={"transaction_id": "UTR123456789"})
    admin = make_user("boss@example.com", name="The Boss", admin=True)
    app_id = admin.get("/api/admin/applications").json()["items"][0]["id"]
    return applicant, admin, app_id


def test_applicants_cannot_use_admin_api(submitted):
    applicant, _, app_id = submitted
    assert applicant.get("/api/admin/applications").status_code == 403
    assert applicant.get(f"/api/admin/applications/{app_id}").status_code == 403
    assert applicant.patch(f"/api/admin/applications/{app_id}", json={"payment_status": "verified"}).status_code == 403
    assert applicant.get("/api/admin/audit-log").status_code == 403


def test_admin_list_filter_search(submitted, make_user):
    _, admin, _ = submitted
    make_user("draft@example.com", name="Drafty Person")  # an account with no application
    r = admin.get("/api/admin/applications").json()
    assert r["total"] == 1 and r["items"][0]["reference_number"].startswith("ASM-")
    assert admin.get("/api/admin/applications?payment_status=verified").json()["total"] == 0
    assert admin.get("/api/admin/applications?payment_status=pending").json()["total"] == 1
    assert admin.get("/api/admin/applications?q=utr1234").json()["total"] == 1
    assert admin.get("/api/admin/applications?q=zzzz").json()["total"] == 0
    assert admin.get("/api/admin/applications?page_size=1000").status_code == 422
    stats = admin.get("/api/admin/stats").json()
    assert stats["total_submitted"] == 1 and stats["payment"]["pending"] == 1


def test_admin_detail_and_bad_ids(submitted):
    _, admin, app_id = submitted
    d = admin.get(f"/api/admin/applications/{app_id}").json()
    assert d["full_name"] == "Alice Applicant" and d["account_email"] == "alice@example.com"
    assert d["resume_url"].startswith("/api/admin/resume/") and "resume_filename" not in d
    assert admin.get("/api/admin/applications/not-a-uuid").status_code == 404
    assert admin.get("/api/admin/applications/00000000-0000-0000-0000-000000000000").status_code == 404


def test_admin_resume_download_is_protected(submitted, client):
    _, admin, app_id = submitted
    url = admin.get(f"/api/admin/applications/{app_id}").json()["resume_url"]
    r = admin.get(url)
    assert r.status_code == 200 and r.content == PDF
    assert r.headers["content-disposition"].startswith("attachment") and "cv.pdf" in r.headers["content-disposition"]
    assert r.headers["content-type"] == "application/pdf"
    assert client.get(url).status_code == 401                                  # anonymous
    for bad in ("../../etc/passwd", "..%2F..%2Fmain.py", "evil.pdf", "a" * 24 + "_" + "b" * 16 + ".pdf"):
        assert admin.get(f"/api/admin/resume/{bad}").status_code == 404, bad


def test_admin_review_rules_audit_and_notifications(submitted, emails, make_user):
    applicant, admin, app_id = submitted
    url = f"/api/admin/applications/{app_id}"
    assert admin.patch(url, json={}).status_code == 422
    assert admin.patch(url, json={"payment_status": "bogus"}).status_code == 422
    # can't shortlist/accept before payment is verified
    assert admin.patch(url, json={"application_status": "accepted"}).status_code == 422

    # reject payment -> applicant may resubmit
    assert admin.patch(url, json={"payment_status": "rejected"}).status_code == 200
    s = applicant.get("/api/applicant/status").json()
    assert s["application"]["payment_status"] == "rejected" and s["next_step"] == "payment"
    assert applicant.post("/api/applicant/payment", json={"transaction_id": "UTR000111222"}).status_code == 200

    n_before = len(emails)
    r = admin.patch(url, json={"payment_status": "verified", "admin_notes": "Looks good. Secret note."})
    assert r.status_code == 200
    assert len(emails) == n_before + 1 and "verified" in emails[-1]["body"] and emails[-1]["to"] == "alice@example.com"
    assert admin.patch(url, json={"application_status": "shortlisted"}).status_code == 200
    assert admin.patch(url, json={"payment_status": "unpaid"}).status_code == 422  # would orphan a shortlisted app

    # no-op update changes nothing / sends nothing
    n = len(emails)
    assert admin.patch(url, json={"application_status": "shortlisted"}).json()["message"] == "No changes"
    assert len(emails) == n

    # applicant sees the result but never the internal notes
    a = applicant.get("/api/applicant/status").json()["application"]
    assert a["payment_status"] == "verified" and a["application_status"] == "shortlisted"
    assert "admin_notes" not in a and "Secret note" not in str(a)

    # audit trail
    log = admin.get("/api/admin/audit-log").json()["items"]
    actions = [i["action"] for i in log]
    assert actions.count("application_update") >= 3
    assert all(i["admin_email"] == "boss@example.com" for i in log)
    assert "Secret note" not in str(log)  # free-text notes are not copied into the log
    assert len(admin.get(f"/api/admin/audit-log?target_id={app_id}").json()["items"]) == len(log)


def test_resume_download_is_audited(submitted):
    _, admin, app_id = submitted
    admin.get(admin.get(f"/api/admin/applications/{app_id}").json()["resume_url"])
    assert "resume_download" in [i["action"] for i in admin.get("/api/admin/audit-log").json()["items"]]


# ── helpers / units ──────────────────────────────────────────────────────────
def test_safe_filename_unit():
    name = file_service._safe_filename("resume.pdf", "user-123")
    assert name.endswith(".pdf") and "/" not in name and "\\" not in name and "resume" not in name
    with pytest.raises(Exception):
        file_service._safe_filename("resume.exe", "user-123")
    assert file_service._safe_filename("a.pdf", "u") != file_service._safe_filename("a.pdf", "u")


def test_display_name_sanitised():
    assert file_service.display_name("../../etc/pa<ss>wd.pdf") == "pa_ss_wd.pdf"
    assert file_service.display_name("") == "resume"


def test_jwt_roundtrip_and_password_hashing():
    token = auth_utils.create_access_token({"sub": "u1", "role": "admin"})
    payload = auth_utils.decode_access_token(token)
    assert payload["sub"] == "u1" and payload["role"] == "admin" and payload["exp"] > payload["iat"]
    h = auth_utils.hash_password("Sup3rSecret-Pass")
    assert auth_utils.verify_password("Sup3rSecret-Pass", h) and not auth_utils.verify_password("nope", h)
    assert not auth_utils.verify_password("anything", None)


def test_unconfigured_database_gives_clear_error(monkeypatch):
    import importlib
    import app.database as db_module
    importlib.reload(db_module)  # restore the real get_supabase (fixtures patched it)
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "supabase_url", "https://YOUR_PROJECT_REF.supabase.co")
    db_module.get_supabase.cache_clear()
    with pytest.raises(db_module.DatabaseNotConfigured, match="SUPABASE_URL"):
        db_module.get_supabase()
