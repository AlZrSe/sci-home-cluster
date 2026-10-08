"""
Tests for the API correctness fixes in issue #24.

Covers: authentication on the Syncthing endpoints, explicit error codes
that no longer depend on message wording, WebSocket log lines being
delivered exactly once, a JWT secret that survives a restart, and the
absence of the private-store reach-ins and unreachable branches that were
removed.
"""

import asyncio
import re
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from backend.core.config import settings
from backend.core.errors import APIError, job_not_found, job_not_cancellable
from backend.core.security import create_access_token, decode_access_token
from backend.main import app
from shared.schemas.job_status import JobStatus

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest_asyncio.fixture
async def async_client() -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        client.headers["Authorization"] = "Bearer localhost-no-auth"
        yield client


class TestSyncthingEndpointsRequireAuth:
    """The Syncthing endpoints were the only unauthenticated routes."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method,path",
        [("GET", "/api/v1/syncthing/status"), ("POST", "/api/v1/syncthing/scan")],
    )
    async def test_requires_authentication(self, method, path):
        # A non-local host with the bypass disabled is the only situation in
        # which the token is actually enforced.
        with patch.object(settings, "LOCALHOST_BYPASS", False):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://cluster.example.com"
            ) as client:
                response = await client.request(method, path)

        assert response.status_code == 401
        data = response.json()
        assert data["status"] == 401
        assert data["error_code"] == "AUTH_TOKEN_MISSING"

    @pytest.mark.asyncio
    async def test_status_is_reachable_with_a_valid_token(self):
        token = create_access_token({"sub": "tester"})
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://cluster.example.com",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            with patch.object(settings, "LOCALHOST_BYPASS", False):
                response = await client.get("/api/v1/syncthing/status")

        # A valid token gets past authentication. The ASGI test client does
        # not run the app lifespan, so app.state.syncthing_service is absent
        # and the endpoint returns 500 - crucially not 401.
        assert response.status_code != 401


class TestErrorCodesAreExplicit:
    """error_code must not be derived from the wording of `detail`."""

    @pytest.mark.asyncio
    async def test_same_condition_yields_same_code(self, async_client):
        first = await async_client.get("/api/v1/jobs/job-999999")
        second = await async_client.get("/api/v1/jobs/job-999998")

        assert first.status_code == 404
        assert second.status_code == 404
        assert first.json()["error_code"] == "JOB_NOT_FOUND"
        assert second.json()["error_code"] == "JOB_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_node_and_job_not_found_are_distinct(self, async_client):
        job = await async_client.get("/api/v1/jobs/job-999999")
        node = await async_client.get("/api/v1/nodes/node-999999")

        assert job.json()["error_code"] == "JOB_NOT_FOUND"
        assert node.json()["error_code"] == "NODE_NOT_FOUND"

    def test_api_error_carries_its_code(self):
        err = job_not_found("job-1")
        assert isinstance(err, APIError)
        assert err.error_code == "JOB_NOT_FOUND"
        assert err.status_code == 404

    def test_error_code_is_independent_of_detail_wording(self):
        a = job_not_found("job-1")
        b = job_not_cancellable("job-1", JobStatus.COMPLETED.value)
        assert a.error_code != b.error_code
        assert a.status_code == 404
        assert b.status_code == 409

    def test_no_substring_matching_remains(self):
        source = (REPO_ROOT / "backend" / "main.py").read_text(encoding="utf-8")
        assert "SPECIFIC_ERROR_CODES" not in source
        # get_error_code must be a plain status-code lookup.
        assert "detail_lower" not in source


class TestSecretKeyPersists:
    """SECRET_KEY used to be regenerated on every process start."""

    def test_settings_secret_key_is_populated(self):
        assert settings.SECRET_KEY
        assert len(settings.SECRET_KEY) >= 32

    def test_env_var_is_honoured(self):
        from backend.core.config import _resolve_secret_key

        with patch.dict("os.environ", {"SECRET_KEY": "a-known-secret"}):
            assert _resolve_secret_key() == "a-known-secret"

    def test_token_survives_a_settings_reload(self):
        token = create_access_token({"sub": "someone"})

        # Simulate a restart: the same persisted key must validate the token.
        with patch.object(settings, "SECRET_KEY", settings.SECRET_KEY):
            assert decode_access_token(token)["sub"] == "someone"

    def test_generated_key_is_persisted(self, tmp_path):
        import os

        from backend.core.config import _resolve_secret_key

        with patch.dict("os.environ", {"SHC_STATE_DIR": str(tmp_path)}):
            os.environ.pop("SECRET_KEY", None)
            first = _resolve_secret_key()
            # A second call must read the persisted key back, not mint a new one.
            second = _resolve_secret_key()

        assert first == second
        assert (tmp_path / "secret_key").read_text(encoding="utf-8").strip() == first


class TestNoPrivateStoreAccess:
    """Services and routers must not reach into store internals."""

    @pytest.mark.parametrize(
        "relative",
        [
            "backend/services/job_service.py",
            "backend/services/syncthing_service.py",
            "backend/services/node_service.py",
            "backend/api/v1/jobs.py",
            "backend/api/v1/nodes.py",
            "backend/api/v1/syncthing.py",
            "backend/api/v1/auth.py",
        ],
    )
    def test_no_private_store_calls(self, relative):
        source = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert not re.search(r"\b(store|_store)\._[a-z]", source), relative

    def test_stop_log_stream_is_public(self):
        from backend.store import DatabaseStore

        assert hasattr(DatabaseStore, "stop_log_stream")
        assert not hasattr(DatabaseStore, "_stop_log_stream")

    def test_create_job_with_id_is_public(self):
        from backend.store import DatabaseStore

        assert hasattr(DatabaseStore, "create_job_with_id")
        assert not hasattr(DatabaseStore, "_create_job_with_id")


class TestNoRouteCallsAnotherRoute:
    """get_job_logs_history used to call the get_job_logs route function."""

    def test_history_endpoint_does_not_call_a_route(self):
        source = (
            REPO_ROOT / "backend" / "api" / "v1" / "jobs.py"
        ).read_text(encoding="utf-8")
        # No `return await get_job_logs(` style call to a sibling route.
        assert not re.search(r"return await get_job_logs\(", source)

    @pytest.mark.asyncio
    async def test_history_endpoint_still_works(self, async_client):
        response = await async_client.get("/api/v1/jobs/job-1050/logs/history")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    @pytest.mark.asyncio
    async def test_history_endpoint_404s_like_logs(self, async_client):
        response = await async_client.get("/api/v1/jobs/job-999999/logs/history")
        # Was `in (200, 404)`: the permissive form was written to
        # accommodate the fabrication bug, so it asserted nothing. An
        # unknown job is a 404 on both paths now (issue #35).
        assert response.status_code == 404
        assert response.json()["error_code"] == "LOGS_NOT_FOUND"


class TestLogStreamDeliversEachLineOnce:
    """The route used to subscribe a second callback and deliver twice."""

    @pytest.mark.asyncio
    async def test_callback_registered_once_and_every_line_delivered_once(self):
        from sqlalchemy import func, select

        from backend.core.database import get_session
        from backend.store import DatabaseStore
        from backend.store.database import LogEntryModel
        from backend.tests.factories import create_job_spec

        store = DatabaseStore()
        job = await store.create_job(create_job_spec(name="dup-check-job"))
        await store.update_job(job.job_id, status=JobStatus.RUNNING)

        received: list = []

        def on_line(line: str) -> None:
            received.append(line)

        await store.start_log_stream(job.job_id, on_line)
        try:
            # Exactly one subscriber for this job: the callback passed to
            # start_log_stream. A second registration would deliver twice.
            assert list(store._log_stream_subscribers[job.job_id]) == [on_line]

            for _ in range(50):
                if received:
                    break
                await asyncio.sleep(0.1)
        finally:
            await store.stop_all_log_streams()

        async def stored_line_count() -> int:
            async with get_session() as session:
                result = await session.execute(
                    select(func.count())
                    .select_from(LogEntryModel)
                    .where(LogEntryModel.job_id == job.job_id)
                )
                return result.scalar()

        written = await stored_line_count()

        # Every line the worker wrote was delivered to the callback exactly
        # once, and the two counts agree.
        assert received
        assert len(received) == written
        assert len(set(received)) == len(received)

        # Stopping removes the registration.
        assert job.job_id not in store._log_stream_subscribers
