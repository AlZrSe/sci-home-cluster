# Specification: Self-Explanatory Error Messages for Frontend and Backend

## Overview
This specification defines improvements to error handling across the Scientific Home Cluster platform. The goal is to provide consistent, actionable error messages that help users understand what went wrong and how to fix it.

---

## 1. Backend Error Response Improvements

### 1.1 Enhanced ErrorResponse Model

**Current State:** `ErrorResponse` has `status`, `title`, `detail`, `instance` fields.

**Required Changes:** Add `error_code` field for programmatic error handling.

```python
# backend/models/error_response.py
class ErrorResponse(BaseModel):
    status: int = Field(..., ge=400, le=599)
    title: str
    detail: str
    instance: str
    error_code: str  # NEW: Machine-readable error code
```

**Error Code Format:** `CATEGORY_SUBCATEGORY` (e.g., `JOB_NOT_FOUND`, `VALIDATION_FAILED`, `AUTH_TOKEN_EXPIRED`, `NODE_OFFLINE`, `SYNCTHING_UNAVAILABLE`)

### 1.2 Standardized Error Codes

| HTTP Status | Error Code | Title | When to Use |
|-------------|------------|-------|-------------|
| 400 | `VALIDATION_FAILED` | Bad Request | Request body validation fails |
| 401 | `AUTH_TOKEN_MISSING` | Unauthorized | No Authorization header |
| 401 | `AUTH_TOKEN_INVALID` | Unauthorized | Token malformed or tampered |
| 401 | `AUTH_TOKEN_EXPIRED` | Unauthorized | JWT token expired |
| 401 | `AUTH_SHARED_TOKEN_INVALID` | Unauthorized | Shared token validation failed |
| 403 | `AUTH_FORBIDDEN` | Forbidden | Valid token but insufficient permissions |
| 404 | `JOB_NOT_FOUND` | Not Found | Job ID doesn't exist |
| 404 | `NODE_NOT_FOUND` | Not Found | Node ID doesn't exist |
| 404 | `METRICS_NOT_FOUND` | Not Found | Metrics for job/node not available |
| 404 | `LOGS_NOT_FOUND` | Not Found | Logs for job not available |
| 409 | `JOB_NOT_RETRYABLE` | Conflict | Job not in FAILED/CANCELLED state |
| 409 | `JOB_NOT_CANCELLABLE` | Conflict | Job not in RUNNING/PENDING state |
| 409 | `RESOURCE_CONFLICT` | Conflict | Resource already exists / concurrency issue |
| 422 | `VALIDATION_FAILED` | Unprocessable Entity | Pydantic model validation fails |
| 500 | `INTERNAL_ERROR` | Internal Server Error | Unexpected server error |
| 503 | `SYNCTHING_UNAVAILABLE` | Service Unavailable | Syncthing not configured/running |
| 503 | `DATABASE_UNAVAILABLE` | Service Unavailable | Database connection failed |
| 503 | `SHARED_TOKEN_NOT_CONFIGURED` | Service Unavailable | Server missing shared token config |

### 1.3 Actionable Error Messages

For each error scenario, the `detail` field should include:
1. **What happened** (clear, non-technical description)
2. **Why it happened** (root cause)
3. **What to do next** (actionable suggestion)

**Examples:**

| Error Code | Current Detail | Improved Detail |
|------------|----------------|-----------------|
| `JOB_NOT_FOUND` | `Job job-123 not found` | `Job job-123 does not exist. It may have been deleted or the ID is incorrect. Check the job list and try again.` |
| `AUTH_TOKEN_EXPIRED` | `Invalid or expired token` | `Your session has expired. Please log in again to get a new access token.` |
| `VALIDATION_FAILED` | `Validation failed: command: String should have at least 3 characters` | `The job spec is invalid: command must be at least 3 characters. Provide a valid command to run (e.g., "python train.py").` |
| `JOB_NOT_RETRYABLE` | `Job job-123 not in retryable state` | `Job job-123 cannot be retried because it is currently RUNNING. Only FAILED or CANCELLED jobs can be retried.` |
| `SYNCTHING_UNAVAILABLE` | `Syncthing not configured` | `Syncthing is not configured on the server. Set the SYNCTHING_ROOT environment variable and restart the API server.` |

### 1.4 Consistent Error Format Across All Endpoints

All endpoints must use the enhanced `ErrorResponse` model. The `http_exception_handler` in `main.py` should be updated to include `error_code`.

**Implementation:**
- Update `http_exception_handler` to map status codes to error codes
- Update `validation_exception_handler` to include error code
- Update `generic_exception_handler` to include error code
- Ensure all `HTTPException` raises include `error_code` in detail or use a custom exception class

---

## 2. Frontend Error Handling Improvements

### 2.1 Parse Backend ErrorResponse in http.ts Service

**Current State:** `ServiceError` only has `status` and generic message.

**Required Changes:**
1. Parse the `ErrorResponse` from backend
2. Create enhanced error class with `error_code`, `title`, `detail`
3. Maintain backward compatibility with `ServiceError`

```typescript
// frontend/src/services/types.ts
export interface BackendErrorResponse {
  status: number;
  title: string;
  detail: string;
  instance: string;
  error_code: string;
}

export class ServiceError extends Error {
  status: number;
  error_code?: string;
  title?: string;
  detail?: string;
  
  constructor(status: number, message: string, backendError?: BackendErrorResponse) {
    super(message);
    this.name = "ServiceError";
    this.status = status;
    if (backendError) {
      this.error_code = backendError.error_code;
      this.title = backendError.title;
      this.detail = backendError.detail;
    }
  }
}
```

### 2.2 User-Friendly Error Messages with Suggestions

Create a utility function to transform backend errors into user-friendly messages:

```typescript
// frontend/src/lib/error-messages.ts
export function getUserFriendlyError(error: ServiceError): { message: string; suggestion?: string } {
  // Map error_code to user-friendly message and suggestion
  const errorMap: Record<string, { message: string; suggestion?: string }> = {
    JOB_NOT_FOUND: {
      message: "Job not found",
      suggestion: "The job may have been deleted. Check the job list and try again."
    },
    AUTH_TOKEN_EXPIRED: {
      message: "Session expired",
      suggestion: "Please log in again to continue."
    },
    // ... more mappings
  };
  
  if (error.error_code && errorMap[error.error_code]) {
    return errorMap[error.error_code];
  }
  
  // Fallback to backend detail or generic message
  return {
    message: error.detail || error.message,
    suggestion: error.status >= 500 ? "Please try again later or contact support." : undefined
  };
}
```

### 2.3 Toast Notifications for API Errors

**Current State:** Toasts only used for success messages in some places (jobs.$jobId.tsx, jobs.new.tsx, ProfileForm.tsx, login.tsx, settings.tsx).

**Required Changes:**
1. Create a centralized error handler hook: `useErrorHandler()`
2. Integrate with React Query's `onError` callbacks
3. Show toast with user-friendly message + suggestion
4. Handle different error types appropriately:
   - 401: Redirect to login (with toast)
   - 403: Show "Access denied" toast
   - 404: Show "Not found" toast with context
   - 409: Show "Conflict" toast with actionable suggestion
   - 422: Show validation errors inline (forms) + toast summary
   - 5xx: Show "Server error" toast with retry suggestion

### 2.4 Improved Error UI in Job Detail and Other Pages

**Job Detail Page (`jobs.$jobId.tsx`):**
- Current: Generic "Job {jobId} could not be loaded" message
- Improved: Show specific error (not found, unauthorized, server error) with action buttons:
  - "Back to Jobs" for 404
  - "Log in" for 401
  - "Retry" for 5xx

**Job Submission Page (`jobs.new.tsx`):**
- Current: Generic "Could not submit the job" toast
- Improved: Parse validation errors (422) and show inline field errors + toast summary

**Other Pages:**
- Nodes page: Handle node not found, metrics unavailable
- Settings page: Handle auth errors, config errors
- Login page: Already has good validation, enhance with backend error codes

---

## 3. User Stories

### US-1: Backend Returns Structured Error Codes
**As a** frontend developer  
**I want** the backend to return machine-readable error codes  
**So that** I can display context-appropriate error messages and actions

**Acceptance Criteria:**
- AC-1.1: ErrorResponse model includes `error_code` field
- AC-1.2: All HTTPException responses include error_code
- AC-1.3: Validation errors include error_code = "VALIDATION_FAILED"
- AC-1.4: 500 errors include error_code = "INTERNAL_ERROR"
- AC-1.5: Auth errors have distinct codes (MISSING, INVALID, EXPIRED)
- AC-1.6: Not found errors have resource-specific codes (JOB_NOT_FOUND, NODE_NOT_FOUND, etc.)
- AC-1.7: Conflict errors have specific codes (JOB_NOT_RETRYABLE, JOB_NOT_CANCELLABLE)
- AC-1.8: Service unavailable errors have specific codes (SYNCTHING_UNAVAILABLE, DATABASE_UNAVAILABLE)

### US-2: Frontend Parses and Displays Actionable Errors
**As a** user  
**I want** to see clear, actionable error messages when something goes wrong  
**So that** I can understand the problem and know how to fix it

**Acceptance Criteria:**
- AC-2.1: ServiceError class includes error_code, title, detail from backend
- AC-2.2: http.ts service parses ErrorResponse and creates enhanced ServiceError
- AC-2.3: Mock service also returns compatible error structure
- AC-2.4: Error message utility maps error_codes to user-friendly messages with suggestions

### US-3: Toast Notifications for API Errors
**As a** user  
**I want** to be notified of API errors via toast notifications  
**So that** I'm aware of failures without needing to check console

**Acceptance Criteria:**
- AC-3.1: Centralized error handler hook created (useErrorHandler)
- AC-3.2: React Query mutations use onError to show toast
- AC-3.3: React Query queries show toast on error (with deduplication)
- AC-3.4: 401 errors show toast and redirect to login
- AC-3.5: 403 errors show "Access denied" toast
- AC-3.6: 404 errors show contextual "Not found" toast
- AC-3.7: 409 errors show conflict toast with actionable suggestion
- AC-3.8: 422 errors show validation toast + inline field errors for forms
- AC-3.9: 5xx errors show "Server error" toast with retry suggestion

### US-4: Improved Error UI in Key Pages
**As a** user  
**I want** contextual error displays in job detail and submission pages  
**So that** I can recover from errors without leaving the page

**Acceptance Criteria:**
- AC-4.1: Job detail page shows specific error state with appropriate actions
- AC-4.2: Job submission page shows inline validation errors from 422 responses
- AC-4.3: Nodes page handles not found and metrics errors gracefully
- AC-4.4: All error states include "Retry" or "Go back" actions where appropriate

---

## 4. Test Scenarios

### Backend Tests

| Test ID | Scenario | Expected Result |
|---------|----------|-----------------|
| BE-01 | GET /jobs/invalid-id | 404, error_code=JOB_NOT_FOUND, actionable detail |
| BE-02 | POST /jobs with invalid spec | 422, error_code=VALIDATION_FAILED, field-specific details |
| BE-03 | Request without Authorization header | 401, error_code=AUTH_TOKEN_MISSING |
| BE-04 | Request with expired JWT | 401, error_code=AUTH_TOKEN_EXPIRED |
| BE-05 | POST /jobs/{id}/retry on RUNNING job | 409, error_code=JOB_NOT_RETRYABLE, actionable detail |
| BE-06 | POST /jobs/{id}/cancel on COMPLETED job | 409, error_code=JOB_NOT_CANCELLABLE, actionable detail |
| BE-07 | GET /nodes/invalid-id | 404, error_code=NODE_NOT_FOUND |
| BE-08 | POST /auth/token with invalid shared token | 401, error_code=AUTH_SHARED_TOKEN_INVALID |
| BE-09 | Syncthing not configured, call /syncthing/status | 503, error_code=SYNCTHING_UNAVAILABLE |
| BE-10 | All error responses include error_code, status, title, detail, instance |

### Frontend Tests

| Test ID | Scenario | Expected Result |
|---------|----------|-----------------|
| FE-01 | httpService.getJob("invalid") throws ServiceError with error_code | ServiceError has error_code="JOB_NOT_FOUND" |
| FE-02 | httpService.createJob(invalidSpec) throws ServiceError with validation errors | ServiceError has error_code="VALIDATION_FAILED", detail includes field errors |
| FE-03 | Mock service errors have compatible structure | Mock ServiceError has error_code property |
| FE-04 | useErrorHandler shows toast for 404 | Toast appears with "Job not found" + suggestion |
| FE-05 | useErrorHandler shows toast for 401 and redirects | Toast appears + navigation to /login |
| FE-06 | useErrorHandler shows toast for 500 | Toast appears with "Server error" + retry suggestion |
| FE-06 | Job detail page shows error state for 404 | "Job not found" message + "Back to Jobs" button |
| FE-07 | Job detail page shows error state for 500 | "Server error" message + "Retry" button |
| FE-08 | Job submission page shows inline errors for 422 | Field errors displayed + summary toast |

### Integration Tests

| Test ID | Scenario | Expected Result |
|---------|----------|-----------------|
| INT-01 | Full flow: submit invalid job spec → see validation errors | Form shows field errors, toast shows summary |
| INT-02 | Full flow: access job detail for deleted job → see not found | Error state with back button |
| INT-03 | Full flow: token expires during session → redirected to login | Toast + redirect to login page |
| INT-04 | Full flow: retry non-retryable job → see conflict error | Toast with "Only failed/cancelled jobs can be retried" |

---

## 5. Definition of Done

- [ ] Backend ErrorResponse model updated with error_code field
- [ ] All exception handlers in main.py updated to include error_code
- [ ] All HTTPException raises use consistent error codes
- [ ] OpenAPI spec updated with error_code in ErrorResponse schema
- [ ] Frontend ServiceError class enhanced with error_code, title, detail
- [ ] http.ts service parses backend ErrorResponse correctly
- [ ] Mock service updated for compatibility
- [ ] Error message utility (error-messages.ts) created with all mappings
- [ ] useErrorHandler hook created and integrated
- [ ] All React Query mutations use onError for toasts
- [ ] All React Query queries handle errors with toasts (deduplicated)
- [ ] Job detail page improved error states
- [ ] Job submission page shows inline validation errors
- [ ] Unit tests for backend error responses pass
- [ ] Unit tests for frontend error parsing pass
- [ ] Integration tests for error flows pass
- [ ] No lint/type errors in backend or frontend
- [ ] PM acceptance review complete

---

## 6. Implementation Priority

1. **Phase 1 (Backend):** Update ErrorResponse model, exception handlers, and all HTTPException raises
2. **Phase 2 (Frontend Core):** Update ServiceError, http.ts parsing, mock service, error-messages utility
3. **Phase 3 (Frontend UI):** useErrorHandler hook, integrate with React Query, toast notifications
4. **Phase 4 (Page Improvements):** Job detail, job submission, nodes pages error states
5. **Phase 5 (Testing):** Unit and integration tests for all error scenarios