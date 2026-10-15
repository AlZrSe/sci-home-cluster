"""
``write_yaml`` must serialise in JSON mode (issue #105, AC-11).

The failure this file pins is not hypothetical and it is silent. Serialised in
Pydantic's python mode, ``write_yaml`` emitted

    status: !!python/object/apply:shared.schemas.job_status.JobStatus
    - PENDING

``read_yaml`` parses with ``yaml.safe_load``, which refuses a python-object
tag with ``ConstructorError``, and ``SyncthingService._process_job_file``
catches ``Exception`` and logs it. So the backend wrote a file that nothing in
the system could read, and the one consumer that noticed said nothing the
operator would see.

Both models the folder carries are asserted here: ``JobState`` (which failed)
and ``NodeSpec`` (which round-tripped already, and must keep doing so - the
agent's heartbeat writes one every 30 s).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import yaml

from shared.file_ops.yaml_utils import read_yaml, write_yaml
from shared.schemas.job_state import JobState
from shared.schemas.job_spec import JobSpec
from shared.schemas.job_status import JobStatus
from shared.schemas.node_spec import GPUInfo, NodeSpec


def a_job_state(job_id: str = "job-1") -> JobState:
    return JobState(
        job_id=job_id,
        spec=JobSpec(
            name="round-trip",
            command="python train.py",
            resources={"gpus": 1, "cpus": 4, "memory_gb": 16},
        ),
        status=JobStatus.PENDING,
        created_at=datetime(2026, 10, 14, 3, 54, 46, 87450),
        retry_count=0,
    )


def a_node_spec(node_id: str = "node-01") -> NodeSpec:
    return NodeSpec(
        node_id=node_id,
        hostname="node-01.lan",
        gpus=[GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)],
        cpus=16,
        memory_gb=64,
        os="Ubuntu 24.04",
        status="ONLINE",
        last_heartbeat=datetime(2026, 10, 14, 3, 54, 46, 87450),
        current_job_id=None,
    )


class TestWriteYamlSerialisesInJsonMode:
    """Every model the folder carries survives ``write_yaml`` -> ``read_yaml``."""

    @pytest.mark.parametrize(
        "state",
        [a_job_state(), a_job_state("job-42")],
        ids=["job-1", "job-42"],
    )
    def test_a_job_state_round_trips(self, tmp_path: Path, state: JobState) -> None:
        target = tmp_path / "jobs" / state.job_id / "state.yaml"

        write_yaml(str(target), state)

        loaded = read_yaml(str(target), JobState)
        assert loaded == state

    def test_the_status_comes_back_as_the_enum_not_a_string(
        self, tmp_path: Path
    ) -> None:
        """
        The assertion AC-11 is really about.

        ``read_yaml`` validates into ``JobState``, so a plain ``== state``
        would pass even if the field arrived as ``"PENDING"`` - Pydantic
        coerces it on the way in. What has to be pinned is that the *file*
        holds a plain string, because that is what ``yaml.safe_load`` can read
        at all: a python-object tag is not a string that happens to be
        coerced, it is a node the loader refuses outright.
        """
        target = tmp_path / "state.yaml"
        state = a_job_state()
        assert isinstance(state.status, JobStatus)

        write_yaml(str(target), state)

        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert raw["status"] == "PENDING"
        assert isinstance(raw["status"], str)
        assert isinstance(read_yaml(str(target), JobState).status, JobStatus)

    def test_the_status_is_not_written_as_a_python_object(self, tmp_path: Path) -> None:
        """
        The document must be loadable by ``safe_load`` itself.

        Asserting on ``safe_load`` rather than on the string keeps this honest
        if PyYAML ever changes how it spells a python-object tag.
        """
        target = tmp_path / "state.yaml"

        write_yaml(str(target), a_job_state())

        text = target.read_text(encoding="utf-8")
        assert "!!python" not in text
        assert yaml.safe_load(text)["status"] == "PENDING"

    def test_a_node_spec_round_trips(self, tmp_path: Path) -> None:
        """
        The other model in the folder, and the one ``write_yaml`` had for
        every production caller before #105 (``agent/hardware.py``).

        Its ``last_heartbeat`` is now an ISO string on disk rather than a YAML
        timestamp, because both writers go through the same function and
        Syncthing compares bytes: the backend and the agent have to emit the
        same document for the same value or every write conflicts.
        """
        target = tmp_path / "nodes" / "node-01.yaml"
        spec = a_node_spec()

        write_yaml(str(target), spec)

        loaded = read_yaml(str(target), NodeSpec)
        assert loaded == spec
        assert loaded.last_heartbeat == spec.last_heartbeat
        assert yaml.safe_load(target.read_text(encoding="utf-8"))["last_heartbeat"] == (
            spec.last_heartbeat.isoformat()
        )

    def test_every_terminal_status_round_trips(self, tmp_path: Path) -> None:
        """
        One status is not enough: the folder is read back at claim time and at
        terminal time (#97, #98, #100), so every member has to survive.
        """
        for index, status in enumerate(JobStatus):
            state = a_job_state(f"job-{index}").model_copy(update={"status": status})
            target = tmp_path / f"job-{index}.yaml"

            write_yaml(str(target), state)

            assert read_yaml(str(target), JobState).status is status

    def test_a_retry_written_state_round_trips_too(self, tmp_path: Path) -> None:
        """
        The shape #105's retry writes, not just the shape create writes.

        They differ in exactly the fields that were broken before: a
        non-zero ``retry_count`` and a status the store has just moved.
        """
        retried = a_job_state().model_copy(
            update={"status": JobStatus.PENDING, "retry_count": 3}
        )
        target = tmp_path / "state.yaml"

        write_yaml(str(target), retried)

        loaded = read_yaml(str(target), JobState)
        assert loaded.retry_count == 3
        assert loaded.status is JobStatus.PENDING
