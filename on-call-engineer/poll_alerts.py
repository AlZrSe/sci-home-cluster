#!/usr/bin/env python3
"""Poll the observability alert API and dispatch firing alerts to a coding agent.

Runs as a long-lived process, polling every minute (configurable). When an
alert fires, its details are handed to a headless coding agent so it can
investigate and propose a fix.

Environment variables:
  ALERT_API_URL        Alert API endpoint (default: Grafana Alertmanager v2)
  GRAFANA_API_TOKEN    Bearer token for the alert API (optional)
  POLL_INTERVAL_SECONDS  Polling interval in seconds (default: 60)
  CODING_AGENT_COMMAND  Command to invoke for a firing alert (default: "opencode run")
  DRY_RUN              If set, print the alert instead of invoking the agent
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("on-call-engineer")

DEFAULT_ALERT_API_URL = (
    "http://localhost:3000/api/alertmanager/grafana/api/v2/alerts?active=true"
)
DEFAULT_AGENT_COMMAND = "opencode run"


def fetch_alerts(url: str, token: str | None) -> list[Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))
    if isinstance(data, list):
        return list(data)
    if isinstance(data, dict):
        # Support a few common envelope shapes.
        for key in ("alerts", "data", "items"):
            if isinstance(data.get(key), list):
                return list(data[key])
    return []


def alert_fingerprint(alert: dict[str, Any]) -> str:
    return str(
        alert.get("fingerprint") or alert.get("id") or json.dumps(alert, sort_keys=True)
    )


def is_firing(alert: dict[str, Any]) -> bool:
    status = alert.get("status")
    if isinstance(status, dict):
        state = str(status.get("state", "")).lower()
    else:
        state = str(status or alert.get("state") or "").lower()
    return state in {"active", "firing", "alerting"}


def invoke_agent(command: str, alert: dict[str, Any]) -> bool:
    payload = json.dumps(alert, indent=2, sort_keys=True)
    if os.getenv("DRY_RUN"):
        logger.info("DRY_RUN: would invoke %s with alert:\n%s", command, payload)
        return True
    try:
        args = shlex.split(command)
    except ValueError as exc:
        logger.error("Invalid CODING_AGENT_COMMAND %r: %s", command, exc)
        return False
    if not args:
        logger.error("CODING_AGENT_COMMAND is empty")
        return False
    try:
        result = subprocess.run(
            args,
            input=payload,
            text=True,
            capture_output=True,
            timeout=int(os.getenv("AGENT_TIMEOUT_SECONDS", "600")),
        )
    except FileNotFoundError:
        logger.error("Coding agent command not found: %s", args[0])
        return False
    except subprocess.TimeoutExpired:
        logger.error("Coding agent timed out")
        return False
    if result.returncode != 0:
        logger.error(
            "Coding agent exited %d: %s", result.returncode, result.stderr.strip()
        )
        return False
    logger.info("Coding agent completed successfully")
    if result.stdout.strip():
        logger.debug("Agent output: %s", result.stdout.strip())
    return True


def main() -> int:
    url = os.getenv("ALERT_API_URL", DEFAULT_ALERT_API_URL)
    token = os.getenv("GRAFANA_API_TOKEN") or os.getenv("ALERT_API_TOKEN")
    interval = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
    command = os.getenv("CODING_AGENT_COMMAND", DEFAULT_AGENT_COMMAND)
    handled: set[str] = set()

    logger.info("Polling %s every %ds", url, interval)
    logger.info("Coding agent command: %s", command)

    while True:
        try:
            alerts = fetch_alerts(url, token)
        except urllib.error.URLError as exc:
            logger.warning("Failed to fetch alerts: %s", exc)
            alerts = []
        except Exception as exc:  # pragma: no cover
            logger.exception("Unexpected error fetching alerts: %s", exc)
            alerts = []

        for alert in alerts:
            if not is_firing(alert):
                continue
            fingerprint = alert_fingerprint(alert)
            if fingerprint in handled:
                continue
            logger.warning("Firing alert: %s", fingerprint)
            if invoke_agent(command, alert):
                handled.add(fingerprint)

        time.sleep(interval)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        logger.info("Shutting down")
        raise SystemExit(0)
