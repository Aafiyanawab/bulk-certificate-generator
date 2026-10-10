# Bulk Certificate Generator (Backend)

A REST API that accepts **one request containing many recipients**, generates a PDF certificate for each
one from a single predefined template, tracks progress, and lets the client download the results.

**Stack:** Python 3.10+, FastAPI, SQLAlchemy 2 + SQLite (any SQLAlchemy-supported relational DB works),
ReportLab (PDF), pytest.

---

## 1. Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Run the application

```bash
uvicorn app.main:app --reload
```

- API: http://127.0.0.1:8000
- Interactive docs (Swagger UI): http://127.0.0.1:8000/docs

Tables are created automatically on startup. Data is stored in `./data/` (SQLite file + generated PDFs).

Optional environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/certificates.db` | Any SQLAlchemy URL (e.g. PostgreSQL) |
| `CERT_STORAGE_DIR` | `./data/certificates` | Where PDFs are written |
| `MAX_RECIPIENTS_PER_JOB` | `1000` | Max recipients in one request |

## 3. Run the tests

```bash
pytest
```

Tests use a temporary database and storage folder, so they never touch your real data.

---

## 4. API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/jobs` | Submit a bulk generation request (returns `202` immediately) |
| GET | `/api/v1/jobs/{job_id}` | Status, progress, counts and list of failures |
| GET | `/api/v1/jobs/{job_id}/certificates` | Paginated list; filter with `?status=success\|failed\|pending&limit=&offset=` |
| GET | `/api/v1/certificates/{certificate_id}/download` | Download one certificate (PDF) |
| GET | `/api/v1/jobs/{job_id}/download` | Download all successful certificates of a finished job (ZIP) |

### Submit a request

```bash
curl -X POST http://127.0.0.1:8000/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d @sample_request.json
```

Body:

```json
{
  "event_name": "Python Backend Bootcamp 2026",
  "issued_by": "Tech Academy",
  "issue_date": "2026-10-10",
  "recipients": [
    {"name": "Aafiya Khan", "email": "aafiya@example.com", "achievement": "for completing the course with Distinction"},
    {"name": "Rahul Sharma", "email": "rahul@example.com"}
  ]
}
```

- `event_name`, `issued_by`, `recipients` are required. `issue_date` defaults to today.
- Per recipient: `name` (1–100 chars) and `email` are required; `achievement` is optional
  (default: *"for successfully participating in"*).

The response contains the job `id`. Example (abridged):

```json
{ "id": "3d8d142d-...", "status": "queued", "total": 3, "succeeded": 0, "failed": 0, "pending": 3, "progress_percent": 0.0 }
```

### Check progress

```bash
curl http://127.0.0.1:8000/api/v1/jobs/<job_id>
```

Job `status` is one of: `queued` → `processing` → `completed` | `completed_with_errors` | `failed`.
`failures` lists every failed recipient with its position in the request and the reason.

### Retrieve certificates

```bash
# list (with download links)
curl "http://127.0.0.1:8000/api/v1/jobs/<job_id>/certificates?status=success"

# one certificate
curl -OJ http://127.0.0.1:8000/api/v1/certificates/<certificate_id>/download

# everything as a ZIP (only after the job is finished)
curl -OJ http://127.0.0.1:8000/api/v1/jobs/<job_id>/download
```

---

## 5. Design decisions

**Background processing instead of synchronous generation.**
A request can contain up to 1000 recipients; generating that many PDFs inside the request would hit client/proxy
timeouts and block the server. `POST /jobs` therefore validates the request, stores the job, returns `202 Accepted`
and runs generation in the background (FastAPI `BackgroundTasks`). The client polls `GET /jobs/{id}`.
*Trade-off:* `BackgroundTasks` runs in the same process, so a server restart mid-job leaves that job in
`processing`. For production I would move to a real queue (Celery/RQ/Arq + Redis) with retry and recovery. The
worker (`services.process_job`) is a plain function taking a `job_id`, so that swap only changes how it is
invoked, not the logic.

**Two levels of validation.**
- *Request level* (missing event name, empty list, more than the max recipients) → whole request rejected with `422`.
- *Recipient level* (empty name, invalid email, duplicate email in the same job) → that recipient is stored as a
  `failed` certificate with the reason; all other recipients are still processed. This is why `recipients` is
  accepted as raw dicts and validated one-by-one rather than rejecting the entire request for one bad row.

**Failure isolation.**
The worker wraps each certificate in its own `try/except` and commits after each one. One failing certificate
never stops the others, and progress is visible while the job runs. Final job status is derived from the counts.
A last-resort handler marks leftover pending certificates as failed if the worker itself crashes, so a job
never stays stuck in `processing` because of an exception.

**Data model.** `jobs` (1) → (N) `certificates`. Each certificate row holds status, error message, file path
and a certificate number (`CERT-<jobid8>-<position>`). Counts/progress are computed from the DB with a
`GROUP BY` rather than stored counters, so they cannot drift out of sync.

**PDF generation.** A single template (landscape A4) rendered with ReportLab. Long names are auto-shrunk to fit.
Files are written to a temp name and renamed atomically, so a half-written PDF is never served. Files are named by
certificate UUID, not recipient name, which avoids path-injection and name collisions.

**Known limitations**
- The built-in PDF fonts only support Latin characters. Names in other scripts (e.g. Devanagari) fail
  *that certificate only*, with a clear error. Fix: register a Unicode TTF font in `generator.py`.
- No authentication / rate limiting (out of scope for this assignment).
- `create_all()` is used instead of migrations (Alembic) to keep setup simple.
- Duplicate detection is per job, not across jobs.

## 6. Project layout

```
app/
  main.py        HTTP endpoints
  services.py    create_job (validation) + process_job (background worker)
  generator.py   PDF template / rendering
  models.py      SQLAlchemy models (Job, Certificate)
  schemas.py     Pydantic request/response schemas
  database.py    engine + session
  config.py      environment-based settings
tests/           pytest suite (API + generator)
sample_request.json
```
