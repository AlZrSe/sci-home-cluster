"""Application-level OpenTelemetry metrics for the backend.

Thin wrappers around OpenTelemetry instruments. Every helper is a no-op when
OpenTelemetry is unavailable or no meter provider is configured, so callers
never have to guard on availability.
"""

from __future__ import annotations

from typing import Any, Optional

from backend.core.telemetry import get_meter

_cache: dict[str, Any] = {}


def _instrument(name: str, factory) -> Any:
    """Lazily create and cache an instrument."""
    if name in _cache:
        return _cache[name]
    meter = get_meter("scientific-home-cluster")
    instrument = None
    if meter is not None:
        try:
            instrument = factory(meter)
        except Exception:
            instrument = None
    _cache[name] = instrument
    return instrument


def _counter(name: str, description: str, unit: str = "1") -> Any:
    return _instrument(
        name, lambda m: m.create_counter(name, description=description, unit=unit)
    )


def _histogram(name: str, description: str, unit: str = "s") -> Any:
    return _instrument(
        name, lambda m: m.create_histogram(name, description=description, unit=unit)
    )


def _gauge(name: str, description: str, unit: str = "1") -> Any:
    return _instrument(
        name, lambda m: m.create_gauge(name, description=description, unit=unit)
    )


def record_http_request(
    method: str, route: str, status_code: int, duration_seconds: float
) -> None:
    counter = _counter(
        "http.server.request.count", "Number of HTTP requests handled.", "1"
    )
    if counter is not None:
        try:
            counter.add(
                1,
                attributes={
                    "http.method": method,
                    "http.route": route,
                    "http.status_code": status_code,
                },
            )
        except Exception:
            pass

    histogram = _histogram(
        "http.server.request.duration", "HTTP request duration.", "s"
    )
    if histogram is not None:
        try:
            histogram.record(
                duration_seconds,
                attributes={
                    "http.method": method,
                    "http.route": route,
                    "http.status_code": status_code,
                },
            )
        except Exception:
            pass

    if status_code >= 400:
        errors = _counter("http.server.errors", "Number of HTTP error responses.", "1")
        if errors is not None:
            try:
                errors.add(
                    1,
                    attributes={
                        "http.method": method,
                        "http.route": route,
                        "http.status_code": status_code,
                    },
                )
            except Exception:
                pass


def record_job_submitted() -> None:
    counter = _counter("jobs.submitted", "Number of jobs submitted.", "1")
    if counter is not None:
        try:
            counter.add(1)
        except Exception:
            pass


def record_job_completed(duration_seconds: Optional[float] = None) -> None:
    counter = _counter("jobs.completed", "Number of jobs completed.", "1")
    if counter is not None:
        try:
            counter.add(1)
        except Exception:
            pass
    if duration_seconds is not None:
        histogram = _histogram("jobs.duration", "Job execution duration.", "s")
        if histogram is not None:
            try:
                histogram.record(duration_seconds)
            except Exception:
                pass


def record_job_failed() -> None:
    counter = _counter("jobs.failed", "Number of jobs failed.", "1")
    if counter is not None:
        try:
            counter.add(1)
        except Exception:
            pass


def set_nodes_online(count: int) -> None:
    gauge = _gauge("nodes.online", "Number of online nodes.", "1")
    if gauge is not None:
        try:
            gauge.set(count)
        except Exception:
            pass


def set_node_heartbeat_age(node_id: str, age_seconds: float) -> None:
    gauge = _gauge(
        "nodes.heartbeat.age_seconds", "Age of the last heartbeat per node.", "s"
    )
    if gauge is not None:
        try:
            gauge.set(age_seconds, attributes={"node_id": node_id})
        except Exception:
            pass


def record_db_query(duration_seconds: float, operation: str = "query") -> None:
    histogram = _histogram("db.query.duration", "Database query duration.", "s")
    if histogram is not None:
        try:
            histogram.record(duration_seconds, attributes={"db.operation": operation})
        except Exception:
            pass


def set_syncthing_available(available: bool) -> None:
    gauge = _gauge(
        "syncthing.folder.available", "Whether the Syncthing folder is available.", "1"
    )
    if gauge is not None:
        try:
            gauge.set(1 if available else 0)
        except Exception:
            pass
