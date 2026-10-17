# Assessment Platform

A production-minded web application where applicants register, submit their profile and resume, pay an assessment fee, and track their application status. Administrators review applications, verify payments, and update statuses.

Stack: **FastAPI** · **Supabase (Postgres + Storage)** · Jinja2 templates + vanilla JS.

---

## Table of Contents
1. [Features](#features)
2. [Architecture](#architecture)
3. [Prerequisites](#prerequisites)
4. [Quick Start (Development)](#quick-start-development)
5. [Database Setup](#database-setup)
6. [Configuration Reference](#configuration-reference)
7. [Promoting an Admin User](#promoting-an-admin-user)
8. [File Storage](#file-storage)
9. [Email Setup](#email-setup)
10. [Running the Tests](#running-the-tests)
11. [Troubleshooting](#troubleshooting)
12. [Running in Production](#running-in-production)
13. [Security Notes](#security-notes)
14. [Remaining Placeholders](#remaining-placeholders)
15. [Known Risks & Hardening TODO](#known-risks--hardening-todo)

---

## Features

- **Secure registration & login** — bcrypt password hashing, JWT sessions (HttpOnly cookies), email verification, password reset, rate limiting
- **Multi-step application form** — personal info, address, education, experience, resume upload (PDF/DOC/DOCX, max 5 MB)
- **Payment workflow** — QR/UPI payment with manual transaction ID submission; admin verifies payments; never auto-approves
- **Application confirmation** — reference number, submission date, payment status, "Under Review" status
- **Applicant dashboard** — view profile, track status, access confirmation details
- **Admin panel** — list/filter/search applications, view details, download resumes securely, update payment/application statuses, add internal notes, view audit log
- **Security** — security headers, rate limiting, CORS, input validation, SQL injection prevention via PostgREST, XSS prevention, private file storage, audit log, role-based access control

### Application flow

```
register → verify email → log in → /apply (profile + resume) → /payment (UPI + transaction ID)
        → /confirmation (reference number) → admin verifies payment → admin shortlists / accepts / rejects
```

Rules enforced by the API:
- An applicant can edit the application until a payment has been submitted. A **rejected** payment unlocks editing and lets them resubmit a transaction ID.
- A transaction ID can only be used once.
- An admin can only shortlist/accept an application whose payment is **verified**.
- Internal admin notes are never returned to applicants.

---

## Architecture

```
.
├── main.py                   # FastAPI app entry point, page routes
├── app/
│   ├── config.py             # Settings (pydantic-settings, reads .env)
│   ├── database.py           # Supabase client singleton (get_supabase)
│   ├── limiter.py            # Shared SlowAPI rate limiter
│   ├── auth.py               # JWT, bcrypt, auth dependencies
│   ├── schemas.py            # Pydantic validation schemas
│   ├── routers/
│   │   ├── auth.py           # /api/auth/* endpoints
│   │   ├── applicant.py      # /api/applicant/* endpoints
│   │   └── admin.py          # /api/admin/* endpoints
│   └── services/
│       ├── file_service.py   # Resume upload, validation, storage
│       ├── email_service.py  # SMTP transactional email
│       └── audit_service.py  # Admin action audit log
├── templates/
│   ├── base.html, index.html # Layout + landing page
│   ├── auth/                 # Register, login, verify, forgot/reset password
│   ├── applicant/            # Multi-step form, payment, confirmation, dashboard
│   ├── admin/                # Admin dashboard, application detail
│   └── errors/               # 404, 500
├── static/
│   ├── css/main.css          # Global responsive stylesheet
│   ├── js/main.js            # Shared JS utilities (api(), esc(), …)
│   └── qrcode.jpeg           # Payment QR code (PLACEHOLDER – replace with yours)
├── uploads/resumes/          # Local resume storage (dev only; git-ignored)
├── migrations/
│   └── 001_initial_schema.sql # Supabase SQL migration
├── tests/                    # pytest suite (uses an in-memory fake Supabase)
├── requirements.txt / requirements-dev.txt
├── .env.example
└── README.md
```

### API overview

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /api/auth/register` | – | Create account, send verification email |
| `POST /api/auth/verify-email` | – | Confirm email with token |
| `POST /api/auth/resend-verification` | – | New verification link |
| `POST /api/auth/login` · `/logout` | – | Start / end session (HttpOnly cookie) |
| `GET /api/auth/session` · `/me` | – / user | Who am I (`/session` never errors) |
| `POST /api/auth/forgot-password` · `/reset-password` | – | Password reset |
| `GET /api/applicant/status` · `/profile` | user | Application + next step |
| `POST /api/applicant/profile` | user | Save application (multipart, with resume) |
| `POST /api/applicant/payment` | user | Submit transaction ID |
| `GET /api/admin/stats` · `/applications` · `/applications/{id}` | admin | Review |
| `PATCH /api/admin/applications/{id}` | admin | Update payment/application status, notes |
| `GET /api/admin/resume/{filename}` | admin | Download a resume (audited) |
| `GET /api/admin/audit-log` | admin | Audit trail |

Interactive docs are available at `/docs` while `ENVIRONMENT=development`.

---

## Prerequisites

- Python 3.11+
- A [Supabase](https://supabase.com/) project (free tier works for development)
- (Optional) SMTP credentials for real verification emails

---

## Quick Start (Development)

```bash
# 1. Clone / navigate to the project directory
git clone https://github.com/Xerxces/Freelance-hub-.git
cd Freelance-hub-

# 2. Create and activate a virtual environment
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env          # Windows: copy .env.example .env
# Edit .env with your Supabase URL, service key, and SECRET_KEY

# 5. Run database migrations (see Database Setup below)

# 6. Start the development server
python main.py
# OR
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 in your browser.

---

## Database Setup

1. Log in to your [Supabase dashboard](https://app.supabase.com/)
2. Select your project → **SQL Editor** → **New Query**
3. Paste the contents of `migrations/001_initial_schema.sql` and click **Run**

This creates the `users`, `applications`, and `audit_log` tables with RLS enabled. The script is safe to re-run.

---

## Configuration Reference

Copy `.env.example` to `.env` and fill in the values:

| Variable | Required | Description |
|---|---|---|
| `SUPABASE_URL` | ✅ | Your Supabase project URL |
| `SUPABASE_SERVICE_KEY` | ✅ | Service role key (keep secret — never expose to frontend) |
| `SUPABASE_ANON_KEY` | ✅ | Anon key (used for reference; backend uses service key) |
| `SECRET_KEY` | ✅ | Random 32-byte hex string for JWT signing |
| `ADMIN_EMAIL` | ✅ | Email of the initial admin account |
| `ASSESSMENT_NAME` | ✅ | Name of your assessment programme |
| `ASSESSMENT_FEE` | ✅ | Fee amount (integer) |
| `ASSESSMENT_CURRENCY` | ✅ | Currency code (e.g. `INR`, `USD`) |
| `PAYMENT_ACCOUNT_NAME` | ✅ | Name shown on the payment page |
| `PAYMENT_UPI_ID` | ✅ | UPI ID shown on the payment page |
| `STORAGE_BACKEND` | — | `local` (dev) or `supabase` (prod) |
| `SMTP_HOST/PORT/USER/PASSWORD`, `EMAIL_FROM` | — | SMTP credentials for emails |
| `APP_BASE_URL` | — | Public site URL, used for links inside emails |
| `ALLOWED_ORIGINS` | — | Comma-separated CORS origins |
| `FORCE_HTTPS` | — | Set to `true` in production |

Generate `SECRET_KEY`:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## Promoting an Admin User

1. Register a normal account at `/register` and verify the email
2. In your Supabase SQL Editor, run:
   ```sql
   UPDATE users SET role = 'admin' WHERE email = 'your-admin-email@example.com';
   ```
3. Log in — you will be redirected to `/admin` automatically

---

## File Storage

**Development:** Resumes are saved to `uploads/resumes/` (local filesystem, never served by the web app). Set `STORAGE_BACKEND=local` in `.env`.

**Production:** Set `STORAGE_BACKEND=supabase` and create a **private** bucket called `resumes` in your Supabase Storage dashboard:
- Bucket → Settings → toggle **Public** OFF
- Add the bucket name to `STORAGE_BUCKET` in `.env`

Resumes are never served from a public URL. Admin downloads go through the authenticated `/api/admin/resume/{filename}` endpoint, which only serves files that belong to an application and records each download in the audit log.

---

## Email Setup

Email verification and password reset need SMTP credentials. For Gmail:
1. Enable 2-factor authentication on your Google account
2. Generate an App Password: **Google Account → Security → App passwords**
3. Set `SMTP_USER` and `SMTP_PASSWORD` in `.env`

**If SMTP is not configured (or still contains the placeholder values from `.env.example`), the app prints each email to the console** — copy the verification link from the terminal where `uvicorn` is running.

---

## Running the Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
```

The suite runs the whole flow (register → verify → apply → pay → admin review) against an in-memory fake of Supabase, so it needs **no** credentials and never touches your real database. (The older `_run_tests.py` in the repo root is a live smoke test that needs a running server and real Supabase credentials.)

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ModuleNotFoundError: No module named 'app'` | The `app/` folder is missing or you are not running from the project root. |
| `RuntimeError: Directory 'static' does not exist` | The `static/` folder is missing from your checkout. |
| API returns **500** "Supabase is not configured" | `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` in `.env` are still placeholders. |
| `relation "public.users" does not exist` | Run `migrations/001_initial_schema.sql` in the Supabase SQL Editor. |
| `Invalid API key` | You used the anon key or a truncated key as `SUPABASE_SERVICE_KEY`. Use the **service_role** key. |
| Verification email never arrives | SMTP not configured → check the server console for the link. |
| `module 'bcrypt' has no attribute '__about__'` | Install the pinned version: `pip install -r requirements.txt` (`bcrypt<4.1`). |
| Login says "Please verify your email" | Open the verification link first (or use *Resend verification email* on the login page). |
| Behind Nginx every user shares one rate limit | Run uvicorn/gunicorn with `--proxy-headers` and `--forwarded-allow-ips` set to your proxy. |

---

## Running in Production

```bash
# Install production ASGI server
pip install gunicorn

# Run with Gunicorn + Uvicorn workers
gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --access-logfile - \
  --error-logfile -
```

### Nginx reverse proxy (recommended)
```nginx
server {
    listen 443 ssl http2;
    server_name yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    # Uploads directory must NOT be served by Nginx
    location /uploads/ { return 404; }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 6M;
    }
}
```

### Environment checklist for production
- [ ] Set `ENVIRONMENT=production` (disables `/docs` and `/redoc`; the app refuses to start with a placeholder `SECRET_KEY`)
- [ ] Set `FORCE_HTTPS=true` (also marks the session cookie `Secure`)
- [ ] Set `SECRET_KEY` to a cryptographically strong 32-byte hex value
- [ ] Use `SUPABASE_SERVICE_KEY` — never the anon key for backend operations
- [ ] Set `ALLOWED_ORIGINS` to your exact production domain only
- [ ] Set `APP_BASE_URL` to your public https URL (used in emails)
- [ ] Set `STORAGE_BACKEND=supabase` with a private bucket
- [ ] Configure real SMTP credentials
- [ ] Run Nginx with TLS (Let's Encrypt)
- [ ] Block direct access to `uploads/` at the web server level

---

## Security Notes

- Passwords are hashed with bcrypt (cost factor 12) — no plaintext passwords stored; 10+ characters with letters and numbers required
- Session tokens are signed JWTs in HttpOnly, SameSite=Lax cookies; the user's role is re-read from the database on every request, so demoting/disabling an account takes effect immediately
- Email-verification and password-reset tokens are random, single-use and expire (24 h / 1 h); only their SHA-256 hash is stored
- Login, forgot-password and registration responses do not reveal whether an email exists (except registration's explicit "already exists" message)
- Rate limiting on auth endpoints (login/register: 10/min, reset: 5/min) and 200/min by default elsewhere
- Input validation on both frontend and backend (Pydantic schemas)
- File uploads validated by extension, MIME type, magic bytes (and `.docx` zip structure), and size
- Safe filenames generated with SHA-256 + random token — no user input in stored filenames
- Resumes stored outside the webroot; admin access requires authentication and authorization
- SQL injection prevented by Supabase PostgREST parameterized queries
- XSS prevented by Jinja auto-escaping and the `esc()` helper in JavaScript
- Row Level Security (RLS) enabled on all Supabase tables, with anon/authenticated access revoked
- CORS restricted to configured origins only
- Security headers set on all responses (X-Frame-Options, X-Content-Type-Options, etc.)
- Audit log records admin actions (status changes, resume downloads) without logging secrets, passwords or free-text notes
- Error responses never expose stack traces or internal details in production

**This application is not claimed to be completely secure.** See [Known Risks](#known-risks--hardening-todo) below.

---

## Remaining Placeholders

Before going live, replace the following:

| Placeholder | Location | Action Required |
|---|---|---|
| `qrcode.jpeg` | `static/qrcode.jpeg` | **Currently a placeholder image** – replace with your actual payment QR code |
| `PAYMENT_ACCOUNT_NAME` | `.env` | Set to your actual payment account name |
| `PAYMENT_UPI_ID` | `.env` | Set to your actual UPI ID |
| `ASSESSMENT_NAME` | `.env` | Set to your programme name |
| `ASSESSMENT_FEE` | `.env` | Set to your actual fee amount |
| `ASSESSMENT_CURRENCY` | `.env` | Set to your currency (e.g. `INR`) |
| `ADMIN_EMAIL` | `.env` | Set to your admin email; promote via SQL |
| Organization name in footer | `templates/base.html` | Update branding in page titles and footer |
| Privacy Policy & Terms | `templates/auth/register.html` | Create `/privacy` and `/terms` pages and link them from the consent checkbox |
| SMTP credentials | `.env` | Required for real email delivery |

---

## Known Risks & Hardening TODO

- **Payment verification is manual.** UPI transaction IDs are not automatically verified. Implement a webhook from your payment gateway or UPI provider for automated verification in production.
- **CSRF protection.** The API uses cookie-based auth. `SameSite=Lax` already blocks cross-site POSTs; for defence in depth add CSRF token validation for state-changing endpoints, or switch to `SameSite=Strict`.
- **Dependency updates.** Run `pip audit` or use Dependabot regularly to catch vulnerable dependencies.
- **Rate limit storage.** SlowAPI defaults to in-memory storage; use Redis (`storage_uri` in `app/limiter.py`) in multi-worker deployments.
- **File storage.** Local `uploads/` is suitable for development only. Use Supabase Storage or S3-compatible private storage in production.
- **Admin list search.** Admin search/pagination runs in memory over the newest 1,000 matching rows; move it into SQL if you expect far more applications.
- **Request body size.** Resume size is enforced by the app, but cap request bodies at the proxy too (`client_max_body_size 6M`).
- **Log PII minimisation.** Review log statements before production to ensure no unnecessary personal data is logged.
- **Brute-force reset tokens.** Consider adding CAPTCHA to the password reset flow.
