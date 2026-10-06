"""
Integration test for signing-key rotation (issue #56), end to end.

The property that actually matters is not "a file was rewritten" - it is that **a
credential an outsider could mint stops verifying**. So this test drives the whole
chain: a published key installed on disk and denylisted, a token minted under it,
the rotation, and the API's own verdict on both tokens.

The synthetic key is denylisted through the module-level constant rather than
through the real one: the real values are in git history, and this suite must not
carry them.

**Trap this test is written around.** Starlette's TestClient sends
``Host: testserver``, which *is* in the localhost bypass list, so
``get_current_token_payload`` short-circuits and a missing - or a forged - token
is accepted by the dependency too. Every assertion here is therefore on the
``valid`` field produced by ``AuthService.validate_token``, which is where the
JWT signature is actually checked. A test written as "expect 401" would pass for a
reason that has nothing to do with this fix.

Mutation that makes it red: removing the rotation; rotating without overwriting
the file; leaving ``settings.SECRET_KEY`` pointing at the old value after the file
changed.
"""

import hashlib
import logging
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from backend.core import config
from backend.core.config import _resolve_secret_key, settings
from backend.core.security import create_access_token
from backend.main import app


def _digest(value: str) -> str:
    """The digest the runtime comparison computes for a key read off disk."""
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_forged_token_stops_verifying_after_rotation(
    tmp_path, monkeypatch, caplog
):
    """
    U10 / AC-4: the old, forgeable token is rejected; the new one is accepted.
    """
    published_key = "published-" + "q" * 40
    key_path = tmp_path / "secret_key"
    # CRLF on purpose, as in the unit tests: a checkout with core.autocrlf=true
    # hands the reader a trailing newline, and a deny-list digest taken over the
    # raw bytes would never match.
    key_path.write_text(published_key + "\r\n", encoding="utf-8")

    monkeypatch.setenv("SHC_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setattr(
        config,
        "PUBLISHED_KEY_SHA256",
        frozenset({_digest(published_key)}),
    )

    # Step 1: the deployment is still signing with the published key, so anyone
    # who read it from the public repository can mint a token.
    monkeypatch.setattr(settings, "SECRET_KEY", published_key)
    forged = create_access_token({"sub": "attacker"}, expires_delta=timedelta(days=1))

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        before = await client.get(
            "/api/v1/auth/verify", headers={"Authorization": f"Bearer {forged}"}
        )
        assert before.status_code == 200, before.text
        assert before.json() == {"valid": True}, (
            "precondition: while the published key is still installed its tokens "
            "must verify, otherwise this test is not measuring anything"
        )

        # Step 2: rotate, the way the first start after the upgrade does.
        with caplog.at_level(logging.DEBUG, logger="backend.core.config"):
            rotated = _resolve_secret_key()
        assert rotated != published_key
        monkeypatch.setattr(settings, "SECRET_KEY", rotated)

        assert key_path.read_text(encoding="utf-8").strip() == rotated, (
            "the file on disk still holds the published key, so the rotation did "
            "not survive a restart"
        )
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
        assert "git history" in warnings[0].getMessage()

        # Step 3a: the forgeable credential is now worthless.
        after = await client.get(
            "/api/v1/auth/verify", headers={"Authorization": f"Bearer {forged}"}
        )
        assert after.status_code == 200, after.text
        assert after.json() == {"valid": False}, (
            "a token signed with the published key still verifies - anyone who "
            "cloned the repository can still mint one"
        )

        # Step 3b: and the rotation did not break auth.
        fresh = create_access_token(
            {"sub": "operator"}, expires_delta=timedelta(days=1)
        )
        good = await client.get(
            "/api/v1/auth/verify", headers={"Authorization": f"Bearer {fresh}"}
        )
        assert good.status_code == 200, good.text
        assert good.json() == {"valid": True}, (
            "the rotated key does not validate its own tokens; the replacement "
            "did not reach the code that signs"
        )
