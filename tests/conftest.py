"""Test fixtures: isolated settings, an in-memory fake Supabase, captured emails."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Environment must be set BEFORE the app (and its cached settings) is imported.
_UPLOADS = tempfile.mkdtemp(prefix="resumes-")
os.environ.update(
    ENVIRONMENT="development",
    SECRET_KEY="0123456789abcdef" * 4,
    UPLOAD_DIR=_UPLOADS,
    STORAGE_BACKEND="local",
    SMTP_HOST="", SMTP_USER="", SMTP_PASSWORD="",
    SUPABASE_URL="https://example.supabase.co",
    SUPABASE_SERVICE_KEY="x" * 60,
    ASSESSMENT_FEE="500", ASSESSMENT_CURRENCY="INR",
    PAYMENT_ACCOUNT_NAME="Test Org", PAYMENT_UPI_ID="testorg@upi",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.database as database  # noqa: E402
from app.limiter import limiter  # noqa: E402
from app.services import email_service  # noqa: E402
from fake_supabase import FakeDB  # noqa: E402
import main  # noqa: E402

PASSWORD = "Sup3rSecret-Pass"


@pytest.fixture(autouse=True)
def fake_db(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(database, "get_db", lambda: db)
    monkeypatch.setattr(database, "get_supabase", lambda: db)
    return db


@pytest.fixture(autouse=True)
def no_rate_limit():
    limiter.reset()
    limiter.enabled = False
    yield
    limiter.enabled = True
    limiter.reset()


@pytest.fixture
def emails(monkeypatch):
    sent = []

    def capture(to, subject, body):
        sent.append({"to": to, "subject": subject, "body": body})
        return True

    monkeypatch.setattr(email_service, "send_email", capture)
    return sent


@pytest.fixture
def new_client():
    clients = []

    def make():
        c = TestClient(main.app, raise_server_exceptions=True)
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.close()


@pytest.fixture
def client(new_client):
    return new_client()


def token_from(emails, to):
    """Extract the token query value from the latest email sent to `to`."""
    for mail in reversed(emails):
        if mail["to"] == to and "token=" in mail["body"]:
            return mail["body"].split("token=")[1].split()[0]
    raise AssertionError(f"no token email for {to}")


@pytest.fixture
def make_user(emails, new_client, fake_db):
    """Register + verify (+ optionally promote) a user; returns a logged-in TestClient."""

    def make(email="alice@example.com", name="Alice Applicant", admin=False, password=PASSWORD, login=True):
        c = new_client()
        r = c.post("/api/auth/register", json={"full_name": name, "email": email, "password": password})
        assert r.status_code == 201, r.text
        r = c.post("/api/auth/verify-email", json={"token": token_from(emails, email)})
        assert r.status_code == 200, r.text
        if admin:
            for u in fake_db.tables["users"]:
                if u["email"] == email:
                    u["role"] = "admin"
        if login:
            r = c.post("/api/auth/login", json={"email": email, "password": password})
            assert r.status_code == 200, r.text
        return c

    return make
