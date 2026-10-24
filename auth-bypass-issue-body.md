# Frontend Authentication Bypass: Login Form Not Rendering on Localhost

## Problem
The login form was not rendering on localhost because the frontend was auto-setting a bypass token (`localhost-no-auth`) in localStorage, which caused the Shell component to redirect authenticated users away from the login page before it could render.

## Root Cause
In `frontend/src/lib/settings.ts`, the `getSettings()` function was auto-injecting the bypass token:
```typescript
if (!cache.token && isLocalhost()) cache = { ...cache, token: LOCAL_TOKEN };
```

This meant:
1. User visits `/login` on localhost
2. `getSettings()` runs, sees no token, detects localhost, injects `LOCAL_TOKEN`
3. Shell component sees token exists, redirects to `/`
4. Login form never renders

## Solution Implemented
1. **Removed frontend auto-bypass**: Modified `frontend/src/lib/settings.ts` to NOT auto-inject the bypass token
2. **Backend handles bypass**: The backend `/auth/verify` and `/auth/validate` endpoints now check for the specific bypass token (`localhost-no-auth`) and localhost host
3. **Login form renders**: Users on localhost can now see and use the login form

## Changes Made
1. `frontend/src/lib/settings.ts`: Removed auto-bypass token injection
2. `frontend/src/routes/login.tsx`: Added check to only redirect if token exists AND is not the bypass token
3. `backend/api/v1/auth.py`: Added localhost bypass logic to `/verify` and `/validate` endpoints
4. `frontend/src/components/layout/Shell.tsx`: Added `shc.loggedOut` flag to prevent redirect loops on logout
5. `frontend/src/routes/settings.tsx`: Sign out now sets `shc.loggedOut` flag
6. `frontend/src/routes/login.tsx`: Clears `shc.loggedOut` flag on login
7. `frontend/tests/e2e/test-utils.ts`: Clears `shc.loggedOut` flag in `resetAuthState()`

## Testing
- e2e tests should now be able to see and interact with the login form on localhost
- Backend tests should verify the bypass works correctly for `localhost-no-auth` token
- Frontend tests should verify login form renders on localhost

## Related Files
- `frontend/src/lib/settings.ts`
- `frontend/src/routes/login.tsx`
- `frontend/src/components/layout/Shell.tsx`
- `frontend/src/routes/settings.tsx`
- `frontend/tests/e2e/test-utils.ts`
- `backend/api/v1/auth.py`
- `backend/core/utils.py` (is_localhost function)
- `backend/core/config.py` (LOCALHOST_BYPASS setting)

## Verification
Run e2e tests against localhost to verify login form renders and authentication works.