"""Pydantic schemas: request validation and response shapes."""
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.config import settings
from app.models import CertificateStatus, JobStatus

DEFAULT_ACHIEVEMENT = "for successfully participating in"


class RecipientIn(BaseModel):
    """One recipient. Validated individually so a bad row does not reject the whole job."""

    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    achievement: str = Field(default=DEFAULT_ACHIEVEMENT, min_length=1, max_length=200)

    @field_validator("name", "achievement", mode="before")
    @classmethod
    def _strip(cls, v: Any) -> Any:
        return v.strip() if isinstance(v, str) else v


class JobCreate(BaseModel):
    """Envelope for a bulk request.

    `recipients` is typed as a list of raw dicts on purpose: each recipient is validated
    later, one by one, so one invalid row is reported as a failed certificate instead of
    rejecting the entire request with a 422.
    """

    event_name: str = Field(min_length=1, max_length=150)
    issued_by: str = Field(min_length=1, max_length=100)
    issue_date: date = Field(default_factory=date.today)
    recipients: list[dict[str, Any]] = Field(min_length=1)

    @field_validator("event_name", "issued_by", mode="before")
    @classmethod
    def _strip(cls, v: Any) -> Any:
        return v.strip() if isinstance(v, str) else v

    @field_validator("recipients")
    @classmethod
    def _limit(cls, v: list) -> list:
        if len(v) > settings.max_recipients:
            raise ValueError(f"A job can contain at most {settings.max_recipients} recipients; split it into several jobs.")
        return v


class CertificateOut(BaseModel):
    id: str
    position: int
    recipient_name: str | None
    recipient_email: str | None
    certificate_number: str | None
    status: CertificateStatus
    error: str | None
    download_url: str | None

    model_config = {"from_attributes": True}


class FailureOut(BaseModel):
    position: int
    recipient_name: str | None
    recipient_email: str | None
    error: str | None


class JobOut(BaseModel):
    id: str
    event_name: str
    issued_by: str
    issue_date: date
    status: JobStatus
    total: int
    succeeded: int
    failed: int
    pending: int
    progress_percent: float
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    links: dict[str, str]
    failures: list[FailureOut] = []


class CertificatePage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CertificateOut]
