"""Pydantic validation schemas (request bodies)."""
import re
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean(v: Optional[str]) -> Optional[str]:
    if v is None:
        return v
    return _CTRL.sub("", v).strip()


def validate_password_strength(password: str) -> str:
    if len(password) < 10:
        raise ValueError("Password must be at least 10 characters long")
    if len(password.encode("utf-8")) > 72:
        raise ValueError("Password must be at most 72 bytes long")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise ValueError("Password must contain at least one letter and one number")
    return password


class _Base(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")


# ── Auth ─────────────────────────────────────────────────────────────────────
class RegisterRequest(_Base):
    full_name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    password: str

    @field_validator("full_name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = _clean(v)
        if re.search(r"[<>]", v):
            raise ValueError("Name contains invalid characters")
        return v

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return v.lower()

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        return validate_password_strength(v)


class LoginRequest(_Base):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return v.lower()


class TokenRequest(_Base):
    token: str = Field(min_length=10, max_length=200)


class EmailRequest(_Base):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return v.lower()


class ResetPasswordRequest(_Base):
    token: str = Field(min_length=10, max_length=200)
    password: str

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        return validate_password_strength(v)


# ── Applicant ────────────────────────────────────────────────────────────────
class ProfileSubmit(_Base):
    # required
    full_name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    education: str = Field(min_length=2, max_length=200)
    address: str = Field(min_length=5, max_length=500)
    experience_level: str = Field(min_length=1, max_length=50)
    # optional
    phone: Optional[str] = Field(default="", max_length=20)
    date_of_birth: Optional[str] = ""
    city: Optional[str] = Field(default="", max_length=100)
    state: Optional[str] = Field(default="", max_length=100)
    postal_code: Optional[str] = Field(default="", max_length=20)
    country: Optional[str] = Field(default="", max_length=100)
    institution: Optional[str] = Field(default="", max_length=200)
    field_of_study: Optional[str] = Field(default="", max_length=200)
    graduation_year: Optional[int] = Field(default=None, ge=1970, le=2100)
    current_employer: Optional[str] = Field(default="", max_length=200)
    job_title: Optional[str] = Field(default="", max_length=200)
    summary: Optional[str] = Field(default="", max_length=2000)

    @model_validator(mode="before")
    @classmethod
    def _blank_to_none(cls, data):
        if isinstance(data, dict) and data.get("graduation_year") in ("", None):
            data = {**data, "graduation_year": None}
        return data

    @field_validator(
        "full_name", "address", "education", "experience_level", "phone", "city", "state",
        "postal_code", "country", "institution", "field_of_study", "current_employer",
        "job_title", "summary",
    )
    @classmethod
    def _strip_ctrl(cls, v):
        return _clean(v)

    @field_validator("full_name")
    @classmethod
    def _name(cls, v: str) -> str:
        if re.search(r"[<>]", v):
            raise ValueError("Name contains invalid characters")
        return v

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return v.lower()

    @field_validator("phone")
    @classmethod
    def _phone(cls, v):
        if v and not re.fullmatch(r"\+?[0-9 ()\-]{7,20}", v):
            raise ValueError("Enter a valid phone number")
        return v

    @field_validator("date_of_birth")
    @classmethod
    def _dob(cls, v):
        if not v:
            return ""
        try:
            d = date.fromisoformat(v)
        except ValueError:
            raise ValueError("Date of birth must be a valid date (YYYY-MM-DD)")
        today = date.today()
        age = today.year - d.year - ((today.month, today.day) < (d.month, d.day))
        if age < 16 or age > 100:
            raise ValueError("Applicants must be between 16 and 100 years old")
        return d.isoformat()


class PaymentSubmit(_Base):
    transaction_id: str = Field(min_length=6, max_length=40)

    @field_validator("transaction_id")
    @classmethod
    def _txn(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", v):
            raise ValueError("Transaction ID may contain only letters, numbers, '-' and '_'")
        return v.upper()


# ── Admin ────────────────────────────────────────────────────────────────────
PaymentStatus = Literal["unpaid", "pending", "verified", "rejected"]
ApplicationStatus = Literal["draft", "under_review", "shortlisted", "accepted", "rejected"]


class AdminUpdate(_Base):
    payment_status: Optional[PaymentStatus] = None
    application_status: Optional[ApplicationStatus] = None
    admin_notes: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("admin_notes")
    @classmethod
    def _notes(cls, v):
        return _clean(v)

    @model_validator(mode="after")
    def _at_least_one(self):
        if self.payment_status is None and self.application_status is None and self.admin_notes is None:
            raise ValueError("Nothing to update")
        return self
