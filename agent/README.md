# Worker Agent

The worker agent runs on each compute node. It is **file-only**: it never opens a
network connection to the backend and holds no credential. It reads the Syncthing
folder, claims pending jobs, executes them, and writes terminal state back.

See the root `README.md` §"Worker Agent (on each compute node)" for the full
configuration reference (flags, `nodes/agent.toml`, heartbeat contract, shell
selection). This file covers installation and deployment.

## Prerequisites

1. **Syncthing** installed on the node and syncing the shared folder with the
   backend. Set `SYNCTHING_ROOT` to the synced folder (not the Syncthing home).
   There is no default; a wrong or unset value silently produces an empty
   cluster. See root `README.md` §"Environment & Auth".
2. **Python 3.11+** with the project installed (`pip install -e .`).
3. **`SHARED_TOKEN`** must be set in the backend environment if you use the
   shared-token auth path. The backend does **not** auto-generate it (issue #64);
   the localhost bypass (`localhost-no-auth`) is what the e2e suite relies on.

## Installation

```bash
git clone https://github.com/AlZrSe/sci-home-cluster.git
cd sci-home-cluster
python -m venv .venv
.venv/bin/pip install -e ".[dev]"   # or pip install -e . for runtime only
```

## Running

Always use the installed console script. It works from any working directory:

```bash
export SYNCTHING_ROOT=/path/to/syncthing-shared
export NODE_ID=node-01
run-agent --node-id node-01
```

Do **not** use `cd agent && python run_agent.py`: that runs the file as a
top-level script and `import shared.*` only resolves by accident of the working
directory (the same class of bug as issues #34 and #58).

Flags: `--node-id` (required, also `$NODE_ID`), `--syncthing-root`
(optional, wins over `$SYNCTHING_ROOT`), `--log-level`.

Exit codes: `0` clean shutdown, `1` configuration error, `2` internal error,
`3` watcher liveness lost, `4` shutdown grace expired.

## What the agent writes into `SYNCTHING_ROOT`

```
<root>/
├── nodes/<node_id>.yaml          # node spec + heartbeat + current_job_id
└── jobs/<job_id>/
    ├── state.yaml                # claim + terminal state (agent-owned fields)
    ├── commands/                 # backend-written, append-only (issue #106)
    ├── acks/                    # agent-written receipts
    └── logs/                    # stdout/stderr capture (issue #101)
```

Deleting `state.yaml` deletes the job from the backend's view (issue #92). The
agent never deletes command files (issue #106 D5).

## Running as a service (systemd)

A unit file is provided at `agent/scientific-home-cluster-agent.service`. Install
it:

```bash
sudo cp agent/scientific-home-cluster-agent.service /etc/systemd/system/
sudo mkdir -p /etc/scientific-home-cluster
sudo cp agent/agent.env.example /etc/scientific-home-cluster/agent.env
# edit /etc/scientific-home-cluster/agent.env
sudo systemctl daemon-reload
sudo systemctl enable --now scientific-home-cluster-agent
```

The unit uses `Restart=on-failure`, an `EnvironmentFile`, and a
`WorkingDirectory` that is **not** `agent/`. It runs as an unprivileged user
(see below).

### Run-as user decision

The agent executes arbitrary submitted `JobSpec.command` strings. Running it as
`root` would make every submitted job root, which is unacceptable on a shared
cluster. The unit therefore runs as a dedicated `shc-agent` user with no sudo.
Operators who need privileged jobs must arrange that through the job's own
mechanism (e.g. `sudo` with a scoped sudoers entry), not by running the agent
as root. This is stated plainly because the agent will run whatever it is told to.

### Credentials boundary

Everything in the agent's environment is inherited by every process it spawns
unless the job's environment policy strips it. Keep `SHARED_TOKEN` and any
backend credential out of the agent's environment; the agent holds no credential
by design and only needs `SYNCTHING_ROOT`, `NODE_ID`, and optionally `AGENT_*`.

## Required vs optional environment variables

| Variable | Required | Notes |
|---|---|---|
| `SYNCTHING_ROOT` | yes | synced folder; dangerous default `/tmp/syncthing` in backend config |
| `NODE_ID` | yes | unique per node |
| `AGENT_LOG_LEVEL` | no | default INFO |
| `AGENT_SHELL` | no | default `auto` |
| `AGENT_SHUTDOWN_GRACE_S` | no | default 10s |
| `SHARED_TOKEN` | no | agent holds no credential; only backend uses it |

## Troubleshooting

- **Agent up but node not appearing:** check `SYNCTHING_ROOT` points at the
  synced folder and that Syncthing has finished the initial sync. The agent
  writes `nodes/<id>.yaml`; if it is absent, the folder is wrong or unwritable.
- **Node OFFLINE:** heartbeat older than `HEARTBEAT_TIMEOUT_SECONDS` (default 90s).
  Check the agent process and the folder sync.
- **Job stuck RUNNING:** the process may have ignored SIGTERM; the agent
  escalates to SIGKILL after the grace period. Check agent logs for
  `tree_survived`.
- **No logs:** log capture is issue #101; until then stdout/stderr go to the
  agent's own stdout (capture in the service journal).
- **No metrics:** metrics sampling is issue #102.

## Transport

The agent is file-only over the Syncthing folder (issue #92). This may change as
issues #99–#106 land; the command-file protocol (issue #106) is the current
delivery mechanism for operator intent (cancel). There is no HTTP between agent
and backend at any point.
"""