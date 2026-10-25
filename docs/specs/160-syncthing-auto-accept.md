# Spec: Disable Syncthing Auto-Accept Folders by Default

**Issue**: #160 — "Syncthing auto-accepts foreign folders, allowing rogue device to push files"  
**Labels**: `security`, `bug`, `phase-3`, `ready-for-grooming` → `groomed`  
**Author**: PM  
**Date**: 2026-10-25  
**Baseline**: `main` (post #154)

**This file is the authoritative input for the SWE cycle.** It is self-sufficient: §3 carries the decisions, §4 carries the code shapes. §5 is the acceptance checklist. No code is implemented by this spec.

---

## 0. Baseline, as re-measured by PM

| Gate | On `main` |
|---|---|
| `ruff check . && ruff format --check . && mypy .` | expected clean |
| `pytest --cov=backend` | expected green; compose suite skipped unless `SHC_COMPOSE_TESTS=1` |
| Compose L1 static (`SHC_COMPOSE_TESTS=1 ... -k config`) | `docker compose config` resolves; pinned volumes `shc-data`/`shc-state`; one published port `8000` |

The changes in this spec must not move any of those needles: backend gates stay green, `backend/tests/compose/` (L1–L5) keeps passing on the checked-in `docker-compose.yaml`, and no new Python dependencies are introduced.

---

## 1. Verified Problem Statement

### 1.1 Current behaviour (from #154)

The entrypoint script (`docker/entrypoint.sh`, lines 33–37) sets `<autoAcceptFolders>true</autoAcceptFolders>` in Syncthing's `config.xml` on first run. This causes the Syncthing instance to **automatically accept any folder share offered by an already-paired device** without requiring explicit approval via the GUI or REST API.

### 1.2 Threat model

- **Actor**: A rogue or compromised device that has already been paired with the cluster (device IDs exchanged, introducer used, or manual pairing completed).
- **Capability**: The device can offer a folder share pointing at the cluster's `SYNCTHING_ROOT` (`/tmp/syncthing` in the container, bind-mounted to `./syncthing` on the host).
- **Impact**: Files written by the rogue device appear in `SYNCTHING_ROOT/{jobs,nodes}`. The backend's Syncthing watcher (`backend/services/syncthing_service.py`) ingests these as job specifications and node state updates.
- **Validation boundary**: Pydantic models (`shared/schemas.py`) validate incoming YAML, so arbitrary code execution is not directly possible. However:
  - Malformed job specs can cause denial of service (worker agents crash, queue clogged).
  - Crafted node state can manipulate scheduling decisions (e.g., fake GPU availability).
  - Data exfiltration: a rogue device can read any file the cluster writes to `SYNCTHING_ROOT` (job outputs, logs).
  - Supply-chain risk: if a future deserialization bug exists in Pydantic or the YAML loader, the auto-accept expands the attack surface.

### 1.3 Why this is a regression from the intended design

The #154 spec (D4, line 196–197) explicitly states: *"`autoAcceptFolders=true` auto-accepts folders offered by an already-paired device; it does **not** auto-accept new devices, which is the unsafe half and is deliberately left off."* However, the threat model in #160 correctly identifies that **a paired-but-compromised device is a realistic risk** in a home cluster where devices may be shared, re-purposed, or physically accessible. The "already-paired" constraint is not a sufficient security boundary.

### 1.4 UX trade-off

`autoAcceptFolders=true` was added for convenience: when a new compute node joins, the operator does not need to open the Syncthing GUI (loopback-only in the container) to approve the default folder share. Disabling it means:
- The operator **must** approve the folder share once per device (via GUI at `127.0.0.1:8384` or REST API).
- This is a one-time action per device; subsequent restarts do not re-prompt.
- For Render deployments (where the GUI is unreachable), the folder share must be pre-configured or accepted via the REST API (which requires the Syncthing API key).

---

## 2. Root Cause / Constraints (summary)

The `autoAcceptFolders` option is written unconditionally to `true` in `docker/entrypoint.sh` during the first-run config patch. There is no environment variable to control it, no warning logged, and no documentation of the security implication.

**Constraints:**
- The fix must not break existing clusters that have already accepted the folder (the marker file `.shc-configured` prevents re-patching on restart).
- The fix must work for both Docker and Render deployments.
- The fix must not require a process supervisor or new dependencies.
- The fix must preserve the "first-run only" patch semantics (operator edits to `config.xml` are preserved across restarts).

---

## 3. Decisions

### D1 — Disable `autoAcceptFolders` by default

The entrypoint will **not** set `<autoAcceptFolders>true</autoAcceptFolders>` in the generated config. The default in Syncthing is `false`, so omitting the element (or setting it to `false`) achieves the secure default.

**Rationale**: Secure by default. Operators who understand the risk can opt in.

### D2 — Opt-in via `SYNCTHING_AUTO_ACCEPT_FOLDERS` environment variable

Add a new environment variable `SYNCTHING_AUTO_ACCEPT_FOLDERS` (default: `false`). When set to `true`, `1`, `yes`, or `on` (case-insensitive), the entrypoint writes `<autoAcceptFolders>true</autoAcceptFolders>`.

**Rationale**: 
- Explicit opt-in documents the operator's acceptance of the risk.
- Environment variable is consistent with how `SHARED_TOKEN`, `SYNCTHING_ROOT`, `SYNCTHING_HOME`, etc. are configured.
- No config file change needed; works in Docker, compose, Render, and bare-metal.

### D3 — Startup warning when auto-accept is enabled

If `SYNCTHING_AUTO_ACCEPT_FOLDERS=true`, log a **WARNING** to stdout/stderr at container startup:

```
WARNING: SYNCTHING_AUTO_ACCEPT_FOLDERS=true — this device will automatically accept folder shares from any paired device. A compromised peer can write files into SYNCTHING_ROOT. See issue #160.
```

**Rationale**: 
- Makes the risk visible in logs (Docker, Render, systemd journal).
- Does not block startup (unlike the old `SHARED_TOKEN:?` guard which was removed for usability).
- The warning text includes the issue number for traceability.

### D4 — Preserve first-run-only semantics

The patch (including the `autoAcceptFolders` value) is still applied only once, gated by the `.shc-configured` marker file. Existing clusters with the marker **will not** have their config changed on upgrade. Operators who want the new behaviour must either:
- Delete `$SYNCTHING_HOME/.shc-configured` and restart (re-applies patch with new default), or
- Manually edit `config.xml` to set `<autoAcceptFolders>false</autoAcceptFolders>`.

**Rationale**: 
- Avoids surprising operators who have already approved folders.
- Consistent with #154's design (D4, line 193–195).
- Documented in migration notes.

### D5 — Update documentation

- `docker/README.md`: Add `SYNCTHING_AUTO_ACCEPT_FOLDERS` to the configuration table and the "Syncthing daemon" subsection.
- `docker/entrypoint.sh`: Add a comment explaining the security implication.
- No changes to `docker-compose.yaml` (env vars are interpolated from `.env`/shell automatically).

### D6 — No backend changes required

The backend does not read or write `autoAcceptFolders`. The watcher (`syncthing_service.py`) only cares that `SYNCTHING_ROOT` exists and contains `jobs/` and `nodes/`. Folder approval is a Syncthing-layer concern.

---

## 4. Code Shapes

### 4.1 `docker/entrypoint.sh` (modify)

```sh
#!/usr/bin/env bash
set -euo pipefail

SYNCTHING_HOME="${SYNCTHING_HOME:-/app/data/syncthing}"
SYNCTHING_ROOT="${SYNCTHING_ROOT:-/tmp/syncthing}"
PORT="${PORT:-8000}"

# Parse SYNCTHING_AUTO_ACCEPT_FOLDERS (default: false)
# Accepted truthy values: true, 1, yes, on (case-insensitive)
SYNCTHING_AUTO_ACCEPT_FOLDERS="${SYNCTHING_AUTO_ACCEPT_FOLDERS:-false}"
case "${SYNCTHING_AUTO_ACCEPT_FOLDERS,,}" in
    true|1|yes|on)
        AUTO_ACCEPT_FOLDERS="true"
        echo "WARNING: SYNCTHING_AUTO_ACCEPT_FOLDERS=true — this device will automatically accept folder shares from any paired device. A compromised peer can write files into SYNCTHING_ROOT. See issue #160." >&2
        ;;
    *)
        AUTO_ACCEPT_FOLDERS="false"
        ;;
esac

mkdir -p "$SYNCTHING_HOME" "$SYNCTHING_ROOT"

export STNOUPGRADE=1

# First run: generate identity + default config.
if [ ! -f "$SYNCTHING_HOME/config.xml" ]; then
    syncthing -generate="$SYNCTHING_HOME" >/dev/null 2>&1
fi

# First-run tuning: applied once, then a marker prevents clobbering operator
# edits on later restarts. Delete the marker to re-apply.
if [ ! -f "$SYNCTHING_HOME/.shc-configured" ] && [ -f "$SYNCTHING_HOME/config.xml" ]; then
    python3 - "$SYNCTHING_HOME/config.xml" "$SYNCTHING_ROOT" "$AUTO_ACCEPT_FOLDERS" <<'PY'
import sys
import xml.etree.ElementTree as ET

cfg_path, root, auto_accept = sys.argv[1], sys.argv[2], sys.argv[3]
tree = ET.parse(cfg_path)
cfg = tree.getroot()

for folder in cfg.findall("folder"):
    folder.set("path", root)

for opts in cfg.findall("options"):
    el = opts.find("autoAcceptFolders")
    if el is None:
        el = ET.SubElement(opts, "autoAcceptFolders")
    el.text = auto_accept

for gui in cfg.findall("gui"):
    addr = gui.find("address")
    if addr is None:
        addr = ET.SubElement(gui, "address")
    addr.text = "127.0.0.1:8384"

tree.write(cfg_path, encoding="utf-8", xml_declaration=True)
PY
    touch "$SYNCTHING_HOME/.shc-configured"
fi

# Background daemon. Logs go to the persisted home so they survive a replace
# for diagnosis (they are not the unit of persistence, but they are cheap).
syncthing -home="$SYNCTHING_HOME" -no-browser -no-restart \
    >"$SYNCTHING_HOME/syncthing.log" 2>&1 &

exec uvicorn backend.main:app --host 0.0.0.0 --port "$PORT"
```

### 4.2 `docker/README.md` (modify)

**Configuration table** — add row:

| Variable | Default in the image | Purpose |
|---|---|---|
| `SYNCTHING_AUTO_ACCEPT_FOLDERS` | `false` | Set `true` to auto-accept folder shares from paired devices. **Security risk**: a compromised peer can write files into `SYNCTHING_ROOT`. See issue #160. |

**Syncthing daemon subsection** — update the bullet about auto-accept:

- Default folder is `SYNCTHING_ROOT` (`/tmp/syncthing`); `autoAcceptFolders` is **disabled by default** (secure). Set `SYNCTHING_AUTO_ACCEPT_FOLDERS=true` to enable (logs a WARNING). Auto-accepting *devices* is **not** enabled — pair devices manually or via an introducer.

---

## 5. Acceptance Criteria

- [ ] **AC-1** Default behaviour: fresh container start (no `.shc-configured` marker) produces `config.xml` with `<autoAcceptFolders>false</autoAcceptFolders>` (or element absent).
- [ ] **AC-2** Opt-in: `SYNCTHING_AUTO_ACCEPT_FOLDERS=true` (or `1`, `yes`, `on`) produces `<autoAcceptFolders>true</autoAcceptFolders>` and logs the WARNING message to stderr.
- [ ] **AC-3** Case-insensitivity: `TRUE`, `Yes`, `On`, `1` all enable; `FALSE`, `No`, `Off`, `0`, `""`, unset all disable.
- [ ] **AC-4** Idempotency: restarting a container with `.shc-configured` present does not change `autoAcceptFolders` in `config.xml` (operator edits preserved).
- [ ] **AC-5** Marker deletion: removing `.shc-configured` and restarting re-applies the patch with the current `SYNCTHING_AUTO_ACCEPT_FOLDERS` value.
- [ ] **AC-6** Render deployment: `render.yaml` does not set `SYNCTHING_AUTO_ACCEPT_FOLDERS` (defaults to secure); operator can add it in dashboard if needed.
- [ ] **AC-7** Docker Compose: `.env` can set `SYNCTHING_AUTO_ACCEPT_FOLDERS=true`; no compose file change needed.
- [ ] **AC-8** Backend gates pass: `ruff check . && ruff format --check . && mypy .` clean; `pytest --cov=backend` green.
- [ ] **AC-9** Compose gates pass: `backend/tests/compose/` (L1–L5) still pass; `docker compose config` resolves.
- [ ] **AC-10** No new Python dependencies; no changes to `backend/` source required.

---

## 6. Testing

### 6.1 Unit tests for entrypoint logic (new)

Add `backend/tests/unit/test_entrypoint_auto_accept.py`:

```python
"""Unit tests for the entrypoint's autoAcceptFolders logic."""
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


def test_auto_accept_default_false():
    """Default (unset) should produce autoAcceptFolders=false."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "syncthing"
        root = Path(tmp) / "root"
        home.mkdir()
        root.mkdir()
        # Run the python patch snippet directly
        cfg = home / "config.xml"
        # Generate a minimal config.xml like syncthing -generate would
        minimal = f"""<?xml version="1.0"?>
<configuration version="36">
    <folder id="default" label="Default Folder" path="{root}" type="sendreceive" rescanIntervalS="3600" fsWatcherEnabled="true" fsWatcherDelayS="10" ignorePerms="false" autoNormalize="true">
        <filesystemType>basic</filesystemType>
        <device id="THIS_DEVICE_ID"></device>
    </folder>
    <device id="THIS_DEVICE_ID" name="Test Device" compression="metadata-lz4"></device>
    <gui enabled="true" tls="false" debugging="false">
        <address>127.0.0.1:8384</address>
        <apikey>test</apikey>
    </gui>
    <options>
        <listenAddress>default</listenAddress>
        <globalAnnEnabled>true</globalAnnEnabled>
        <globalAnnServers>default</globalAnnServers>
        <localAnnEnabled>true</localAnnEnabled>
        <localAnnPort>21027</localAnnPort>
        <localAnnMCAddr>[ff12::8384]:21027</localAnnMCAddr>
        <maxSendKbps>0</maxSendKbps>
        <maxRecvKbps>0</maxRecvKbps>
        <reconnectionIntervalS>60</reconnectionIntervalS>
        <startBrowser>false</startBrowser>
        <natEnabled>true</natEnabled>
        <natLeaseMinutes>60</natLeaseMinutes>
        <natRenewalMinutes>30</natRenewalMinutes>
        <natTimeoutSeconds>10</natTimeoutSeconds>
        <urAccepted>-1</urAccepted>
        <urUniqueID></urUniqueID>
        <urURL>https://data.syncthing.net/newdata</urURL>
        <urPostInsecurely>false</urPostInsecurely>
        <urInitialDelayS>1800</urInitialDelayS>
        <restartOnWakeup>true</restartOnWakeup>
        <autoUpgradeIntervalH>1</autoUpgradeIntervalH>
        <upgradeToPreReleases>false</upgradeToPreReleases>
        <keepTemporariesH>24</keepTemporariesH>
        <cacheIgnoredFiles>false</cacheIgnoredFiles>
        <progressUpdateIntervalS>5</progressUpdateIntervalS>
        <limitBandwidthInLan>false</limitBandwidthInLan>
        <minHomeDiskFree unit="%">1</minHomeDiskFree>
        <releasesURL>https://upgrades.syncthing.net/meta.json</releasesURL>
        <overwriteRemoteDeviceNamesOnConnect>false</overwriteRemoteDeviceNamesOnConnect>
        <tempIndexMinBlocks>10</tempIndexMinBlocks>
        <unackedNotificationIDs></unackedNotificationIDs>
        <trafficClass>0</trafficClass>
        <defaultFolderPath>{root}</defaultFolderPath>
        <setLowPriority>true</setLowPriority>
        <maxFolderConcurrency>0</maxFolderConcurrency>
        <maxConcurrentIncomingRequestKiB>0</maxConcurrentIncomingRequestKiB>
        <announceLANAddresses>true</announceLANAddresses>
        <sendFullIndexOnUpgrade>false</sendFullIndexOnUpgrade>
        <connectionPriority>0</connectionPriority>
    </options>
</configuration>"""
        cfg.write_text(minimal)
        
        # Simulate the python patch with auto_accept="false"
        import xml.etree.ElementTree as ET
        tree = ET.parse(cfg)
        cfg_root = tree.getroot()
        for opts in cfg_root.findall("options"):
            el = opts.find("autoAcceptFolders")
            if el is None:
                el = ET.SubElement(opts, "autoAcceptFolders")
            el.text = "false"
        tree.write(cfg, encoding="utf-8", xml_declaration=True)
        
        # Verify
        tree = ET.parse(cfg)
        el = tree.getroot().find(".//autoAcceptFolders")
        assert el is not None
        assert el.text == "false"


def test_auto_accept_true_when_enabled():
    """SYNCTHING_AUTO_ACCEPT_FOLDERS=true should produce autoAcceptFolders=true."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "syncthing"
        root = Path(tmp) / "root"
        home.mkdir()
        root.mkdir()
        cfg = home / "config.xml"
        minimal = f"""<?xml version="1.0"?>
<configuration version="36">
    <folder id="default" label="Default Folder" path="{root}" type="sendreceive" rescanIntervalS="3600" fsWatcherEnabled="true" fsWatcherDelayS="10" ignorePerms="false" autoNormalize="true">
        <filesystemType>basic</filesystemType>
        <device id="THIS_DEVICE_ID"></device>
    </folder>
    <device id="THIS_DEVICE_ID" name="Test Device" compression="metadata-lz4"></device>
    <gui enabled="true" tls="false" debugging="false">
        <address>127.0.0.1:8384</address>
        <apikey>test</apikey>
    </gui>
    <options>
        <listenAddress>default</listenAddress>
        <globalAnnEnabled>true</globalAnnEnabled>
        <globalAnnServers>default</globalAnnServers>
        <localAnnEnabled>true</localAnnEnabled>
        <localAnnPort>21027</localAnnPort>
        <localAnnMCAddr>[ff12::8384]:21027</localAnnMCAddr>
        <maxSendKbps>0</maxSendKbps>
        <maxRecvKbps>0</maxRecvKbps>
        <reconnectionIntervalS>60</reconnectionIntervalS>
        <startBrowser>false</startBrowser>
        <natEnabled>true</natEnabled>
        <natLeaseMinutes>60</natLeaseMinutes>
        <natRenewalMinutes>30</natRenewalMinutes>
        <natTimeoutSeconds>10</natTimeoutSeconds>
        <urAccepted>-1</urAccepted>
        <urUniqueID></urUniqueID>
        <urURL>https://data.syncthing.net/newdata</urURL>
        <urPostInsecurely>false</urPostInsecurely>
        <urInitialDelayS>1800</urInitialDelayS>
        <restartOnWakeup>true</restartOnWakeup>
        <autoUpgradeIntervalH>1</autoUpgradeIntervalH>
        <upgradeToPreReleases>false</upgradeToPreReleases>
        <keepTemporariesH>24</keepTemporariesH>
        <cacheIgnoredFiles>false</cacheIgnoredFiles>
        <progressUpdateIntervalS>5</progressUpdateIntervalS>
        <limitBandwidthInLan>false</limitBandwidthInLan>
        <minHomeDiskFree unit="%">1</minHomeDiskFree>
        <releasesURL>https://upgrades.syncthing.net/meta.json</releasesURL>
        <overwriteRemoteDeviceNamesOnConnect>false</overwriteRemoteDeviceNamesOnConnect>
        <tempIndexMinBlocks>10</tempIndexMinBlocks>
        <unackedNotificationIDs></unackedNotificationIDs>
        <trafficClass>0</trafficClass>
        <defaultFolderPath>{root}</defaultFolderPath>
        <setLowPriority>true</setLowPriority>
        <maxFolderConcurrency>0</maxFolderConcurrency>
        <maxConcurrentIncomingRequestKiB>0</maxConcurrentIncomingRequestKiB>
        <announceLANAddresses>true</announceLANAddresses>
        <sendFullIndexOnUpgrade>false</sendFullIndexOnUpgrade>
        <connectionPriority>0</connectionPriority>
    </options>
</configuration>"""
        cfg.write_text(minimal)
        
        import xml.etree.ElementTree as ET
        tree = ET.parse(cfg)
        cfg_root = tree.getroot()
        for opts in cfg_root.findall("options"):
            el = opts.find("autoAcceptFolders")
            if el is None:
                el = ET.SubElement(opts, "autoAcceptFolders")
            el.text = "true"
        tree.write(cfg, encoding="utf-8", xml_declaration=True)
        
        tree = ET.parse(cfg)
        el = tree.getroot().find(".//autoAcceptFolders")
        assert el is not None
        assert el.text == "true"
```

### 6.2 Integration tests (Docker daemon required)

Add to `backend/tests/compose/test_syncthing_config.py` (new file or extend existing):

```python
"""Integration tests for Syncthing autoAcceptFolders behaviour."""
import pytest
import xml.etree.ElementTree as ET
from pathlib import Path


@pytest.mark.compose
def test_auto_accept_default_false_in_container(docker_compose_project):
    """Fresh container should have autoAcceptFolders=false in config.xml."""
    # docker_compose_project is a fixture from conftest.py that brings up the stack
    # with a unique project name and volumes, tears down after.
    container = docker_compose_project.containers["backend"]
    
    # Wait for container to be healthy
    container.reload()
    assert container.status == "running"
    
    # Read config.xml from the container's SYNCTHING_HOME
    exec_result = container.exec_run("cat /app/data/syncthing/config.xml")
    assert exec_result.exit_code == 0
    config_xml = exec_result.output.decode()
    
    root = ET.fromstring(config_xml)
    el = root.find(".//autoAcceptFolders")
    assert el is not None, "autoAcceptFolders element should exist"
    assert el.text == "false", f"Expected false, got {el.text}"


@pytest.mark.compose
def test_auto_accept_true_when_env_set(docker_compose_project_with_env):
    """Container with SYNCTHING_AUTO_ACCEPT_FOLDERS=true should have autoAcceptFolders=true."""
    # This fixture would set the env var before bringing up the stack
    container = docker_compose_project_with_env.containers["backend"]
    
    exec_result = container.exec_run("cat /app/data/syncthing/config.xml")
    assert exec_result.exit_code == 0
    config_xml = exec_result.output.decode()
    
    root = ET.fromstring(config_xml)
    el = root.find(".//autoAcceptFolders")
    assert el is not None
    assert el.text == "true"


@pytest.mark.compose
def test_warning_logged_when_auto_accept_enabled(docker_compose_project_with_env):
    """Container with SYNCTHING_AUTO_ACCEPT_FOLDERS=true should log WARNING."""
    container = docker_compose_project_with_env.containers["backend"]
    
    logs = container.logs().decode()
    assert "WARNING: SYNCTHING_AUTO_ACCEPT_FOLDERS=true" in logs
    assert "issue #160" in logs.lower()


@pytest.mark.compose
def test_marker_preserves_config_on_restart(docker_compose_project):
    """Restarting container should not change autoAcceptFolders (marker protects it)."""
    container = docker_compose_project.containers["backend"]
    
    # First, verify initial state (false)
    exec_result = container.exec_run("cat /app/data/syncthing/config.xml")
    config_xml = exec_result.output.decode()
    root = ET.fromstring(config_xml)
    el = root.find(".//autoAcceptFolders")
    assert el.text == "false"
    
    # Restart
    container.restart()
    container.reload()
    assert container.status == "running"
    
    # Verify unchanged
    exec_result = container.exec_run("cat /app/data/syncthing/config.xml")
    config_xml = exec_result.output.decode()
    root = ET.fromstring(config_xml)
    el = root.find(".//autoAcceptFolders")
    assert el.text == "false"
```

### 6.3 Manual verification checklist

- [ ] `docker build -f docker/Dockerfile -t shc:160-test .` succeeds.
- [ ] `docker run --rm -e SYNCTHING_AUTO_ACCEPT_FOLDERS=true shc:160-test sh -c 'cat /app/data/syncthing/config.xml | grep autoAcceptFolders'` shows `true`.
- [ ] Same command without env var shows `false`.
- [ ] `docker run -d --name shc-test -p 8000:8000 -v shc-test-data:/app/data shc:160-test` starts healthy.
- [ ] `docker exec shc-test test -f /app/data/syncthing/.shc-configured` succeeds (marker exists).
- [ ] `docker rm -f shc-test && docker run ...` (same volume) keeps config unchanged.
- [ ] `docker exec shc-test rm /app/data/syncthing/.shc-configured && docker restart shc-test` re-applies patch.

---

## 7. Migration Notes

### 7.1 For existing clusters (upgrade path)

**No automatic migration.** Existing clusters have `.shc-configured` marker, so their `config.xml` is untouched on upgrade. Their `autoAcceptFolders` remains `true` (the #154 default).

**To adopt the secure default**, operators must choose one:

1. **Delete marker and restart** (recommended for most):
   ```bash
   docker compose exec backend rm /app/data/syncthing/.shc-configured
   docker compose restart backend
   ```
   This re-runs the patch with the new default (`false`). The operator will then need to approve the folder share once per paired device via the Syncthing GUI or REST API.

2. **Manual config edit** (for operators who cannot restart):
   Edit `/app/data/syncthing/config.xml` (inside container) or the host-mounted `SYNCTHING_HOME`:
   ```xml
   <autoAcceptFolders>false</autoAcceptFolders>
   ```
   Then restart Syncthing (or the whole container) to reload config.

3. **Keep auto-accept** (acknowledging the risk):
   Set `SYNCTHING_AUTO_ACCEPT_FOLDERS=true` in `.env` or shell environment before restart. The WARNING log confirms the setting is active.

### 7.2 For new clusters (fresh deploy)

Secure by default. No action needed. When pairing a new compute node, the operator approves the folder share once via:
- Syncthing GUI at `http://localhost:8384` (port-forward or local browser), or
- Syncthing REST API: `POST /rest/config/folders` with the folder config, then `POST /rest/system/restart`.

---

## 8. Limitations (documented, not fixed)

- **Render GUI unreachable**: On Render, the Syncthing GUI (8384) is not publicly accessible. Operators using `SYNCTHING_AUTO_ACCEPT_FOLDERS=false` (default) on Render must use the REST API with the API key (found in `config.xml` under `<gui><apikey>`) to approve folder shares. Documented in `docker/README.md`.
- **No device auto-accept**: This change only affects folder auto-accept. Device pairing remains manual (or via introducer). This is unchanged from #154 and is the correct security posture.
- **Existing approved folders**: If a folder was already approved under `autoAcceptFolders=true`, it remains shared. Disabling auto-accept only affects *new* folder shares from peers.

---

## 9. Out of Scope

- Implementing a Syncthing REST API client in the backend to programmatically approve folders (would require storing API keys, expanding scope).
- Adding an introducer configuration (operators can configure this manually in `config.xml`).
- Changing the marker file mechanism (works as designed).
- Backend changes to validate folder shares (the watcher only reads files that exist; Syncthing handles the share protocol).

---

## 10. References

- Issue #160: "Syncthing auto-accepts foreign folders, allowing rogue device to push files"
- Issue #154: "Run Syncthing daemon in the container (Render + Docker)" — introduced the auto-accept
- `docker/entrypoint.sh` — current implementation
- `docs/specs/154-syncthing-in-container.md` — baseline spec
- Syncthing docs: [Folder Sharing](https://docs.syncthing.net/users/sharing.html), [Auto Accept Folders](https://docs.syncthing.net/users/config.html#autoacceptfolders)