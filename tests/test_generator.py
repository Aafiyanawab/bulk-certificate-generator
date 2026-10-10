from datetime import date

import pytest

from app.generator import CertificateData, generate_certificate


def _data(name="Alice Smith"):
    return CertificateData(
        recipient_name=name,
        achievement="for successfully participating in",
        event_name="Python Bootcamp",
        issued_by="Tech Academy",
        issue_date=date(2026, 10, 10),
        certificate_number="CERT-TEST-0001",
    )


def test_generates_a_valid_pdf(tmp_path):
    out = generate_certificate(_data(), tmp_path / "a.pdf")
    assert out.exists()
    assert out.read_bytes().startswith(b"%PDF")
    assert not list(tmp_path.glob("*.tmp"))  # no temp files left behind


def test_very_long_name_still_renders(tmp_path):
    out = generate_certificate(_data("A" * 100), tmp_path / "long.pdf")
    assert out.read_bytes().startswith(b"%PDF")


def test_unsupported_characters_raise_and_leave_no_file(tmp_path):
    with pytest.raises(ValueError, match="Unsupported character"):
        generate_certificate(_data("अफ़िया"), tmp_path / "bad.pdf")
    assert not (tmp_path / "bad.pdf").exists()
