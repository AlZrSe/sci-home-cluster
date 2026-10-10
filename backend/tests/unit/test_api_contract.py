"""
Contract tests for the public API surface (issue #19).

These pin the JSON the API serves and the validation it applies to job
submissions. They exist so that a future schema refactor cannot silently
change the contract that the frontend in `frontend/` depends on.
"""

import pytest
from pydantic import ValidationError

from shared.schemas import (
    CPUMetric,
    ErrorResponse,
    JobListResult,
    JobQuery,
    JobSpec,
    JobState,
    JobStatus,
    NodeSpec,
    Resources,
    RetryPolicy,
    TokenValidationResponse,
)


class TestJobSpecContract:
    """JobSpec is the input contract for POST /jobs."""

    def _resources(self, **overrides):
        params = {"gpus": 1, "cpus": 4, "memory_gb": 16}
        params.update(overrides)
        return Resources(**params)

    def test_optional_fields_have_the_documented_defaults(self):
        spec = JobSpec(
            name="my-job",
            command="python train.py",
            resources=self._resources(),
        )

        assert spec.working_dir == "/sync/projects/new-run"
        assert spec.paths.input is None
        assert spec.paths.output is None
        assert spec.retry.max_retries == 3
        assert spec.retry.retry_delay_seconds == 60
        assert spec.env == {}
        assert spec.resources.vram_gb == 0

    def test_defaults_survive_a_round_trip(self):
        spec = JobSpec(
            name="my-job",
            command="python train.py",
            resources=self._resources(),
        )
        again = JobSpec(**spec.model_dump())

        assert again == spec

    def test_name_pattern_is_enforced(self):
        with pytest.raises(ValidationError):
            JobSpec(name="ab", command="python x.py", resources=self._resources())
        with pytest.raises(ValidationError):
            JobSpec(
                name="has space", command="python x.py", resources=self._resources()
            )
        with pytest.raises(ValidationError):
            JobSpec(name="bad!char", command="python x.py", resources=self._resources())

    def test_name_pattern_allows_the_documented_characters(self):
        for name in ("abc", "a-b_c", "Job123", "a_b-C9"):
            spec = JobSpec(
                name=name, command="python x.py", resources=self._resources()
            )
            assert spec.name == name

    def test_command_minimum_length(self):
        with pytest.raises(ValidationError):
            JobSpec(name="my-job", command="ab", resources=self._resources())

    def test_resource_bounds(self):
        with pytest.raises(ValidationError):
            self._resources(gpus=-1)
        with pytest.raises(ValidationError):
            self._resources(cpus=0)
        with pytest.raises(ValidationError):
            self._resources(memory_gb=0)
        with pytest.raises(ValidationError):
            self._resources(gpus=99)

    def test_retry_bounds_and_defaults(self):
        assert RetryPolicy().max_retries == 3
        with pytest.raises(ValidationError):
            RetryPolicy(max_retries=-1)
        with pytest.raises(ValidationError):
            RetryPolicy(max_retries=99)
        with pytest.raises(ValidationError):
            RetryPolicy(retry_delay_seconds=999999)


class TestResponseTypes:
    """Response models must keep the field names and types the UI reads."""

    def test_cpu_metrics_are_integers(self):
        from datetime import datetime

        metric = CPUMetric(
            timestamp=datetime.now(),
            cpu_percent=45,
            memory_percent=60,
            temperature_c=62,
            memory_used_gb=12.5,
        )
        dumped = metric.model_dump()

        assert isinstance(dumped["cpu_percent"], int)
        assert isinstance(dumped["memory_percent"], int)
        assert isinstance(dumped["temperature_c"], int)
        # memory_used_gb is a float, as documented in openapi.yaml.
        assert isinstance(dumped["memory_used_gb"], float)

    def test_cpu_metrics_expose_the_fields_the_client_reads(self):
        # frontend/src/lib/types.ts marks both of these as required on
        # CPUMetric, and openapi.yaml lists them as required.
        from datetime import datetime

        assert set(CPUMetric.model_fields) == {
            "timestamp",
            "cpu_percent",
            "memory_percent",
            "temperature_c",
            "memory_used_gb",
        }
        with pytest.raises(ValidationError):
            CPUMetric(timestamp=datetime.now(), cpu_percent=45, memory_percent=60)

    def test_cpu_metrics_are_bounded(self):
        from datetime import datetime

        with pytest.raises(ValidationError):
            CPUMetric(
                timestamp=datetime.now(),
                cpu_percent=150,
                memory_percent=60,
                temperature_c=60,
                memory_used_gb=1.0,
            )

    def test_status_stays_an_enum_not_a_string(self):
        state = JobState(
            job_id="job-1",
            spec=JobSpec(
                name="my-job",
                command="python train.py",
                resources=Resources(gpus=1, cpus=1, memory_gb=1),
            ),
            status=JobStatus.RUNNING,
            created_at="2026-01-01T00:00:00",
            retry_count=0,
        )

        assert isinstance(state.status, JobStatus)
        assert state.status == JobStatus.RUNNING
        # but it still serializes as its string value
        assert state.model_dump()["status"] == "RUNNING"

    def test_job_id_pattern(self):
        with pytest.raises(ValidationError):
            JobState(
                job_id="not-a-job-id",
                spec=JobSpec(
                    name="my-job",
                    command="python train.py",
                    resources=Resources(gpus=1, cpus=1, memory_gb=1),
                ),
                status=JobStatus.PENDING,
                created_at="2026-01-01T00:00:00",
                retry_count=0,
            )

    def test_node_status_pattern(self):
        with pytest.raises(ValidationError):
            NodeSpec(
                node_id="node-x",
                hostname="x.lan",
                gpus=[],
                cpus=4,
                memory_gb=16,
                os="Linux",
                status="MAINTENANCE",
                last_heartbeat="2026-01-01T00:00:00",
            )

    def test_error_response_shape(self):
        err = ErrorResponse(
            status=404,
            title="Not Found",
            detail="Job job-1 not found",
            instance="/api/v1/jobs/job-1",
            error_code="JOB_NOT_FOUND",
        )
        assert set(err.model_dump()) == {
            "status",
            "title",
            "detail",
            "instance",
            "error_code",
        }

    def test_token_validation_response_shape(self):
        assert set(TokenValidationResponse(valid=True).model_dump()) == {"valid"}

    def test_job_list_result_shape(self):
        result = JobListResult(items=[], total=0)
        assert set(result.model_dump()) == {"items", "total"}

    def test_job_query_defaults_match_the_router(self):
        q = JobQuery()
        assert q.limit == 10
        assert q.offset == 0
        assert q.status is None
        with pytest.raises(ValidationError):
            JobQuery(limit=0)
        with pytest.raises(ValidationError):
            JobQuery(limit=101)
