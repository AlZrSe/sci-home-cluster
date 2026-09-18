
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from backend.core.config import settings
from backend.core.security import decode_access_token
from backend.core.utils import is_localhost

reusable_oauth2 = HTTPBearer(
    schemeName='Authorization',
    description='Enter your bearer token in the format **Bearer <token>**',
    auto_error=False,
)

async def get_current_token_payload(
    request: Request,
    token: HTTPAuthorizationCredentials = Depends(reusable_oauth2),
) -> dict:
    '''
    Validate the token and return the payload.
    If LOCALHOST_BYPASS is True and the host is local, return a fake payload.
    '''
    # Check if we are in localhost bypass mode
    if settings.LOCALHOST_BYPASS:
        # Get the hostname from the request
        hostname = request.url.hostname
        if is_localhost(hostname):
            # Bypass authentication: return a fixed payload
            return {'sub': 'localhost_user'}

    # If not bypassing, then we require a token
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Not authenticated',
            headers={'WWW-Authenticate': 'Bearer'},
        )
    
    try:
        payload = decode_access_token(token.credentials)
        return payload
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Could not validate credentials',
            headers={'WWW-Authenticate': 'Bearer'},
        )

