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

# Directories already exist with correct ownership (created in Dockerfile / entrypoint-root.sh)
# mkdir -p kept for defense-in-depth (handles edge cases where volume mount is empty)
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

# Background daemon. Logs go to the persisted home for diagnosis.
syncthing -home="$SYNCTHING_HOME" -no-browser -no-restart \
    >"$SYNCTHING_HOME/syncthing.log" 2>&1 &

exec uvicorn backend.main:app --host 0.0.0.0 --port "$PORT"
