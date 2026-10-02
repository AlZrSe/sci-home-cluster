"""
Database-backed data store for the Scientific Home Cluster backend.
Uses SQLAlchemy 2.0 async with SQLite.
"""

import asyncio
import random
import zlib
from typing import Callable, Dict, List, Optional, Set, Tuple
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.core.database import get_session
from shared.schemas.job_status import JobStatus
from shared.schemas.job_state import JobState
from shared.schemas.job_spec import JobSpec
from shared.schemas.node_spec import NodeSpec, GPUInfo
from shared.schemas.job_metrics import JobMetrics, JobMetricsSummary
from shared.schemas.gpu_metric import GPUMetric
from shared.schemas.cpu_metric import CPUMetric
from backend.store.database import (
    JobModel,
    NodeModel,
    GPUMetricModel,
    CPUMetricModel,
    LogEntryModel,
)


def stable_seed(*parts: str) -> int:
    """
    Deterministic seed derived from the given strings.

    builtin hash() is salted per process (PYTHONHASHSEED), so seeding
    from it made generated metrics and logs differ between runs of the
    same input. crc32 is stable across processes and platforms.
    """
    return 1337 + zlib.crc32(":".join(parts).encode("utf-8"))


# Assumed system memory when deriving memory_used_gb from a percentage. The
# frontend mock server does the same thing against its own node's RAM; on the
# backend a single constant keeps the generated series deterministic.
SYSTEM_MEMORY_GB = 32


class DatabaseStore:
    """Thread-safe database-backed store for jobs, nodes, metrics, and logs."""

    def __init__(self):
        self._job_counter: int = 0
        self._seeded: bool = False
        self._seed_lock = asyncio.Lock()
        self._log_stream_tasks: Dict[str, asyncio.Task] = {}
        self._log_stream_subscribers: Dict[str, Set[Callable[[str], None]]] = {}

    async def _seed_data(self, session: AsyncSession) -> None:
        """Seed the database with initial data matching the frontend mock server."""
        result = await session.execute(select(func.count(JobModel.job_id)))
        job_count: Optional[int] = result.scalar()
        if job_count and job_count > 0:
            return

        REFERENCE_TIME = datetime(2026, 9, 19, 12, 0, 0)

        def iso(ms_ago: int) -> str:
            past = REFERENCE_TIME.timestamp() - (ms_ago / 1000.0)
            return datetime.fromtimestamp(past).isoformat().replace("T", " ")[:19]

        nodes_data = [
            {
                "node_id": "node-alpha",
                "hostname": "alpha.lan",
                "gpus": [{"name": "NVIDIA RTX 4090", "memory_gb": 24}],
                "cpus": 16,
                "memory_gb": 64,
                "os": "Ubuntu 24.04",
                "status": "ONLINE",
                "last_heartbeat": iso(4_000),
                "current_job_id": "job-1041",
            },
            {
                "node_id": "node-beta",
                "hostname": "beta.lan",
                "gpus": [
                    {"name": "NVIDIA RTX 3090", "memory_gb": 24},
                    {"name": "NVIDIA RTX 3090", "memory_gb": 24},
                ],
                "cpus": 24,
                "memory_gb": 128,
                "os": "Ubuntu 22.04",
                "status": "ONLINE",
                "last_heartbeat": iso(11_000),
                "current_job_id": "job-1039",
            },
            {
                "node_id": "node-gamma",
                "hostname": "gamma.lan",
                "gpus": [{"name": "Apple M3 Max (MPS)", "memory_gb": 36}],
                "cpus": 14,
                "memory_gb": 36,
                "os": "macOS 15.3",
                "status": "ONLINE",
                "last_heartbeat": iso(28_000),
            },
            {
                "node_id": "node-delta",
                "hostname": "delta.lan",
                "gpus": [],
                "cpus": 8,
                "memory_gb": 32,
                "os": "Debian 12",
                "status": "OFFLINE",
                "last_heartbeat": iso(1_400_000),
            },
        ]

        for node_dict in nodes_data:
            gpus = [GPUInfo(**gpu_dict) for gpu_dict in node_dict["gpus"]]
            node_dict["gpus"] = [gpu.model_dump() for gpu in gpus]
            node_dict["last_heartbeat"] = datetime.fromisoformat(
                node_dict["last_heartbeat"].replace(" ", "T")
            )
            node = NodeModel(**node_dict)
            session.add(node)

        names = [
            "protein-fold-batch",
            "cfd-mesh-sweep",
            "mnist-ablation",
            "genome-align",
            "monte-carlo-photon",
            "spectra-denoise",
            "lattice-qcd-run",
            "climate-downscale",
            "docking-screen",
            "raman-classifier",
        ]

        statuses: List[JobStatus] = [
            JobStatus.RUNNING,
            JobStatus.RUNNING,
            JobStatus.PENDING,
            JobStatus.COMPLETED,
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.COMPLETED,
            JobStatus.PENDING,
            JobStatus.RUNNING,
        ]

        rnd = random.Random(1337)

        def make_spec(name: str) -> JobSpec:
            gpus = rnd.randint(1, 2)
            script_name = name.replace("-", "_")
            return JobSpec(
                name=name,
                command=(
                    f"python -u scripts/{script_name}.py "
                    f"--config configs/{name}.yaml"
                ),
                working_dir=f"/sync/projects/{name}",
                env={"PYTHONUNBUFFERED": "1", "CUDA_VISIBLE_DEVICES": "0"},
                resources={
                    "gpus": gpus,
                    "cpus": rnd.choice([4, 8, 12, 16]),
                    "memory_gb": rnd.choice([8, 16, 32, 64]),
                    "vram_gb": rnd.choice([8, 12, 16, 24]),
                },
                paths={"input": f"data/{name}/in", "output": f"data/{name}/out"},
                retry={"max_retries": 3, "retry_delay_seconds": 60},
            )

        for i, name in enumerate(names):
            status = statuses[i]
            created = 1000 * 60 * (12 + i * 47)
            running = status != JobStatus.PENDING
            done = status in (
                JobStatus.COMPLETED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            )
            job_id = f"job-{1050 - i}"
            spec = make_spec(name)
            node_id = (
                rnd.choice(["node-alpha", "node-beta", "node-gamma"])
                if running
                else None
            )
            created_at = datetime.fromtimestamp(
                REFERENCE_TIME.timestamp() - (created / 1000.0)
            )
            started_at = (
                datetime.fromtimestamp(
                    REFERENCE_TIME.timestamp() - ((created - 1000 * 60 * 3) / 1000.0)
                )
                if running
                else None
            )
            completed_at = (
                datetime.fromtimestamp(
                    REFERENCE_TIME.timestamp() - ((created - 1000 * 60 * 40) / 1000.0)
                )
                if done
                else None
            )
            exit_code = (
                0
                if status == JobStatus.COMPLETED
                else (137 if status == JobStatus.FAILED else None)
            )
            error = (
                "CUDA out of memory at step 12841"
                if status == JobStatus.FAILED
                else None
            )
            retry_count = 2 if status == JobStatus.FAILED else 0

            job = JobModel(
                job_id=job_id,
                spec=spec.model_dump(),
                status=status,
                node_id=node_id,
                created_at=created_at,
                started_at=started_at,
                completed_at=completed_at,
                exit_code=exit_code,
                error=error,
                retry_count=retry_count,
            )
            session.add(job)
            job_num = int(job_id.split("-")[1])
            if job_num > self._job_counter:
                self._job_counter = job_num

        await session.commit()

    async def _ensure_seeded(self) -> None:
        """
        Ensure the database is seeded, at most once per store instance.

        This used to run a SELECT count(*) plus a session on every single
        store call (~20 call sites, all of them on request paths).
        """
        if self._seeded:
            return
        async with self._seed_lock:
            if self._seeded:
                return
            if settings.SEED_DEMO_DATA:
                async with get_session() as session:
                    await self._seed_data(session)
            # The job counter lives in memory, so it has to be re-derived
            # from the database on startup or ids repeat after a restart.
            #
            # This must run even when demo seeding is off. _job_counter
            # starts at 0 and create_job does `+= 1`, so skipping the sync
            # makes the first job after a restart `job-1` - which collides
            # with low-numbered ids arriving via the Syncthing folder scan
            # and raises IntegrityError on the job_id primary key (HTTP 500).
            await self._sync_job_counter()
            self._seeded = True

    async def _sync_job_counter(self) -> None:
        """Set the in-memory job counter to the highest id already stored."""
        async with get_session() as session:
            result = await session.execute(select(JobModel.job_id))
            for (job_id,) in result.all():
                if not job_id.startswith("job-"):
                    continue
                try:
                    job_num = int(job_id[4:])
                except ValueError:
                    continue
                if job_num > self._job_counter:
                    self._job_counter = job_num

    def _model_to_job_state(self, job_model: JobModel) -> JobState:
        """Convert a JobModel to a JobState."""
        return JobState(
            job_id=job_model.job_id,
            spec=JobSpec(**job_model.spec),
            status=job_model.status,
            node_id=job_model.node_id,
            created_at=job_model.created_at,
            started_at=job_model.started_at,
            completed_at=job_model.completed_at,
            exit_code=job_model.exit_code,
            error=job_model.error,
            retry_count=job_model.retry_count,
        )

    def _model_to_node_spec(self, node_model: NodeModel) -> NodeSpec:
        """Convert a NodeModel to a NodeSpec."""
        return NodeSpec(
            node_id=node_model.node_id,
            hostname=node_model.hostname,
            gpus=[GPUInfo(**gpu) for gpu in node_model.gpus],
            cpus=node_model.cpus,
            memory_gb=node_model.memory_gb,
            os=node_model.os,
            status=node_model.status,
            last_heartbeat=node_model.last_heartbeat,
            current_job_id=node_model.current_job_id,
        )

    # Job CRUD operations
    async def create_job(self, job_spec: JobSpec) -> JobState:
        """Create a new job and return the created job state."""
        await self._ensure_seeded()
        async with get_session() as session:
            self._job_counter += 1
            job_id = f"job-{self._job_counter}"

            now = datetime.now()
            job = JobModel(
                job_id=job_id,
                spec=job_spec.model_dump(),
                status=JobStatus.PENDING,
                created_at=now,
                retry_count=0,
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)

            return self._model_to_job_state(job)

    async def create_job_with_id(self, job_state: JobState) -> JobState:
        """Create a job with a specific job_id (used for sync from Syncthing)."""
        await self._ensure_seeded()
        async with get_session() as session:
            job = JobModel(
                job_id=job_state.job_id,
                spec=job_state.spec.model_dump(),
                status=job_state.status,
                node_id=job_state.node_id,
                created_at=job_state.created_at,
                started_at=job_state.started_at,
                completed_at=job_state.completed_at,
                exit_code=job_state.exit_code,
                error=job_state.error,
                retry_count=job_state.retry_count,
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)

            job_num = int(job_state.job_id.split("-")[1])
            if job_num > self._job_counter:
                self._job_counter = job_num

            return self._model_to_job_state(job)

    async def get_job(self, job_id: str) -> Optional[JobState]:
        """Get a job by ID."""
        await self._ensure_seeded()
        async with get_session() as session:
            result = await session.execute(
                select(JobModel).where(JobModel.job_id == job_id)
            )
            job_model = result.scalar_one_or_none()
            if job_model:
                return self._model_to_job_state(job_model)
            return None

    async def list_jobs(
        self,
        status: Optional[str] = None,
        node_id: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[List[JobState], int]:
        """
        List jobs with filtering and pagination.
        Returns a tuple of (jobs, total_count).
        """
        await self._ensure_seeded()
        async with get_session() as session:
            query = select(JobModel)

            if status:
                try:
                    job_status = JobStatus(status)
                    query = query.where(JobModel.status == job_status)
                except ValueError:
                    return [], 0

            if node_id:
                query = query.where(JobModel.node_id == node_id)

            if search:
                search_lower = search.lower()
                query = query.where(
                    JobModel.spec.op("->>")("name").ilike(f"%{search_lower}%")
                )

            total_result = await session.execute(
                select(func.count()).select_from(query.subquery())
            )
            total = total_result.scalar()

            query = (
                query.order_by(JobModel.created_at.desc()).limit(limit).offset(offset)
            )
            result = await session.execute(query)
            job_models = result.scalars().all()

            jobs = [self._model_to_job_state(job) for job in job_models]
            return jobs, total

    async def update_job(self, job_id: str, **kwargs) -> Optional[JobState]:
        """Update a job's fields and return the updated job."""
        await self._ensure_seeded()
        async with get_session() as session:
            result = await session.execute(
                select(JobModel).where(JobModel.job_id == job_id)
            )
            job_model = result.scalar_one_or_none()
            if not job_model:
                return None

            for key, value in kwargs.items():
                if key == "spec" and isinstance(value, JobSpec):
                    setattr(job_model, key, value.model_dump())
                elif hasattr(job_model, key):
                    setattr(job_model, key, value)

            await session.commit()
            await session.refresh(job_model)
            return self._model_to_job_state(job_model)

    async def delete_job(self, job_id: str) -> bool:
        """Delete a job by ID. Returns True if deleted, False if not found."""
        await self._ensure_seeded()
        async with get_session() as session:
            result = await session.execute(
                select(JobModel).where(JobModel.job_id == job_id)
            )
            job_model = result.scalar_one_or_none()
            if not job_model:
                return False

            # The child tables have no ON DELETE CASCADE, and SQLite does not
            # enforce foreign keys by default, so the dependent rows have to
            # be removed explicitly or they are orphaned.
            await session.execute(
                delete(GPUMetricModel).where(GPUMetricModel.job_id == job_id)
            )
            await session.execute(
                delete(CPUMetricModel).where(CPUMetricModel.job_id == job_id)
            )
            await session.execute(
                delete(LogEntryModel).where(LogEntryModel.job_id == job_id)
            )
            await session.delete(job_model)
            await session.commit()
            await self.stop_log_stream(job_id)
            return True

    # Node CRUD operations
    async def list_nodes(self) -> List[NodeSpec]:
        """List all nodes."""
        await self._ensure_seeded()
        async with get_session() as session:
            result = await session.execute(select(NodeModel))
            node_models = result.scalars().all()
            return [self._model_to_node_spec(node) for node in node_models]

    async def get_node(self, node_id: str) -> Optional[NodeSpec]:
        """Get a node by ID."""
        await self._ensure_seeded()
        async with get_session() as session:
            result = await session.execute(
                select(NodeModel).where(NodeModel.node_id == node_id)
            )
            node_model = result.scalar_one_or_none()
            if node_model:
                return self._model_to_node_spec(node_model)
            return None

    async def update_node(self, node_id: str, **kwargs) -> Optional[NodeSpec]:
        """Update a node's fields and return the updated node."""
        await self._ensure_seeded()
        async with get_session() as session:
            result = await session.execute(
                select(NodeModel).where(NodeModel.node_id == node_id)
            )
            node_model = result.scalar_one_or_none()
            if not node_model:
                return None

            for key, value in kwargs.items():
                if key == "gpus" and isinstance(value, list):
                    setattr(node_model, key, [gpu.model_dump() for gpu in value])
                elif hasattr(node_model, key):
                    setattr(node_model, key, value)

            await session.commit()
            await session.refresh(node_model)
            return self._model_to_node_spec(node_model)

    async def create_node(self, node_spec: NodeSpec) -> NodeSpec:
        """Create a new node and return the created node spec."""
        await self._ensure_seeded()
        async with get_session() as session:
            result = await session.execute(
                select(NodeModel).where(NodeModel.node_id == node_spec.node_id)
            )
            if result.scalar_one_or_none():
                raise ValueError(f"Node with ID {node_spec.node_id} already exists")

            node = NodeModel(
                node_id=node_spec.node_id,
                hostname=node_spec.hostname,
                gpus=[gpu.model_dump() for gpu in node_spec.gpus],
                cpus=node_spec.cpus,
                memory_gb=node_spec.memory_gb,
                os=node_spec.os,
                status=node_spec.status,
                last_heartbeat=node_spec.last_heartbeat,
                current_job_id=node_spec.current_job_id,
            )
            session.add(node)
            await session.commit()
            await session.refresh(node)
            return self._model_to_node_spec(node)

    async def delete_node(self, node_id: str) -> bool:
        """Delete a node by ID. Returns True if deleted, if not found."""
        await self._ensure_seeded()
        async with get_session() as session:
            result = await session.execute(
                select(NodeModel).where(NodeModel.node_id == node_id)
            )
            node_model = result.scalar_one_or_none()
            if not node_model:
                return False

            await session.delete(node_model)
            await session.commit()
            return True

    async def reset(self) -> None:
        """
        Empty every table and mark the store as already seeded.

        Deliberately gated on the same SEED_DEMO_DATA flag as
        _ensure_seeded(), even though the flag has no production caller
        here. reset() invokes _seed_data() directly and so bypasses
        _ensure_seeded() entirely - gating only _ensure_seeded would
        leave this call seeding unconditionally, and the autouse
        reset_singleton_store fixture calls reset() around every test, so
        the application singleton would be repopulated while every test
        still passed. Gating both keeps the two entry points consistent
        and makes "seeding is off" observable end to end.

        _job_counter stays at 0 and _seeded stays True regardless of the
        flag: the tables really were just emptied, so there is nothing
        left to seed and nothing to derive a counter from.
        """
        async with get_session() as session:
            await session.execute(delete(LogEntryModel))
            await session.execute(delete(CPUMetricModel))
            await session.execute(delete(GPUMetricModel))
            await session.execute(delete(JobModel))
            await session.execute(delete(NodeModel))
            await session.commit()

            self._job_counter = 0
            for task in self._log_stream_tasks.values():
                task.cancel()
            self._log_stream_tasks.clear()
            self._log_stream_subscribers.clear()

            if settings.SEED_DEMO_DATA:
                await self._seed_data(session)
            # reset() clears the table, so the cached "already seeded" flag
            # has to be restored or the store would never re-seed.
            self._seeded = True

    # Metrics operations
    async def get_job_metrics(self, job_id: str) -> Optional[JobMetrics]:
        """
        Get the stored metrics for a job.

        Returns None when the job does not exist, which is how the route
        decides on METRICS_NOT_FOUND. That existence check is NOT gated on
        SEED_DEMO_DATA: a lookup miss is not demo data.

        When the job exists but nothing has been collected, the answer
        depends on SEED_DEMO_DATA - see the gate at the bottom.
        """
        await self._ensure_seeded()
        async with get_session() as session:
            result = await session.execute(
                select(JobModel).where(JobModel.job_id == job_id)
            )
            if not result.scalar_one_or_none():
                return None

            result = await session.execute(
                select(GPUMetricModel)
                .where(GPUMetricModel.job_id == job_id)
                .order_by(GPUMetricModel.timestamp)
            )
            gpu_models = result.scalars().all()

            result = await session.execute(
                select(CPUMetricModel)
                .where(CPUMetricModel.job_id == job_id)
                .order_by(CPUMetricModel.timestamp)
            )
            cpu_models = result.scalars().all()

            if gpu_models or cpu_models:
                gpu_metrics = [
                    GPUMetric(
                        timestamp=m.timestamp,
                        gpu_index=m.gpu_index,
                        memory_used_mb=m.memory_used_mb,
                        memory_total_mb=m.memory_total_mb,
                        utilization_percent=m.utilization_percent,
                        temperature_c=m.temperature_c,
                    )
                    for m in gpu_models
                ]
                cpu_metrics = [
                    CPUMetric(
                        timestamp=m.timestamp,
                        cpu_percent=m.cpu_percent,
                        memory_percent=m.memory_percent,
                        temperature_c=m.temperature_c,
                        memory_used_gb=m.memory_used_gb,
                    )
                    for m in cpu_models
                ]
                summary = self._calculate_summary(gpu_metrics, cpu_metrics)
                return JobMetrics(
                    job_id=job_id,
                    gpu_metrics=gpu_metrics,
                    cpu_metrics=cpu_metrics,
                    summary=summary,
                )

        # The job exists, but nothing has been collected for it yet.
        #
        # SEED_DEMO_DATA gates *fabrication*, not existence (issue #35).
        # With it off, a read is only a read: no row is written and the
        # caller gets an empty series.
        if settings.SEED_DEMO_DATA:
            # Demo data only - persists to the tables, as it always has.
            metrics = await self._generate_job_metrics(job_id)
            if metrics:
                await self._store_job_metrics(job_id, metrics)
            return metrics

        return self._empty_metrics(job_id)

    async def _store_job_metrics(self, job_id: str, metrics: JobMetrics) -> None:
        """Store job metrics in the database."""
        async with get_session() as session:
            for gpu_metric in metrics.gpu_metrics:
                model = GPUMetricModel(
                    job_id=job_id,
                    timestamp=gpu_metric.timestamp,
                    gpu_index=gpu_metric.gpu_index,
                    memory_used_mb=gpu_metric.memory_used_mb,
                    memory_total_mb=gpu_metric.memory_total_mb,
                    utilization_percent=gpu_metric.utilization_percent,
                    temperature_c=gpu_metric.temperature_c,
                )
                session.add(model)
            for cpu_metric in metrics.cpu_metrics:
                model = CPUMetricModel(
                    job_id=job_id,
                    timestamp=cpu_metric.timestamp,
                    cpu_percent=cpu_metric.cpu_percent,
                    memory_percent=cpu_metric.memory_percent,
                    temperature_c=cpu_metric.temperature_c,
                    memory_used_gb=cpu_metric.memory_used_gb,
                )
                session.add(model)
            await session.commit()

    async def get_node_metrics(self, node_id: str) -> Optional[JobMetrics]:
        """
        Get the stored metrics for a node.

        None when the node does not exist (the route turns that into
        NODE_METRICS_NOT_FOUND); an empty series when it exists but has
        reported nothing. Same contract as get_job_metrics, and for the
        same reason (issue #35): a freshly registered node must not be
        painted as a broken one.
        """
        await self._ensure_seeded()
        async with get_session() as session:
            result = await session.execute(
                select(NodeModel).where(NodeModel.node_id == node_id)
            )
            if not result.scalar_one_or_none():
                return None

            result = await session.execute(
                select(GPUMetricModel)
                .where(GPUMetricModel.job_id == f"node:{node_id}")
                .order_by(GPUMetricModel.timestamp)
            )
            gpu_models = result.scalars().all()

            result = await session.execute(
                select(CPUMetricModel)
                .where(CPUMetricModel.job_id == f"node:{node_id}")
                .order_by(CPUMetricModel.timestamp)
            )
            cpu_models = result.scalars().all()

            if gpu_models or cpu_models:
                gpu_metrics = [
                    GPUMetric(
                        timestamp=m.timestamp,
                        gpu_index=m.gpu_index,
                        memory_used_mb=m.memory_used_mb,
                        memory_total_mb=m.memory_total_mb,
                        utilization_percent=m.utilization_percent,
                        temperature_c=m.temperature_c,
                    )
                    for m in gpu_models
                ]
                cpu_metrics = [
                    CPUMetric(
                        timestamp=m.timestamp,
                        cpu_percent=m.cpu_percent,
                        memory_percent=m.memory_percent,
                        temperature_c=m.temperature_c,
                        memory_used_gb=m.memory_used_gb,
                    )
                    for m in cpu_models
                ]
                summary = self._calculate_summary(gpu_metrics, cpu_metrics)
                return JobMetrics(
                    job_id=f"node:{node_id}",
                    gpu_metrics=gpu_metrics,
                    cpu_metrics=cpu_metrics,
                    summary=summary,
                )

        # The node exists, but no agent has reported for it yet. See the
        # matching gate in get_job_metrics (issue #35).
        if settings.SEED_DEMO_DATA:
            # Demo data only - persists to the tables, as it always has.
            metrics = await self._generate_node_metrics(node_id)
            if metrics:
                await self._store_node_metrics(node_id, metrics)
            return metrics

        return self._empty_metrics(f"node:{node_id}")

    async def _store_node_metrics(self, node_id: str, metrics: JobMetrics) -> None:
        """Store node metrics in the database."""
        async with get_session() as session:
            for gpu_metric in metrics.gpu_metrics:
                model = GPUMetricModel(
                    job_id=f"node:{node_id}",
                    timestamp=gpu_metric.timestamp,
                    gpu_index=gpu_metric.gpu_index,
                    memory_used_mb=gpu_metric.memory_used_mb,
                    memory_total_mb=gpu_metric.memory_total_mb,
                    utilization_percent=gpu_metric.utilization_percent,
                    temperature_c=gpu_metric.temperature_c,
                )
                session.add(model)
            for cpu_metric in metrics.cpu_metrics:
                model = CPUMetricModel(
                    job_id=f"node:{node_id}",
                    timestamp=cpu_metric.timestamp,
                    cpu_percent=cpu_metric.cpu_percent,
                    memory_percent=cpu_metric.memory_percent,
                    temperature_c=cpu_metric.temperature_c,
                    memory_used_gb=cpu_metric.memory_used_gb,
                )
                session.add(model)
            await session.commit()

    def _empty_metrics(self, job_id: str) -> JobMetrics:
        """
        The "it exists, nothing has been collected" answer.

        An empty series rather than a 404: the job (or node) is real, it
        just has no samples. This is deliberately NOT a 404 - a freshly
        created job would otherwise be indistinguishable from a typo.

        The summary is all zeros, which is the correct value for a series
        with no samples. Callers must detect emptiness from the arrays
        (`gpu_metrics == [] and cpu_metrics == []`), never from the
        summary: an all-zero summary is indistinguishable from a GPU that
        really did measure nothing (issue #35, AC-4/AC-12).
        """
        return JobMetrics(
            job_id=job_id,
            gpu_metrics=[],
            cpu_metrics=[],
            summary=self._calculate_summary([], []),
        )

    def _calculate_summary(
        self, gpu_metrics: List[GPUMetric], cpu_metrics: List[CPUMetric]
    ) -> JobMetricsSummary:
        """
        Calculate summary statistics from metrics.

        Total by construction, including for two empty series - which is
        what _empty_metrics relies on. Two things keep the empty case from
        raising, and both are load-bearing:

          * the `if gpu_metrics` ternaries below. They guard on the LIST,
            not on its values, so the `mems = [0]` placeholder set for the
            empty case is never handed to min()/max()/_avg().
          * `_avg`'s `if values else 0`, which short-circuits before
            `sum(values) / len(values)` can divide by zero.

        Removing either turns every empty metrics response into a 500.
        Do not "simplify" them away (issue #35, AC-12).
        """
        if gpu_metrics:
            mems = [m.memory_used_mb for m in gpu_metrics]
            utils = [m.utilization_percent for m in gpu_metrics]
        else:
            mems = [0]
            utils = [0]

        def _avg(values: list) -> int:
            return round(sum(values) / len(values)) if values else 0

        return JobMetricsSummary(
            gpu_memory_min_mb=min(mems) if gpu_metrics else 0,
            gpu_memory_max_mb=max(mems) if gpu_metrics else 0,
            gpu_memory_avg_mb=_avg(mems) if gpu_metrics else 0,
            gpu_util_min=min(utils) if gpu_metrics else 0,
            gpu_util_max=max(utils) if gpu_metrics else 0,
            gpu_util_avg=_avg(utils) if gpu_metrics else 0,
            cpu_avg_percent=_avg([m.cpu_percent for m in cpu_metrics]),
        )

    async def _generate_node_metrics(self, node_id: str) -> Optional[JobMetrics]:
        """
        DEMO DATA ONLY - gated by SEED_DEMO_DATA at its call site.

        Deterministic from stable_seed(), so it is also the oracle that
        pins the shape of the demo dataset. It is not, and must not become,
        a fallback for real data: no agent reports node samples yet.
        """
        node = await self.get_node(node_id)
        if not node:
            return None

        seed = stable_seed("node", node_id)
        rnd = random.Random(seed)

        has_gpus = len(node.gpus) > 0
        gpu_count = len(node.gpus)

        total = 24_576 if has_gpus else 0
        util = 55 if has_gpus else 0
        mem = 9_000 if has_gpus else 0
        cpu = 30

        gpu_metrics: List[GPUMetric] = []
        cpu_metrics: List[CPUMetric] = []

        for i in range(120, -1, -1):
            if has_gpus:
                util = min(99, max(6, util + (rnd.random() - 0.5) * 18))
                mem = min(total, max(1200, mem + (rnd.random() - 0.45) * 900))
            cpu = min(100, max(4, cpu + (rnd.random() - 0.5) * 14))

            ms_ago = i * 30_000
            timestamp = datetime.fromtimestamp(
                datetime.now().timestamp() - (ms_ago / 1000.0)
            )

            if has_gpus:
                for gpu_idx in range(gpu_count):
                    gpu_metrics.append(
                        GPUMetric(
                            timestamp=timestamp,
                            gpu_index=gpu_idx,
                            memory_used_mb=round(mem),
                            memory_total_mb=total,
                            utilization_percent=round(util),
                            temperature_c=round(48 + util * 0.28),
                        )
                    )

            cpu_metrics.append(
                CPUMetric(
                    timestamp=timestamp,
                    cpu_percent=round(cpu),
                    memory_percent=round(30 + cpu * 0.4),
                    temperature_c=round(45 + cpu * 0.35),
                    memory_used_gb=round(
                        (SYSTEM_MEMORY_GB * round(30 + cpu * 0.4)) / 100, 2
                    ),
                )
            )

        summary = self._calculate_summary(gpu_metrics, cpu_metrics)

        return JobMetrics(
            job_id=f"node:{node_id}",
            gpu_metrics=gpu_metrics,
            cpu_metrics=cpu_metrics,
            summary=summary,
        )

    async def _generate_job_metrics(self, job_id: str) -> Optional[JobMetrics]:
        """
        DEMO DATA ONLY - gated by SEED_DEMO_DATA at its call site.

        Uses the same algorithm as the frontend mock server, and is seeded
        from stable_seed() so two calls agree. It is the determinism
        oracle for the demo dataset; it is NOT a source of metrics for a
        real job (issue #35).
        """
        job = await self.get_job(job_id)
        if not job:
            return None

        seed = stable_seed("job", job_id)
        rnd = random.Random(seed)

        total = 24_576
        util = 55
        mem = 9_000
        cpu = 30

        gpu_metrics: List[GPUMetric] = []
        cpu_metrics: List[CPUMetric] = []

        for i in range(120, -1, -1):
            util = min(99, max(6, util + (rnd.random() - 0.5) * 18))
            mem = min(total, max(1200, mem + (rnd.random() - 0.45) * 900))
            cpu = min(100, max(4, cpu + (rnd.random() - 0.5) * 14))
            ms_ago = i * 30_000
            timestamp = datetime.fromtimestamp(
                datetime.now().timestamp() - (ms_ago / 1000.0)
            )

            gpu_metrics.append(
                GPUMetric(
                    timestamp=timestamp,
                    gpu_index=0,
                    memory_used_mb=round(mem),
                    memory_total_mb=total,
                    utilization_percent=round(util),
                    temperature_c=round(48 + util * 0.28),
                )
            )

            cpu_metrics.append(
                CPUMetric(
                    timestamp=timestamp,
                    cpu_percent=round(cpu),
                    memory_percent=round(30 + cpu * 0.4),
                    temperature_c=round(45 + cpu * 0.35),
                    memory_used_gb=round(
                        (SYSTEM_MEMORY_GB * round(30 + cpu * 0.4)) / 100, 2
                    ),
                )
            )

        summary = self._calculate_summary(gpu_metrics, cpu_metrics)

        return JobMetrics(
            job_id=job_id,
            gpu_metrics=gpu_metrics,
            cpu_metrics=cpu_metrics,
            summary=summary,
        )

    # Log operations
    async def get_job_logs(self, job_id: str) -> Optional[List[str]]:
        """
        Get the stored log lines for a job.

        Returns None when the job does not exist, which is what makes
        logs_not_found reachable in the routes. That check is NOT gated on
        SEED_DEMO_DATA: whether or not demo data is enabled, an unknown id
        is an unknown id and gets a 404 rather than a convincing 64-line
        log file (issue #35, D3).

        When the job exists but has no stored lines, the answer depends on
        SEED_DEMO_DATA - see the gate at the bottom.
        """
        await self._ensure_seeded()
        async with get_session() as session:
            result = await session.execute(
                select(JobModel).where(JobModel.job_id == job_id)
            )
            if not result.scalar_one_or_none():
                return None

            result = await session.execute(
                select(LogEntryModel)
                .where(LogEntryModel.job_id == job_id)
                .order_by(LogEntryModel.timestamp)
            )
            log_models = result.scalars().all()

            if log_models:
                return [log.line for log in log_models]

        # The job exists, but nothing has been logged for it yet. See the
        # matching gate in get_job_metrics (issue #35).
        if settings.SEED_DEMO_DATA:
            # Demo data only - persists to the tables, as it always has.
            logs = self._generate_job_logs(job_id)
            await self._store_job_logs(job_id, logs)
            return logs

        return []

    async def _store_job_logs(self, job_id: str, logs: List[str]) -> None:
        """Store job logs in the database."""
        async with get_session() as session:
            now = datetime.now()
            for i, line in enumerate(logs):
                log_entry = LogEntryModel(
                    job_id=job_id,
                    timestamp=now,
                    line=line,
                )
                session.add(log_entry)
            await session.commit()

    def _generate_job_logs(self, job_id: str) -> List[str]:
        """
        DEMO DATA ONLY - gated by SEED_DEMO_DATA at its call site.

        Deterministic from stable_seed(), so it is also the oracle pinning
        the shape of the demo log batch. Not a source of logs for a real
        job: no agent writes them yet (issue #35).
        """
        seed = stable_seed("job-log", job_id)
        rnd = random.Random(seed)

        def stamp() -> str:
            return datetime.now().isoformat().replace("T", " ")[:19]

        log_templates = [
            lambda s: f"INFO  step={s} loss={(2.4 - s * 0.0007):.4f} lr=3.0e-4",
            lambda s: f"INFO  step={s} throughput="
            f"{(180 + rnd.random() * 40):.1f} samples/s",
            lambda s: "DEBUG allocator: "
            f"reserved={(8 + rnd.random() * 6):.2f} GiB step={s}",
            lambda s: f"INFO  checkpoint written to data/out/ckpt-{s}.pt",
            lambda _: "WARN  syncthing folder scan delayed by 1.4s",
        ]

        lines = [
            f"{stamp()} INFO  job {job_id} accepted by scheduler",
            f"{stamp()} INFO  syncing input folder via syncthing (device alpha)",
            f"{stamp()} INFO  environment ready: python 3.12.4, torch 2.6.0+cu124",
            f"{stamp()} INFO  starting command",
        ]

        for i in range(60):
            step = 1000 + i * 50
            template = rnd.choice(log_templates)
            lines.append(f"{stamp()} {template(step)}")

        return lines

    # WebSocket log streaming simulation
    def log_stream_available(self) -> bool:
        """
        Whether the log stream has anything to send at all.

        Every line the stream emits is generated from the same templates
        as _generate_job_logs, so with SEED_DEMO_DATA off the stream can
        only ever produce invented output. The WebSocket route uses this
        to close with an explicit reason rather than hold a client open on
        a socket that will never say anything (issue #35, AC-8).
        """
        return settings.SEED_DEMO_DATA

    def _register_log_stream_subscriber(
        self, job_id: str, on_line: Callable[[str], None]
    ) -> None:
        """
        Record on_line as a subscriber for this job's stream.

        Without this the worker generates lines and stores them but never
        hands any to the caller. Extracted from start_log_stream to keep
        that function's complexity inside the project's ruff limit.
        """
        if job_id not in self._log_stream_subscribers:
            self._log_stream_subscribers[job_id] = set()
        self._log_stream_subscribers[job_id].add(on_line)

    async def start_log_stream(
        self, job_id: str, on_line: Callable[[str], None]
    ) -> Optional[asyncio.Task]:
        """
        Start simulating log streaming for a job.

        Returns the task that can be cancelled to stop the stream, or None
        when the stream is unavailable because SEED_DEMO_DATA is off - in
        which case no task is created, no line is generated and nothing is
        written to log_entries (issue #35, AC-8).
        """
        await self._ensure_seeded()

        if not self.log_stream_available():
            # Demo data only. Refusing here is what keeps the route from
            # registering a subscriber for a stream that will never tick;
            # the guard inside _stream_worker below is what keeps the
            # worker honest if it is started by any other caller.
            return None

        # Register the callback. Without this the worker generates lines and
        # stores them but never hands any to the caller.
        self._register_log_stream_subscriber(job_id, on_line)

        async def _stream_worker():
            step = 5000
            try:
                while True:
                    job = await self.get_job(job_id)
                    if not job or job.status != JobStatus.RUNNING:
                        break

                    # DEMO DATA ONLY. Checked per tick, not just at
                    # start_log_stream: this worker invents a line AND
                    # commits it to log_entries, so leaving it running
                    # after the flag is flipped would keep writing an
                    # invented row every 1.4s for as long as the job is
                    # RUNNING, with no read involved (issue #35, AC-9).
                    if not self.log_stream_available():
                        break

                    step += 50
                    seed = stable_seed("job-stream", job_id) + step
                    rnd = random.Random(seed)
                    template = rnd.choice(
                        [
                            lambda s: (
                                f"INFO  step={s} "
                                f"loss={(2.4 - s * 0.0007):.4f} lr=3.0e-4"
                            ),
                            lambda s: (
                                "INFO  step={s} throughput="
                                f"{(180 + rnd.random() * 40):.1f} samples/s"
                            ),
                            lambda s: (
                                "DEBUG allocator: "
                                f"reserved={(8 + rnd.random() * 6):.2f} GiB step={s}"
                            ),
                            lambda s: (
                                f"INFO  checkpoint written to " f"data/out/ckpt-{s}.pt"
                            ),
                            lambda _: "WARN  syncthing folder scan delayed by 1.4s",
                        ]
                    )
                    timestamp = datetime.now().isoformat().replace("T", " ")[:19]
                    line = f"{timestamp} {template(step)}"

                    async with get_session() as session:
                        log_entry = LogEntryModel(
                            job_id=job_id, timestamp=datetime.now(), line=line
                        )
                        session.add(log_entry)
                        await session.commit()

                    if job_id in self._log_stream_subscribers:
                        for callback in self._log_stream_subscribers[job_id]:
                            try:
                                callback(line)
                            except Exception:
                                pass

                    await asyncio.sleep(1.4)
            except asyncio.CancelledError:
                pass
            finally:
                self._log_stream_tasks.pop(job_id, None)
                self._log_stream_subscribers.pop(job_id, None)

        task = asyncio.create_task(_stream_worker())
        self._log_stream_tasks[job_id] = task
        return task

    async def stop_log_stream(self, job_id: str):
        """Stop the log streaming simulation for a job."""
        if job_id in self._log_stream_tasks:
            task = self._log_stream_tasks[job_id]
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self._log_stream_tasks.pop(job_id, None)

        self._log_stream_subscribers.pop(job_id, None)

    async def stop_all_log_streams(self) -> None:
        """Stop every log stream this store instance is running."""
        for job_id in list(self._log_stream_tasks):
            await self.stop_log_stream(job_id)
        self._log_stream_tasks.clear()
        self._log_stream_subscribers.clear()

    def subscribe_to_log_stream(self, job_id: str, on_line: Callable[[str], None]):
        """Subscribe to log stream updates for a job."""
        if job_id not in self._log_stream_subscribers:
            self._log_stream_subscribers[job_id] = set()
        self._log_stream_subscribers[job_id].add(on_line)

    def unsubscribe_from_log_stream(self, job_id: str, on_line: Callable[[str], None]):
        """Unsubscribe from log stream updates for a job."""
        if job_id in self._log_stream_subscribers:
            self._log_stream_subscribers[job_id].discard(on_line)
            if not self._log_stream_subscribers[job_id]:
                del self._log_stream_subscribers[job_id]


# Global singleton store instance
_store_instance: Optional[DatabaseStore] = None


def get_store() -> DatabaseStore:
    """Get the singleton store instance."""
    global _store_instance
    if _store_instance is None:
        _store_instance = DatabaseStore()
    return _store_instance
