# Spec: Heartbeat Contract — Configurable Timeout, Documented Cadence, Pinned by Tests

**Issue**: #96 — "The 90-second offline threshold is hardcoded in two places, documented nowhere, and pinned by no test"
**Labels**: `agent`, `phase-2`, `ready-for-grooming` → `groomed`
**Author**: PM
**Date**: 2026-10-14

---

## 1. Verified Problem Statement

Node liveness is decided by two hardcoded numbers in one function, and neither is configurable, documented as a contract, or pinned by a test. An agent author has no way to learn that it must beat a 90-second deadline except by reading `syncthing_service.py`.

`backend/services/syncthing_service.py:346-378`:

```python
async def _heartbeat_monitor(self):
    """Monitor node heartbeats and mark offline nodes."""
    while self._running:
        try:
            await asyncio.sleep(30)  # Check every 30 seconds
            ...
            for node in nodes:
                ...
                delta = (now - last_hb).total_seconds()
                if delta > 90 and node.status == "ONLINE":
                    await store.update_node(node.node_id, status="OFFLINE")
                    ...
                elif delta <= 90 and node.status == "OFFLINE":
                    await store.update_node(node.node_id, status="ONLINE")
```

Three facts follow, and none of them is written down anywhere an agent author would look:

1. **90 seconds is the offline deadline.** An agent whose heartbeat interval exceeds it will be marked `OFFLINE` while it is perfectly healthy.
2. **The effective deadline is worse than 90 seconds.** The monitor only *observes* every 30 s, so a node can be stale by up to 120 s before the flip happens. The threshold that matters in practice is the observation cadence, not the comparison.
3. **Recovery is asymmetric with the file transport.** A node returns to `ONLINE` via the DB, but if the transport is file-based, the agent's next heartbeat write also carries `status` — and `syncthing_service.py:321` writes `status` straight from the YAML. So the file and the monitor can disagree, and the file wins on the next tick.

`docs/syncthing-sync-service-spec.md:56` explicitly flagged this and it was never done: *"Consider making heartbeat timeout configurable."*

### Why it matters

- **A laptop suspends.** A home-cluster node that is a laptop will sleep for hours. On resume its heartbeat is stale by hours, the monitor marks it `OFFLINE`, and the first thing it does is rewrite `last_heartbeat` — which flips it back. The window of a wrongly-`OFFLINE` node is avoidable but its length depends on an undocumented number.
- **Node status drives scheduling.** `NodeSpec.status` is what tells the rest of the system a node is usable. Getting this wrong means either a dead node keeps being offered work, or a live node stops receiving it.
- **There is no test on this threshold.** Nothing in `backend/tests/` asserts the 90 s behaviour, so it can be changed by accident without anything failing. That matters more than usual given that `syncthing_service` is one of the modules **excluded from mypy** (`pyproject.toml:70-80`) — i.e. the module is both untypechecked and untested at exactly this point.

---

## 2. Root Cause

The two numbers (`30` for check interval, `90` for offline threshold) were baked into `_heartbeat_monitor` before any agent existed. They were never extracted to settings, never documented as a contract the agent must meet, and never pinned by tests. The agent (`agent/loop.py:49`) independently hardcoded `HEARTBEAT_INTERVAL_S = 30.0` with a comment referencing the backend's hardcoded numbers, creating a duplicated contract that can drift.

---

## 3. Open Questions — Resolved

The issue left several questions open. Each is decided here, with rationale.

### Q1 — What settings, with what names and defaults?

**Decision: Two settings in `backend/core/config.py`:**

- `HEARTBEAT_TIMEOUT_SECONDS` — default `90` (the offline threshold)
- `HEARTBEAT_CHECK_INTERVAL_SECONDS` — default `30` (the monitor's sleep interval)

Names and defaults preserve today's behaviour exactly. The agent will read these from the shared config file (`nodes/agent.toml`) or environment.

### Q2 — Is the relationship between check interval and timeout enforced?

**Decision: Yes — at startup, fail fast if `check_interval >= timeout`.**

The monitor wakes every `check_interval` seconds and compares `delta > timeout`. If `check_interval >= timeout`, a healthy node that writes exactly on schedule can be observed at `delta == check_interval >= timeout` and flipped `OFFLINE`. The contract is that the monitor must observe *at least twice* within the timeout to give the agent a full interval of slack. Enforcing this at `Settings` construction (or in `SyncthingService.start()`) makes the misconfiguration visible immediately rather than at the first false `OFFLINE` flip.

### Q3 — What is the agent's default heartbeat interval?

**Decision: `22` seconds with jitter of `±2` seconds (uniform).**

The recommended cadence is `timeout / 4` (~22 s at the default 90 s). This leaves room for two missed writes plus watcher latency before the monitor's 30 s tick could observe a stale heartbeat. The interval is configurable via `AGENT_HEARTBEAT_INTERVAL_S` in the shared config, defaulting to `22`. A test asserts `interval < timeout` with margin.

### Q4 — Should the agent's heartbeat writes be jittered?

**Decision: Yes — uniform jitter `±2` seconds, seeded for test determinism.**

A home cluster's nodes tend to be laptops that wake together, and a synchronised fleet all hitting the watcher at the same instant is exactly what the 500 ms debounce (`syncthing_service.py:64-73`) was added to smooth. The jitter is applied to the *sleep* between writes, not to the timestamp written (the timestamp is always `datetime.now(timezone.utc)`). The PRNG is seeded from `node_id` so the same node gets the same jitter sequence across restarts, and tests can pin it.

### Q5 — Should the agent write a heartbeat immediately on startup and after resume?

**Decision: Yes — immediate write on startup (already done in #95 via the first supervisor tick) and immediate write on resume.**

On resume from suspend, the agent knows it slept (the event loop was paused). It should write a heartbeat immediately rather than waiting out its normal interval. The supervisor tick already runs on startup; for resume we add an explicit "I woke up" tick triggered by a `SIGCONT` handler or by detecting a large monotonic clock jump. (Implementation detail: the agent loop runs in a single thread; a `time.monotonic()` delta > `2 * interval` between ticks is a reliable suspend detector.)

### Q6 — Timezone handling

**Decision: Already fixed by #121 — both sides use aware UTC.**

#121 changed `NodeModel.last_heartbeat` to `DateTime(timezone=True)`, the monitor to `datetime.now(timezone.utc)`, and the agent to emit `datetime.now(timezone.utc)`. The coercion block in `_heartbeat_monitor` (lines 359-362) is removed. This spec does not re-decide timezone; it records that the contract is **aware UTC on both sides** and a test pins it.

### Q7 — Mypy exclusion for `syncthing_service.py`

**Decision: The exclusion is revisited. #121 fixed the timezone coercion that was the main type error source. If mypy now passes on the module, the override is removed from `pyproject.toml`. If not, the remaining errors are fixed here.**

---

## 4. Scope Boundaries

| In Scope | Out of Scope |
|---|---|
| `HEARTBEAT_TIMEOUT_SECONDS` and `HEARTBEAT_CHECK_INTERVAL_SECONDS` in `Settings` | Adding a third node status (`DRAINING`, `BUSY`) — see #1 open question 4 |
| Startup validation `check_interval < timeout` | HTTP transport for heartbeats (file write stays) — see #92 |
| Backend tests: stale→OFFLINE, fresh→ONLINE, boundary, no redundant write | Changing the file watcher debounce (500 ms) |
| Agent default interval, jitter, seeded PRNG, immediate write on resume | Agent clean-shutdown "going offline" declaration |
| Mypy override removal or fix for `syncthing_service.py` | Moving the monitor out of the watcher (open question 4 in #96) |

### Coupling to #95 — recorded, not fixed here

#95 (node registration) is merged. The agent writes `nodes/<node_id>.yaml` with `last_heartbeat` every 30s (hardcoded). This spec makes that interval configurable and documented. The agent's `HEARTBEAT_INTERVAL_S` constant in `agent/loop.py:49` is replaced by a setting.

### Coupling to #121 — recorded, not fixed here

#121 (heartbeat timezone fix) is **merged**. The store column is `DateTime(timezone=True)`, the monitor uses `datetime.now(timezone.utc)`, and the agent emits aware UTC. This spec assumes that convention and does not re-litigate it. The tests in this spec use aware UTC timestamps.

---

## 5. Implementation Approach

### 5.1 Backend settings — `backend/core/config.py`

Add two fields to `Settings`:

```python
# Heartbeat contract (issue #96)
HEARTBEAT_TIMEOUT_SECONDS: int = 90
HEARTBEAT_CHECK_INTERVAL_SECONDS: int = 30


@field_validator("HEARTBEAT_CHECK_INTERVAL_SECONDS")
@classmethod
def _check_heartbeat_interval_vs_timeout(cls, v: int, info) -> int:
    timeout = info.data.get("HEARTBEAT_TIMEOUT_SECONDS")
    if timeout is not None and v >= timeout:
        raise ValueError(
            f"HEARTBEAT_CHECK_INTERVAL_SECONDS ({v}) must be < "
            f"HEARTBEAT_TIMEOUT_SECONDS ({timeout}), otherwise a healthy "
            f"node can be observed at delta >= timeout and flipped OFFLINE"
        )
    return v
```

The validator runs at `Settings` construction (module import time), so a misconfigured `.env` fails before the server starts accepting requests.

### 5.2 Syncthing service — `backend/services/syncthing_service.py`

Replace the two hardcoded numbers with settings:

```python
from backend.core.config import settings

async def _heartbeat_monitor(self):
    """Monitor node heartbeats and mark offline nodes."""
    while self._running:
        try:
            await asyncio.sleep(settings.HEARTBEAT_CHECK_INTERVAL_SECONDS)
            store = get_store()
            nodes = await store.list_nodes()
            now = datetime.now(timezone.utc)

            for node in nodes:
                if node.last_heartbeat:
                    last_hb = node.last_heartbeat
                    delta = (now - last_hb).total_seconds()
                    timeout = settings.HEARTBEAT_TIMEOUT_SECONDS
                    if delta > timeout and node.status == "ONLINE":
                        await store.update_node(node.node_id, status="OFFLINE")
                        logger.info(
                            f"Node {node.node_id} marked OFFLINE "
                            f"(last heartbeat {delta:.0f}s ago, timeout={timeout}s)"
                        )
                    elif delta <= timeout and node.status == "OFFLINE":
                        await store.update_node(node.node_id, status="ONLINE")
                        logger.info(
                            f"Node {node.node_id} marked ONLINE "
                            f"(last heartbeat {delta:.0f}s ago, timeout={timeout}s)"
                        )
```

The `if node.last_heartbeat.tzinfo is None:` coercion block is already removed by #121.

### 5.3 Agent configuration — `agent/config.py`

Add to `AgentSettings`:

```python
# Heartbeat contract (issue #96)
AGENT_HEARTBEAT_INTERVAL_S: float = 22.0
AGENT_HEARTBEAT_JITTER_S: float = 2.0
```

Add to `_SOURCE_NAMES` mapping and `_cli_layer`/`_env_layer`/`_toml_layer` so it can be set via `nodes/agent.toml`, env var `AGENT_HEARTBEAT_INTERVAL_S`, or CLI flag `--heartbeat-interval` (optional, not required for grooming).

Validator: `AGENT_HEARTBEAT_INTERVAL_S > 0` and `AGENT_HEARTBEAT_JITTER_S >= 0` and `AGENT_HEARTBEAT_JITTER_S < AGENT_HEARTBEAT_INTERVAL_S`.

### 5.4 Agent loop — `agent/loop.py`

Remove the module-level `HEARTBEAT_INTERVAL_S` constant. Read the interval from `self._settings.AGENT_HEARTBEAT_INTERVAL_S` and jitter from `self._settings.AGENT_HEARTBEAT_JITTER_S`.

In `_registration_tick`:

```python
async def _registration_tick(self) -> None:
    """One registration tick: probe this machine, rewrite the node file."""
    try:
        written = write_node_spec(
            self._paths, collect_node_spec(self._settings.NODE_ID)
        )
    except OSError as exc:
        logger.warning(...)
        return
    if self._watcher is not None:
        self._watcher.ledger.record(written)
    
    # Schedule next tick with jitter
    interval = self._settings.AGENT_HEARTBEAT_INTERVAL_S
    jitter = self._settings.AGENT_HEARTBEAT_JITTER_S
    if jitter > 0:
        # Seeded PRNG: hash(node_id + tick_count) -> uniform [-jitter, +jitter]
        seed = hash((self._settings.NODE_ID, self._tick_count))
        rng = random.Random(seed)
        interval += rng.uniform(-jitter, jitter)
    await asyncio.sleep(max(0.1, interval))
```

The supervisor already manages the tick; we just vary the sleep. The `_tick_count` is an instance counter incremented each tick.

**Resume detection:** In `_registration_tick`, track `last_tick_monotonic = time.monotonic()`. If `time.monotonic() - last_tick_monotonic > 2 * interval`, treat as resume and log "resume detected, writing immediate heartbeat" (the current tick already writes, so no extra write needed — the tick that detects the gap *is* the immediate write).

### 5.5 Documentation updates

**Syncthing service spec** (`docs/syncthing-sync-service-spec.md`): Add a section "Heartbeat Contract" documenting:
- The two settings, defaults, and the invariant `check_interval < timeout`
- The recommended agent cadence: `timeout / 4` (22 s at defaults)
- The reasoning: monitor tick + agent interval + watcher latency = effective deadline

**Agent documentation** (README or `agent/docs/`): Document `AGENT_HEARTBEAT_INTERVAL_S` and `AGENT_HEARTBEAT_JITTER_S`, the recommended value, and the resume behaviour.

### 5.6 Mypy override

Run `mypy backend/services/syncthing_service.py`. If clean, remove the override from `pyproject.toml`:

```toml
# Remove this block if mypy passes:
[[tool.mypy-overrides]]
module = "backend.services.syncthing_service"
ignore_errors = true
```

If errors remain, fix them in this issue rather than keeping the override.

---

## 6. Acceptance Criteria

### Functional

- **AC-1** — `HEARTBEAT_TIMEOUT_SECONDS` and `HEARTBEAT_CHECK_INTERVAL_SECONDS` in `Settings` with defaults 90 and 30.
  ```bash
  python -c "
  from backend.core.config import settings
  assert settings.HEARTBEAT_TIMEOUT_SECONDS == 90
  assert settings.HEARTBEAT_CHECK_INTERVAL_SECONDS == 30
  "
  ```

- **AC-2** — Startup validation fails if `check_interval >= timeout`.
  ```bash
  HEARTBEAT_CHECK_INTERVAL_SECONDS=90 HEARTBEAT_TIMEOUT_SECONDS=90 python -c "
  from backend.core.config import Settings
  Settings()  # Should raise ValueError
  " && echo "FAIL: should have raised" || echo "PASS: raised as expected"
  ```

- **AC-3** — Backend tests cover:
  - **Stale → OFFLINE**: Node with `last_heartbeat = now - 120s` → status becomes `OFFLINE`.
  - **Fresh → ONLINE**: Node with `last_heartbeat = now - 10s` and status `OFFLINE` → status becomes `ONLINE`.
  - **Boundary `delta == timeout` → ONLINE**: Node with `last_heartbeat = now - 90s` (exactly at threshold) → status stays/ becomes `ONLINE` (because condition is `delta > timeout`).
  - **No redundant write**: Node already `ONLINE` with fresh heartbeat → no `update_node` call; node already `OFFLINE` with stale heartbeat → no `update_node` call.

- **AC-4** — Agent default heartbeat interval satisfies `interval < timeout` with margin, asserted by test.
  ```python
  # In agent test
  from agent.config import build_settings
  from backend.core.config import settings as backend_settings

  agent_settings = build_settings(...)
  assert (
      agent_settings.AGENT_HEARTBEAT_INTERVAL_S
      < backend_settings.HEARTBEAT_TIMEOUT_SECONDS
  )
  # Margin: at least 2x the interval fits in timeout
  assert (
      agent_settings.AGENT_HEARTBEAT_INTERVAL_S * 2
      <= backend_settings.HEARTBEAT_TIMEOUT_SECONDS
  )
  ```

- **AC-5** — Agent heartbeat writes are jittered; jitter is seeded/tested.
  - Unit test: two agents with same `node_id` and same tick count produce same jitter; different `node_id` produces different jitter.
  - Integration test: run agent loop for 10 ticks, collect intervals, assert all in `[interval - jitter, interval + jitter]` and not all identical.

- **AC-6** — Agent writes heartbeat immediately on startup and after resume (simulated).
  - Startup: the first `_registration_tick` runs immediately after `_ensure_folder()` succeeds (already verified by #95 AC-9).
  - Resume: simulate by advancing `time.monotonic()` by `> 2 * interval` between ticks; assert a write happens on the tick that detects the gap (log message "resume detected" or equivalent).

- **AC-7** — `syncthing_service.py` passes mypy (was excluded, now clean). `ruff check . && ruff format --check . && mypy .` all exit 0 with no new `type: ignore`.

- **AC-8** — Recommended cadence documented in Syncthing service spec AND agent docs, with numbers and reasoning.

- **AC-9** — All gates green: `pytest -q` full suite count does not drop; `ruff`/`mypy` clean.

### Quality

- **AC-10** — No new `type: ignore`, no widening of existing mypy overrides.
- **AC-11** — The decision table in §3 is reflected in code comments at the settings and the monitor.
- **AC-12** — The jitter PRNG is deterministic per `node_id` + tick (testable, reproducible).

---

## 7. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `check_interval >= timeout` misconfiguration in production | Low | High | Startup validator (AC-2) makes it a fast failure, not a silent bug. |
| Jitter causes flaky tests | Medium | Medium | Seeded PRNG (AC-12); tests pin the sequence. |
| Agent resume detection misses a suspend | Low | Medium | Monotonic clock jump > `2 * interval` is a reliable heuristic; the agent's next tick writes anyway. |
| `syncthing_service.py` mypy errors remain after #121 | Low | Medium | Fix them in this issue rather than keeping the override. |
| Settings drift between backend and agent (different defaults) | Medium | High | Both read from the same `nodes/agent.toml` (shared config); a test asserts the agent's default interval < backend timeout. |
| File transport asymmetry: agent writes `status="ONLINE"` but monitor flips to `OFFLINE` between ticks | Medium | Low | The monitor wins on the next tick (30 s). This is existing behaviour; the contract documents it. A later HTTP transport can eliminate it. |

---

## 8. Definition of Done

- [ ] Spec reviewed and issue labelled `groomed`
- [ ] Implementation complete (SWE)
- [ ] AC-1 … AC-12 verified with evidence (QA)
- [ ] `pytest -q` green; `ruff`/`mypy` clean
- [ ] PM acceptance

---

## 9. Related

- #95 — node registration file (agent writes `last_heartbeat`; cadence now configurable)
- #92 — transport decision (heartbeat cost under file-only; recorded in #92 open question 1)
- #121 — heartbeat timezone fix (merged; convention is aware UTC on both sides)
- #102 — node metrics writer (may also write timestamps; out of scope)
- `docs/syncthing-sync-service-spec.md` — service spec updated with heartbeat contract
- `shared/auth/localhost_hosts.json` — unrelated but noted: localhost bypass list duplicated in backend and frontend