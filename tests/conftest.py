import os
import shutil
import tempfile

# Point the app at a throw-away DB and storage folder BEFORE the app is imported.
_TMP = tempfile.mkdtemp(prefix="certgen_tests_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["CERT_STORAGE_DIR"] = f"{_TMP}/certs"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_state():
    """Fresh tables and empty storage for every test."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    shutil.rmtree(settings.storage_dir, ignore_errors=True)
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    yield


@pytest.fixture
def client():
    # NOTE: TestClient runs FastAPI BackgroundTasks before returning the response to the
    # test, so after a POST the job has already been processed.
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


def make_payload(recipients=None, **overrides):
    payload = {
        "event_name": "Python Bootcamp",
        "issued_by": "Tech Academy",
        "issue_date": "2026-10-10",
        "recipients": recipients
        if recipients is not None
        else [
            {"name": "Alice Smith", "email": "alice@example.com"},
            {"name": "Bob Jones", "email": "bob@example.com", "achievement": "for outstanding work in"},
        ],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def payload_factory():
    return make_payload
