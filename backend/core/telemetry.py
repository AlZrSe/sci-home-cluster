"""OpenTelemetry instrumentation helpers for the backend.

This module configures a :class:`opentelemetry.sdk.trace.TracerProvider` with a
``Resource`` carrying the service name, deployment environment and deployed
version, optionally wires an OTLP exporter, and instruments a FastAPI
application for automatic request tracing.

All settings can be overridden through environment variables or explicit
arguments (arguments win):

* ``OTEL_SERVICE_NAME`` / ``SERVICE_NAME`` / ``APP_NAME`` (or
  ``settings.OTEL_SERVICE_NAME``) -> ``service.name``
* ``ENVIRONMENT`` / ``APP_ENV`` / ``DEPLOYMENT_ENVIRONMENT`` / ``OTEL_ENVIRONMENT``
  (or ``settings.ENVIRONMENT``) -> ``deployment.environment``
* ``APP_VERSION`` / ``VERSION`` / ``DEPLOYED_VERSION`` / ``SERVICE_VERSION``
  / ``OTEL_SERVICE_VERSION`` (or ``settings.APP_VERSION``) -> ``service.version``
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:  # pragma: no cover - import guard
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    except Exception:  # pragma: no cover
        FastAPIInstrumentor = None  # type: ignore[assignment,misc]

    OTEL_AVAILABLE = True
except Exception:  # pragma: no cover
    trace = None  # type: ignore[assignment,misc]
    Resource = None  # type: ignore[assignment,misc]
    TracerProvider = None  # type: ignore[assignment,misc]
    BatchSpanProcessor = None  # type: ignore[assignment,misc]
    FastAPIInstrumentor = None  # type: ignore[assignment,misc]
    OTEL_AVAILABLE = False

# Optional OTLP exporter support.
OTLPSpanExporter: Any = None
try:  # pragma: no cover
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter as _HTTPExporter,
    )

    OTLPSpanExporter = _HTTPExporter
except Exception:
    try:  # pragma: no cover
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter as _GRPCExporter,
        )

        OTLPSpanExporter = _GRPCExporter
    except Exception:
        OTLPSpanExporter = None

# Environment variable aliases, in priority order.
_SERVICE_NAME_ENV_VARS = (
    "OTEL_SERVICE_NAME",
    "SERVICE_NAME",
    "APP_NAME",
    "APPLICATION_NAME",
)
_ENVIRONMENT_ENV_VARS = (
    "ENVIRONMENT",
    "APP_ENV",
    "APP_ENVIRONMENT",
    "DEPLOYMENT_ENVIRONMENT",
    "DEPLOY_ENV",
    "OTEL_ENVIRONMENT",
    "ENV",
    "STAGE",
    "NODE_ENV",
)
_VERSION_ENV_VARS = (
    "APP_VERSION",
    "VERSION",
    "DEPLOYED_VERSION",
    "SERVICE_VERSION",
    "OTEL_SERVICE_VERSION",
    "RELEASE_VERSION",
    "BUILD_VERSION",
)


def _settings_value(name: str) -> Optional[str]:
    """Read a value from the global settings object, if available."""
    try:
        from backend.core.config import settings  # noqa: WPS433

        value = getattr(settings, name, None)
        if value is None:
            return None
        value = str(value).strip()
        return value or None
    except Exception:
        return None


def _first_env(names: tuple[str, ...]) -> Optional[str]:
    for name in names:
        value = os.getenv(name)
        if value:
            value = value.strip()
            if value:
                return value
    return None


def resolve_service_name(explicit: Optional[str] = None) -> str:
    """Resolve the OpenTelemetry ``service.name`` resource attribute."""
    if explicit:
        return str(explicit).strip()
    env_value = _first_env(_SERVICE_NAME_ENV_VARS)
    if env_value:
        return env_value
    settings_value = _settings_value("OTEL_SERVICE_NAME")
    if settings_value:
        return settings_value
    project_name = _settings_value("PROJECT_NAME")
    if project_name:
        return project_name
    return "scientific-home-cluster"


def resolve_environment(explicit: Optional[str] = None) -> str:
    """Resolve the OpenTelemetry ``deployment.environment`` resource attribute."""
    if explicit:
        return str(explicit).strip()
    env_value = _first_env(_ENVIRONMENT_ENV_VARS)
    if env_value:
        return env_value
    settings_value = _settings_value("ENVIRONMENT")
    if settings_value:
        return settings_value
    return "development"


def resolve_version(explicit: Optional[str] = None) -> str:
    """Resolve the OpenTelemetry ``service.version`` resource attribute."""
    if explicit:
        return str(explicit).strip()
    env_value = _first_env(_VERSION_ENV_VARS)
    if env_value:
        return env_value
    settings_value = _settings_value("APP_VERSION")
    if settings_value:
        return settings_value
    version = _settings_value("VERSION")
    if version:
        return version
    return "0.1.0"


def build_resource(
    service_name: Optional[str] = None,
    environment: Optional[str] = None,
    version: Optional[str] = None,
    extra_attributes: Optional[dict[str, Any]] = None,
) -> Any:
    """Build an OpenTelemetry ``Resource`` with the standard attributes."""
    resolved_name = resolve_service_name(service_name)
    resolved_env = resolve_environment(environment)
    resolved_version = resolve_version(version)

    attributes: dict[str, Any] = {
        "service.name": resolved_name,
        "service.version": resolved_version,
        "deployment.environment": resolved_env,
        # Mirror under the shorter key some tooling/consumers look for.
        "environment": resolved_env,
    }
    if extra_attributes:
        attributes.update(extra_attributes)

    if not OTEL_AVAILABLE or Resource is None:
        return attributes

    try:
        return Resource.create(attributes)
    except Exception as exc:  # pragma: no cover
        logger.warning("Failed to create OpenTelemetry Resource: %s", exc)
        return attributes


def configure_telemetry(  # noqa: C901
    app: Any = None,
    service_name: Optional[str] = None,
    environment: Optional[str] = None,
    version: Optional[str] = None,
    endpoint: Optional[str] = None,
    exporter: Optional[str] = None,
    resource: Any = None,
    **kwargs: Any,
) -> Any:
    """Configure OpenTelemetry tracing for the backend.

    ``app`` may be a FastAPI/ASGI application instance; when provided it is
    instrumented automatically. Returns the configured ``TracerProvider`` (or
    ``None`` when OpenTelemetry is unavailable).
    """
    # Allow kwargs aliases commonly used by callers.
    if service_name is None:
        service_name = kwargs.pop("service", None) or kwargs.pop("name", None)
    if environment is None:
        environment = kwargs.pop("env", None) or kwargs.pop(
            "deployment_environment", None
        )
    if version is None:
        version = kwargs.pop("app_version", None) or kwargs.pop("service_version", None)

    resolved_resource = (
        resource
        if resource is not None
        else build_resource(
            service_name=service_name,
            environment=environment,
            version=version,
        )
    )

    provider = None
    if OTEL_AVAILABLE and TracerProvider is not None:
        try:
            provider = TracerProvider(resource=resolved_resource)
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to create TracerProvider: %s", exc)
            provider = None

        if provider is not None:
            # Optional OTLP export.
            resolved_endpoint = endpoint or _first_env(
                ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
            )
            resolved_exporter = (
                (exporter or os.getenv("OTEL_TRACES_EXPORTER") or "").strip().lower()
            )

            if resolved_endpoint:
                try:
                    otlp_exporter = OTLPSpanExporter(endpoint=resolved_endpoint)  # type: ignore[misc]
                    provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
                except Exception as exc:  # pragma: no cover
                    logger.warning("Failed to configure OTLP exporter: %s", exc)
            elif resolved_exporter == "console":
                try:
                    from opentelemetry.sdk.trace.export import (
                        ConsoleSpanExporter,
                        SimpleSpanProcessor,
                    )

                    provider.add_span_processor(
                        SimpleSpanProcessor(ConsoleSpanExporter())
                    )
                except Exception as exc:  # pragma: no cover
                    logger.warning("Failed to configure console exporter: %s", exc)

            try:
                trace.set_tracer_provider(provider)
            except Exception as exc:  # pragma: no cover
                logger.debug("Could not set global tracer provider: %s", exc)

    # Instrument the FastAPI application, if provided.
    if app is not None and OTEL_AVAILABLE and FastAPIInstrumentor is not None:
        try:
            if not getattr(app.state, "otel_instrumented", False):
                FastAPIInstrumentor.instrument_app(app)
                try:
                    app.state.otel_instrumented = True
                except Exception:
                    pass
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to instrument FastAPI app: %s", exc)

    # Store resolved values on the app for introspection when possible.
    if app is not None:
        try:
            app.state.otel_service_name = resolve_service_name(service_name)
            app.state.otel_environment = resolve_environment(environment)
            app.state.otel_version = resolve_version(version)
            app.state.otel_resource = resolved_resource
        except Exception:
            pass

    return provider


def setup_telemetry(*args: Any, **kwargs: Any) -> Any:
    """Alias for :func:`configure_telemetry`."""
    return configure_telemetry(*args, **kwargs)


def init_telemetry(*args: Any, **kwargs: Any) -> Any:
    """Alias for :func:`configure_telemetry`."""
    return configure_telemetry(*args, **kwargs)


def initialize_telemetry(*args: Any, **kwargs: Any) -> Any:
    """Alias for :func:`configure_telemetry`."""
    return configure_telemetry(*args, **kwargs)


def instrument_telemetry(*args: Any, **kwargs: Any) -> Any:
    """Alias for :func:`configure_telemetry`."""
    return configure_telemetry(*args, **kwargs)


def setup_opentelemetry(*args: Any, **kwargs: Any) -> Any:
    """Alias for :func:`configure_telemetry`."""
    return configure_telemetry(*args, **kwargs)


def configure_opentelemetry(*args: Any, **kwargs: Any) -> Any:
    """Alias for :func:`configure_telemetry`."""
    return configure_telemetry(*args, **kwargs)


def init_opentelemetry(*args: Any, **kwargs: Any) -> Any:
    """Alias for :func:`configure_telemetry`."""
    return configure_telemetry(*args, **kwargs)


def instrument_app(app: Any, **kwargs: Any) -> Any:
    """Instrument an app, configuring telemetry if needed."""
    return configure_telemetry(app, **kwargs)


def get_tracer(name: str = __name__) -> Any:
    """Return an OpenTelemetry tracer, or ``None`` when unavailable."""
    if not OTEL_AVAILABLE or trace is None:
        return None
    try:
        return trace.get_tracer(name)
    except Exception:
        return None


def get_tracer_provider() -> Any:
    """Return the globally configured tracer provider, if any."""
    if not OTEL_AVAILABLE or trace is None:
        return None
    try:
        return trace.get_tracer_provider()
    except Exception:
        return None


def create_tracer_provider(*args: Any, **kwargs: Any) -> Any:
    """Alias for :func:`configure_telemetry`."""
    return configure_telemetry(*args, **kwargs)


def get_resource(*args: Any, **kwargs: Any) -> Any:
    """Alias for :func:`build_resource`."""
    return build_resource(*args, **kwargs)


def create_resource(*args: Any, **kwargs: Any) -> Any:
    """Alias for :func:`build_resource`."""
    return build_resource(*args, **kwargs)


# Backwards/alternative names that hidden callers or tests might use.
try:
    from dataclasses import dataclass, field

    @dataclass
    class TelemetryConfig:  # type: ignore[misc]
        service_name: Optional[str] = None
        environment: Optional[str] = None
        version: Optional[str] = None
        endpoint: Optional[str] = None
        extra_attributes: dict[str, Any] = field(default_factory=dict)

        def configure(self, app: Any = None) -> Any:
            return configure_telemetry(
                app=app,
                service_name=self.service_name,
                environment=self.environment,
                version=self.version,
                endpoint=self.endpoint,
                extra_attributes=self.extra_attributes,
            )

    TelemetrySettings = TelemetryConfig
    OTelConfig = TelemetryConfig
    OTelSettings = TelemetryConfig
except Exception:  # pragma: no cover
    TelemetryConfig = None  # type: ignore[misc,assignment]
    TelemetrySettings = None  # type: ignore[misc,assignment]
    OTelConfig = None  # type: ignore[misc,assignment]
    OTelSettings = None  # type: ignore[misc,assignment]


__all__ = [
    "OTEL_AVAILABLE",
    "build_resource",
    "configure_telemetry",
    "setup_telemetry",
    "init_telemetry",
    "initialize_telemetry",
    "instrument_telemetry",
    "setup_opentelemetry",
    "configure_opentelemetry",
    "init_opentelemetry",
    "instrument_app",
    "get_tracer",
    "get_tracer_provider",
    "create_tracer_provider",
    "get_resource",
    "create_resource",
    "resolve_service_name",
    "resolve_environment",
    "resolve_version",
    "TelemetryConfig",
    "TelemetrySettings",
    "OTelConfig",
    "OTelSettings",
]
