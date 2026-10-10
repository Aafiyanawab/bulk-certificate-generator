"""Central configuration, read from environment variables with sane defaults."""
import os
from pathlib import Path


class Settings:
    def __init__(self) -> None:
        self.database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/certificates.db")
        self.storage_dir: Path = Path(os.getenv("CERT_STORAGE_DIR", "./data/certificates"))
        # Upper bound on recipients per request. Protects the server from a single
        # enormous request; clients with more recipients should split into several jobs.
        self.max_recipients: int = int(os.getenv("MAX_RECIPIENTS_PER_JOB", "1000"))


settings = Settings()
