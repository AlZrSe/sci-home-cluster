"""
Explicit API errors.

`error_code` used to be derived by substring-matching the human-readable
`detail` string, which meant the machine-readable code changed whenever
the wording of a message changed - and that "job" matched any detail
containing the word "job". Codes are now attached at the raise site.
"""

from typing import Optional

from fastapi import HTTPException, status


class APIError(HTTPException):
    """
    An HTTPException that carries a stable, machine-readable error code.

    The response body shape is unchanged: the handler in backend.main
    renders these into the ErrorResponse the frontend already parses.
    """

    def __init__(
        self,
        status_code: int,
        detail: str,
        error_code: str,
        headers: Optional[dict] = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.error_code = error_code


# --- Auth -------------------------------------------------------------------


def auth_token_missing(detail: str) -> APIError:
    return APIError(
        status.HTTP_401_UNAUTHORIZED,
        detail,
        "AUTH_TOKEN_MISSING",
        headers={"WWW-Authenticate": "Bearer"},
    )


def auth_token_invalid(detail: str) -> APIError:
    return APIError(
        status.HTTP_401_UNAUTHORIZED,
        detail,
        "AUTH_TOKEN_INVALID",
        headers={"WWW-Authenticate": "Bearer"},
    )


def auth_token_expired(detail: str) -> APIError:
    return APIError(
        status.HTTP_401_UNAUTHORIZED,
        detail,
        "AUTH_TOKEN_EXPIRED",
        headers={"WWW-Authenticate": "Bearer"},
    )


def auth_shared_token_invalid(detail: str) -> APIError:
    return APIError(
        status.HTTP_401_UNAUTHORIZED,
        detail,
        "AUTH_SHARED_TOKEN_INVALID",
        headers={"WWW-Authenticate": "Bearer"},
    )


def shared_token_not_configured(detail: str) -> APIError:
    return APIError(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        detail,
        "SHARED_TOKEN_NOT_CONFIGURED",
    )


# --- Resources --------------------------------------------------------------


def job_not_found(job_id: str) -> APIError:
    return APIError(
        status.HTTP_404_NOT_FOUND,
        (
            f"Job {job_id} does not exist. "
            "It may have been deleted or the ID is incorrect. "
            "Check the job list and try again."
        ),
        "JOB_NOT_FOUND",
    )


def node_not_found(node_id: str) -> APIError:
    return APIError(
        status.HTTP_404_NOT_FOUND,
        (
            f"Node {node_id} does not exist. "
            "It may have been removed from the cluster or the ID is incorrect. "
            "Check the node list and try again."
        ),
        "NODE_NOT_FOUND",
    )


def metrics_not_found(job_id: str) -> APIError:
    return APIError(
        status.HTTP_404_NOT_FOUND,
        (
            f"Metrics for job {job_id} are not available. "
            "The job may not have run yet or metrics collection failed. "
            "Ensure the job has started and try again."
        ),
        "METRICS_NOT_FOUND",
    )


def logs_not_found(job_id: str) -> APIError:
    return APIError(
        status.HTTP_404_NOT_FOUND,
        (
            f"Logs for job {job_id} are not available. "
            "The job may not have started yet or log collection failed. "
            "Ensure the job has started and try again."
        ),
        "LOGS_NOT_FOUND",
    )


def job_not_retryable(job_id: str, current_status: str) -> APIError:
    return APIError(
        status.HTTP_409_CONFLICT,
        (
            f"Job {job_id} cannot be retried because it is currently "
            f"{current_status}. Only FAILED or CANCELLED jobs can be retried."
        ),
        "JOB_NOT_RETRYABLE",
    )


def job_not_cancellable(job_id: str, current_status: str) -> APIError:
    return APIError(
        status.HTTP_409_CONFLICT,
        (
            f"Job {job_id} cannot be cancelled because it is currently "
            f"{current_status}. Only RUNNING or PENDING jobs can be cancelled."
        ),
        "JOB_NOT_CANCELLABLE",
    )


def syncthing_unavailable(detail: str) -> APIError:
    return APIError(
        status.HTTP_503_SERVICE_UNAVAILABLE, detail, "SYNCTHING_UNAVAILABLE"
    )
