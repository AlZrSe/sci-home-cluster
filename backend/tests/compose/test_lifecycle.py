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

import time

import pytest

from backend.tests.compose.conftest import (
    COMPOSE_PYTESTMARK,
    HEALTH_TIMEOUT_S,
    IMAGE_TAG,
    NON_LOCALHOST_HOST_HEADER,
    ClusterClient,
    ComposeStack,
    _mint_jwt,
    run_compose,
    submit_job,
    volume_exists,
)

pytestmark = COMPOSE_PYTESTMARK


def _restart_count(name: str) -> int:
    """
    `.RestartCount` for a container addressed by NAME.

    NOT read out of `.State`: `RestartCount` is a SIBLING of `State` in
    `docker inspect` output, not a member of it, so a `{{json .State}}` template
    silently yields 0 forever. That mistake cost a 120 s debugging detour during
    development and would have made this test fail while pointing at the restart
    policy rather than at the template.
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
    M-5.3 (the property, at the mechanism that can actually observe it) - the
    restart policy `docker-compose.yaml` DECLARES recovers a container whose
    process dies unexpectedly.

    Protects `docker-compose.yaml:276-282`: "Survives a host reboot, which is the
    case that matters for a home cluster that has to come back by itself after
    power is restored." A power cut is, from Docker's point of view, exactly this
    case - the process stops, nothing asked it to.

    The policy value is READ from the stack's resolved config, not hardcoded, so
    this test tracks the file rather than restating it.
    """
    declared = live_stack.resolved_config()["services"]["backend"]["restart"]
    assert declared == "unless-stopped", (
        f"the stack declares restart={declared!r}; this test exercises the "
        "policy the file names (docker-compose.yaml:276-282)"
    )

    # One-off, outside compose, so a restart loop cannot disturb the shared
    # stack (D3). Its PID 1 exits on its own - nothing asks it to stop - which is
    # the condition the restart policy exists for.
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
            "sleep 12; exit 3",
        ]
    )
    try:
        # Restart policies only engage after a container has been up for ten
        # seconds, so the process has to outlive that before it exits.
        deadline = time.monotonic() + 120
        restart_count = 0
        while time.monotonic() < deadline:
            restart_count = _restart_count(name)
            if restart_count >= 1:
                break
            time.sleep(2.0)

        assert restart_count >= 1, (
            f"a container whose process exited on its own under "
            f"`--restart {declared}` was never restarted (RestartCount="
            f"{restart_count}). That is exactly the case "
            "docker-compose.yaml:276-282 claims to cover - a host reboot, a "
            "power cut, an OOM kill - and `on-failure` is refused precisely "
            'because "came back wrong" is better as a visible crash loop.'
        )
    finally:
        # `-v` and not just `-f`: `docker rm` leaves the container's ANONYMOUS
        # volumes behind, and this image declares two (`docker/Dockerfile:122`),
        # so omitting it leaks two 64-hex-named volumes per run - a suite whose
        # whole subject is containment should not trail orphans around.
        run_compose(["docker", "rm", "-f", "-v", name], check=False)


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
    """
    stack = live_stack
    cid = stack.container_id()
    before = stack.inspect("{{json .State}}", cid)
    assert (
        int(before.get("RestartCount", 0)) == 0
    ), f"RestartCount is already {before['RestartCount']} before the kill"

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
    assert int(state.get("RestartCount", 0)) == 0, (
        f"RestartCount is {state['RestartCount']} after `docker kill`. If a future "
        "Docker honours the restart policy for an API-issued kill, this "
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
