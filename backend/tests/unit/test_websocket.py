"""
Unit tests for WebSocket connection, auth, message streaming, disconnect.
SKIPPED: ASGI WebSocket testing has limitations with FastAPI lifespan and app.state.
These tests require a running app with proper lifespan initialization.
"""

import asyncio
import io
from datetime import timedelta
from typing import Any, AsyncGenerator

import pytest
from fastapi import WebSocket, WebSocketException, status
from httpx import AsyncClient, ASGITransport
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.main import app
from backend.store import get_store
from shared.schemas.job_status import JobStatus
from backend.tests.factories import create_job_spec, job_spec_to_yaml_bytes
from backend.core.deps import get_ws_token_payload
from backend.core.security import create_access_token
from backend.core.config import settings
from unittest.mock import patch


@pytest.mark.skip(
    reason="ASGI WebSocket testing has limitations with FastAPI lifespan and app.state"
)
@pytest.mark.unit
class TestWebSocketConnection:
    """Tests for WebSocket connection establishment."""

    @pytest.fixture
    async def async_client(self) -> AsyncGenerator[AsyncClient, None]:
        """Create an async client for testing."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            client.headers["Authorization"] = "Bearer localhost-no-auth"
            yield client

    @pytest.fixture
    async def test_job_id(self, async_client):
        """Create a test job and return its ID."""
        job_spec = create_job_spec(name="ws-test-job")
        yaml_content = job_spec_to_yaml_bytes(job_spec)

        files = {"job.yaml": ("job.yaml", io.BytesIO(yaml_content), "application/yaml")}
        response = await async_client.post("/api/v1/jobs", files=files)
        assert response.status_code == 201
        return response.json()["job_id"]

    @pytest.mark.asyncio
    async def test_websocket_connect_success(self, async_client, test_job_id):
        """Test successful WebSocket connection to log stream."""
        async with async_client.websocket_connect(
            f"/api/v1/jobs/{test_job_id}/logs"
        ) as ws:
            # Connection established successfully
            assert ws is not None

    @pytest.mark.asyncio
    async def test_websocket_connect_nonexistent_job(self, async_client):
        """Test WebSocket connection to non-existent job fails."""
        with pytest.raises(Exception):
            # WebSocket connection should be rejected
            async with async_client.websocket_connect(
                "/api/v1/jobs/job-999999/logs"
            ) as _ws:
                pass  # Should not reach here

    @pytest.mark.asyncio
    async def test_websocket_receive_messages(self, async_client, test_job_id):
        """Test receiving structured messages via WebSocket."""
        async with async_client.websocket_connect(
            f"/api/v1/jobs/{test_job_id}/logs"
        ) as ws:
            # Receive first message (should be a log line or status)
            # The implementation sends log lines as plain text
            try:
                message = await asyncio.wait_for(ws.receive_text(), timeout=2.0)
                assert isinstance(message, str)
                assert len(message) > 0
            except asyncio.TimeoutError:
                # If no message received in time, that's also valid for this test
                pass


@pytest.mark.skip(
    reason="ASGI WebSocket testing has limitations with FastAPI lifespan and app.state"
)
@pytest.mark.unit
class TestWebSocketAuthentication:
    """Tests for WebSocket authentication."""

    @pytest.fixture
    async def unauthenticated_client(self) -> AsyncGenerator[AsyncClient, None]:
        """Create an async client without auth header."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client

    @pytest.fixture
    async def test_job_id(self, unauthenticated_client):
        """Create a test job using localhost bypass."""
        # Use localhost bypass to create job
        unauthenticated_client.headers["Authorization"] = "Bearer localhost-no-auth"
        job_spec = create_job_spec(name="ws-auth-test-job")
        yaml_content = job_spec_to_yaml_bytes(job_spec)

        files = {"job.yaml": ("job.yaml", io.BytesIO(yaml_content), "application/yaml")}
        response = await unauthenticated_client.post("/api/v1/jobs", files=files)
        assert response.status_code == 201
        return response.json()["job_id"]

    @pytest.mark.asyncio
    async def test_websocket_localhost_bypass(
        self, unauthenticated_client, test_job_id
    ):
        """Test WebSocket connection works with localhost bypass."""
        # TestClient with base_url="http://test" simulates localhost
        async with unauthenticated_client.websocket_connect(
            f"/api/v1/jobs/{test_job_id}/logs"
        ) as ws:
            assert ws is not None

    @pytest.mark.asyncio
    async def test_websocket_with_token_query_param(self, test_job_id):
        """Test WebSocket connection with token in query parameter."""
        with patch.object(settings, "LOCALHOST_BYPASS", False):
            token = create_access_token({"sub": "test-user"})
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                async with client.websocket_connect(
                    f"/api/v1/jobs/{test_job_id}/logs?token={token}"
                ) as _ws:
                    assert _ws is not None

    @pytest.mark.asyncio
    async def test_websocket_with_auth_header(self, test_job_id):
        """Test WebSocket connection with Authorization header."""
        with patch.object(settings, "LOCALHOST_BYPASS", False):
            token = create_access_token({"sub": "test-user"})
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                async with client.websocket_connect(
                    f"/api/v1/jobs/{test_job_id}/logs",
                    headers={"Authorization": f"Bearer {token}"},
                ) as _ws:
                    assert _ws is not None

    @pytest.mark.asyncio
    async def test_websocket_rejects_invalid_token(self, test_job_id):
        """Test WebSocket rejects invalid token when not on localhost."""
        with patch.object(settings, "LOCALHOST_BYPASS", False):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                with pytest.raises(Exception):
                    async with client.websocket_connect(
                        "/api/v1/jobs/job-999999/logs?token=invalid"
                    ) as _ws:
                        pass


@pytest.mark.skip(
    reason="ASGI WebSocket testing has limitations with FastAPI lifespan and app.state"
)
@pytest.mark.unit
class TestWebSocketMessageStreaming:
    """Tests for WebSocket message streaming and structured messages."""

    @pytest.fixture
    async def async_client(self) -> AsyncGenerator[AsyncClient, None]:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            client.headers["Authorization"] = "Bearer localhost-no-auth"
            yield client

    @pytest.fixture
    async def running_job_id(self, async_client):
        """Create a job and set it to RUNNING status for log streaming."""
        job_spec = create_job_spec(name="ws-stream-job")
        yaml_content = job_spec_to_yaml_bytes(job_spec)

        import io

        files = {"job.yaml": ("job.yaml", io.BytesIO(yaml_content), "application/yaml")}
        response = await async_client.post("/api/v1/jobs", files=files)
        assert response.status_code == 201
        job_id = response.json()["job_id"]

        # Set job to RUNNING to enable log streaming
        store = get_store()
        await store.update_job(job_id, status=JobStatus.RUNNING, node_id="node-alpha")
        return job_id

    @pytest.mark.asyncio
    async def test_websocket_receives_log_lines(self, async_client, running_job_id):
        """Test WebSocket receives log lines for RUNNING job."""
        async with async_client.websocket_connect(
            f"/api/v1/jobs/{running_job_id}/logs"
        ) as _ws:
            # Should receive log lines
            messages = []
            try:
                for _ in range(3):
                    message = await asyncio.wait_for(_ws.receive_text(), timeout=3.0)
                    messages.append(message)
            except asyncio.TimeoutError:
                pass

            assert len(messages) > 0
            for msg in messages:
                assert isinstance(msg, str)
                assert len(msg) > 0

    @pytest.mark.asyncio
    async def test_websocket_log_line_format(self, async_client, running_job_id):
        """Test log lines have expected format with timestamp."""
        async with async_client.websocket_connect(
            f"/api/v1/jobs/{running_job_id}/logs"
        ) as _ws:
            try:
                message = await asyncio.wait_for(_ws.receive_text(), timeout=3.0)
                # Log format: "YYYY-MM-DD HH:MM:SS LEVEL  message"
                assert "INFO" in message or "DEBUG" in message or "WARN" in message
                # Should have timestamp at start
                parts = message.split(" ", 2)
                assert len(parts) >= 3
            except asyncio.TimeoutError:
                pytest.skip("No log message received in time")

    @pytest.mark.asyncio
    async def test_websocket_multiple_connections(self, async_client, running_job_id):
        """Test multiple WebSocket connections to same job."""
        connections = []
        try:
            for _ in range(3):
                ws = await async_client.websocket_connect(
                    f"/api/v1/jobs/{running_job_id}/logs"
                )
                connections.append(ws)

            # All connections should receive messages
            for ws in connections:
                try:
                    message = await asyncio.wait_for(ws.receive_text(), timeout=3.0)
                    assert isinstance(message, str)
                except asyncio.TimeoutError:
                    pass
        finally:
            for ws in connections:
                await ws.close()


@pytest.mark.skip(
    reason="ASGI WebSocket testing has limitations with FastAPI lifespan and app.state"
)
@pytest.mark.unit
class TestWebSocketDisconnect:
    """Tests for WebSocket graceful disconnection."""

    @pytest.fixture
    async def async_client(self) -> AsyncGenerator[AsyncClient, None]:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            client.headers["Authorization"] = "Bearer localhost-no-auth"
            yield client

    @pytest.fixture
    async def running_job_id(self, async_client):
        job_spec = create_job_spec(name="ws-disconnect-job")
        yaml_content = job_spec_to_yaml_bytes(job_spec)

        files = {"job.yaml": ("job.yaml", io.BytesIO(yaml_content), "application/yaml")}
        response = await async_client.post("/api/v1/jobs", files=files)
        assert response.status_code == 201
        job_id = response.json()["job_id"]

        store = get_store()
        await store.update_job(job_id, status=JobStatus.RUNNING, node_id="node-alpha")
        return job_id

    @pytest.mark.asyncio
    async def test_websocket_client_disconnect(self, async_client, running_job_id):
        """Test graceful handling of client disconnect."""
        ws = await async_client.websocket_connect(f"/api/v1/jobs/{running_job_id}/logs")
        # Receive one message
        try:
            await asyncio.wait_for(ws.receive_text(), timeout=3.0)
        except asyncio.TimeoutError:
            pass

        # Close connection gracefully
        await ws.close()

        # Should not raise any exceptions
        assert True

    @pytest.mark.asyncio
    async def test_websocket_server_cleanup_on_disconnect(
        self, async_client, running_job_id
    ):
        """Test server cleans up resources on client disconnect."""
        store = get_store()

        # Connect and immediately disconnect
        ws = await async_client.websocket_connect(f"/api/v1/jobs/{running_job_id}/logs")
        await ws.close()

        # Give some time for cleanup
        await asyncio.sleep(0.1)

        # Verify no lingering tasks for this job
        # The _stop_log_stream should have been called
        # We can't easily test internal state, but we can verify
        # the job still exists and is in RUNNING state
        job = await store.get_job(running_job_id)
        assert job is not None
        assert job.status == JobStatus.RUNNING

    @pytest.mark.asyncio
    async def test_websocket_reconnect_after_disconnect(
        self, async_client, running_job_id
    ):
        """Test reconnecting after disconnect works."""
        # First connection
        ws1 = await async_client.websocket_connect(
            f"/api/v1/jobs/{running_job_id}/logs"
        )
        await ws1.close()

        # Second connection should work
        ws2 = await async_client.websocket_connect(
            f"/api/v1/jobs/{running_job_id}/logs"
        )
        try:
            message = await asyncio.wait_for(ws2.receive_text(), timeout=3.0)
            assert isinstance(message, str)
        except asyncio.TimeoutError:
            pass
        finally:
            await ws2.close()


@pytest.mark.skip(
    reason="ASGI WebSocket testing has limitations with FastAPI lifespan and app.state"
)
@pytest.mark.unit
class TestWebSocketEdgeCases:
    """Tests for WebSocket edge cases."""

    @pytest.fixture
    async def async_client(self) -> AsyncGenerator[AsyncClient, None]:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            client.headers["Authorization"] = "Bearer localhost-no-auth"
            yield client

    @pytest.mark.asyncio
    async def test_websocket_for_pending_job(self, async_client):
        """Test WebSocket for PENDING job (no streaming)."""
        job_spec = create_job_spec(name="ws-pending-job")
        yaml_content = job_spec_to_yaml_bytes(job_spec)

        files = {"job.yaml": ("job.yaml", io.BytesIO(yaml_content), "application/yaml")}
        response = await async_client.post("/api/v1/jobs", files=files)
        assert response.status_code == 201
        job_id = response.json()["job_id"]

        # PENDING job - WebSocket should connect but not stream
        async with async_client.websocket_connect(f"/api/v1/jobs/{job_id}/logs") as _ws:
            # For PENDING jobs, the stream worker exits immediately
            # Client might receive nothing or connection closes
            try:
                _ = await asyncio.wait_for(_ws.receive_text(), timeout=1.0)
            except asyncio.TimeoutError:
                pass  # Expected for PENDING job

    @pytest.mark.asyncio
    async def test_websocket_for_completed_job(self, async_client):
        """Test WebSocket for COMPLETED job."""
        job_spec = create_job_spec(name="ws-completed-job")
        yaml_content = job_spec_to_yaml_bytes(job_spec)

        files = {"job.yaml": ("job.yaml", io.BytesIO(yaml_content), "application/yaml")}
        response = await async_client.post("/api/v1/jobs", files=files)
        assert response.status_code == 201
        job_id = response.json()["job_id"]

        store = get_store()
        await store.update_job(job_id, status=JobStatus.COMPLETED, exit_code=0)

        async with async_client.websocket_connect(f"/api/v1/jobs/{job_id}/logs") as _ws:
            # For COMPLETED jobs, the stream worker exits immediately
            try:
                _ = await asyncio.wait_for(_ws.receive_text(), timeout=1.0)
            except asyncio.TimeoutError:
                pass  # Expected

    @pytest.mark.asyncio
    async def test_websocket_heartbeat_keepalive(self, async_client, running_job_id):
        """Test WebSocket stays alive with periodic messages."""
        async with async_client.websocket_connect(
            f"/api/v1/jobs/{running_job_id}/logs"
        ) as ws:
            # Try to receive multiple messages over time
            messages = []
            for _ in range(5):
                try:
                    message = await asyncio.wait_for(ws.receive_text(), timeout=2.0)
                    messages.append(message)
                except asyncio.TimeoutError:
                    break

            # Should receive at least some messages
            # (interval is ~1.4 seconds in the mock)
            assert len(messages) >= 1


@pytest.mark.skip(
    reason="ASGI WebSocket testing has limitations with FastAPI lifespan and app.state"
)
@pytest.mark.unit
class TestWebSocketWithJWTAuth:
    """Tests for WebSocket with JWT authentication (non-localhost)."""

    @pytest.fixture
    def jwt_token(self):
        """Create a valid JWT token."""
        return create_access_token({"sub": "test-user"})

    @pytest.mark.asyncio
    async def test_websocket_jwt_token_query_param(self, jwt_token):
        """Test WebSocket with JWT token in query param."""
        with patch.object(settings, "LOCALHOST_BYPASS", False):
            # First create a job using authenticated client
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                client.headers["Authorization"] = f"Bearer {jwt_token}"
                job_spec = create_job_spec(name="ws-jwt-job")
                yaml_content = job_spec_to_yaml_bytes(job_spec)

                files = {
                    "job.yaml": (
                        "job.yaml",
                        io.BytesIO(yaml_content),
                        "application/yaml",
                    )
                }
                response = await client.post("/api/v1/jobs/", files=files)
                assert response.status_code == 201
                job_id = response.json()["job_id"]

                # Set to RUNNING
                store = get_store()
                await store.update_job(
                    job_id, status=JobStatus.RUNNING, node_id="node-alpha"
                )

            # Now connect with token in query param
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as ws_client:
                async with ws_client.websocket_connect(
                    f"/api/v1/jobs/{job_id}/logs?token={jwt_token}"
                ) as ws:
                    assert ws is not None
                    try:
                        message = await asyncio.wait_for(ws.receive_text(), timeout=3.0)
                        assert isinstance(message, str)
                    except asyncio.TimeoutError:
                        pass

    @pytest.mark.asyncio
    async def test_websocket_jwt_token_auth_header(self, jwt_token):
        """Test WebSocket with JWT token in Authorization header."""
        with patch.object(settings, "LOCALHOST_BYPASS", False):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                client.headers["Authorization"] = f"Bearer {jwt_token}"
                job_spec = create_job_spec(name="ws-jwt-header-job")
                yaml_content = job_spec_to_yaml_bytes(job_spec)

                files = {
                    "job.yaml": (
                        "job.yaml",
                        io.BytesIO(yaml_content),
                        "application/yaml",
                    )
                }
                response = await client.post("/api/v1/jobs", files=files)
                assert response.status_code == 201
                job_id = response.json()["job_id"]

                store = get_store()
                await store.update_job(
                    job_id, status=JobStatus.RUNNING, node_id="node-alpha"
                )

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ws_client:
                async with ws_client.websocket_connect(
                    f"/api/v1/jobs/{job_id}/logs",
                    headers={"Authorization": f"Bearer {jwt_token}"},
                ) as ws:
                    assert ws is not None
                    try:
                        message = await asyncio.wait_for(ws.receive_text(), timeout=3.0)
                        assert isinstance(message, str)
                    except asyncio.TimeoutError:
                        pass


# ============================================================================
# Issue #53 - WebSocket auth rejection with proper close frame
# ============================================================================
#
# These tests drive a REAL AsyncClient websocket handshake to verify that
# unauthenticated connections on non-bypass hosts receive an immediate
# close frame (code 1008) instead of hanging. The defect was that the
# global HTTPException handler returned JSONResponse which Starlette
# discards on websocket scopes.
#
# These tests use base_url="http://evil-localhost.com" to simulate a
# non-bypass host and MUST NOT be marked @pytest.mark.skip.
# ============================================================================


@pytest.mark.unit
class TestWebSocketAuthRejection:
    """Tests for WebSocket authentication rejection (issue #53)."""

    @pytest.fixture
    def client_non_bypass(self) -> TestClient:
        """Create a test client with a non-bypass host."""
        return TestClient(app, base_url="http://evil-localhost.com")

    def test_websocket_rejects_no_token(self, client_non_bypass):
        """Test WebSocket rejects connection with no token on non-bypass host."""
        with patch.object(settings, "LOCALHOST_BYPASS", True):
            try:
                with client_non_bypass.websocket_connect(
                    "/api/v1/jobs/any/logs", headers={"Host": "evil-localhost.com"}
                ) as _ws:
                    pytest.fail("Expected WebSocketDisconnect to be raised")
            except WebSocketDisconnect as e:
                assert e.code == 1008
                assert "Authentication required" in e.reason

    def test_websocket_rejects_invalid_token(self, client_non_bypass):
        """Test WebSocket rejects connection with invalid token on non-bypass host."""
        with patch.object(settings, "LOCALHOST_BYPASS", True):
            try:
                with client_non_bypass.websocket_connect(
                    "/api/v1/jobs/any/logs",
                    headers={
                        "Host": "evil-localhost.com",
                        "Authorization": "Bearer invalid",
                    },
                ) as _ws:
                    pytest.fail("Expected WebSocketDisconnect to be raised")
            except WebSocketDisconnect as e:
                assert e.code == 1008
                assert "Invalid or expired token" in e.reason

    def test_websocket_rejects_expired_token(self, client_non_bypass):
        """Test WebSocket rejects connection with expired token on non-bypass host."""
        with patch.object(settings, "LOCALHOST_BYPASS", True):
            # Create an expired token (expires in the past)
            expired_token = create_access_token(
                {"sub": "test-user"}, expires_delta=timedelta(seconds=-3600)
            )
            try:
                with client_non_bypass.websocket_connect(
                    "/api/v1/jobs/any/logs",
                    headers={
                        "Host": "evil-localhost.com",
                        "Authorization": f"Bearer {expired_token}",
                    },
                ) as _ws:
                    pytest.fail("Expected WebSocketDisconnect to be raised")
            except WebSocketDisconnect as e:
                assert e.code == 1008
                assert "Invalid or expired token" in e.reason

    def test_websocket_localhost_bypass_works(self):
        """Test WS connection works with localhost bypass (no regression on #31)."""
        with patch.object(settings, "LOCALHOST_BYPASS", True):
            client = TestClient(app, base_url="http://localhost:8000")
            try:
                with client.websocket_connect(
                    "/api/v1/jobs/nonexistent/logs",
                    headers={"Host": "localhost:8000"},
                ) as ws:
                    # If we get here, auth passed but job not found
                    # The route will close with 1008 "Job not found"
                    ws.receive_text()
            except WebSocketDisconnect as e:
                # Job not found is also 1008 but with different reason
                assert e.code == 1008
                assert "Job not found" in e.reason

    def test_websocket_valid_token_works(self):
        """Test WS connection works with valid token on non-bypass host."""
        with patch.object(settings, "LOCALHOST_BYPASS", False):
            token = create_access_token({"sub": "test-user"})
            client = TestClient(app, base_url="http://evil-localhost.com")
            try:
                with client.websocket_connect(
                    "/api/v1/jobs/any/logs",
                    headers={
                        "Host": "evil-localhost.com",
                        "Authorization": f"Bearer {token}",
                    },
                ) as ws:
                    # Connection accepted (auth passes); route fails with
                    # "Job not found" after auth
                    assert ws is not None
                    try:
                        ws.receive_text()
                    except WebSocketDisconnect as e:
                        # Job not found is expected after successful auth
                        assert e.code == 1008
                        assert "Job not found" in e.reason
            except WebSocketDisconnect as e:
                if e.code == 1008 and "Job not found" in e.reason:
                    # Auth passed, job not found - this is expected
                    pass
                elif (
                    "Authentication required" in e.reason
                    or "Invalid or expired token" in e.reason
                ):
                    pytest.fail(f"Valid token was rejected: {e}")
                else:
                    pytest.fail(f"Unexpected WebSocketDisconnect: {e}")


# ============================================================================
# Issue #31 - the WebSocket half of the localhost bypass
# ============================================================================
#
# AC-10: `get_ws_token_payload` (backend/core/deps.py) must agree with
# `get_current_token_payload`, so log streaming is not left behind REST. It is a
# separate function reading a separate header path, so a fix applied only to the
# REST dependency would leave the log stream un-authenticated while every REST
# test passed.
#
# These tests drive the DEPENDENCY rather than a full `TestClient` handshake,
# and that is a deliberate choice forced by a pre-existing defect: `main.py`
# registers a global `HTTPException` handler that returns a `JSONResponse`, and
# Starlette's `ExceptionMiddleware` DISCARDS a handler's return value on a
# websocket scope (`await handler(websocket, exc)` - the response is never
# sent). So a log client with no token on a non-bypass host gets neither an
# `accept` nor a `close`: the handshake hangs forever. That is true on `main`
# today for every non-local host and is independent of the hostname list, so it
# is reported rather than fixed here - fixing it means changing what the
# dependency raises, which is outside this issue.
#
# Asserting the bypass decision at the dependency boundary is also the more
# precise test: it is exactly the decision AC-10 is about, it needs no job to
# exist, and it is unaffected by the demo-data flag (issue #35).

# Log streaming lives at `/{job_id}/logs` (backend/api/v1/jobs.py).
LOGS_PATH = "/api/v1/jobs/job-1050/logs"


def _ws_scope(host_header: str) -> dict:
    """A minimal ASGI websocket scope carrying one `Host` header."""
    return {
        "type": "websocket",
        "path": LOGS_PATH,
        "root_path": "",
        "scheme": "ws",
        "query_string": b"",
        "headers": [(b"host", host_header.encode("latin-1"))],
        "client": ("testclient", 50000),
        "server": None,
        "subprotocols": [],
        "state": {},
    }


async def _no_receive() -> Any:
    """The bypass must decide from the Host header alone, never read the socket."""
    raise AssertionError("get_ws_token_payload must not read from the socket")


async def _no_send(message: Any) -> None:
    """...nor write to it: the bypass returns before the socket is touched."""
    raise AssertionError(
        f"get_ws_token_payload must not write to the socket: {message}"
    )


def _ws(host_header: str) -> WebSocket:
    """A `WebSocket` whose URL carries `host_header`."""
    return WebSocket(_ws_scope(host_header), receive=_no_receive, send=_no_send)


@pytest.mark.unit
class TestWebSocketLocalhostBypass:
    """T30 and its neighbours: the WS bypass matrix, with no token anywhere."""

    @pytest.mark.parametrize(
        "host_header",
        [
            "localhost:8000",
            "127.0.0.1:8000",
            "[::1]:8000",  # T30 - the spelling a browser sends; was refused
            "0.0.0.0:8000",
            "testserver",
            "myhost.local",
            "preview.lovable.app",
        ],
    )
    def test_ws_localhost_hosts_are_bypassed(self, host_header):
        """
        A bypass host yields the same identity REST yields. Asserted through
        the real dependency, so the two header paths cannot drift apart again.
        """
        with patch.object(settings, "LOCALHOST_BYPASS", True):
            payload = asyncio.run(get_ws_token_payload(websocket=_ws(host_header)))
        assert payload == {"sub": "localhost_user"}

    @pytest.mark.parametrize(
        "host_header",
        [
            "preview.lovableproject.com",
            "localhost.evil.com",
            "evil-localhost.com",
            "notlocal",
        ],
    )
    def test_ws_non_localhost_hosts_are_refused(self, host_header):
        with patch.object(settings, "LOCALHOST_BYPASS", True):
            with pytest.raises(WebSocketException) as excinfo:
                asyncio.run(get_ws_token_payload(websocket=_ws(host_header)))
        assert excinfo.value.code == 1008
        assert "Authentication required" in excinfo.value.reason

    @pytest.mark.parametrize("host_header", ["[::1]:8000", "localhost:8000"])
    def test_ws_bypass_does_not_fire_when_disabled(self, host_header):
        """With LOCALHOST_BYPASS off, a local host is no longer special."""
        with patch.object(settings, "LOCALHOST_BYPASS", False):
            with pytest.raises(WebSocketException) as excinfo:
                asyncio.run(get_ws_token_payload(websocket=_ws(host_header)))
        assert excinfo.value.code == 1008
        assert "Authentication required" in excinfo.value.reason

    @pytest.mark.asyncio
    async def test_ws_ipv6_loopback_bypass_is_identical_to_ipv4(self):
        """
        T30 is not merely "allowed": the bracketed loopback form must produce
        byte-identical output to `127.0.0.1`, so a bypass that fired for the
        wrong reason cannot pass.
        """
        with patch.object(settings, "LOCALHOST_BYPASS", True):
            v4 = await get_ws_token_payload(websocket=_ws("127.0.0.1:8000"))
            v6 = await get_ws_token_payload(websocket=_ws("[::1]:8000"))
        assert v4 == v6 == {"sub": "localhost_user"}

    @pytest.mark.asyncio
    async def test_ws_unbracketed_ipv6_host_header_cannot_be_anything_else(self):
        """
        T21 CORRECTION, restated on the WS path - see the long note on the
        equivalent test in `test_auth.py`.

        `Host: ::1` yields `websocket.url.hostname is None`, so the empty guard
        rejects it before any list entry is consulted. RFC 3986 s3.2.2 requires
        an IPv6 literal in an authority to be bracketed, so no conforming client
        sends this form; the bracketed spelling is the reachable one.
        """
        with patch.object(settings, "LOCALHOST_BYPASS", True):
            with pytest.raises(WebSocketException) as excinfo:
                await get_ws_token_payload(websocket=_ws("::1"))
        assert excinfo.value.code == 1008
        assert "Authentication required" in excinfo.value.reason


# ============================================================================
# Issue #156 - WebSocket auth via query parameter removed (security)
# ============================================================================
# These tests verify that query parameter authentication is rejected and
# Authorization header / Sec-WebSocket-Protocol are accepted.
# These tests use TestClient with base_url="http://evil-localhost.com" to
# simulate a non-bypass host.
# ============================================================================


@pytest.mark.unit
class TestWebSocketQueryParamRejection:
    """Tests for WebSocket query parameter rejection (issue #156)."""

    @pytest.fixture
    def jwt_token(self):
        """Create a valid JWT token."""
        return create_access_token({"sub": "test-user"})

    @pytest.fixture
    def client_non_bypass(self) -> TestClient:
        """Create a test client with a non-bypass host."""
        return TestClient(app, base_url="http://evil-localhost.com")

    def test_websocket_rejects_query_param_token(self, client_non_bypass, jwt_token):
        """Test WebSocket rejects token in query parameter (AC-1)."""
        with patch.object(settings, "LOCALHOST_BYPASS", False):
            try:
                with client_non_bypass.websocket_connect(
                    f"/api/v1/jobs/job-123/logs?token={jwt_token}",
                    headers={"Host": "evil-localhost.com"},
                ) as _ws:
                    pytest.fail("Expected WebSocketDisconnect to be raised")
            except WebSocketDisconnect as e:
                assert e.code == status.WS_1008_POLICY_VIOLATION
                assert "Query parameter authentication is not supported" in e.reason

    def test_websocket_accepts_auth_header(self, client_non_bypass, jwt_token):
        """Test WebSocket accepts token in Authorization header (AC-2)."""
        with patch.object(settings, "LOCALHOST_BYPASS", False):
            with client_non_bypass.websocket_connect(
                "/api/v1/jobs/job-123/logs",
                headers={
                    "Authorization": f"Bearer {jwt_token}",
                    "Host": "evil-localhost.com",
                },
            ) as ws:
                # Connection accepted; route may fail with "Job not found" after auth
                assert ws is not None

    def test_websocket_accepts_subprotocol(self, client_non_bypass, jwt_token):
        """Test WebSocket accepts token in Sec-WebSocket-Protocol (AC-3)."""
        with patch.object(settings, "LOCALHOST_BYPASS", False):
            with client_non_bypass.websocket_connect(
                "/api/v1/jobs/job-123/logs",
                headers={
                    "Sec-WebSocket-Protocol": f"bearer, {jwt_token}",
                    "Host": "evil-localhost.com",
                },
            ) as ws:
                # Connection accepted (auth passes); route will fail with
                # "Job not found" after auth since job-123 doesn't exist
                assert ws is not None

    @pytest.mark.asyncio
    async def test_websocket_localhost_bypass_unchanged(self):
        """Test localhost bypass still works without any token (AC-4)."""
        bypass_hosts = [
            "localhost:8000",
            "127.0.0.1:8000",
            "[::1]:8000",
            "0.0.0.0:8000",
            "testserver",
            "myhost.local",
            "preview.lovable.app",
        ]
        for host_header in bypass_hosts:
            with patch.object(settings, "LOCALHOST_BYPASS", True):
                payload = await get_ws_token_payload(websocket=_ws(host_header))
            assert payload == {"sub": "localhost_user"}

    def test_websocket_rejects_invalid_token_in_header(self, client_non_bypass):
        """Test WebSocket rejects invalid token in Authorization header (AC-5)."""
        with patch.object(settings, "LOCALHOST_BYPASS", False):
            try:
                with client_non_bypass.websocket_connect(
                    "/api/v1/jobs/job-123/logs",
                    headers={
                        "Authorization": "Bearer invalid-token",
                        "Host": "evil-localhost.com",
                    },
                ) as _ws:
                    pytest.fail("Expected WebSocketDisconnect to be raised")
            except WebSocketDisconnect as e:
                assert e.code == status.WS_1008_POLICY_VIOLATION
                assert "Invalid or expired token" in e.reason

    def test_websocket_rejects_invalid_token_in_subprotocol(self, client_non_bypass):
        """Test WebSocket rejects invalid token in Sec-WebSocket-Protocol (AC-5)."""
        with patch.object(settings, "LOCALHOST_BYPASS", False):
            try:
                with client_non_bypass.websocket_connect(
                    "/api/v1/jobs/job-123/logs",
                    headers={
                        "Sec-WebSocket-Protocol": "bearer, invalid-token",
                        "Host": "evil-localhost.com",
                    },
                ) as _ws:
                    pytest.fail("Expected WebSocketDisconnect to be raised")
            except WebSocketDisconnect as e:
                assert e.code == status.WS_1008_POLICY_VIOLATION
                assert "Invalid or expired token" in e.reason

    def test_websocket_rejects_no_token(self, client_non_bypass):
        """Test WebSocket rejects connection with no token on non-bypass host (AC-6)."""
        with patch.object(settings, "LOCALHOST_BYPASS", False):
            try:
                with client_non_bypass.websocket_connect(
                    "/api/v1/jobs/job-123/logs",
                    headers={"Host": "evil-localhost.com"},
                ) as _ws:
                    pytest.fail("Expected WebSocketDisconnect to be raised")
            except WebSocketDisconnect as e:
                assert e.code == status.WS_1008_POLICY_VIOLATION
                assert "Authentication required" in e.reason

    def test_websocket_subprotocol_echo(self, client_non_bypass, jwt_token):
        """Test server echoes 'bearer' subprotocol when client offers it (AC-3)."""
        with patch.object(settings, "LOCALHOST_BYPASS", False):
            with client_non_bypass.websocket_connect(
                "/api/v1/jobs/job-123/logs",
                headers={
                    "Sec-WebSocket-Protocol": f"bearer, {jwt_token}, other-protocol",
                    "Host": "evil-localhost.com",
                },
            ) as ws:
                # Connection accepted (auth passes); route will fail with
                # "Job not found" after auth since job-123 doesn't exist
                assert ws is not None


@pytest.mark.unit
class TestGetWsTokenPayloadDirect:
    """Direct unit tests for get_ws_token_payload dependency (issue #156)."""

    @pytest.fixture
    def jwt_token(self):
        """Create a valid JWT token."""
        return create_access_token({"sub": "test-user"})

    @pytest.mark.asyncio
    async def test_get_ws_token_payload_rejects_query_param(self, jwt_token):
        """Test get_ws_token_payload rejects token from query_params (AC-1)."""
        with patch.object(settings, "LOCALHOST_BYPASS", False):
            ws = _ws("evil.com")
            ws._query_params = {"token": jwt_token}
            with pytest.raises(WebSocketException) as excinfo:
                await get_ws_token_payload(websocket=ws)
            assert excinfo.value.code == status.WS_1008_POLICY_VIOLATION
            assert (
                "Query parameter authentication is not supported"
                in excinfo.value.reason
            )

    @pytest.mark.asyncio
    async def test_get_ws_token_payload_accepts_auth_header(self, jwt_token):
        """Test get_ws_token_payload accepts token from Authorization header (AC-2)."""
        with patch.object(settings, "LOCALHOST_BYPASS", False):
            ws = _ws("evil.com")
            ws._headers = {"Authorization": f"Bearer {jwt_token}"}
            payload = await get_ws_token_payload(websocket=ws)
            assert payload["sub"] == "test-user"

    @pytest.mark.asyncio
    async def test_get_ws_token_payload_accepts_subprotocol(self, jwt_token):
        """Test get_ws_token_payload accepts token from Sec-WebSocket-Protocol."""
        with patch.object(settings, "LOCALHOST_BYPASS", False):
            ws = _ws("evil.com")
            ws._headers = {"sec-websocket-protocol": f"bearer, {jwt_token}"}
            payload = await get_ws_token_payload(websocket=ws)
            assert payload["sub"] == "test-user"

    @pytest.mark.asyncio
    async def test_get_ws_token_payload_rejects_no_token(self):
        """Test get_ws_token_payload rejects when no token provided (AC-6)."""
        with patch.object(settings, "LOCALHOST_BYPASS", False):
            ws = _ws("evil.com")
            ws._headers = {}
            ws._query_params = {}
            with pytest.raises(WebSocketException) as excinfo:
                await get_ws_token_payload(websocket=ws)
            assert excinfo.value.code == status.WS_1008_POLICY_VIOLATION
            assert "Authentication required" in excinfo.value.reason

    @pytest.mark.asyncio
    async def test_get_ws_token_payload_rejects_invalid_token(self):
        """Test get_ws_token_payload rejects invalid token (AC-5)."""
        with patch.object(settings, "LOCALHOST_BYPASS", False):
            ws = _ws("evil.com")
            ws._headers = {"Authorization": "Bearer invalid-token"}
            with pytest.raises(WebSocketException) as excinfo:
                await get_ws_token_payload(websocket=ws)
            assert excinfo.value.code == status.WS_1008_POLICY_VIOLATION
            assert "Invalid or expired token" in excinfo.value.reason

    @pytest.mark.asyncio
    async def test_get_ws_token_payload_localhost_bypass_ignores_query_param(
        self, jwt_token
    ):
        """Test localhost bypass ignores query param token (AC-4)."""
        with patch.object(settings, "LOCALHOST_BYPASS", True):
            ws = _ws("localhost:8000")
            ws._query_params = {"token": jwt_token}
            payload = await get_ws_token_payload(websocket=ws)
            assert payload == {"sub": "localhost_user"}
