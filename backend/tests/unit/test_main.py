"""
Integration tests for the main application.
"""

from pathlib import Path

from fastapi.testclient import TestClient
from backend.main import _database_display_name, app
from backend.core.config import absolute_sqlite_path, settings


def test_root_endpoint():
    """Test the root endpoint returns correct message."""
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "Welcome to Scientific Home Cluster API"}


def test_health_endpoint(seeded_cluster):
    """
    Test the health endpoint returns correct status.

    ``seeded_cluster`` registers a node explicitly. The overall status is
    "healthy" only when at least one node is registered, so this test
    depends on its own fixture rather than on ambient demo seeding.
    """
    client = TestClient(app)
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["version"] == settings.VERSION
    # Check that all required health check sections are present
    assert "store" in data
    assert "config" in data
    assert "syncthing" in data
    assert "database" in data


def test_health_reports_absolute_database_path(seeded_cluster):
    """
    T10 / AC-8: /health names the absolute database path.

    It used to report the bare file name, which is identical for every
    candidate database on disk - so a developer could not tell from the API
    which file the process had opened (issue #34).
    """
    client = TestClient(app)
    response = client.get("/api/v1/health")
    assert response.status_code == 200

    reported = response.json()["database"]["url"]

    assert Path(reported).is_absolute(), f"database.url must be absolute: {reported}"
    # It agrees with what the settings singleton actually points at, so the
    # report cannot drift from the file the engine opens.
    assert reported == str(absolute_sqlite_path(settings.DATABASE_URL))


def test_relative_database_url_health_display(monkeypatch, tmp_path):
    """
    T11 / AC-8: a relative URL does not render as an ambiguous bare file name.

    Still legal - it is warned about, not rejected - but /health has to resolve
    it the way SQLite will, or it reports "./x.db" and stays just as
    unanswerable as before.
    """
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite:///./x.db")
    monkeypatch.chdir(tmp_path)

    reported = _database_display_name(settings.DATABASE_URL)

    assert reported == str(tmp_path / "x.db")
    assert Path(reported).is_absolute()


def test_database_display_name_strips_credentials():
    """/health must never echo credentials, whatever it reports."""
    reported = _database_display_name("postgresql://user:hunter2@db.internal/shc")
    assert "hunter2" not in reported
    assert reported == "db.internal/shc"
    # A non-file SQLite URL has no path to report, but must still not be blank.
    assert _database_display_name("sqlite:///:memory:") == ":memory:"


def test_http_exception_handler():
    """Test that HTTPException is converted to ErrorResponse format."""
    client = TestClient(app)
    response = client.get("/api/v1/jobs/nonexistent-job-id")
    # Job not found returns 404 with ErrorResponse
    assert response.status_code == 404
    data = response.json()
    assert data["status"] == 404
    assert data["title"] == "Not Found"
    assert "error_code" in data
    assert data["error_code"] == "JOB_NOT_FOUND"
    assert "instance" in data


def test_validation_exception_handler():
    """Test that validation errors return ErrorResponse format."""
    client = TestClient(app)
    # Send invalid token (too short) to trigger validation error
    response = client.post(
        "/api/v1/auth/validate",
        json={"token": "short"},
        headers={"Authorization": "Bearer localhost-no-auth"},
    )
    assert response.status_code == 422
    data = response.json()
    assert data["status"] == 422
    assert data["title"] == "Unprocessable Entity"
    assert "error_code" in data
    assert data["error_code"] == "VALIDATION_FAILED"
    assert "instance" in data


def test_request_id_middleware():
    """Test that X-Request-ID is added to responses."""
    client = TestClient(app)
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert "x-request-id" in response.headers
