"""Transactional email over SMTP.

If SMTP is not configured (or sending fails in development) the email is printed to the
console instead, so the verification / reset links are always reachable while developing.
"""
import logging
import smtplib
from email.message import EmailMessage

from app.config import get_settings

logger = logging.getLogger(__name__)


def send_email(to: str, subject: str, body: str) -> bool:
    s = get_settings()
    if not s.smtp_configured:
        logger.info("EMAIL (SMTP not configured – console only)\nTo: %s\nSubject: %s\n\n%s\n", to, subject, body)
        return False

    msg = EmailMessage()
    msg["From"] = s.email_from or s.smtp_user
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as server:
            server.starttls()
            server.login(s.smtp_user, s.smtp_password)
            server.send_message(msg)
        return True
    except Exception as exc:  # never let email problems break a request
        logger.error("Failed to send email to %s: %s", to, exc)
        if not s.is_production:
            logger.info("EMAIL (fallback to console)\nTo: %s\nSubject: %s\n\n%s\n", to, subject, body)
        return False


def send_verification_email(to: str, name: str, token: str) -> bool:
    s = get_settings()
    link = f"{s.app_base_url.rstrip('/')}/auth/verify-email?token={token}"
    return send_email(
        to,
        f"Verify your email – {s.assessment_name}",
        f"Hello {name},\n\nPlease verify your email address by opening this link (valid for 24 hours):\n\n{link}\n\n"
        "If you did not create an account, you can ignore this message.\n",
    )


def send_password_reset_email(to: str, name: str, token: str) -> bool:
    s = get_settings()
    link = f"{s.app_base_url.rstrip('/')}/auth/reset-password?token={token}"
    return send_email(
        to,
        f"Reset your password – {s.assessment_name}",
        f"Hello {name},\n\nUse this link to choose a new password (valid for 1 hour):\n\n{link}\n\n"
        "If you did not request this, you can ignore this message.\n",
    )


def send_submission_email(to: str, name: str, reference: str) -> bool:
    s = get_settings()
    return send_email(
        to,
        f"Application received – {reference}",
        f"Hello {name},\n\nWe have received your application for {s.assessment_name}.\n"
        f"Reference number: {reference}\n\nYour payment is awaiting verification by our team. "
        "You can track your status any time from your dashboard.\n",
    )


def send_status_email(to: str, name: str, reference: str, payment_status: str, application_status: str) -> bool:
    s = get_settings()
    return send_email(
        to,
        f"Application update – {reference}",
        f"Hello {name},\n\nThere is an update on your {s.assessment_name} application ({reference}).\n\n"
        f"Payment status:     {payment_status.replace('_', ' ')}\n"
        f"Application status: {application_status.replace('_', ' ')}\n\n"
        f"Log in to view details: {s.app_base_url.rstrip('/')}/dashboard\n",
    )
