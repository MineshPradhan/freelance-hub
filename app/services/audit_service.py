"""Audit log for sensitive admin actions. Never stores passwords, tokens or secrets."""
import logging
from typing import Any, Optional

from fastapi import Request

from app import database

logger = logging.getLogger(__name__)


def client_ip(request: Optional[Request]) -> Optional[str]:
    if request is None or request.client is None:
        return None
    return request.client.host


def log_action(
    admin_id: Optional[str],
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
    request: Optional[Request] = None,
) -> None:
    """Record an action. Failures are logged but never raised to the caller."""
    try:
        database.get_db().table("audit_log").insert(
            {
                "admin_id": admin_id,
                "action": action,
                "target_type": target_type,
                "target_id": target_id,
                "details": details or {},
                "ip_address": client_ip(request),
            }
        ).execute()
    except Exception as exc:
        logger.error("Could not write audit log entry '%s': %s", action, exc)
