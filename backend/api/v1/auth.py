"""
Authentication API endpoints.
"""

from datetime import timedelta
from fastapi import APIRouter, Depends, Header, Request, Response
from backend.core.config import settings
from backend.core.deps import get_current_token_payload
from backend.core.errors import (
    auth_rate_limited,
    auth_shared_token_invalid,
    auth_token_expired,
    shared_token_not_configured,
)
from backend.core.rate_limit import get_rate_limiter
from shared.schemas.token_validation import (
    TokenValidationRequest,
    TokenValidationResponse,
    TokenCreateRequest,
    TokenCreateResponse,
    TokenRefreshRequest,
    TokenRefreshResponse,
)
from backend.services.auth_service import AuthService

router = APIRouter()


def _store_rate_limit_headers(request: Request, response: Response) -> None:
    """Store rate limit headers in request state for exception handler."""
    request.state.rate_limit_headers = {
        "X-RateLimit-Limit": response.headers.get("X-RateLimit-Limit"),
        "X-RateLimit-Remaining": response.headers.get("X-RateLimit-Remaining"),
        "X-RateLimit-Reset": response.headers.get("X-RateLimit-Reset"),
    }


@router.get("/verify", response_model=TokenValidationResponse)
async def verify_token(
    request: Request,
    response: Response,
    authorization: str = Header(None),
    payload: dict = Depends(get_current_token_payload),
):
    """
    Validate bearer token via GET with Authorization header.
    """
    limiter = get_rate_limiter()
    endpoint = "/api/v1/auth/verify"

    if not authorization or not authorization.startswith("Bearer "):
        # Check rate limit for invalid request
        allowed, error_detail = await limiter.check_rate_limit(
            request, response, endpoint
        )
        _store_rate_limit_headers(request, response)
        if not allowed:
            retry_after = int(response.headers.get("Retry-After", "60"))
            raise auth_rate_limited(error_detail or "Rate limited", retry_after)
        await limiter.record_failure(request, endpoint, "missing_or_malformed_header")
        return TokenValidationResponse(valid=False)

    token = authorization[7:]  # Remove "Bearer " prefix

    # Localhost bypass: if the request comes from a localhost host AND the
    # token is the localhost bypass token ("localhost-no-auth"), we consider
    # it valid. The `get_current_token_payload` dependency returns a payload
    # with `sub: "localhost_user"` for localhost hosts, but we only bypass
    # validation for the specific bypass token to avoid interfering with tests.
    if payload.get("sub") == "localhost_user" and token == "localhost-no-auth":
        await limiter.record_success(request)
        # Add rate limit headers for successful request
        await limiter.check_rate_limit(request, response, endpoint)
        _store_rate_limit_headers(request, response)
        return TokenValidationResponse(valid=True)

    auth_service = AuthService()
    is_valid = await auth_service.validate_token(token)

    if is_valid:
        await limiter.record_success(request)
        # Add rate limit headers for successful request
        await limiter.check_rate_limit(request, response, endpoint)
        _store_rate_limit_headers(request, response)
    else:
        # Check rate limit for failed request
        allowed, error_detail = await limiter.check_rate_limit(
            request, response, endpoint
        )
        _store_rate_limit_headers(request, response)
        # Always record failure to increment counter
        await limiter.record_failure(request, endpoint, "invalid_token")
        if not allowed:
            retry_after = int(response.headers.get("Retry-After", "60"))
            raise auth_rate_limited(error_detail or "Rate limited", retry_after)

    return TokenValidationResponse(valid=is_valid)


@router.post("/validate", response_model=TokenValidationResponse)
async def validate_token(
    request: Request,
    response: Response,
    token_request: TokenValidationRequest,
    payload: dict = Depends(get_current_token_payload),
):
    """
    Validate bearer token via POST with JSON body.
    """
    limiter = get_rate_limiter()
    endpoint = "/api/v1/auth/validate"

    # Localhost bypass: if the request comes from a localhost host AND the
    # token is the localhost bypass token ("localhost-no-auth"), we consider
    # it valid. The `get_current_token_payload` dependency returns a payload
    # with `sub: "localhost_user"` for localhost hosts, but we only bypass
    # validation for the specific bypass token to avoid interfering with tests.
    is_localhost = payload.get("sub") == "localhost_user"
    if is_localhost and token_request.token == "localhost-no-auth":
        await limiter.record_success(request)
        # Add rate limit headers for successful request
        await limiter.check_rate_limit(request, response, endpoint)
        _store_rate_limit_headers(request, response)
        return TokenValidationResponse(valid=True)

    auth_service = AuthService()
    is_valid = await auth_service.validate_token(token_request.token)

    if is_valid:
        await limiter.record_success(request)
        # Add rate limit headers for successful request
        await limiter.check_rate_limit(request, response, endpoint)
        _store_rate_limit_headers(request, response)
    else:
        # Check rate limit for failed request
        allowed, error_detail = await limiter.check_rate_limit(
            request, response, endpoint
        )
        _store_rate_limit_headers(request, response)
        # Always record failure to increment counter
        await limiter.record_failure(request, endpoint, "invalid_token")
        if not allowed:
            retry_after = int(response.headers.get("Retry-After", "60"))
            raise auth_rate_limited(error_detail or "Rate limited", retry_after)

    return TokenValidationResponse(valid=is_valid)


@router.post("/token", response_model=TokenCreateResponse)
async def create_token(
    request: Request,
    response: Response,
    token_request: TokenCreateRequest,
):
    """
    Create a new JWT access token by validating against shared token.

    This endpoint accepts a shared token and returns a JWT token if valid.
    """
    limiter = get_rate_limiter()
    endpoint = "/api/v1/auth/token"

    auth_service = AuthService()

    # Validate the shared token
    if not auth_service.get_shared_token():
        # Check rate limit for failed request
        allowed, error_detail = await limiter.check_rate_limit(
            request, response, endpoint
        )
        _store_rate_limit_headers(request, response)
        await limiter.record_failure(request, endpoint, "shared_token_not_configured")
        if not allowed:
            retry_after = int(response.headers.get("Retry-After", "60"))
            raise auth_rate_limited(error_detail or "Rate limited", retry_after)
        raise shared_token_not_configured(
            "Shared token is not configured on the server. "
            "Set the SHARED_TOKEN environment variable and restart the API server."
        )

    is_valid = await auth_service.verify_shared_token(token_request.shared_token)
    if not is_valid:
        # Check rate limit for failed request
        allowed, error_detail = await limiter.check_rate_limit(
            request, response, endpoint
        )
        _store_rate_limit_headers(request, response)
        # Always record failure to increment counter
        await limiter.record_failure(request, endpoint, "invalid_shared_token")
        if not allowed:
            retry_after = int(response.headers.get("Retry-After", "60"))
            raise auth_rate_limited(error_detail or "Rate limited", retry_after)
        raise auth_shared_token_invalid(
            "The provided shared token is invalid. "
            "Please check your credentials and try again."
        )

    # Create new JWT token
    expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = await auth_service.create_token(expires_delta=expires_delta)

    await limiter.record_success(request)
    # Add rate limit headers for successful request
    await limiter.check_rate_limit(request, response, endpoint)
    _store_rate_limit_headers(request, response)

    return TokenCreateResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/refresh", response_model=TokenRefreshResponse)
async def refresh_token(
    request: Request,
    response: Response,
    token_request: TokenRefreshRequest,
):
    """
    Refresh an existing JWT token.

    Returns a new token with extended expiration if the current token is valid.
    """
    limiter = get_rate_limiter()
    endpoint = "/api/v1/auth/refresh"

    auth_service = AuthService()

    new_token = await auth_service.refresh_token(token_request.token)
    if not new_token:
        # Check rate limit for failed request
        allowed, error_detail = await limiter.check_rate_limit(
            request, response, endpoint
        )
        _store_rate_limit_headers(request, response)
        # Always record failure to increment counter
        await limiter.record_failure(request, endpoint, "invalid_or_expired_token")
        if not allowed:
            retry_after = int(response.headers.get("Retry-After", "60"))
            raise auth_rate_limited(error_detail or "Rate limited", retry_after)
        raise auth_token_expired(
            "Your session has expired or the token is invalid. "
            "Please log in again to get a new access token."
        )

    await limiter.record_success(request)
    # Add rate limit headers for successful request
    await limiter.check_rate_limit(request, response, endpoint)
    _store_rate_limit_headers(request, response)

    return TokenRefreshResponse(
        access_token=new_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
