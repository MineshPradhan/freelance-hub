"""Supabase client singleton."""
from functools import lru_cache

from app.config import get_settings


class DatabaseNotConfigured(RuntimeError):
    pass


@lru_cache
def get_supabase():
    """Return the shared Supabase client (service-role key; backend only)."""
    s = get_settings()
    url, key = s.supabase_url, s.supabase_service_key
    if (
        not url
        or "YOUR_PROJECT_REF" in url
        or not key
        or key.upper().startswith("YOUR_")
    ):
        raise DatabaseNotConfigured(
            "Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_KEY in your .env file."
        )
    from supabase import create_client  # imported lazily so the app can boot without it

    return create_client(url, key)


# Short alias used across the code base.
get_db = get_supabase


def is_duplicate_error(exc: Exception) -> bool:
    """True if a PostgREST error is a unique-constraint violation (SQLSTATE 23505)."""
    code = getattr(exc, "code", None)
    return code == "23505" or "duplicate key" in str(exc).lower()
