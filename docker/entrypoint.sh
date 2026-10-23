#!/usr/bin/env bash
set -euo pipefail

SYNCTHING_HOME="${SYNCTHING_HOME:-/app/data/.syncthing}"
SYNCTHING_ROOT="${SYNCTHING_ROOT:-/tmp/syncthing}"
PORT="${PORT:-8000}"

mkdir -p "$SYNCTHING_HOME" "$SYNCTHING_ROOT"

export STNOUPGRADE=1

# First run: generate identity + default config.
if [ ! -f "$SYNCTHING_HOME/config.xml" ]; then
    syncthing -generate="$SYNCTHING_HOME" >/dev/null 2>&1
fi

# First-run tuning: applied once, then a marker prevents clobbering operator
# edits on later restarts. Delete the marker to re-apply.
if [ ! -f "$SYNCTHING_HOME/.shc-configured" ] && [ -f "$SYNCTHING_HOME/config.xml" ]; then
    python3 - "$SYNCTHING_HOME/config.xml" "$SYNCTHING_ROOT" <<'PY'
import sys
import xml.etree.ElementTree as ET

cfg_path, root = sys.argv[1], sys.argv[2]
tree = ET.parse(cfg_path)
cfg = tree.getroot()

for folder in cfg.findall("folder"):
    folder.set("path", root)

for opts in cfg.findall("options"):
    el = opts.find("autoAcceptFolders")
    if el is None:
        el = ET.SubElement(opts, "autoAcceptFolders")
    el.text = "true"

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
