"""
MUST M-1.1 .. M-1.10 - the checked-in file's own decisions, pinned by
`docker compose config` (layer L1, no daemon state touched).

`docker compose config` is a pure parse-and-interpolate: it creates and removes
no container, network or volume, so every scenario here is safe against a
developer who has a cluster running. That is the best return-on-risk ratio in
the whole issue: ten load-bearing decisions pinned at zero cost and zero danger.

Each of those decisions reads like an oversight from the resolved document alone.
Only the comments in `docker-compose.yaml` say otherwise, and a comment does not
fail a build - which is the defect this issue exists to end. So each test below
names the lines whose reasoning it is protecting.

Run just this layer with:

    SHC_COMPOSE_TESTS=1 pytest backend/tests/compose -m compose -k config

It still needs `SHC_COMPOSE_TESTS=1`, because AC-25 requires every test under
`backend/tests/compose/` to be SKIPPED without it. See the note in
`conftest.py::COMPOSE_PYTESTMARK`.
"""

import json
import re
from pathlib import Path
from typing import Optional

import pytest
import yaml

from backend.tests.compose.conftest import (
    COMPOSE_FILE,
    COMPOSE_PYTESTMARK,
    DOCKERFILE,
    DESTRUCTIVE_ENV,
    EXECUTED_ARGVS,
    REPO_ROOT,
    ComposeRun,
    InterlockViolation,
    assert_override_engaged,
    assert_override_renames_volumes,
    checked_in_run,
    compose_argv,
    destructive_allowed,
    run_destructive_compose,
)

pytestmark = COMPOSE_PYTESTMARK

#: `docker-compose.yaml:119-120` and the comment above it.
SERVICE = "backend"


def _is_absolute(value: str) -> bool:
    """Absolute for pathlib, including a Windows drive-letter spelling."""
    return Path(value).is_absolute() or bool(re.match(r"^[A-Za-z]:[\\/]", value))


def _norm(value: str) -> str:
    """A comparison spelling that survives Windows' `\\` versus `/`."""
    return Path(value).as_posix().rstrip("/").lower()


@pytest.fixture(scope="module")
def raw_compose() -> dict:
    """The checked-in file as written, not as resolved.

    Some assertions are about what is ABSENT from the source (`version:`, an
    interpolated port) and interpolation would hide both: a resolved document
    has already substituted every `${...}`.
    """
    return yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def raw_ports_line() -> str:
    """The single raw line under the top-level `ports:` key."""
    lines = COMPOSE_FILE.read_text(encoding="utf-8").splitlines()
    index = next(i for i, line in enumerate(lines) if line.rstrip() == "    ports:")
    return lines[index + 1]


@pytest.fixture(scope="module")
def dockerfile_args() -> dict[str, str]:
    """Every top-level `ARG NAME[=default]` in docker/Dockerfile.

    Anchored at the start of a line so a mention inside a comment - and this file
    has many - is not mistaken for a declaration.
    """
    pattern = re.compile(r"^ARG\s+([A-Za-z_][A-Za-z0-9_]*)(?:=(.*))?$")
    args: dict[str, str] = {}
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            args[match.group(1)] = match.group(2) or ""
    return args


# ---------------------------------------------------------------------------
# M-1.1  A fresh clone needs no .env (AC-1)
# ---------------------------------------------------------------------------


def test_config_resolves_with_no_dotenv(compose_run: ComposeRun) -> None:
    """
    M-1.1 / AC-1 - `docker compose config --format json` exits 0, writes nothing
    to stderr, and yields exactly one service.

    Protects `docker-compose.yaml:14-26` ("NO .env IS NEEDED"), `:144-151` (why
    not `env_file:` - "a fresh clone has no .env, so `env_file` would fail a
    purely static `docker compose config` with a filesystem error"), and `:173`
    (`${SHARED_TOKEN:-}` rather than `:?`).

    The stderr assertion is load-bearing on its own: compose warns about a
    deprecated field or an unset variable on stderr and still exits 0, so the
    exit code alone would let the warnings this file is written to avoid pass.
    """
    assert compose_run.returncode == 0, compose_run.describe()
    assert compose_run.stderr == "", (
        "compose wrote to stderr for a checkout with no .env:\n"
        f"{compose_run.stderr}\n"
        "A fresh clone has to configure nothing (docker-compose.yaml:14-26), so "
        "this resolution must be silent."
    )
    assert list(json.loads(compose_run.stdout)["services"]) == [
        SERVICE
    ], "expected exactly the one service `backend`"


# ---------------------------------------------------------------------------
# M-1.2  One service, and it is `backend` (AC-1)
# ---------------------------------------------------------------------------


def test_only_one_service_and_it_is_backend() -> None:
    """
    M-1.2 - `docker compose config --services` lists exactly `["backend"]`.

    Protects `docker-compose.yaml:36-70` and its five reasons. Co-location is
    false in every real GPU cluster, and the trap is real: `run-agent` IS on
    PATH inside the image (`docker/Dockerfile:103`, `pyproject.toml:177-182`),
    so `docker compose exec backend run-agent --node-id node-01` would genuinely
    work - and would run the one compute node inside the API container, with its
    privileges, executing arbitrary JobSpec.command payloads. The assertion is on
    the SERVICE SET, not on a comment, so that cannot be "helpfully" added later.
    """
    run = checked_in_run("config", "--services")
    assert run.returncode == 0, run.describe()
    assert run.stdout.split() == [SERVICE], run.describe()


def test_the_agent_is_never_a_service_or_an_entrypoint(raw_compose: dict) -> None:
    """
    Extended out-of-scope check, on the PARSED document rather than the text.

    `docker-compose.yaml:56-62` names `run-agent` in its own prose - reason 5 is
    entirely about that string - so a substring search over the raw file would
    match the warning it is meant to enforce. What matters is whether the string
    reaches a service *definition*: a service named for the agent, or a
    command/entrypoint that would launch one.
    """
    assert "agent" not in raw_compose["services"], (
        f"services are {list(raw_compose['services'])}; the worker agent is "
        "deliberately not one of them (docker-compose.yaml:36-70)"
    )
    for name, service in raw_compose["services"].items():
        for key in ("command", "entrypoint", "container_name"):
            value = service.get(key)
            rendered = " ".join(value) if isinstance(value, list) else str(value)
            assert "run-agent" not in rendered, (
                f"service {name!r} has {key}={rendered!r}. `run-agent` is on PATH "
                "in this image (docker/Dockerfile:103), so that line would work - "
                "and it would run the one compute node inside the API container, "
                "executing arbitrary JobSpec.command payloads "
                "(docker-compose.yaml:56-62)."
            )


# ---------------------------------------------------------------------------
# M-1.3  No `version:` key (AC-2)
# ---------------------------------------------------------------------------


def test_resolved_top_level_keys_are_exactly_four(
    compose_config: dict, raw_compose: dict
) -> None:
    """
    M-1.3 / AC-2 - the resolved top-level keys are exactly
    `{name, networks, services, volumes}`, and the raw document carries no
    top-level `version:`.

    Protects `docker-compose.yaml:28-29`: "Compose v2 ignores it and emits no
    warning, so a file carrying one looks authoritative while configuring
    nothing." The resolved half catches a `version:` that compose passes through;
    the raw half catches one the resolver drops. Neither alone is enough.
    """
    assert set(compose_config) == {
        "name",
        "networks",
        "services",
        "volumes",
    }, f"resolved top-level keys are {sorted(compose_config)}"
    assert "version" not in raw_compose, (
        "docker-compose.yaml must not carry a top-level `version:` key; compose "
        "v2 ignores it silently (docker-compose.yaml:28-29)"
    )


# ---------------------------------------------------------------------------
# M-1.4  Build-arg defaults byte-identical to the Dockerfile's ARGs (AC-3)
# ---------------------------------------------------------------------------


def test_build_arg_defaults_are_byte_identical_to_the_dockerfile(
    compose_config: dict, dockerfile_args: dict[str, str]
) -> None:
    """
    M-1.4 / AC-3 - the ARG set is exactly `{VITE_CLUSTER_BACKEND, VITE_API_URL}`,
    each compose default equals the Dockerfile's default, and neither side
    carries a knob the other lacks.

    Protects `docker-compose.yaml:82-87`: "if this file and the Dockerfile
    disagreed, `docker compose build` and `docker build -f docker/Dockerfile -t
    shc:latest .` would produce two different images from the same tree - both
    succeed, and the job-log WebSocket breaks in only one of them."

    Compared as *parsed values*, not as the literal
    `${VITE_API_URL:-http://localhost:8000/api/v1}` spelling, because the compose
    default has to be read through the `${VAR:-default}` interpolation before the
    two sides are comparable at all. `!override`-free, single-file resolution, so
    this is the checked-in file's own numbers.
    """
    # Only the VITE_* ARGs are build args shared with compose; the Dockerfile
    # may declare additional internal ARGs (e.g. SYNCTHING_VERSION, TARGETARCH)
    # that are not compose build args and are not compared here.
    dockerfile_build_args = {
        name: default
        for name, default in dockerfile_args.items()
        if name.startswith("VITE_")
    }
    build_args = compose_config["services"][SERVICE]["build"]["args"]
    assert set(dockerfile_build_args) == {"VITE_CLUSTER_BACKEND", "VITE_API_URL"}, (
        f"docker/Dockerfile declares {sorted(dockerfile_build_args)}; the compose "
        "file's build-arg block is documented as covering exactly these two "
        "(docker-compose.yaml:82-87)"
    )
    assert set(build_args) == set(dockerfile_build_args), (
        f"compose build args {sorted(build_args)} and Dockerfile ARGs "
        f"{sorted(dockerfile_args)} differ. A knob on one side only is a knob "
        "that silently does nothing on the other."
    )
    for name, dockerfile_default in dockerfile_build_args.items():
        assert build_args[name] == dockerfile_default, (
            f"{name}: docker-compose.yaml defaults to {build_args[name]!r}, "
            f"docker/Dockerfile to {dockerfile_default!r}. The identity is "
            "load-bearing - the two build paths would produce different images "
            "from one tree (docker-compose.yaml:82-87)."
        )


def test_vite_api_url_default_is_absolute(dockerfile_args: dict[str, str]) -> None:
    """
    M-2.4's precondition, stated where the build arg is read.

    Protects `docker-compose.yaml:95-98` and `docker/Dockerfile:61-64`:
    `frontend/src/services/http.ts` derives the socket URL with
    `base().replace(/^http/, "ws")`, so a relative base produces
    `new WebSocket("/api/v1/...")`, which throws in a browser and kills the job
    log stream. The bundle content itself is asserted in `test_image_content.py`;
    this pins the *argument* so a relative default fails before a slow build.
    """
    default = dockerfile_args["VITE_API_URL"]
    assert default.startswith("http://") or default.startswith("https://"), (
        f"VITE_API_URL defaults to {default!r}; it must be absolute, or the "
        "job-log WebSocket breaks (docker/Dockerfile:61-64)"
    )


# ---------------------------------------------------------------------------
# M-1.5  DB_POOL absent from the service (AC-4)
# ---------------------------------------------------------------------------


def test_db_pool_is_not_a_key_of_the_service(compose_config: dict) -> None:
    """
    M-1.5 / AC-4 - `DB_POOL` is not a key of `services.backend.environment` at
    all.

    Protects `docker-compose.yaml:183-220` (the do-not-override block),
    `docker/README.md:125-154`, and `Settings.DB_POOL` at
    **`backend/core/config.py:413`** - not `:330`, which four files still cite
    and which is stale. The default there is the literal string `pooled`, and
    that value does not boot: `backend/core/database.py:108` maps anything other
    than `"null"` to a bare `QueuePool`, which SQLAlchemy 2.0 rejects on an async
    engine. MUST M-5.5 pins that crash; this pins the absence that prevents it.
    """
    environment = compose_config["services"][SERVICE]["environment"]
    assert "DB_POOL" not in environment, (
        f"DB_POOL is set to {environment.get('DB_POOL')!r} in the compose file. It "
        "is inherited from the image, which bakes DB_POOL=null "
        "(docker/Dockerfile:128). Overriding it is the single easiest way to "
        "break this deployment (docker-compose.yaml:183-220); issue #130 tracks "
        "the underlying defect."
    )


# ---------------------------------------------------------------------------
# M-1.6  The two volume names are pinned (AC-5)
# ---------------------------------------------------------------------------


def test_volume_names_are_pinned_not_project_prefixed(compose_config: dict) -> None:
    """
    M-1.6 / AC-5 - `shc-data.name == "shc-data"`, `shc-state.name == "shc-state"`,
    and neither is `<project>_shc-data`.

    Protects `docker-compose.yaml:302-320` and its three concrete costs: `docker
    volume ls` stops answering "do I still have the old database?"; the
    `docker run -v shc-data:/app/data` example at `docker/README.md:221-226`
    stops sharing state with `docker compose up`, "making the two documented
    paths two different clusters"; and renaming the checkout orphans the data.

    This is ALSO the reason the suite's own override has to rename them: MEASURED,
    `COMPOSE_PROJECT_NAME` does not namespace a pinned `name:` at all, which is
    what makes the blast radius in D4 real.
    """
    volumes = compose_config["volumes"]
    assert volumes["shc-data"]["name"] == "shc-data", volumes
    assert volumes["shc-state"]["name"] == "shc-state", volumes
    project = compose_config["name"]
    for key, pinned in (("shc-data", "shc-data"), ("shc-state", "shc-state")):
        assert volumes[key]["name"] != f"{project}_{pinned}", (
            f"{key} resolved to a project-prefixed name; `name:` pins it "
            "deliberately (docker-compose.yaml:302-320)"
        )


# ---------------------------------------------------------------------------
# M-1.7  Three mounts; the Syncthing one is a long-syntax bind (AC-6)
# ---------------------------------------------------------------------------


def test_three_mounts_with_the_syncthing_bind_last(compose_config: dict) -> None:
    """
    M-1.7, first half / AC-6 - exactly three mounts:
    `volume -> /app/data` from `shc-data`, `volume -> /app/.shc` from
    `shc-state`, and `bind -> /tmp/syncthing` from an ABSOLUTE source equal to
    `<repo-root>/syncthing`.

    Protects `docker-compose.yaml:222-232` ("Three mounts, and the count is the
    point") and `:234-274`. Long syntax rather than the short
    `- ./syncthing:/tmp/syncthing` so `type: bind` is *stated* in the resolved
    config "instead of letting compose infer it from a string", and so a Windows
    source such as `D:\\syncthing-shared` is not ambiguous to a parser. The
    absolute source is asserted because relative paths resolve against the
    project directory (`:263-268`) and a regression there would silently point
    the mount somewhere else on another machine.
    """
    mounts = compose_config["services"][SERVICE]["volumes"]
    assert len(mounts) == 3, f"expected three mounts, got {len(mounts)}: {mounts}"

    assert mounts[0]["type"] == "volume", mounts[0]
    assert mounts[0]["target"] == "/app/data", mounts[0]
    assert mounts[0]["source"] == "shc-data", mounts[0]

    assert mounts[1]["type"] == "volume", mounts[1]
    assert mounts[1]["target"] == "/app/.shc", mounts[1]
    assert mounts[1]["source"] == "shc-state", mounts[1]

    bind = mounts[2]
    assert bind["type"] == "bind", (
        f"the Syncthing mount resolved as {bind['type']!r}. A named volume would "
        "be invisible to every agent on every other node "
        "(docker-compose.yaml:234-245); that failure is issue #104's silent "
        "second cluster."
    )
    assert bind["target"] == "/tmp/syncthing", bind
    source = bind["source"]
    assert _is_absolute(source), f"bind source {source!r} is not absolute"
    assert _norm(source) == _norm(str(REPO_ROOT / "syncthing")), (
        f"bind source {source!r} is not <repo-root>/syncthing; a relative source "
        "resolves against the project directory (docker-compose.yaml:263-268)"
    )


def test_syncthing_root_moves_only_the_bind_source(
    compose_config: dict, tmp_path
) -> None:
    """
    M-1.7, second half / AC-6 - with `SYNCTHING_ROOT` set to a temporary
    directory, ONLY the bind's `source` changes; the container's own
    `SYNCTHING_ROOT` stays the literal `/tmp/syncthing`.

    Protects `docker-compose.yaml:175-181`: "The CONTAINER path, a literal, and
    deliberately not the value the operator typed... Two different directories
    behind one variable name - the part worth saying out loud." The two volumes,
    the port, the build args and the image are compared too, because an override
    that moved more than the bind source would be a *different* deployment.

    The value is injected through `env_extra`, the only injection channel
    (D7.2), so it cannot leak into another scenario's resolution.
    """
    probe = tmp_path / "probe-shared"
    probe.mkdir()

    default_env = compose_config["services"][SERVICE]["environment"]
    default_mounts = compose_config["services"][SERVICE]["volumes"]

    overridden = json.loads(
        checked_in_run(
            "config", "--format", "json", env_extra={"SYNCTHING_ROOT": str(probe)}
        ).stdout
    )
    service = overridden["services"][SERVICE]

    assert service["environment"]["SYNCTHING_ROOT"] == "/tmp/syncthing", (
        "the container's SYNCTHING_ROOT must stay the literal /tmp/syncthing "
        "(docker-compose.yaml:175-181); the host value is consumed as the bind "
        "source only"
    )
    assert service["environment"] == default_env, (
        f"setting SYNCTHING_ROOT changed the service environment to "
        f"{service['environment']}; only the bind source may move"
    )

    mount = next(m for m in service["volumes"] if m.get("target") == "/tmp/syncthing")
    assert _norm(mount["source"]) == _norm(
        str(probe)
    ), f"bind source is {mount['source']!r}, expected {str(probe)!r}"
    assert (
        service["volumes"][:2] == default_mounts[:2]
    ), "setting SYNCTHING_ROOT moved one of the two named volumes"
    assert (
        service["ports"] == compose_config["services"][SERVICE]["ports"]
    ), "setting SYNCTHING_ROOT changed the published port"
    assert service["image"] == compose_config["services"][SERVICE]["image"]


# ---------------------------------------------------------------------------
# M-1.8  The published port is the constant 8000 (AC-8)
# ---------------------------------------------------------------------------


def test_published_port_is_the_constant_8000(
    compose_config: dict, raw_ports_line: str
) -> None:
    """
    M-1.8 / AC-8 - one published-port entry, `published == "8000"`, `target ==
    8000`, protocol `tcp`, and the raw line contains no `${`.

    Protects `docker-compose.yaml:112-120` and `docker/README.md:200-202`: "A
    CONSTANT, not a variable. A `${SHC_PORT:-8000}` would look more flexible and
    would let an operator change the published port without touching
    VITE_API_URL - producing a container that serves perfectly and a bundle that
    points somewhere else. The two cannot be derived from one another: build args
    are interpolated before any container exists."

    **Read against the single-file resolution.** The live stack runs on a
    different port - the suite's own override republishes it (D5) - so this is
    the only assertion in the suite that sees the shipped number, and it is the
    reason the override is not a hole in the coverage.
    """
    ports = compose_config["services"][SERVICE]["ports"]
    assert len(ports) == 1, f"expected exactly one published port, got {ports}"
    entry = ports[0]
    assert entry["published"] == "8000", entry
    assert entry["target"] == 8000, entry
    assert entry["protocol"] == "tcp", entry
    assert "${" not in raw_ports_line, (
        f"the ports entry is written as {raw_ports_line.strip()!r}; it must be a "
        "literal. See docker-compose.yaml:112-118 for why a ${SHC_PORT:-8000} is "
        "refused rather than merely discouraged."
    )


# ---------------------------------------------------------------------------
# M-1.9  Only the documented keys, and the documented restart policy (AC-4/AC-9)
# ---------------------------------------------------------------------------


def test_the_service_carries_only_the_documented_keys(compose_config: dict) -> None:
    """
    M-1.9 - no `healthcheck`, `depends_on`, `container_name`, `user` or
    `version` on the service, and `restart == "unless-stopped"`.

    Protects `docker-compose.yaml:122-134` ("Exactly two keys. That is a
    decision, not an omission") and `:276-300`. Each of those absences reads
    like an oversight to a later editor:

    * no `healthcheck` - the image's own is inherited, and re-stating it would
      duplicate a value the image owns and let the two drift;
    * no `depends_on` - there is one service, so the key is either invalid or a
      self-edge;
    * no `container_name` - compose already namespaces by project, and pinning
      it blocks `up --scale`;
    * no `user` - the Dockerfile has no `USER`, so the container is root and
      `${UID}:${GID}` breaks on Docker Desktop and on Windows. Known,
      documented, not hidden.
    * `unless-stopped` rather than `always` - after a deliberate `docker compose
      stop`, `always` would start the container again at the next daemon start.
      `on-failure` is refused: "came back wrong" is better as a visible crash
      loop than as a policy that hides it.

    The `restart` value is the ONLY thing pinning `unless-stopped` over `always`:
    MUST M-5.3 proves the container comes back after an unexpected death, which
    both policies do. They differ only after a deliberate stop or a daemon
    restart, neither of which a test can reach.
    """
    service = compose_config["services"][SERVICE]
    for key in ("healthcheck", "depends_on", "container_name", "user", "version"):
        assert key not in service, (
            f"`{key}` is set on the service: {service[key]!r}. Its absence is a "
            "documented decision, not an omission (docker-compose.yaml:122-134, "
            ":292-300)"
        )
    assert service["restart"] == "unless-stopped", (
        f"restart policy is {service['restart']!r}; `unless-stopped` rather than "
        "`always` is the whole reason an operator who stopped the cluster to "
        "debug finds it still stopped (docker-compose.yaml:276-282)"
    )


# ---------------------------------------------------------------------------
# M-1.10  SHARED_TOKEN defaults to empty, never to a shipped value (AC-4)
# ---------------------------------------------------------------------------


def test_shared_token_defaults_to_empty_under_a_scrubbed_env(
    compose_config: dict,
) -> None:
    """
    M-1.10 / AC-4 - under a scrubbed environment the service's `SHARED_TOKEN` is
    `""`, and the service environment is a subset of `{SHARED_TOKEN,
    SYNCTHING_ROOT}`.

    Protects `docker-compose.yaml:142`, `:156-165` and `:173`: "`${VAR:-fallback}`
    would mean shipping a default token. No." The subset assertion is what makes
    AC-4 whole - it also keeps the five image-owned values
    (SERVE_FRONTEND, FRONTEND_DIR, SHC_DATA_DIR, PYTHONPATH, DB_POOL) inherited
    rather than re-stated here, "which would make this file a second source of
    truth for values the image owns, and the two would drift silently"
    (`:122-134`).

    The value is empty, not absent: `${SHARED_TOKEN:-}` keeps the key present so
    the container's environment is explicit about there being no configured token.
    """
    environment = compose_config["services"][SERVICE]["environment"]
    assert set(environment) <= {"SHARED_TOKEN", "SYNCTHING_ROOT"}, (
        f"the service environment is {sorted(environment)}. Only those two keys "
        "may be set here; the five image-owned values are inherited on purpose "
        "(docker-compose.yaml:122-134, :183-220)"
    )
    assert environment["SHARED_TOKEN"] == "", (
        f"SHARED_TOKEN resolved to {environment['SHARED_TOKEN']!r} under a "
        "scrubbed environment; it must default to empty. A non-empty fallback "
        "would ship a token (docker-compose.yaml:142, :173)"
    )


# ---------------------------------------------------------------------------
# The containment interlock itself (AC-21, D4c)
# ---------------------------------------------------------------------------


def test_destructive_verbs_cannot_resolve_to_the_checked_in_volumes() -> None:
    """
    The interlock, proved WITHOUT running `docker`.

    AC-21's second half asks that "the suite never issues a `down -v` that
    resolves to the checked-in file's own volume names". The suite-wide version
    of that audit runs at session teardown, over every argv actually built - but
    this is the unit-level proof that the refusal is reachable at all, and it is
    deliberately a *pure* test. The failure mode being guarded against is
    deleting a developer's live database, so a test that proved the interlock by
    attempting the violation would itself be the hazard.

    `run_destructive_compose` requires the override path as a positional
    argument and builds its argv from it; `assert_override_engaged` then refuses
    any argv whose `-f` flags do not carry it. Passing only
    `-f docker-compose.yaml` is therefore not discouraged, it is unreachable.

    Tighten the exception assertions to the specific class too: a bare
    `pytest.raises(Exception)` would also pass if the helper raised `TypeError`
    or `KeyError` for some unrelated reason, which is the same
    passes-for-the-wrong-reason trap one level down.
    """
    checked_in_only = compose_argv(None, "down", "-v")
    with pytest.raises(InterlockViolation, match="checked-in"):
        assert_override_engaged(COMPOSE_FILE, checked_in_only)

    override = REPO_ROOT / "generated" / "override.yaml"
    with pytest.raises(InterlockViolation, match="not among the -f flags"):
        assert_override_engaged(override, compose_argv(None, "down", "-v"))

    # And the accepted shape is accepted for the right reason.
    assert_override_engaged(override, compose_argv(override, "down", "-v"))


def test_an_override_that_renames_nothing_is_refused() -> None:
    """
    DEFECT 4 REGRESSION TEST: an override present on the command line but
    renaming nothing is REFUSED.

    `assert_override_engaged` used to check only that the override path appeared
    among the `-f` flags, so an override that renames nothing passed it - and a
    `down -v` through that override resolved to `shc-data` / `shc-state` exactly
    as the single-file form does. Safety rested entirely on `_override_yaml`
    happening to be the only producer.

    `assert_override_renames_volumes` closes that: it reads the MERGED config and
    refuses when either volume still carries its checked-in name. This test drives
    it with the four shapes that matter, all pure, all safe to run:

      1. renames nothing        -> refused (the defect)
      2. renames only shc-data   -> refused (a half-measure is not a shield)
      3. renames only shc-state  -> refused
      4. renames both           -> accepted, which is what the suite's own
                                   override does

    A test that only exercised case 4 would pass just as happily with the check
    deleted, so the refusal cases are the point of this test.
    """
    override = REPO_ROOT / "generated" / "override.yaml"

    def config(data_name: Optional[str], state_name: Optional[str]) -> dict:
        """A merged config whose volumes carry exactly these names.

        `Optional[str]` means "the key is absent", which is what a merge that
        renames nothing looks like. Built from an explicit dict rather than
        **kwargs deliberately: `shc-data` is not a legal Python keyword argument,
        so a kwargs helper silently produces `shc_data`, every lookup misses, and
        all four cases collapse into the same first-iteration refusal. That
        happened while writing this test, and cases 2 and 3 passed for the wrong
        reason - which is precisely what these negative controls exist to catch.
        """
        volumes: dict = {}
        if data_name is not None:
            volumes["shc-data"] = {"name": data_name}
        if state_name is not None:
            volumes["shc-state"] = {"name": state_name}
        return {"volumes": volumes}

    # 1. renames nothing.
    with pytest.raises(InterlockViolation, match="shc-data"):
        assert_override_renames_volumes(override, config("shc-data", "shc-state"))

    # 2. renames only shc-data - a half-measure is not a shield.
    with pytest.raises(InterlockViolation, match="shc-state"):
        assert_override_renames_volumes(override, config("shc-e2e-x-data", "shc-state"))

    # 3. renames only shc-state.
    with pytest.raises(InterlockViolation, match="shc-data"):
        assert_override_renames_volumes(override, config("shc-data", "shc-e2e-x-state"))

    # 4. renames neither because the override declares no volumes at all - the
    #    shape an empty or stray override file produces.
    with pytest.raises(InterlockViolation, match="shc-data"):
        assert_override_renames_volumes(override, {"volumes": {}})

    # 5. renames both: the shape the suite's own override has.
    assert_override_renames_volumes(
        override, config("shc-e2e-x-data", "shc-e2e-x-state")
    )


def test_the_suite_owns_a_real_override_that_passes_the_interlock(
    compose_stack,
) -> None:
    """
    The positive control for the preceding refusal test: the override the suite
    ACTUALLY generates renames both volumes, so the tightened interlock does not
    refuse the suite's own stack.

    Without this, "the interlock refuses everything" and "the interlock is
    correct" would be indistinguishable from the failure side.
    """
    volumes = compose_stack.resolved_config()["volumes"]
    assert volumes["shc-data"]["name"] != "shc-data", volumes
    assert volumes["shc-state"]["name"] != "shc-state", volumes
    assert_override_renames_volumes(
        compose_stack.override_path, compose_stack.resolved_config()
    )


def test_a_renaming_nothing_override_is_refused_before_the_verb_runs(
    tmp_path,
) -> None:
    """
    DEFECT 4 ORDERING PROOF: `run_destructive_compose` refuses BEFORE the verb is
    executed - not merely that the guard fires at all.

    The preceding test drives `assert_override_renames_volumes` directly, so it
    says nothing about WHERE `run_destructive_compose` calls it. This one drives
    the real entry point.

    **A guard that runs after the verb has gone out is not a guard.** So the
    assertion is not "an InterlockViolation was raised" - a witness verb that is
    harmless and idempotent succeeds either way, and the exception still arrives
    afterwards, which is exactly how the first version of this test passed with
    the guard deliberately moved after `_execute_argv`.

    Two things make it load-bearing instead:

      * the witness verb is **`ps`**, which is read-only and - crucially -
        *distinguishable from the internal `config` resolve* the guard itself
        performs. So the log can be checked for the witness specifically rather
        than for "did anything run", which would trip on the guard's own parse.
      * the assertion reads the shared EXECUTION log, appended at the top of
        `_execute_argv` before `subprocess.run` is reached. If the guard is ever
        reordered after the call, the witness is in the log and this goes red.

    `ps` is read-only, so even in the mutated world the worst outcome is a wasted
    `docker compose ps`. A test that proved this by attempting the real
    destructive verb would be the hazard rather than the check.
    """
    override = tmp_path / "renames-nothing.yaml"
    override.write_text(
        "# A valid override that changes only the published port - and so renames\n"
        "# nothing. `down -v` through this file deletes shc-data and shc-state.\n"
        "services:\n"
        "  backend:\n"
        "    ports: !override\n"
        '      - "18099:8000"\n',
        encoding="utf-8",
    )

    witness = "ps"
    # Scoped to the calls THIS test makes, not the whole session.
    #
    # The previous version scanned every argv the suite had ever executed for the
    # token `ps`, and asserted that the last recorded call did not contain it.
    # Both halves are order-dependent: `ComposeStack.container_id()` issues
    # `docker compose ... ps -q backend`, so any earlier test that touched the
    # stack poisons the scan, and a future `-p randomly` would flip it at random.
    # That is the spec's own D3 - "do not rely on order" - and it reproduced as:
    #
    #   pytest backend/tests/compose/test_lifecycle.py \
    #          backend/tests/compose/test_compose_config.py -k "..."
    #   E  AssertionError: sanity: the previous exec log should not already end
    #      in the witness
    #
    # Snapshotting the length and scanning only the suffix is the pattern the
    # negative control below already uses; this adopts it rather than inventing a
    # second one.
    before = len(EXECUTED_ARGVS)
    with pytest.raises(InterlockViolation, match="does not rename"):
        run_destructive_compose(override, witness, "--format", "json")

    # Both the witness verb AND this test's own override path, so a `ps` from any
    # other source cannot be mistaken for the witness even if the window grows.
    ran_witness = [
        argv
        for argv in EXECUTED_ARGVS[before:]
        if witness in argv and str(override) in " ".join(argv)
    ]
    assert ran_witness == [], (
        "the interlock raised, but the witness verb had ALREADY been executed: "
        f"{[' '.join(a) for a in ran_witness]}. The guard is after the call, so "
        "it refused something already done. `config` inside the guard is "
        "expected here and is not the witness - `ps` is."
    )


def test_a_renaming_override_would_pass_the_same_gate(tmp_path) -> None:
    """
    The negative control for the witness choice in the test above.

    Without this, "the ordering test passes because nothing ran" and "it passes
    because the guard fired first" are indistinguishable - and so is "the witness
    verb is simply never reached at all".

    This drives the same entry point through an override that DOES rename, so the
    gate must let it through and the witness verb MUST then appear in the log. If
    the witness could not run, the assertion in the ordering test would be
    vacuously true.

    `ps` is read-only, so this cannot destroy anything either.
    """
    override = tmp_path / "renames-both.yaml"
    override.write_text(
        "volumes:\n"
        "  shc-data:\n"
        "    name: shc-e2e-ordering-probe-data\n"
        "  shc-state:\n"
        "    name: shc-e2e-ordering-probe-state\n",
        encoding="utf-8",
    )
    if not destructive_allowed():
        pytest.skip(
            f"{DESTRUCTIVE_ENV} is not 1, so `run_destructive_compose` skips "
            "before reaching the witness verb and this control would prove "
            "nothing. Re-run with it set."
        )

    before = len(EXECUTED_ARGVS)
    run_destructive_compose(override, "ps", "--format", "json", check=False)
    assert len(EXECUTED_ARGVS) > before, (
        "the witness verb did not execute even for a valid override, so the "
        "ordering test's 'nothing ran' assertion could not distinguish a "
        "correctly-ordered guard from a broken harness."
    )
