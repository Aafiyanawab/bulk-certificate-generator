"""Business logic: creating jobs, validating recipients and running the worker."""
import logging
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.generator import CertificateData, generate_certificate
from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.schemas import JobCreate, RecipientIn

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _format_validation_error(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())


def create_job(db: Session, payload: JobCreate) -> Job:
    """Persist a job plus one Certificate row per recipient.

    Invalid recipients are NOT rejected up front: they are stored as FAILED rows with the
    reason, so the client can see exactly which entries were bad. Valid ones stay PENDING
    and are picked up by the worker.
    """
    job = Job(
        event_name=payload.event_name,
        issued_by=payload.issued_by,
        issue_date=payload.issue_date,
        total=len(payload.recipients),
    )
    db.add(job)
    db.flush()  # assigns job.id

    seen_emails: set[str] = set()
    pending_count = 0
    for position, raw in enumerate(payload.recipients):
        cert = Certificate(job_id=job.id, position=position)
        try:
            recipient = RecipientIn.model_validate(raw)
        except ValidationError as exc:
            cert.status = CertificateStatus.FAILED
            cert.error = f"Invalid recipient: {_format_validation_error(exc)}"
            # Keep whatever the client sent so they can identify the bad row.
            cert.recipient_name = str(raw.get("name"))[:200] if raw.get("name") is not None else None
            cert.recipient_email = str(raw.get("email"))[:320] if raw.get("email") is not None else None
        else:
            cert.recipient_name = recipient.name
            cert.recipient_email = str(recipient.email).lower()
            cert.achievement = recipient.achievement
            if cert.recipient_email in seen_emails:
                cert.status = CertificateStatus.FAILED
                cert.error = "Duplicate email in this request"
            else:
                seen_emails.add(cert.recipient_email)
                cert.certificate_number = f"CERT-{job.id[:8].upper()}-{position + 1:04d}"
                pending_count += 1
        db.add(cert)

    # If nothing is left to generate there is no point queueing the job.
    if pending_count == 0:
        job.status = JobStatus.FAILED
        job.finished_at = _now()

    db.commit()
    db.refresh(job)
    return job


def job_counts(db: Session, job_id: str) -> dict[str, int]:
    """Counts per certificate status, computed from the DB so they are always accurate."""
    rows = db.execute(
        select(Certificate.status, func.count()).where(Certificate.job_id == job_id).group_by(Certificate.status)
    ).all()
    counts = {s.value: 0 for s in CertificateStatus}
    for status, n in rows:
        counts[status.value] = n
    return counts


def process_job(job_id: str) -> None:
    """Background worker: generate every PENDING certificate of the job.

    - Opens its own DB session (the request's session is closed by the time this runs).
    - Commits after each certificate so progress is visible while the job runs.
    - Catches errors per certificate so one failure never stops the rest.
    """
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None or job.status not in (JobStatus.QUEUED, JobStatus.PROCESSING):
            return
        job.status = JobStatus.PROCESSING
        job.started_at = job.started_at or _now()
        db.commit()

        job_dir = settings.storage_dir / job.id
        pending = db.scalars(
            select(Certificate)
            .where(Certificate.job_id == job.id, Certificate.status == CertificateStatus.PENDING)
            .order_by(Certificate.position)
        ).all()

        for cert in pending:
            try:
                data = CertificateData(
                    recipient_name=cert.recipient_name,
                    achievement=cert.achievement,
                    event_name=job.event_name,
                    issued_by=job.issued_by,
                    issue_date=job.issue_date,
                    certificate_number=cert.certificate_number,
                )
                path = generate_certificate(data, job_dir / f"{cert.id}.pdf")
                cert.file_path = str(path)
                cert.status = CertificateStatus.SUCCESS
                cert.error = None
            except Exception as exc:  # isolate failures per certificate
                logger.exception("Certificate %s failed", cert.id)
                cert.status = CertificateStatus.FAILED
                cert.error = f"Generation failed: {exc}"[:1000]
            db.commit()

        _finalize_job(db, job)
    except Exception:
        # Unexpected crash outside the per-certificate handler: don't leave the job
        # stuck in "processing" forever.
        logger.exception("Job %s crashed", job_id)
        db.rollback()
        job = db.get(Job, job_id)
        if job is not None:
            for cert in db.scalars(
                select(Certificate).where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.PENDING)
            ):
                cert.status = CertificateStatus.FAILED
                cert.error = "Job aborted due to an internal error"
            db.flush()
            _finalize_job(db, job)
    finally:
        db.close()


def _finalize_job(db: Session, job: Job) -> None:
    counts = job_counts(db, job.id)
    if counts["success"] == 0:
        job.status = JobStatus.FAILED
    elif counts["failed"] > 0:
        job.status = JobStatus.COMPLETED_WITH_ERRORS
    else:
        job.status = JobStatus.COMPLETED
    job.finished_at = _now()
    db.commit()
