"""Application settings, loaded from environment variables / .env (pydantic-settings)."""
import logging
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
PLACEHOLDER_SECRET_PREFIX = "CHANGE_ME"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Supabase
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_anon_key: str = ""

    # App secrets / sessions
    secret_key: str = "CHANGE_ME_GENERATE_A_STRONG_SECRET_KEY"
    session_cookie_name: str = "assessment_session"
    access_token_expire_minutes: int = 60

    # Admin
    admin_email: str = ""

    # File storage
    storage_backend: str = "local"  # "local" | "supabase"
    upload_dir: str = "uploads/resumes"
    storage_bucket: str = "resumes"
    max_resume_size_bytes: int = 5 * 1024 * 1024

    # Assessment / payment
    assessment_name: str = "Professional Skills Assessment"
    assessment_fee: int = 500
    assessment_currency: str = "INR"
    payment_account_name: str = ""
    payment_upi_id: str = ""

    # CORS
    allowed_origins: str = "http://localhost:8000,http://127.0.0.1:8000"

    # Email
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = "noreply@example.com"
    app_base_url: str = "http://127.0.0.1:8000"

    # Environment
    environment: str = "development"
    force_https: bool = False

    # ── derived helpers ──────────────────────────────────────────────────────
    @property
    def origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def smtp_configured(self) -> bool:
        """SMTP counts as configured only if real-looking credentials are present."""
        if not (self.smtp_host and self.smtp_user and self.smtp_password):
            return False
        if self.smtp_password.upper().startswith("YOUR_"):
            return False
        if self.smtp_user.lower() in {"your_email@gmail.com", "you@example.com"}:
            return False
        return True

    @property
    def upload_path(self) -> Path:
        p = Path(self.upload_dir)
        return p if p.is_absolute() else BASE_DIR / p


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    weak = s.secret_key.startswith(PLACEHOLDER_SECRET_PREFIX) or len(s.secret_key) < 32
    if weak:
        if s.is_production:
            raise RuntimeError(
                "SECRET_KEY is missing/weak. Generate one with: "
                'python -c "import secrets; print(secrets.token_hex(32))"'
            )
        logger.warning(
            "SECRET_KEY is a placeholder or too short. Fine for local development, "
            "but generate a real one before deploying."
        )
    return s
