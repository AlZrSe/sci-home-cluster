"""
Cookie-based authentication utilities.

Handles setting, clearing, and validating signed auth cookies.
"""

import hmac
import logging
from typing import Optional

from fastapi import Request, Response

from backend.core.config import settings
from backend.core.utils import is_localhost

logger = logging.getLogger(__name__)


def _sign_cookie_value(value: str, secret: str) -> str:
    """Sign a cookie value with HMAC-SHA256."""
    signature = hmac.new(
        secret.encode("utf-8"), value.encode("utf-8"), "sha256"
    ).hexdigest()
    return f"{value}.{signature}"


def _verify_cookie_value(signed_value: str, secret: str) -> Optional[str]:
    """Verify and extract a signed cookie value.

    Returns the value if valid, None otherwise.
    """
    try:
        value, signature = signed_value.rsplit(".", 1)
        expected_signature = hmac.new(
            secret.encode("utf-8"), value.encode("utf-8"), "sha256"
        ).hexdigest()
        if hmac.compare_digest(signature, expected_signature):
            return value
    except (ValueError, AttributeError):
        pass
    return None


def get_cookie_options(
    is_localhost_request: bool,
) -> dict:
    """
    Get cookie options based on whether the request is from localhost.

    On localhost: Secure=False, SameSite=Lax
    On production: Secure=True, SameSite=Strict
    """
    if is_localhost_request:
        return {
            "secure": False,
            "samesite": "lax",
        }
    return {
        "secure": settings.AUTH_COOKIE_SECURE,
        "samesite": settings.AUTH_COOKIE_SAME_SITE.lower(),
    }


def set_auth_cookie(
    response: Response,
    token: str,
    request: Optional[Request] = None,
) -> None:
    """
    Set the authentication cookie on the response.

    Args:
        response: FastAPI Response object
        token: The JWT token to store in the cookie
        request: Optional Request object to detect localhost
    """
    # Detect if this is a localhost request
    is_localhost_request = False
    if request is not None:
        hostname = request.url.hostname
        is_localhost_request = is_localhost(hostname)

    options = get_cookie_options(is_localhost_request)

    # Sign the token with the cookie secret
    # AUTH_COOKIE_SECRET is guaranteed to be set in model_post_init
    cookie_secret = settings.AUTH_COOKIE_SECRET or ""
    signed_token = _sign_cookie_value(token, cookie_secret)

    response.set_cookie(
        key=settings.AUTH_COOKIE_NAME,
        value=signed_token,
        httponly=True,
        path="/",
        max_age=settings.AUTH_COOKIE_MAX_AGE_SECONDS,
        secure=options["secure"],
        samesite=options["samesite"],
    )

    logger.debug(
        "Auth cookie set: name=%s, httponly=%s, secure=%s, samesite=%s, max_age=%s",
        settings.AUTH_COOKIE_NAME,
        True,
        options["secure"],
        options["samesite"],
        settings.AUTH_COOKIE_MAX_AGE_SECONDS,
    )


def clear_auth_cookie(response: Response, request: Optional[Request] = None) -> None:
    """
    Clear the authentication cookie by setting max_age=0.

    Args:
        response: FastAPI Response object
        request: Optional Request object to detect localhost (for consistent options)
    """
    is_localhost_request = False
    if request is not None:
        hostname = request.url.hostname
        is_localhost_request = is_localhost(hostname)

    options = get_cookie_options(is_localhost_request)

    response.set_cookie(
        key=settings.AUTH_COOKIE_NAME,
        value="",
        httponly=True,
        path="/",
        max_age=0,
        secure=options["secure"],
        samesite=options["samesite"],
    )

    logger.debug("Auth cookie cleared: name=%s", settings.AUTH_COOKIE_NAME)


def get_token_from_cookie(request: Request) -> Optional[str]:
    """
    Extract and validate the authentication token from the cookie.

    Args:
        request: FastAPI Request object

    Returns:
        The validated JWT token if present and valid, None otherwise
    """
    signed_token = request.cookies.get(settings.AUTH_COOKIE_NAME)
    if not signed_token:
        return None

    # AUTH_COOKIE_SECRET is guaranteed to be set in model_post_init
    cookie_secret = settings.AUTH_COOKIE_SECRET or ""
    token = _verify_cookie_value(signed_token, cookie_secret)
    if token is None:
        logger.debug("Invalid or tampered auth cookie")
        return None

    return token
