import io
import zipfile

import app.services as services
from app.models import Certificate, Job
from app.generator import generate_certificate as real_generate

JOBS = "/api/v1/jobs"


# ---------- creating a job ----------

def test_create_job_returns_202_with_job_info(client, payload_factory):
    r = client.post(JOBS, json=payload_factory())
    assert r.status_code == 202
    body = r.json()
    assert body["id"]
    assert body["total"] == 2
    assert body["links"]["self"] == f"{JOBS}/{body['id']}"


def test_job_is_persisted(client, db, payload_factory):
    job_id = client.post(JOBS, json=payload_factory()).json()["id"]
    assert db.get(Job, job_id).total == 2
    assert db.query(Certificate).filter_by(job_id=job_id).count() == 2


# ---------- input validation ----------

def test_empty_recipient_list_rejected(client, payload_factory):
    assert client.post(JOBS, json=payload_factory(recipients=[])).status_code == 422


def test_missing_event_name_rejected(client, payload_factory):
    p = payload_factory()
    del p["event_name"]
    assert client.post(JOBS, json=p).status_code == 422


def test_too_many_recipients_rejected(client, payload_factory):
    many = [{"name": f"P{i}", "email": f"p{i}@example.com"} for i in range(1001)]
    assert client.post(JOBS, json=payload_factory(recipients=many)).status_code == 422


def test_invalid_recipients_are_reported_but_valid_ones_still_generated(client, payload_factory):
    recipients = [
        {"name": "Alice Smith", "email": "alice@example.com"},
        {"name": "", "email": "bob@example.com"},            # empty name
        {"name": "Carol", "email": "not-an-email"},          # bad email
        {"name": "Dave"},                                     # missing email
        {"name": "Alice Again", "email": "ALICE@example.com"},  # duplicate (case-insensitive)
    ]
    job = client.post(JOBS, json=payload_factory(recipients=recipients)).json()
    detail = client.get(f"{JOBS}/{job['id']}").json()

    assert detail["status"] == "completed_with_errors"
    assert detail["succeeded"] == 1
    assert detail["failed"] == 4
    assert [f["position"] for f in detail["failures"]] == [1, 2, 3, 4]
    assert "Duplicate" in detail["failures"][3]["error"]
    assert "email" in detail["failures"][2]["error"]


def test_job_with_only_invalid_recipients_is_failed_immediately(client, payload_factory):
    job = client.post(JOBS, json=payload_factory(recipients=[{"name": ""}])).json()
    assert job["status"] == "failed"
    assert job["failed"] == 1 and job["succeeded"] == 0


# ---------- certificate generation ----------

def test_certificates_are_generated_as_pdfs(client, db, payload_factory):
    job_id = client.post(JOBS, json=payload_factory()).json()["id"]
    certs = db.query(Certificate).filter_by(job_id=job_id).all()
    assert len(certs) == 2
    for c in certs:
        assert c.status.value == "success"
        assert c.certificate_number.startswith("CERT-")
        with open(c.file_path, "rb") as f:
            assert f.read(4) == b"%PDF"


# ---------- status / progress ----------

def test_status_and_progress_lifecycle(client, db, payload_factory):
    from app.schemas import JobCreate

    # Create the job WITHOUT running the worker -> it must look "queued, 0%".
    job = services.create_job(db, JobCreate(**payload_factory()))
    queued = client.get(f"{JOBS}/{job.id}").json()
    assert queued["status"] == "queued"
    assert queued["progress_percent"] == 0
    assert queued["pending"] == 2

    # Run the worker -> it must look "completed, 100%".
    services.process_job(job.id)
    done = client.get(f"{JOBS}/{job.id}").json()
    assert done["status"] == "completed"
    assert done["progress_percent"] == 100
    assert done["succeeded"] == 2 and done["pending"] == 0
    assert done["finished_at"] is not None


def test_unknown_job_returns_404(client):
    assert client.get(f"{JOBS}/does-not-exist").status_code == 404


# ---------- an individual failure must not stop the others ----------

def test_single_certificate_failure_does_not_block_others(client, monkeypatch, payload_factory):
    def flaky(data, out_path):
        if data.recipient_name == "Bob Jones":
            raise RuntimeError("renderer exploded")
        return real_generate(data, out_path)

    monkeypatch.setattr(services, "generate_certificate", flaky)

    recipients = [
        {"name": "Alice Smith", "email": "alice@example.com"},
        {"name": "Bob Jones", "email": "bob@example.com"},
        {"name": "Carol White", "email": "carol@example.com"},
    ]
    job_id = client.post(JOBS, json=payload_factory(recipients=recipients)).json()["id"]
    detail = client.get(f"{JOBS}/{job_id}").json()

    assert detail["status"] == "completed_with_errors"
    assert detail["succeeded"] == 2
    assert detail["failed"] == 1
    assert detail["failures"][0]["recipient_name"] == "Bob Jones"
    assert "renderer exploded" in detail["failures"][0]["error"]


def test_unrenderable_name_fails_only_that_certificate(client, payload_factory):
    recipients = [
        {"name": "Alice Smith", "email": "alice@example.com"},
        {"name": "अफ़िया", "email": "aafiya@example.com"},
    ]
    job_id = client.post(JOBS, json=payload_factory(recipients=recipients)).json()["id"]
    detail = client.get(f"{JOBS}/{job_id}").json()
    assert detail["succeeded"] == 1 and detail["failed"] == 1
    assert "Unsupported character" in detail["failures"][0]["error"]


# ---------- retrieving certificates ----------

def test_list_certificates_with_status_filter_and_pagination(client, payload_factory):
    recipients = [{"name": f"Person {i}", "email": f"p{i}@example.com"} for i in range(5)]
    recipients.append({"name": "", "email": "x"})
    job_id = client.post(JOBS, json=payload_factory(recipients=recipients)).json()["id"]

    all_ = client.get(f"{JOBS}/{job_id}/certificates").json()
    assert all_["total"] == 6

    ok = client.get(f"{JOBS}/{job_id}/certificates", params={"status": "success", "limit": 2, "offset": 1}).json()
    assert ok["total"] == 5
    assert [i["position"] for i in ok["items"]] == [1, 2]
    assert all(i["download_url"] for i in ok["items"])

    failed = client.get(f"{JOBS}/{job_id}/certificates", params={"status": "failed"}).json()
    assert failed["total"] == 1 and failed["items"][0]["download_url"] is None


def test_download_single_certificate(client, payload_factory):
    job_id = client.post(JOBS, json=payload_factory()).json()["id"]
    item = client.get(f"{JOBS}/{job_id}/certificates").json()["items"][0]
    r = client.get(item["download_url"])
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")


def test_download_failed_or_unknown_certificate(client, payload_factory):
    job_id = client.post(JOBS, json=payload_factory(recipients=[
        {"name": "Alice", "email": "a@example.com"}, {"name": "", "email": "b@example.com"}])).json()["id"]
    failed = client.get(f"{JOBS}/{job_id}/certificates", params={"status": "failed"}).json()["items"][0]
    assert client.get(f"/api/v1/certificates/{failed['id']}/download").status_code == 409
    assert client.get("/api/v1/certificates/nope/download").status_code == 404


def test_download_job_as_zip_contains_only_successful(client, payload_factory):
    recipients = [
        {"name": "Alice", "email": "a@example.com"},
        {"name": "Bob", "email": "b@example.com"},
        {"name": "", "email": "bad"},
    ]
    job_id = client.post(JOBS, json=payload_factory(recipients=recipients)).json()["id"]
    r = client.get(f"{JOBS}/{job_id}/download")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert len(names) == 2 and all(n.endswith(".pdf") for n in names)


def test_zip_not_available_while_job_is_running(client, db, payload_factory):
    from app.schemas import JobCreate

    job = services.create_job(db, JobCreate(**payload_factory()))  # queued, worker not run
    assert client.get(f"{JOBS}/{job.id}/download").status_code == 409
