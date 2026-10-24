"""Re-export of backend.core.telemetry for alternate import paths."""

from backend.core.telemetry import *  # noqa: F401,F403
from backend.core.telemetry import (  # noqa: F401
    configure_telemetry,
    setup_telemetry,
    init_telemetry,
    initialize_telemetry,
    instrument_telemetry,
    setup_opentelemetry,
    configure_opentelemetry,
    init_opentelemetry,
    instrument_app,
    get_tracer,
    build_resource,
    get_resource,
    create_resource,
    resolve_service_name,
    resolve_environment,
    resolve_version,
    OTEL_AVAILABLE,
)
