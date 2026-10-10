"""FastAPI application: HTTP layer only. Business logic lives in services.py."""
import io
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import services
from app.config import settings
from app.database import Base, engine, get_db
from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.schemas import CertificateOut, CertificatePage, FailureOut, JobCreate, JobOut

API = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)  # fine for this scope; use Alembic migrations in production
    yield


app = FastAPI(title="Bulk Certificate Generator", version="1.0.0", lifespan=lifespan)


# ---------- helpers ----------

def _get_job_or_404(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


def _job_out(db: Session, job: Job, include_failures: bool = True) -> JobOut:
    counts = services.job_counts(db, job.id)
    done = counts["success"] + counts["failed"]
    failures: list[FailureOut] = []
    if include_failures:
        rows = db.scalars(
            select(Certificate)
            .where(Certificate.job_id == job.id, Certificate.status == CertificateStatus.FAILED)
            .order_by(Certificate.position)
        ).all()
        failures = [
            FailureOut(position=r.position, recipient_name=r.recipient_name, recipient_email=r.recipient_email, error=r.error)
            for r in rows
        ]
    return JobOut(
        id=job.id,
        event_name=job.event_name,
        issued_by=job.issued_by,
        issue_date=job.issue_date,
        status=job.status,
        total=job.total,
        succeeded=counts["success"],
        failed=counts["failed"],
        pending=counts["pending"],
        progress_percent=round(100 * done / job.total, 1) if job.total else 100.0,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        links={
            "self": f"{API}/jobs/{job.id}",
            "certificates": f"{API}/jobs/{job.id}/certificates",
            "download_all": f"{API}/jobs/{job.id}/download",
        },
        failures=failures,
    )


def _cert_out(c: Certificate) -> CertificateOut:
    return CertificateOut(
        id=c.id,
        position=c.position,
        recipient_name=c.recipient_name,
        recipient_email=c.recipient_email,
        certificate_number=c.certificate_number,
        status=c.status,
        error=c.error,
        download_url=f"{API}/certificates/{c.id}/download" if c.status == CertificateStatus.SUCCESS else None,
    )


# ---------- endpoints ----------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post(f"{API}/jobs", response_model=JobOut, status_code=202)
def create_job(payload: JobCreate, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Accept a bulk request. Returns immediately (202); generation runs in the background."""
    job = services.create_job(db, payload)
    if job.status == JobStatus.QUEUED:
        background.add_task(services.process_job, job.id)
    return _job_out(db, job)


@app.get(f"{API}/jobs/{{job_id}}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db)):
    """Status, progress and the list of failures for a job."""
    return _job_out(db, _get_job_or_404(db, job_id))


@app.get(f"{API}/jobs/{{job_id}}/certificates", response_model=CertificatePage)
def list_certificates(
    job_id: str,
    status: CertificateStatus | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Paginated list of a job's certificates, optionally filtered by status."""
    _get_job_or_404(db, job_id)
    where = [Certificate.job_id == job_id]
    if status:
        where.append(Certificate.status == status)
    total = db.scalar(select(func.count()).select_from(Certificate).where(*where))
    rows = db.scalars(
        select(Certificate).where(*where).order_by(Certificate.position).limit(limit).offset(offset)
    ).all()
    return CertificatePage(total=total, limit=limit, offset=offset, items=[_cert_out(r) for r in rows])


@app.get(f"{API}/certificates/{{certificate_id}}/download")
def download_certificate(certificate_id: str, db: Session = Depends(get_db)):
    """Download a single generated certificate as a PDF."""
    cert = db.get(Certificate, certificate_id)
    if cert is None:
        raise HTTPException(404, "Certificate not found")
    if cert.status != CertificateStatus.SUCCESS or not cert.file_path or not Path(cert.file_path).exists():
        raise HTTPException(409, f"Certificate is not available (status: {cert.status.value})")
    safe_name = "".join(ch if ch.isalnum() else "_" for ch in (cert.recipient_name or "certificate"))
    return FileResponse(cert.file_path, media_type="application/pdf", filename=f"{safe_name}_{cert.certificate_number}.pdf")


@app.get(f"{API}/jobs/{{job_id}}/download")
def download_job_zip(job_id: str, db: Session = Depends(get_db)):
    """Download all successfully generated certificates of a finished job as one ZIP."""
    job = _get_job_or_404(db, job_id)
    if job.status in (JobStatus.QUEUED, JobStatus.PROCESSING):
        raise HTTPException(409, "Job is still running; check its status first")
    certs = db.scalars(
        select(Certificate)
        .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.SUCCESS)
        .order_by(Certificate.position)
    ).all()
    if not certs:
        raise HTTPException(409, "No certificates were generated for this job")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:  # PDFs are already compressed
        for c in certs:
            p = Path(c.file_path)
            if p.exists():
                zf.write(p, arcname=f"{c.certificate_number}.pdf")
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="certificates_{job_id[:8]}.zip"'},
    )
