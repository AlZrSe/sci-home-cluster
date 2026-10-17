"""
MUST M-5.1 .. M-5.5 - lifecycle, state survival and restart policy (layer L5).

**No test in this module depends on test order.** pytest gives no cross-module
ordering guarantee, and alphabetically naming this file `test_z_lifecycle.py`
only works under the default plugin set - a future `-p randomly` would silently
invalidate it. So every scenario here is self-sufficient: it requests the stack
fixture, performs its own `down` / `up` / `down -v`, and re-establishes the
stack in its own teardown (the `live_stack` fixture below). Any test that runs
after finds a working stack regardless of what ran before.

Two of the five scenarios tear the stack down on purpose, because what they
assert - that plain `down` KEEPS both volumes, and that `down -v` DELETES both -
is only observable by doing it. MUST M-5.2 is additionally gated behind
``SHC_COMPOSE_ALLOW_DESTRUCTIVE=1`` so it costs a second, deliberate word.

One scenario needed a measured deviation, documented at length where it happens:
**MUST M-5.3's `docker kill` does not restart the container on Docker Desktop
29.8.2** - Docker treats an API-issued kill as a manual stop and suppresses the
restart policy for it. The property `docker-compose.yaml:276-282` claims is
therefore asserted at the mechanism that can observe it (a process that exits on
its own under the declared policy), with the `docker kill` behaviour itself
pinned as a re-checkable assertion so the reason is recorded rather than
asserted in prose.
"""

import json
import sys
import time
import uuid
from typing import Optional

import pytest

from backend.tests.compose.conftest import (
    COMPOSE_FILE,
    COMPOSE_INVOCATIONS,
    COMPOSE_PYTESTMARK,
    HEALTH_TIMEOUT_S,
    IMAGE_TAG,
    NON_LOCALHOST_HOST_HEADER,
    ClusterClient,
    ComposeStack,
    _mint_jwt,
    audit_destructive_invocations,
    run_compose,
    submit_job,
    volume_exists,
)

pytestmark = COMPOSE_PYTESTMARK


def _probe(name: str) -> tuple[str, int, Optional[str]]:
    """(status, restart_count, health) for a container addressed by NAME."""
    status = run_compose(
        ["docker", "inspect", name, "--format", "{{.State.Status}}"], check=False
    ).stdout.strip()
    health = run_compose(
        [
            "docker",
            "inspect",
            name,
            "--format",
            "{{if .State.Health}}{{.State.Health.Status}}{{end}}",
        ],
        check=False,
    ).stdout.strip()
    return status, _restart_count(name), health or None


def _restart_count(name: str) -> int:
    """
    `.RestartCount` for a container addressed by NAME.

    NOT read out of `.State`: `RestartCount` is a SIBLING of `State` in
    `docker inspect` output, not a member of it, so a `{{json .State}}` template
    silently yields 0 forever. That mistake cost a 120 s debugging detour during
    development, and - worse, and only found in review - it made three control
    assertions in this suite unable to fail.
    """
    result = run_compose(
        ["docker", "inspect", name, "--format", "{{.RestartCount}}"], check=False
    )
    if result.returncode != 0:
        return 0
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 0


@pytest.fixture
def live_stack(compose_stack: ComposeStack) -> ComposeStack:
    """
    The stack, guaranteed healthy on entry and left healthy on exit.

    Every scenario in this module tears the stack down or restarts it, so each
    one re-establishes it here rather than leaving that to whichever test happens
    to run next. That is what makes this module order-independent (D3).
    """
    if compose_stack.health_status() != "healthy":
        compose_stack.up()
        compose_stack.wait_healthy(HEALTH_TIMEOUT_S)
    yield compose_stack
    if compose_stack.health_status() != "healthy":
        compose_stack.up()
    compose_stack.wait_healthy(HEALTH_TIMEOUT_S)


# ---------------------------------------------------------------------------
# M-5.1  Data survives `down` + `up` (AC-20)
# ---------------------------------------------------------------------------


def test_state_survives_a_plain_down_and_up(live_stack: ComposeStack) -> None:
    """
    M-5.1 / AC-20 - with a submitted job and a recorded digest of the token file:
    `docker compose down` (no `-v`) then `docker compose up -d` leaves BOTH
    volumes present, the job still retrievable, and the token digest UNCHANGED.

    Protects `docker-compose.yaml:321-325` ("Plain `docker compose down` keeps
    both, and `docker compose up -d` recreates containers without touching
    either") and `docker/README.md:204-209`.

    **The token digest is the load-bearing assertion.** `shc-state` holds the JWT
    signing key, so losing it does not merely log users out - it makes every
    token this cluster has ever issued stop verifying. The digest is compared
    rather than the bytes, for the reason `backend/tests/conftest.py:94-107`
    gives: the value can end up in a failure message on a developer's console,
    and it is exactly the thing that must not be printed.

    The job is what proves `shc-data` survived, since a volume that still exists
    can still be empty.
    """
    stack = live_stack
    jwt = _mint_jwt(stack)
    job_id = submit_job(stack, jwt)
    digest_before = stack.token_digest()

    # `down` without `-v`. The destructive helper is NOT used: this verb must
    # never delete anything, which is the whole point of the scenario.
    stack.down()
    for name in stack.volume_names:
        assert volume_exists(name), (
            f"`docker compose down` removed {name}. Plain `down` keeps both "
            "volumes (docker-compose.yaml:321-325); losing shc-data loses the "
            "cluster and losing shc-state invalidates every JWT."
        )

    stack.up()
    stack.wait_healthy(HEALTH_TIMEOUT_S)

    with ClusterClient(stack.port) as instance:
        response = instance.get(
            f"/api/v1/jobs/{job_id}", headers={"Authorization": f"Bearer {jwt}"}
        )
    assert response.status_code == 200, (
        f"job {job_id} is gone after down+up ({response.status_code}). The "
        "database lives on shc-data (/app/data) and plain `down` keeps it."
    )
    assert response.json()["job_id"] == job_id

    digest_after = stack.token_digest()
    assert digest_after == digest_before, (
        "the token file at /app/.shc/shared_token changed across a plain "
        "`down` + `up`. That is the shc-state volume being recreated: the "
        "signing key lives there too, so every JWT this cluster has ever issued "
        "would stop verifying (docker/Dockerfile:120-122). Digests, not values."
    )


# ---------------------------------------------------------------------------
# M-5.2  `down -v` deletes both volumes (AC-21)
# ---------------------------------------------------------------------------


def test_down_with_volumes_deletes_the_throwaway_volumes(
    live_stack: ComposeStack,
) -> None:
    """
    M-5.2 / AC-21 - `docker compose down -v` makes `docker volume inspect` FAIL
    for both renamed volumes. **Override stack only.**

    Protects `docker-compose.yaml:321-325` ("`docker compose down -v` DELETES
    BOTH. Losing `shc-data` loses the cluster. Losing `shc-state` invalidates
    every JWT this cluster has ever issued") and `:313-319` (why not `external:
    true`).

    **Why the rename is the whole point.** Run against the checked-in file, this
    exact command deletes a developer's live cluster - the volumes are pinned
    with `name:` at `docker-compose.yaml:327-330`, and MEASURED,
    `COMPOSE_PROJECT_NAME` does not namespace them. So the stack runs behind a
    generated override that renames both, `run_destructive_compose` REQUIRES that
    override path as an argument, and this scenario needs a second opt-in
    (`SHC_COMPOSE_ALLOW_DESTRUCTIVE=1`) on top of the first. Running this against
    the checked-in file is not discouraged; it is unreachable.

    The containment is verified from the other side too: `shc-data` and
    `shc-state` are asserted to still EXIST after the teardown, so a passing test
    is evidence the interlock worked rather than evidence it did not.
    """
    stack = live_stack
    for name in stack.volume_names:
        assert volume_exists(name), (
            f"{name} does not exist before `down -v`; the scenario is testing "
            "nothing"
        )

    # Skips unless SHC_COMPOSE_ALLOW_DESTRUCTIVE=1 - inside the helper, so no
    # caller can forget it.
    stack.destructive("down", "-v")

    for name in stack.volume_names:
        assert not volume_exists(name), (
            f"{name} still exists after `down -v`. docker-compose.yaml:321-325 "
            "documents that this deletes both volumes; if a future compose "
            "version stopped doing so, this assertion is where it shows up."
        )

    # The other side of containment: the developer's own volumes are untouched.
    for name in ("shc-data", "shc-state"):
        assert volume_exists(name), (
            f"{name} is GONE. The suite ran `down -v` against its own override "
            "stack, and this volume is not the one it owns - the interlock "
            "failed."
        )


# ---------------------------------------------------------------------------
# M-5.3  An unexpected death is recovered from (AC-22)
#
# MEASURED DEVIATION FROM THE SPEC, and the reason this scenario is two tests.
#
# The spec's mechanism is `docker kill` on the compose container. On Docker
# Desktop 29.8.2 / Compose v5.6.0 that does NOT restart it. Measured twice, with
# `unless-stopped` and with `--restart=always` forced:
#
#     $ docker kill <cid>        -> status=exited  ExitCode=137  RestartCount=0
#     $ docker compose kill -s KILL backend -> status=exited  RestartCount=0
#     $ docker update --restart=always <cid>; docker kill <cid>
#                                  -> status=exited  RestartCount=0
#
# while a process that exits BY ITSELF under the same declared policy does
# recover (`docker run --restart unless-stopped shc:latest sh -c 'sleep 12;
# exit 3'` -> `RestartCount=1`). Docker treats a `kill` issued through the API
# as a MANUAL stop and suppresses the restart policy for it, by design. So the
# spec's literal mechanism cannot observe the property `docker-compose.yaml:276-282`
# claims, and no amount of retrying or timeout changes that.
#
# What is asserted here instead, honestly and without weakening the claim:
#   1. the policy the file DECLARES is `unless-stopped`;
#   2. that policy does recover the container from an UNEXPECTED process death -
#      run outside compose, because the compose container cannot be made to die
#      unexpectedly from a test (its PID 1 ignores signals from inside its own
#      namespace, and a daemon-level kill is a manual stop);
#   3. the `docker kill` behaviour itself, as the platform fact it is, with a
#      non-vacuity control - so the reason the mechanism above is unusable is a
#      recorded, re-checkable assertion rather than a paragraph in a docstring.
#
# **Honest scoping - read this before quoting either test.** Neither proves the
# `unless-stopped`-not-`always` distinction. Those differ only after a deliberate
# `docker compose stop` or a daemon restart, neither of which a test can reach.
# That claim is protected ONLY by MUST M-1.9's `restart == "unless-stopped"`
# assertion. Neither test here may be presented as testing it.
# ---------------------------------------------------------------------------


def test_declared_restart_policy_recovers_an_unexpected_death(
    live_stack: ComposeStack,
) -> None:
    """
    M-5.3 / AC-22 (the property, at the mechanism that can actually observe it) -
    a container running the real image under the restart policy
    `docker-compose.yaml` DECLARES reaches `(healthy)`, suffers an unexpected
    process death, and then reaches `(healthy)` AGAIN with `.RestartCount >= 1`.

    Protects `docker-compose.yaml:276-282`: "Survives a host reboot, which is the
    case that matters for a home cluster that has to come back by itself after
    power is restored." A power cut is, from Docker's point of view, exactly this
    case - the process stops, nothing asked it to.

    **Both halves of AC-22 are asserted, not one.** The earlier version of this
    test used a container running `sh -c 'sleep 12; exit 3'`, which proved the
    restart policy fires and nothing else: that container never became healthy,
    so `.RestartCount >= 1` was delivered but "reaches healthy again" was not.
    Now the rogue container runs the image's own application, so the full
    operator-visible property - was serving, died, is serving again - is the thing
    under test. Measured on Docker Desktop 29.8.2: healthy at ~10 s, killed at
    25 s, healthy again at ~35 s with `RestartCount == 1`.

    **Control assertions, both real:**
      * healthy BEFORE the death, so "healthy again" cannot be satisfied by a
        container that was never serving;
      * the recovery wait requires `RestartCount >= 1` **and** `healthy` together,
        so the assertion cannot be satisfied by the FIRST healthy reading.

    The policy value is READ from the stack's resolved config, not hardcoded, so
    this test tracks the file rather than restating it.

    **Why the death has to come from inside.** PID 1 of a container ignores
    signals sent from within its own PID namespace, so `kill -9 1` from `exec`
    is a no-op - measured. The app therefore runs as a BACKGROUND job under `sh`
    (which is PID 1) and is killed from there: an unexpected death of the
    application, with PID 1 exiting non-zero behind it. That is not a manual stop
    - the daemon was never asked to stop anything, which is precisely the
    distinction `docker kill` fails (see the sibling test).
    """
    declared = live_stack.resolved_config()["services"]["backend"]["restart"]
    assert declared == "unless-stopped", (
        f"the stack declares restart={declared!r}; this test exercises the "
        "policy the file names (docker-compose.yaml:276-282)"
    )

    # One-off, outside compose, so a restart loop cannot disturb the shared stack
    # (D3). PID 1 is `sh`; the app is a background job it kills after 25 s and
    # then exits 137 - an unexpected failure, not a stop.
    name = f"{live_stack.project_name}-rogue"
    run_compose(
        [
            "docker",
            "run",
            "-d",
            "--restart",
            declared,
            "--name",
            name,
            IMAGE_TAG,
            "sh",
            "-c",
            "uvicorn backend.main:app --host 0.0.0.0 --port 8000 & "
            "sleep 25; kill -9 $!; exit 137",
        ]
    )
    try:
        # CONTROL 1: it really was serving, so "healthy again" means something.
        healthy_before = _wait_for(name, lambda s, r, h: h == "healthy", 90)
        assert healthy_before, (
            f"{name} never became healthy before the injected death, so a "
            "subsequent `healthy` would prove nothing. The image's own "
            "HEALTHCHECK is what decides this (docker/Dockerfile:136-137)."
        )
        # CONTROL 1b: and that first healthy reading has RestartCount 0, so the
        # recovery predicate below (>= 1) provably cannot be satisfied by it.
        # Without this the "again" in "healthy again" would be unchecked.
        _status, restarts_at_first_healthy, _health = _probe(name)
        assert restarts_at_first_healthy == 0, (
            f"the container reported healthy at RestartCount="
            f"{restarts_at_first_healthy}, so the recovery wait below could be "
            "satisfied by this same reading and would prove nothing."
        )

        # The property: restarted AND healthy again. Both conditions at once, so
        # the first healthy reading (RestartCount 0) cannot satisfy it.
        deadline = time.monotonic() + 120
        recovered = False
        restart_count = 0
        while time.monotonic() < deadline:
            status, restart_count, health = _probe(name)
            if restart_count >= 1 and health == "healthy":
                recovered = True
                break
            time.sleep(2.0)

        assert recovered, (
            f"a container running the real image was healthy, then its "
            f"application was killed unexpectedly, and it did not come back "
            f"serving under `--restart {declared}` (RestartCount="
            f"{restart_count}, last status={status!r}). That is exactly the "
            "case docker-compose.yaml:276-282 claims to cover - a host reboot, "
            "a power cut, an OOM kill - and `on-failure` is refused precisely "
            'because "came back wrong" is better as a visible crash loop.'
        )
        assert restart_count >= 1, (
            "the container reported healthy without a restart, so it never came "
            "back from the death at all."
        )
    finally:
        # `-v` and not just `-f`: `docker rm` leaves the container's ANONYMOUS
        # volumes behind, and this image declares two (`docker/Dockerfile:122`),
        # so omitting it leaks two 64-hex-named volumes per run - a suite whose
        # whole subject is containment should not trail orphans around.
        run_compose(["docker", "rm", "-f", "-v", name], check=False)


def _wait_for(name: str, predicate, timeout: float, interval: float = 2.0) -> bool:
    """Poll `_probe(name)` until `predicate` holds or the timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(*_probe(name)):
            return True
        time.sleep(interval)
    return False


def test_docker_kill_is_treated_as_a_manual_stop(live_stack: ComposeStack) -> None:
    """
    The `docker kill` half of M-5.3, asserted as the PLATFORM FACT it is.

    Protects nothing in `docker-compose.yaml` directly, and says so: this is here
    because the spec's M-5.3 mechanism is `docker kill`, and a reader who tries it
    by hand will watch the container stay dead for the better part of five
    minutes and reasonably conclude the compose file is broken. It is not. Docker
    treats an API-issued kill as a manual stop and suppresses the restart policy
    for it - measured with `unless-stopped` and with `always` forced - while an
    unexpected exit is recovered (the sibling test above).

    **Control assertion first:** `RestartCount == 0` before the kill. Without it
    "still 0 after the kill" would be vacuous - it is what a correct restart
    policy would ALSO show immediately after a kill, before it had acted.

    **Both `RestartCount` reads go through `ComposeStack.restart_count()`**, which
    templates `{{.RestartCount}}` in its own right. This test originally read
    `state.get("RestartCount", 0)` out of `{{json .State}}`, where the field does
    not exist - so BOTH assertions evaluated `0 == 0` and passed against any
    container whatsoever, including one that had restarted seven times. A control
    that cannot fail is the exact thing issue #134 exists to prevent.
    """
    stack = live_stack
    cid = stack.container_id()
    restarts_before = stack.restart_count(cid)
    assert restarts_before == 0, (
        f"RestartCount is already {restarts_before} before the kill, so "
        "'still zero afterwards' would prove nothing."
    )

    killed = run_compose(["docker", "kill", cid])
    assert killed.returncode == 0, killed.describe()

    # Give the policy a fair window to act before concluding it did not.
    deadline = time.monotonic() + 30
    state = stack.inspect("{{json .State}}", cid)
    while time.monotonic() < deadline and state.get("Status") == "running":
        time.sleep(2.0)
        state = stack.inspect("{{json .State}}", cid)

    assert state.get("Status") == "exited", (
        f"after `docker kill` the container is {state.get('Status')!r}, not "
        "'exited'. Docker suppressed the restart policy for this stop, so the "
        "sibling test's mechanism is the one that observes the policy. Re-measure "
        "before concluding the compose file changed."
    )
    assert stack.restart_count(cid) == 0, (
        f"RestartCount is {stack.restart_count(cid)} after `docker kill`. If a "
        "future Docker honours the restart policy for an API-issued kill, this "
        "assertion goes red - which is the interesting direction, and the reason "
        "it is an assertion rather than a comment."
    )


# ---------------------------------------------------------------------------
# M-5.4  A JWT survives `compose restart` (AC-23)
# ---------------------------------------------------------------------------


def test_a_jwt_survives_compose_restart(live_stack: ComposeStack) -> None:
    """
    M-5.4 / AC-23 - a JWT minted BEFORE `docker compose restart backend` still
    verifies afterwards: 200 `{"valid": true}` with
    `Host: cluster.example.com` + `Authorization: Bearer <jwt>`.

    Protects `docker/Dockerfile:120-122` ("the signing key means every restart
    otherwise logs every user out") and `:122` (`VOLUME ["/app/data",
    "/app/.shc"]`), plus `docker-compose.yaml:157-159` and `:232`
    (`shc-state:/app/.shc`).

    **The `Host` override is what makes this non-vacuous, and the 401 control is
    mandatory.** Without the override, the localhost bypass at
    `backend/core/deps.py:26-31` would let the request through whether or not
    the JWT verified - the exact trap named in
    `backend/tests/integration/test_signing_key_rotation.py:12-19`. Without the
    control, a change that silently stopped verifying tokens would still return
    200 and this test would go green. Both halves are asserted here.
    """
    stack = live_stack
    jwt = _mint_jwt(stack)

    stack.restart("backend")
    stack.wait_healthy(HEALTH_TIMEOUT_S)

    with ClusterClient(stack.port) as instance:
        verified = instance.get(
            "/api/v1/auth/verify",
            headers={
                "Host": NON_LOCALHOST_HOST_HEADER,
                "Authorization": f"Bearer {jwt}",
            },
        )
        rejected = instance.get(
            "/api/v1/auth/verify",
            headers={"Host": NON_LOCALHOST_HOST_HEADER},
        )

    assert verified.status_code == 200, verified.text
    assert verified.json() == {"valid": True}, (
        f"{verified.text} - a token minted before `compose restart` stopped "
        "verifying. The signing key is on the shc-state volume "
        "(docker-compose.yaml:232); losing it logs every user out."
    )
    assert rejected.status_code == 401, (
        f"control: the same request with Host: {NON_LOCALHOST_HOST_HEADER} and no "
        f"Authorization answered {rejected.status_code}, not 401. Without this "
        "the `valid: true` above would be satisfied by the localhost bypass "
        "rather than by the signature check."
    )


# ---------------------------------------------------------------------------
# M-5.5  `DB_POOL=pooled` crashes the container (AC-24) - current behaviour
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "issue #130: Settings.DB_POOL's default is the literal string `pooled` "
        "(backend/core/config.py:413), and backend/core/database.py:108 maps "
        "anything other than 'null' to a bare QueuePool, which SQLAlchemy 2.0 "
        "rejects on an async engine. The container therefore exits non-zero with "
        "`Pool class QueuePool cannot be used with asyncio engine`. THIS TEST "
        "ASSERTS THE FIXED BEHAVIOUR - the container starts - so it fails today "
        "(XFAIL) and turns RED the moment `pooled` starts working "
        "(XPASS(strict)), forcing a deliberate update as part of the #130 fix "
        "rather than allowing a silent behaviour change. strict=True is the "
        "point: a non-strict xfail would be a test that can never fail, which is "
        "what this whole issue exists to prevent. The scenario-table wording "
        "('exits non-zero ... xfail(strict=True)') reads as if the crash itself "
        "should be asserted under a strict xfail, which pytest reports as a "
        "FAILURE today (XPASS(strict)) and would leave the suite red; the "
        "assertion and the marker have to disagree about the polarity for both "
        "AC-24's 'turns red the moment pooled starts working' and the "
        "Definition of Done's 'green' to hold at once."
    ),
)
def test_db_pool_pooled_does_not_start(image_id: str) -> None:
    """
    M-5.5 / AC-24 - a one-off `docker run --rm -e DB_POOL=pooled shc:latest`
    EXITS ZERO and does not emit `Pool class QueuePool cannot be used with
    asyncio engine`.

    Protects `docker-compose.yaml:183-212`, `backend/core/database.py:108` and
    issue #130. This is the trap in issue #134's own failure-mode list: the very
    first version of the compose file set `DB_POOL: pooled`, the container never
    reached `/health`, and `restart: unless-stopped` turned that into a crash
    loop. Nothing stopped it being reintroduced.

    Today it exits 137-style non-zero with the SQLAlchemy `ArgumentError`, so
    the first assertion fails and the test reports XFAIL with the defect's
    diagnostic in the message. That keeps the defect **observably present** -
    US-5's requirement - without making the suite red. When #130 is fixed the
    test XPASSes and `strict=True` makes that red.

    **It runs OUTSIDE compose, deliberately** (D3): a bare `docker run` with no
    port, no mounts and no restart policy. A crash loop against the shared stack
    would take out every scenario queued behind it - and today that container
    really does crash.

    It still carries the `compose` marker, because AC-25 requires EVERY test
    under `backend/tests/compose/` to be SKIPPED without `SHC_COMPOSE_TESTS=1`
    and this one needs the Docker daemon and the image just as much as the rest.
    """
    run = run_compose(
        ["docker", "run", "--rm", "-e", "DB_POOL=pooled", "shc:latest"],
        check=False,
        timeout=60,
    )
    crashed = "Pool class QueuePool cannot be used with asyncio engine" in run.output
    assert run.returncode == 0, (
        f"`docker run -e DB_POOL=pooled` exited {run.returncode} instead of 0. "
        + (
            "It died with the documented `Pool class QueuePool cannot be used "
            "with asyncio engine` (backend/core/database.py:108), which is issue "
            "#130: `pooled` is Settings.DB_POOL's own default "
            "(backend/core/config.py:413) and is not implemented for an async "
            "engine. This is the defect this test exists to make observable."
            if crashed
            else "It died for an UNDOCUMENTED reason, so this test no longer "
            "pins the defect it was written to pin (docker-compose.yaml:183-212)."
        )
        + f"\n$ docker run --rm -e DB_POOL=pooled shc:latest\n{run.describe()}"
    )
    assert not crashed, (
        "the container exited zero but the logs still carry the SQLAlchemy "
        "ArgumentError - a partial fix. `pooled` must stop crashing completely, "
        "not merely stop preventing startup."
    )


# ---------------------------------------------------------------------------
# SHOULD S-6  A container replace preserves state
# ---------------------------------------------------------------------------


def test_force_recreate_preserves_state(live_stack: ComposeStack) -> None:
    """
    SHOULD S-6 - `up -d --wait --force-recreate` leaves the submitted job
    retrievable and the token digest unchanged.

    `docker compose up -d --force-recreate` is the documented operator action
    for picking up a new image, so it is the one an operator runs most often and
    the one whose blast radius (`/app/data` and `/app/.shc` are anonymous volumes
    unless something is mounted there) is least obvious. Cheap to add once
    MUST M-5.1's stack is already up.
    """
    stack = live_stack
    jwt = _mint_jwt(stack)
    job_id = submit_job(stack, jwt)
    digest_before = stack.token_digest()

    stack.up("--force-recreate")
    stack.wait_healthy(HEALTH_TIMEOUT_S)

    with ClusterClient(stack.port) as instance:
        response = instance.get(
            f"/api/v1/jobs/{job_id}", headers={"Authorization": f"Bearer {jwt}"}
        )
    assert response.status_code == 200, (
        f"job {job_id} did not survive --force-recreate ({response.status_code}). "
        "Both state volumes must be NAMED and mounted, or a replace rebuilds "
        "them (docker-compose.yaml:231-232)."
    )
    assert stack.token_digest() == digest_before, (
        "the shared token changed across --force-recreate, so shc-state was "
        "rebuilt. Every JWT this cluster has ever issued would stop verifying "
        "(digests, not values)."
    )


# ---------------------------------------------------------------------------
# Negative controls for the controls (defects 2 and 5)
#
# Each of these exists because a control that CANNOT FAIL is worse than no
# control, and because three such controls were found in review having been
# shipped as if they worked.
# ---------------------------------------------------------------------------


def test_restart_count_is_not_readable_from_state(image_id: str) -> None:
    """
    DEFECT 2 REGRESSION TEST: proves the repaired `RestartCount` controls can
    fail, and that the shape they replaced could not.

    Three control assertions in this suite read
    `state.get("RestartCount", 0)` out of `docker inspect --format
    '{{json .State}}'`. `RestartCount` is a SIBLING of `State`, not a member, so
    that expression is `0` forever - each of those controls evaluated `0 == 0` and
    passed against any container whatsoever, including one that had restarted
    seven times.

    The proof is differential and needs a container that HAS restarted, so this
    runs one: `--restart always` with a process that outlives the ten-second
    restart-policy window and then exits. Once `RestartCount >= 1` the two reads
    must DISAGREE - the real template reports the count, the `.State` template
    reports 0 - which is exactly the gap that made the controls vacuous.

    If this test ever passes with `RestartCount == 0`, it has proved nothing.
    """
    name = f"{uuid.uuid4().hex[:10]}-restartproof"
    try:
        run_compose(
            [
                "docker",
                "run",
                "-d",
                "--restart",
                "always",
                "--name",
                name,
                IMAGE_TAG,
                "sh",
                "-c",
                "sleep 12; exit 3",
            ]
        )
        deadline = time.monotonic() + 90
        real = 0
        while time.monotonic() < deadline:
            real = _restart_count(name)
            if real >= 1:
                break
            time.sleep(2.0)

        assert real >= 1, (
            f"{name} never reached RestartCount >= 1 (last read {real}), so this "
            "test has not reproduced the condition the vacuous controls were "
            "evaluated under and proves nothing."
        )

        # The two templates disagree, on a container that has actually
        # restarted. This single assertion is what makes the repaired controls
        # trustworthy: `{{json .State}}` yields 0 where the truth is `real`.
        from_state = json.loads(
            run_compose(
                ["docker", "inspect", name, "--format", "{{json .State}}"],
                check=False,
            ).stdout
        )
        assert "RestartCount" not in from_state, (
            "`.State` now carries a RestartCount key; if Docker ever moves the "
            "field, the repaired controls and this test should both be revisited."
        )
        assert (
            int(from_state.get("RestartCount", 0)) == 0 != real
        ), f"expected the `.State` read to report 0 while the real count is {real}"
        assert (
            _restart_count(name) == real >= 1
        ), "the repaired read disagrees with itself"
    finally:
        run_compose(["docker", "rm", "-f", "-v", name], check=False)


def test_destructive_invocations_audit_catches_a_planted_rogue() -> None:
    """
    DEFECT 5 REGRESSION TEST: proves the session-wide audit SEES a test body's
    invocation, and that its rule is neither blind nor trigger-happy.

    Two defects lived here. First, pytest imports this conftest twice (`conftest`
    and `backend.tests.compose.conftest`) and the module-level registry was
    therefore two lists: fixtures recorded into one, test bodies read the other,
    and the teardown audit saw nothing - blind to exactly where a rogue `down -v`
    would be written. The registry now lives in `sys.modules`, shared by both.

    Second, the rule matched any argv containing `rm` and `-v`, which also matched
    the suite's own legitimate `docker rm -f -v <rogue-container>` cleanup.

    So this plants each shape into the SHARED registry - read back through the
    dotted import path a test body would use, which is the identity that was
    previously disconnected - and asserts the audit's verdict on each:

      * a bare `docker compose down -v`          -> FLAGGED (the blast radius)
      * the suite's own `docker rm -f -v <name>`  -> NOT flagged (anonymous
        volumes of a named container; it cannot reach a pinned project volume)
      * `down` without `-v`                      -> NOT flagged (keeps the data)
      * `down -v` WITH both `-f` flags           -> NOT flagged (that is M-5.2)

    The planted argv is removed afterwards, so the real session teardown audit
    still sees a clean registry.
    """
    rogue = ["docker", "compose", "down", "-v"]
    legitimate_cleanup = ["docker", "rm", "-f", "-v", "some-rogue-container"]
    keeps_volumes = ["docker", "compose", "down"]
    sanctioned = ["docker", "compose", "-f", str(COMPOSE_FILE), "-f", "x", "down", "-v"]

    imported = COMPOSE_INVOCATIONS
    assert sys.modules["conftest"].COMPOSE_INVOCATIONS is imported, (
        "the conftest module and the dotted import are STILL two different "
        "registry objects. The audit would be blind again - this assertion is "
        "the regression guard for the double-import itself."
    )

    baseline = list(imported)
    try:
        imported[:] = baseline + [rogue]
        assert audit_destructive_invocations(), (
            "the audit did not flag a bare `docker compose down -v`. It is blind "
            "to the exact invocation it exists to catch."
        )

        imported[:] = baseline + [legitimate_cleanup]
        assert audit_destructive_invocations() == [], (
            "the audit flagged the suite's own `docker rm -f -v <name>` cleanup. "
            "A false positive that fires at teardown would make the suite "
            "un-runnable."
        )

        imported[:] = baseline + [keeps_volumes]
        assert audit_destructive_invocations() == [], (
            "`docker compose down` without -v keeps the volumes and is not the "
            "blast radius being guarded against."
        )

        imported[:] = baseline + [sanctioned]
        assert audit_destructive_invocations() == [], (
            "a `down -v` carrying both -f flags is MUST M-5.2, the one sanctioned "
            "destructive verb in the suite."
        )
    finally:
        imported[:] = baseline
