# On-Call Engineer

Polls the observability alert API once per minute. When an alert fires, its
details are passed to a headless coding agent for investigation.

## Run

```bash
cd on-call-engineer
python poll_alerts.py
```

Configuration is via environment variables; see the module docstring in
`poll_alerts.py`.
