"""
In-memory data store for the Scientific Home Cluster backend.
"""
import asyncio
import random
from typing import Dict, List, Optional, Tuple, Set, Callable, TypeVar
from datetime import datetime

from backend.models.job_state import JobState
from backend.models.job_spec import JobSpec
from backend.models.job_status import JobStatus
from backend.models.node_spec import NodeSpec, GPUInfo
from backend.models.job_metrics import JobMetrics, JobMetricsSummary
from backend.models.gpu_metric import GPUMetric
from backend.models.cpu_metric import CPUMetric



T = TypeVar('T')


class LinearCongruentialGenerator:
    'Linear Congruential Generator matching the frontend mock server implementation.'
    
    def __init__(self, seed: int = 1337):
        self.seed = seed
        self.multiplier = 1103515245
        self.increment = 12345
        self.modulus = 2147483648
    
    def rand(self) -> float:
        'Generate next random number in [0, 1).'
        self.seed = (self.seed * self.multiplier + self.increment) % self.modulus
        return self.seed / self.modulus
    
    def pick(self, arr: List[T]) -> T:
        'Pick a random element from the array.'
        if not arr:
            raise ValueError('Cannot pick from an empty array')
        return arr[math.floor(self.rand() * len(arr))]
class InMemoryStore:
    """Thread-safe in-memory store for jobs, nodes, metrics, and logs."""
    
    def __init__(self):
        # Locks for thread safety
        self._jobs_lock = asyncio.Lock()
        self._nodes_lock = asyncio.Lock()
        self._metrics_lock = asyncio.Lock()
        self._logs_lock = asyncio.Lock()
        
        # Storage
        self._jobs: Dict[str, JobState] = {}
        self._nodes: Dict[str, NodeSpec] = {}
        self._metrics_cache: Dict[str, JobMetrics] = {}
        self._log_history: Dict[str, List[str]] = {}
        
        # For job ID generation
        self._job_counter: int = 0
        
        # For WebSocket log streaming simulation
        self._log_stream_tasks: Dict[str, asyncio.Task] = {}
        self._log_stream_subscribers: Dict[str, Set[Callable[[str], None]]] = {}
        
        # Initialize with seed data
        self._seed_data()
    
    def _seed_data(self):
        """Seed the store with data matching the frontend mock server."""
        # Use a deterministic random seed for seed data generation
        seed = 1337
        rnd = random.Random(seed)
        
        # Fixed reference time for deterministic seed data
        REFERENCE_TIME = datetime(2026, 9, 19, 12, 0, 0)
        
        # Helper function to generate ISO timestamp string for given milliseconds ago
        def iso(msAgo: int) -> str:
            past = REFERENCE_TIME.timestamp() - (msAgo / 1000.0)
            return datetime.fromtimestamp(past).isoformat().replace("T", " ")[:19]
        
        # Generate nodes exactly as in the mock server
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
        
        # Convert to NodeSpec objects and store
        for node_dict in nodes_data:
            gpus = [GPUInfo(**gpu_dict) for gpu_dict in node_dict["gpus"]]
            node_dict["gpus"] = gpus
            # Note: last_heartbeat is a string, but NodeSpec expects a datetime.
            # We'll convert the string back to datetime for the model.
            # However, the mock server's last_heartbeat is an ISO string without
            # milliseconds, and the NodeSpec model expects a datetime.
            # We'll parse the string.
            node_dict["last_heartbeat"] = datetime.fromisoformat(node_dict["last_heartbeat"].replace(" ", "T"))
            node = NodeSpec(**node_dict)
            self._nodes[node.node_id] = node
        
        # Generate jobs exactly as in the mock server
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
        
        def makeSpec(name: str) -> JobSpec:
            gpus = rnd.randint(1, 2)  # 1 or 2
            return JobSpec(
                name=name,
                command=f"python -u scripts/{name.replace('-', '_')}.py --config configs/{name}.yaml",
                working_dir=f"/sync/projects/{name}",
                env={"PYTHONUNBUFFERED": "1", "CUDA_VISIBLE_DEVICES": "0"},
                resources={
                    "gpus": gpus,
                    "cpus": rnd.choice([4, 8, 12, 16]),
                    "memory_gb": rnd.choice([8, 16, 32, 64]),
                    "vram_gb": rnd.choice([8, 12, 16, 24]),
                },
                paths={ "input": f"data/{name}/in", "output": f"data/{name}/out" },
                retry={ "max_retries": 3, "retry_delay_seconds": 60 },
            )
        
        for i, name in enumerate(names):
            status = statuses[i]
            created = 1000 * 60 * (12 + i * 47)  # milliseconds ago
            running = status != JobStatus.PENDING
            done = status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)
            job_id = f"job-{1050 - i}"
            spec = makeSpec(name)
            node_id = rnd.choice(["node-alpha", "node-beta", "node-gamma"]) if running else None
            # Create datetime objects for the timestamps
            created_at = datetime.fromtimestamp(REFERENCE_TIME.timestamp() - (created / 1000.0))
            started_at = datetime.fromtimestamp(REFERENCE_TIME.timestamp() - ((created - 1000 * 60 * 3) / 1000.0)) if running else None
            completed_at = datetime.fromtimestamp(REFERENCE_TIME.timestamp() - ((created - 1000 * 60 * 40) / 1000.0)) if done else None
            exit_code = 0 if status == JobStatus.COMPLETED else (137 if status == JobStatus.FAILED else None)
            error = "CUDA out of memory at step 12841" if status == JobStatus.FAILED else None
            retry_count = 2 if status == JobStatus.FAILED else 0
            
            job_state = JobState(
                job_id=job_id,
                spec=spec,
                status=status,
                node_id=node_id,
                created_at=created_at,
                started_at=started_at,
                completed_at=completed_at,
                exit_code=exit_code,
                error=error,
                retry_count=retry_count,
            )
            self._jobs[job_id] = job_state
            # Initialize job counter for future job IDs
            job_num = int(job_id.split("-")[1])
            if job_num > self._job_counter:
                self._job_counter = job_num
            
            # Initialize empty log history for the job
            self._log_history[job_id] = []
    
    # Job CRUD operations
    async def create_job(self, job_spec: JobSpec) -> JobState:
        """Create a new job and return the created job state."""
        async with self._jobs_lock:
            # Generate a new job ID
            self._job_counter += 1
            job_id = f"job-{self._job_counter}"
            
            # Create the job state with default values
            now = datetime.now()
            job_state = JobState(
                job_id=job_id,
                spec=job_spec,
                status=JobStatus.PENDING,
                created_at=now,
                retry_count=0,
            )
            
            # Store the job
            self._jobs[job_id] = job_state
            
            # Initialize empty log history for the job
            async with self._logs_lock:
                self._log_history[job_id] = []
            
            return job_state
    
    async def get_job(self, job_id: str) -> Optional[JobState]:
        """Get a job by ID."""
        async with self._jobs_lock:
            return self._jobs.get(job_id)
    
    async def list_jobs(
        self, 
        status: Optional[str] = None,
        node_id: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> Tuple[List[JobState], int]:
        """
        List jobs with filtering and pagination.
        Returns a tuple of (jobs, total_count).
        """
        async with self._jobs_lock:
            # Start with all jobs
            jobs = list(self._jobs.values())
            
            # Apply filters
            if status:
                try:
                    job_status = JobStatus(status)
                    jobs = [job for job in jobs if job.status == job_status]
                except ValueError:
                    # Invalid status, return empty list
                    pass
            
            if node_id:
                jobs = [job for job in jobs if job.node_id == node_id]
            
            if search:
                # Search in job name (from spec)
                search_lower = search.lower()
                jobs = [job for job in jobs if search_lower in job.spec.name.lower()]
            
            # Get total count before pagination
            total = len(jobs)
            
            # Apply pagination
            jobs = jobs[offset:offset + limit]
            
            return jobs, total
    
    async def update_job(self, job_id: str, **kwargs) -> Optional[JobState]:
        """Update a job's fields and return the updated job."""
        async with self._jobs_lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            
            # Update the fields that are provided
            for key, value in kwargs.items():
                if hasattr(job, key):
                    setattr(job, key, value)
            
            return job
    
    async def delete_job(self, job_id: str) -> bool:
        """Delete a job by ID. Returns True if deleted, False if not found."""
        async with self._jobs_lock:
            if job_id in self._jobs:
                del self._jobs[job_id]
                # Also clean up associated data
                async with self._metrics_lock:
                    self._metrics_cache.pop(job_id, None)
                async with self._logs_lock:
                    self._log_history.pop(job_id, None)
                # Stop any log streaming simulation for this job
                await self._stop_log_stream(job_id)
                return True
            return False
    
    # Node CRUD operations
    async def list_nodes(self) -> List[NodeSpec]:
        """List all nodes."""
        async with self._nodes_lock:
            return list(self._nodes.values())
    
    async def get_node(self, node_id: str) -> Optional[NodeSpec]:
        """Get a node by ID."""
        async with self._nodes_lock:
            return self._nodes.get(node_id)
    
    async def update_node(self, node_id: str, **kwargs) -> Optional[NodeSpec]:
        """Update a node's fields and return the updated node."""
        async with self._nodes_lock:
            node = self._nodes.get(node_id)
            if not node:
                return None
            
            # Update the fields that are provided
            for key, value in kwargs.items():
                if hasattr(node, key):
                    setattr(node, key, value)
            
            return node

    async def create_node(self, node_spec: NodeSpec) -> NodeSpec:
        """Create a new node and return the created node spec."""
        async with self._nodes_lock:
            # Check if node already exists
            if node_spec.node_id in self._nodes:
                raise ValueError(f"Node with ID {node_spec.node_id} already exists")
            
            # Store the node
            self._nodes[node_spec.node_id] = node_spec
            return node_spec

    async def delete_node(self, node_id: str) -> bool:
        """Delete a node by ID. Returns True if deleted, if not found."""
        async with self._nodes_lock:
            if node_id in self._nodes:
                del self._nodes[node_id]
                return True
            return False

    async def reset(self):
        """Reset the store to its initial seeded state for testing."""
        # Acquire all locks to ensure thread safety during reset
        async with self._jobs_lock, self._nodes_lock, self._metrics_lock, self._logs_lock:
            # Clear all storage
            self._jobs.clear()
            self._nodes.clear()
            self._metrics_cache.clear()
            self._log_history.clear()
            
            # Reset job counter
            self._job_counter = 0
            
            # Clear any active log stream tasks
            for task in self._log_stream_tasks.values():
                task.cancel()
            self._log_stream_tasks.clear()
            
            # Clear log stream subscribers
            self._log_stream_subscribers.clear()
            
            # Re-seed with initial data
            self._seed_data()

    # Metrics operations
    async def get_job_metrics(self, job_id: str) -> Optional[JobMetrics]:
        """Get job metrics, generating and caching if not present."""
        # First check cache
        async with self._metrics_lock:
            if job_id in self._metrics_cache:
                return self._metrics_cache[job_id]
        
        # If not in cache, generate metrics
        metrics = await self._generate_job_metrics(job_id)
        
        # Cache the metrics
        async with self._metrics_lock:
            self._metrics_cache[job_id] = metrics
        
        return metrics
    
    async def _generate_job_metrics(self, job_id: str) -> Optional[JobMetrics]:
        """Generate job metrics using the same algorithm as the mock server."""
        # Verify the job exists
        job = await self.get_job(job_id)
        if not job:
            return None
        
        # Use a job-specific seed derived from the job ID and the global seed (1337)
        # to ensure deterministic metrics for each job.
        # We'll use a simple hash of the job ID combined with the global seed.
        seed = 1337 + hash(job_id)
        rnd = random.Random(seed)
        
        # Constants from the mock server
        total = 24_576
        util = 55
        mem = 9_000
        cpu = 30
        
        gpu_metrics: List[GPUMetric] = []
        cpu_metrics: List[CPUMetric] = []
        
        for i in range(120, -1, -1):  # i from 120 down to 0 inclusive
            util = min(99, max(6, util + (rnd.random() - 0.5) * 18))
            mem = min(total, max(1200, mem + (rnd.random() - 0.45) * 900))
            cpu = min(100, max(4, cpu + (rnd.random() - 0.5) * 14))
            timestamp = datetime.now()  # In the mock server, timestamp is based on i*30_000 ms ago
            # However, note that the mock server uses:
            #   const timestamp = iso(i * 30_000);
            # where iso(msAgo) returns a timestamp msAgo milliseconds ago.
            # We'll replicate that.
            ms_ago = i * 30_000
            timestamp = datetime.fromtimestamp(datetime.now().timestamp() - (ms_ago / 1000.0))
            
            gpu_metrics.append(GPUMetric(
                timestamp=timestamp,
                gpu_index=0,
                memory_used_mb=round(mem),
                memory_total_mb=total,
                utilization_percent=round(util),
                temperature_c=round(48 + util * 0.28),
            ))
            
            cpu_metrics.append(CPUMetric(
                timestamp=timestamp,
                cpu_percent=round(cpu),
                memory_percent=round(30 + cpu * 0.4),
            ))
        
        # Calculate summary
        mems = [m.memory_used_mb for m in gpu_metrics]
        utils = [m.utilization_percent for m in gpu_metrics]
        avg = lambda a: round(sum(a) / len(a)) if a else 0
        
        summary = JobMetricsSummary(
            gpu_memory_min_mb=min(mems),
            gpu_memory_max_mb=max(mems),
            gpu_memory_avg_mb=avg(mems),
            gpu_util_min=min(utils),
            gpu_util_max=max(utils),
            gpu_util_avg=avg(utils),
            cpu_avg_percent=avg([m.cpu_percent for m in cpu_metrics]),
        )
        
        return JobMetrics(
            job_id=job_id,
            gpu_metrics=gpu_metrics,
            cpu_metrics=cpu_metrics,
            summary=summary,
        )
    
    # Log operations
    async def get_job_logs(self, job_id: str) -> List[str]:
        """Get job logs, generating and caching if not present."""
        async with self._logs_lock:
            if job_id in self._log_history:
                return self._log_history[job_id]
            # If not present, generate logs (like the mock server)
            logs = self._generate_job_logs(job_id)
            self._log_history[job_id] = logs
        return logs

    def _generate_job_logs(self, job_id: str) -> List[str]:
        """Generate job logs using the same algorithm as the mock server."""
        # Use a job-specific seed derived from the job ID and the global seed (1337)
        # to ensure deterministic logs for each job.
        seed = 1337 + hash(job_id)
        rnd = random.Random(seed)
        
        def stamp() -> str:
            return datetime.now().isoformat().replace("T", " ")[:19]
        
        log_templates = [
            (lambda s: f"INFO  step={s} loss={(2.4 - s * 0.0007):.4f} lr=3.0e-4"),
            (lambda s: f"INFO  step={s} throughput={(180 + rnd.random() * 40):.1f} samples/s"),
            (lambda s: f"DEBUG allocator: reserved={(8 + rnd.random() * 6):.2f} GiB step={s}"),
            (lambda s: f"INFO  checkpoint written to data/out/ckpt-{s}.pt"),
            (lambda s: f"WARN  syncthing folder scan delayed by 1.4s"),
        ]
        
        lines = [
            f"{stamp()} INFO  job {job_id} accepted by scheduler",
            f"{stamp()} INFO  syncing input folder via syncthing (device alpha)",
            f"{stamp()} INFO  environment ready: python 3.12.4, torch 2.6.0+cu124",
            f"{stamp()} INFO  starting command",
        ]
        
        for i in range(60):
            # In the mock server, the step in the log template is 1000 + i * 50
            step = 1000 + i * 50
            template = rnd.choice(log_templates)
            lines.append(f"{stamp()} {template(step)}")
        
        return lines
    
    # WebSocket log streaming simulation
    async def start_log_stream(self, job_id: str, on_line: Callable[[str], None]) -> asyncio.Task:
        """
        Start simulating log streaming for a job.
        Returns a task that can be cancelled to stop the stream.
        """
        async with self._logs_lock:
            if job_id not in self._log_history:
                # Initialize log history if not present
                self._log_history[job_id] = self._generate_job_logs(job_id)
        
        # Create a task that will periodically add new log lines
        async def _stream_worker():
            step = 5000  # Starting step from the mock server
            try:
                while True:
                    # Check if the job exists and is running
                    job = await self.get_job(job_id)
                    if not job or job.status != JobStatus.RUNNING:
                        # Stop streaming if job is not running
                        break
                    
                    # Generate a new log line
                    step += 50
                    # Use the same logic as the mock server's subscribeLogs
                    # We need to pick a log template using the same random sequence
                    # as the mock server would use. However, for simplicity, we'll
                    # use a job-specific random generator.
                    seed = 1337 + hash(job_id) + step  # Vary the seed with step to get different sequence
                    rnd = random.Random(seed)
                    template = rnd.choice([
                        (lambda s: f"INFO  step={s} loss={(2.4 - s * 0.0007):.4f} lr=3.0e-4"),
                        (lambda s: f"INFO  step={s} throughput={(180 + rnd.random() * 40):.1f} samples/s"),
                        (lambda s: f"DEBUG allocator: reserved={(8 + rnd.random() * 6):.2f} GiB step={s}"),
                        (lambda s: f"INFO  checkpoint written to data/out/ckpt-{s}.pt"),
                        (lambda s: f"WARN  syncthing folder scan delayed by 1.4s"),
                    ])
                    line = f"{datetime.now().isoformat().replace('T', ' ')[:19]} {template(step)}"
                    
                    # Add the line to the job's log history
                    async with self._logs_lock:
                        if job_id in self._log_history:
                            self._log_history[job_id].append(line)
                    
                    # Notify subscribers
                    if job_id in self._log_stream_subscribers:
                        for callback in self._log_stream_subscribers[job_id]:
                            try:
                                callback(line)
                            except Exception:
                                pass  # Ignore errors in callbacks
                    
                    # Wait before generating the next line
                    await asyncio.sleep(1.4)  # Same interval as mock server
            except asyncio.CancelledError:
                # Task was cancelled, exit gracefully
                pass
        
        task = asyncio.create_task(_stream_worker())
        self._log_stream_tasks[job_id] = task
        return task
    
    async def _stop_log_stream(self, job_id: str):
        """Stop the log streaming simulation for a job."""
        if job_id in self._log_stream_tasks:
            task = self._log_stream_tasks[job_id]
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            del self._log_stream_tasks[job_id]
        
        # Clean up subscribers
        if job_id in self._log_stream_subscribers:
            del self._log_stream_subscribers[job_id]
    
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















