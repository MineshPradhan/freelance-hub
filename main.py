"""
Assessment Platform – FastAPI Application Entry Point
"""
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.config import get_settings
from app.limiter import limiter
from app.routers import admin, applicant, auth

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s – %(message)s",
)
logger = logging.getLogger(__name__)

settings = get_settings()

# Resolve static/ and templates/ relative to this file so the app starts from any directory.
BASE_DIR = Path(__file__).resolve().parent

# ── App init ──────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Assessment Platform API",
    version="1.0.0",
    # Hide docs in production
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url="/redoc" if settings.environment != "production" else None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-CSRF-Token"],
)


# ── Security headers middleware ────────────────────────────────────────────────
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    if settings.force_https:
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response


# ── Static files & templates ──────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(applicant.router)
app.include_router(admin.router)


# ── Page routes (HTML) ────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(request, "auth/register.html")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "auth/login.html")


@app.get("/auth/verify-email", response_class=HTMLResponse)
async def verify_email_page(request: Request, token: str = ""):
    return templates.TemplateResponse(request, "auth/verify_email.html", {"token": token})


@app.get("/auth/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    return templates.TemplateResponse(request, "auth/forgot_password.html")


@app.get("/auth/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str = ""):
    return templates.TemplateResponse(request, "auth/reset_password.html", {"token": token})


@app.get("/apply", response_class=HTMLResponse)
async def apply_page(request: Request):
    return templates.TemplateResponse(request, "applicant/apply.html", {
        "assessment_name": settings.assessment_name,
        "assessment_fee": settings.assessment_fee,
        "assessment_currency": settings.assessment_currency,
    })


@app.get("/payment", response_class=HTMLResponse)
async def payment_page(request: Request):
    return templates.TemplateResponse(request, "applicant/payment.html", {
        "assessment_name": settings.assessment_name,
        "assessment_fee": settings.assessment_fee,
        "assessment_currency": settings.assessment_currency,
        "payment_account_name": settings.payment_account_name,
        "payment_upi_id": settings.payment_upi_id,
    })


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    return templates.TemplateResponse(request, "applicant/dashboard.html", {
        "assessment_name": settings.assessment_name,
    })


@app.get("/confirmation", response_class=HTMLResponse)
async def confirmation_page(request: Request):
    return templates.TemplateResponse(request, "applicant/confirmation.html")


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return templates.TemplateResponse(request, "admin/dashboard.html")


@app.get("/admin/application/{application_id}", response_class=HTMLResponse)
async def admin_application_detail(request: Request, application_id: str):
    return templates.TemplateResponse(request, "admin/application_detail.html", {
        "application_id": application_id,
    })


# ── Global error handlers ─────────────────────────────────────────────────────
@app.exception_handler(404)
async def not_found(request: Request, exc):
    if request.url.path.startswith("/api/"):
        # Keep the specific message from HTTPException(404, "...") when there is one.
        detail = getattr(exc, "detail", None)
        return JSONResponse(status_code=404, content={"detail": detail if isinstance(detail, str) and detail != "Not Found" else "Not found"})
    return templates.TemplateResponse(request, "errors/404.html", status_code=404)


@app.exception_handler(500)
async def server_error(request: Request, exc):
    logger.exception("Unhandled server error: %s", exc)
    if request.url.path.startswith("/api/"):
        # Show real error in development so issues can be diagnosed
        detail = str(exc) if settings.environment == "development" else "An internal error occurred"
        return JSONResponse(status_code=500, content={"detail": detail})
    return templates.TemplateResponse(request, "errors/500.html", status_code=500)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=settings.environment == "development",
    )
