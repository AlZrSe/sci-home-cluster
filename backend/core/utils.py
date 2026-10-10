"""
Utility functions for the backend.
"""

from typing import Optional

# Hosts that get the localhost bypass, and the suffixes whose subdomains do.
#
# Entries are CANONICAL: lowercase, and IPv6 WITHOUT surrounding brackets.
# `request.url.hostname` in backend/core/deps.py parses the client-supplied
# `Host` header through urlparse, which strips `[` `]` - so a request over
# IPv6 loopback arrives as "::1", never as "[::1]". Listing "[::1]" here
# would make a unit test pass while the real bug stayed open, which is what
# issue #31 found. `is_localhost` normalises instead, so both spellings
# resolve and the two lists hold the same tokens one for one.
#
# The frontend keeps its own copy of this list in
# `frontend/src/lib/settings.ts`. THE TWO MUST BE CHANGED TOGETHER.
# `shared/auth/localhost_hosts.json` holds the entries both sides share and is
# the reviewable artifact; tests in both repositories assert against it, so
# drift fails CI rather than being a comment asking nicely. Production code
# on either side deliberately keeps a literal list because the frontend
# bundler cannot reach outside its own directory - see shared/auth/README.md.
LOCALHOST_EXACT_HOSTS = [
    # "localhost" - the dev default, and on both sides.
    "localhost",
    # "127.0.0.1" - IPv4 loopback, what a browser sends for 127.0.0.1:5173.
    "127.0.0.1",
    # "::1" - IPv6 loopback (RFC 4291 s2.5.3), added by issue #31 and the
    # ONLY entry this change adds. Reserved and unroutable, so no outsider
    # can present it without already being able to send "Host: localhost".
    "::1",
    # "0.0.0.0" - backend-only: the wildcard BIND address. A dev serving on
    # 0.0.0.0:8000 and browsing to 127.0.0.1:8000 may have a proxy preserve
    # the original Host. A browser reports "0.0.0.0" only if it resolved it,
    # where the failure mode is a spurious login screen, not a 401 storm.
    "0.0.0.0",
    # "testserver" - backend-only: Starlette TestClient's default Host.
    # Load-bearing for the whole TestClient-based suite; without it every
    # test request would need an Authorization header. A browser never
    # sends it.
    "testserver",
]

LOCALHOST_SUFFIXES = [
    # ".local" - mDNS, reserved by RFC 6762 and not ICANN-delegable, so it
    # cannot be minted on the public internet. On both sides.
    ".local",
    # ".lovable.app" - Lovable-owned preview hosts; already on both sides.
    ".lovable.app",
]

# Deliberately on NEITHER list: ".lovableproject.com". It is the one entry a
# third party could plausibly present in a Host header, so issue #31 dropped
# it from the frontend rather than mirroring it here.
#
# Note that this list is a developer-convenience list, NOT a security
# boundary: the bypass reads a client-supplied Host header, so any client
# that can reach the backend and set an arbitrary Host can already claim
# "localhost" and get the bypass today. Adding loopback-shaped entries
# therefore grants no capability "localhost" does not already grant.
# See docs/spec.md#authentication.


def get_settings():
    """Get application settings."""
    from backend.core.config import settings

    return settings


def is_localhost(hostname: Optional[str]) -> bool:
    """
    Check if a hostname is localhost or a local development domain.

    This is used for the localhost bypass logic where authentication
    may be bypassed for development convenience.

    The input is normalised before matching: surrounding whitespace is
    trimmed (RFC 7230 s3.2.4 lets a sender append optional whitespace to a
    field value, and a recipient ignores it), the result is lowercased, and
    one surrounding `[` ... `]` is stripped. So the browser's
    `window.location.hostname` ("[::1]") and Starlette's
    `request.url.hostname` ("::1") resolve to the same entry, which is what
    lets the frontend and backend share one set of tokens. See
    LOCALHOST_EXACT_HOSTS above.
    """
    if not hostname:
        return False

    host = hostname.strip().lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]

    return host in LOCALHOST_EXACT_HOSTS or any(
        host.endswith(suffix) for suffix in LOCALHOST_SUFFIXES
    )
