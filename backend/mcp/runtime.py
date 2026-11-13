"""Runtime globals for MCP transports (e.g. Syncthing service instance)."""

_syncthing_service = None


def set_syncthing_service(svc):
    global _syncthing_service
    _syncthing_service = svc


def get_syncthing_service():
    return _syncthing_service
