"""
Utility functions for the backend.
"""

def get_settings():
    """Get application settings."""
    from backend.core.config import settings
    return settings


def is_localhost(hostname: str) -> bool:
    """
    Check if a hostname is localhost or a local development domain.
    
    This is used for the localhost bypass logic where authentication
    may be bypassed for development convenience.
    """
    if not hostname:
        return False
        
    hostname = hostname.lower()
    return (
        hostname in ["localhost", "127.0.0.1", "0.0.0.0"] or
        hostname.endswith(".local") or
        hostname.endswith(".lovable.app")
    )