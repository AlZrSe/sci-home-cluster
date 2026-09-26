"""
Syncthing service for scanning and monitoring the Syncthing shared folder.
Provides read-only access to job and node state files.
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Dict, Optional, Set
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from backend.models.job_state import JobState
from backend.models.node_spec import NodeSpec
from backend.store.memory import get_store
from shared.file_ops import read_yaml

logger = logging.getLogger(__name__)


class SyncthingEventHandler(FileSystemEventHandler):
    """Handle file system events for Syncthing folder."""

    def __init__(self, service: "SyncthingService"):
        self.service = service
        self._pending_events: Dict[str, float] = {}  # file_path -> timestamp
        self._debounce_ms = 500
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        """Set the event loop for async operations."""
        self._loop = loop

    def on_created(self, event):
        if not event.is_directory and self._is_relevant_file(event.src_path):
            self._schedule_process(event.src_path)

    def on_modified(self, event):
        if not event.is_directory and self._is_relevant_file(event.src_path):
            self._schedule_process(event.src_path)

    def on_deleted(self, event):
        if not event.is_directory and self._is_relevant_file(event.src_path):
            self._schedule_delete(event.src_path)

    def _is_relevant_file(self, path: str) -> bool:
        """Check if the file is a relevant YAML state file."""
        path_obj = Path(path)
        # Only process .yaml files (case-insensitive)
        if path_obj.suffix.lower() != ".yaml":
            return False
        # Ignore temporary files
        if path_obj.name.startswith(".") or path_obj.name.endswith(".tmp"):
            return False
        return True

    def _schedule_process(self, file_path: str):
        """Schedule a file for processing with debouncing."""
        self._pending_events[file_path] = time.time()
        # Schedule debounced processing
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._debounced_process(file_path), self._loop
            )

    def _schedule_delete(self, file_path: str):
        """Schedule a file deletion for processing."""
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._process_delete(file_path), self._loop
            )

    async def _debounced_process(self, file_path: str):
        """Process file after debounce period."""
        await asyncio.sleep(self._debounce_ms / 1000.0)
        # Check if another event came in during debounce
        if (
            self._pending_events.get(file_path, 0)
            > time.time() - self._debounce_ms / 1000.0
        ):
            return  # Another event will handle it
        self._pending_events.pop(file_path, None)
        await self._process_file(file_path)

    async def _process_file(self, file_path: str):
        """Process a created/modified YAML file."""
        try:
            path_obj = Path(file_path)
            relative_path = path_obj.relative_to(self.service.root_path)

            # Determine if it's a job or node file
            if relative_path.parts[0] == "jobs" and len(relative_path.parts) >= 3:
                job_id = relative_path.parts[1]
                if relative_path.parts[2] == "state.yaml":
                    await self.service._process_job_file(job_id, file_path)
            elif relative_path.parts[0] == "nodes" and len(relative_path.parts) >= 2:
                node_id = relative_path.parts[1].replace(".yaml", "")
                await self.service._process_node_file(node_id, file_path)

        except Exception as e:
            logger.error(f"Error processing file {file_path}: {e}")

    async def _process_delete(self, file_path: str):
        """Process a deleted YAML file."""
        try:
            path_obj = Path(file_path)
            relative_path = path_obj.relative_to(self.service.root_path)

            if relative_path.parts[0] == "jobs" and len(relative_path.parts) >= 3:
                job_id = relative_path.parts[1]
                if relative_path.parts[2] == "state.yaml":
                    await self.service._handle_job_deleted(job_id)
            elif relative_path.parts[0] == "nodes" and len(relative_path.parts) >= 2:
                node_id = relative_path.parts[1].replace(".yaml", "")
                await self.service._handle_node_deleted(node_id)

        except Exception as e:
            logger.error(f"Error processing delete for {file_path}: {e}")


class SyncthingService:
    """Service for scanning and monitoring Syncthing shared folder."""

    def __init__(self, root_path: Path):
        self.root_path = Path(root_path)
        self.observer: Optional[Observer] = None
        self.event_handler: Optional[SyncthingEventHandler] = None
        self._running = False
        self._scan_task: Optional[asyncio.Task] = None
        self._heartbeat_check_task: Optional[asyncio.Task] = None
        self._processed_files: Set[str] = set()

    async def start(self):
        """Start the Syncthing watcher."""
        if self._running:
            return

        logger.info(f"Starting Syncthing service with root: {self.root_path}")

        # Ensure directories exist
        jobs_dir = self.root_path / "jobs"
        nodes_dir = self.root_path / "nodes"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        nodes_dir.mkdir(parents=True, exist_ok=True)

        # Initial scan
        await self._initial_scan()

        # Start file system watcher
        self.event_handler = SyncthingEventHandler(self)
        # Set the event loop for cross-thread async operations
        self.event_handler.set_loop(asyncio.get_running_loop())
        self.observer = Observer()
        self.observer.schedule(self.event_handler, str(jobs_dir), recursive=True)
        self.observer.schedule(self.event_handler, str(nodes_dir), recursive=True)
        self.observer.start()

        # Start periodic heartbeat check
        self._running = True
        self._heartbeat_check_task = asyncio.create_task(self._heartbeat_monitor())

        logger.info("Syncthing service started")

    async def stop(self):
        """Stop the Syncthing watcher."""
        if not self._running:
            return

        logger.info("Stopping Syncthing service")
        self._running = False

        # Stop observer
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=5.0)
            self.observer = None

        # Cancel tasks
        if self._heartbeat_check_task:
            self._heartbeat_check_task.cancel()
            try:
                await self._heartbeat_check_task
            except asyncio.CancelledError:
                pass

        logger.info("Syncthing service stopped")

    async def _initial_scan(self):
        """Perform initial scan of existing files."""
        logger.info("Performing initial scan of Syncthing folder")

        jobs_dir = self.root_path / "jobs"
        nodes_dir = self.root_path / "nodes"

        # Scan jobs
        if jobs_dir.exists():
            for job_dir in jobs_dir.iterdir():
                if job_dir.is_dir():
                    state_file = job_dir / "state.yaml"
                    if state_file.exists():
                        await self._process_job_file(job_dir.name, str(state_file))

        # Scan nodes
        if nodes_dir.exists():
            for node_file in nodes_dir.glob("*.yaml"):
                if node_file.is_file():
                    node_id = node_file.stem
                    await self._process_node_file(node_id, str(node_file))

        logger.info(
            f"Initial scan complete. Processed {len(self._processed_files)} files"
        )

    async def _process_job_file(self, job_id: str, file_path: str):
        """Process a job state YAML file."""
        try:
            # Validate job ID format
            if not job_id.startswith("job-") or not job_id[4:].isdigit():
                logger.warning(f"Invalid job ID format: {job_id}")
                return

            # Read and parse YAML
            job_state = read_yaml(Path(file_path), JobState)
            if job_state is None:
                logger.warning(f"Failed to parse job state file: {file_path}")
                return

            # Update store
            store = get_store()
            existing_job = await store.get_job(job_id)
            if existing_job:
                # Update only fields from YAML
                await self._update_job_from_yaml(store, job_state)
            else:
                # Create new job with the job_id from YAML
                await store._create_job_with_id(job_state)

            self._processed_files.add(file_path)
            logger.debug(f"Processed job file: {job_id}")

        except Exception as e:
            logger.error(f"Error processing job file {file_path}: {e}")

    async def _update_job_from_yaml(self, store, job_state: JobState):
        """Update job in store with data from YAML."""
        await store.update_job(
            job_state.job_id,
            status=job_state.status,
            node_id=job_state.node_id,
            started_at=job_state.started_at,
            completed_at=job_state.completed_at,
            exit_code=job_state.exit_code,
            error=job_state.error,
            retry_count=job_state.retry_count,
        )

    async def _process_node_file(self, node_id: str, file_path: str):
        """Process a node state YAML file."""
        try:
            # Read and parse YAML
            node_spec = read_yaml(Path(file_path), NodeSpec)
            if node_spec is None:
                logger.warning(f"Failed to parse node spec file: {file_path}")
                return

            # Update store
            store = get_store()
            existing_node = await store.get_node(node_id)
            if existing_node:
                await store.update_node(
                    node_id,
                    hostname=node_spec.hostname,
                    gpus=node_spec.gpus,
                    cpus=node_spec.cpus,
                    memory_gb=node_spec.memory_gb,
                    os=node_spec.os,
                    status=node_spec.status,
                    last_heartbeat=node_spec.last_heartbeat,
                    current_job_id=node_spec.current_job_id,
                )
            else:
                await store.create_node(node_spec)

            self._processed_files.add(file_path)
            logger.debug(f"Processed node file: {node_id}")

        except Exception as e:
            logger.error(f"Error processing node file {file_path}: {e}")

    async def _handle_job_deleted(self, job_id: str):
        """Handle job file deletion."""
        store = get_store()
        await store.delete_job(job_id)
        logger.info(f"Deleted job from store: {job_id}")

    async def _handle_node_deleted(self, node_id: str):
        """Handle node file deletion."""
        store = get_store()
        await store.delete_node(node_id)
        logger.info(f"Deleted node from store: {node_id}")

    async def _heartbeat_monitor(self):
        """Monitor node heartbeats and mark offline nodes."""
        while self._running:
            try:
                await asyncio.sleep(30)  # Check every 30 seconds
                store = get_store()
                nodes = await store.list_nodes()
                now = datetime.now()

                for node in nodes:
                    # Calculate time since last heartbeat
                    if node.last_heartbeat:
                        # Ensure timezone-aware comparison
                        if node.last_heartbeat.tzinfo is None:
                            last_hb = node.last_heartbeat.replace(tzinfo=now.tzinfo)
                        else:
                            last_hb = node.last_heartbeat

                        delta = (now - last_hb).total_seconds()
                        if delta > 90 and node.status == "ONLINE":
                            await store.update_node(node.node_id, status="OFFLINE")
                            logger.info(
                                f"Node {node.node_id} marked OFFLINE "
                                f"(last heartbeat {delta:.0f}s ago)"
                            )
                        elif delta <= 90 and node.status == "OFFLINE":
                            await store.update_node(node.node_id, status="ONLINE")
                            logger.info(f"Node {node.node_id} marked ONLINE")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in heartbeat monitor: {e}")

    async def manual_scan(self) -> Dict[str, int]:
        """Manually trigger a full scan of the Syncthing folder."""
        logger.info("Manual scan triggered")

        jobs_dir = self.root_path / "jobs"
        nodes_dir = self.root_path / "nodes"

        # Get current files
        current_job_files = set()
        current_node_files = set()

        if jobs_dir.exists():
            for job_dir in jobs_dir.iterdir():
                if job_dir.is_dir():
                    state_file = job_dir / "state.yaml"
                    if state_file.exists():
                        current_job_files.add(str(state_file))

        if nodes_dir.exists():
            for node_file in nodes_dir.glob("*.yaml"):
                if node_file.is_file():
                    current_node_files.add(str(node_file))

        current_files = current_job_files | current_node_files

        # Detect deleted files
        deleted_files = self._processed_files - current_files
        for file_path in deleted_files:
            try:
                path_obj = Path(file_path)
                relative_path = path_obj.relative_to(self.root_path)

                if relative_path.parts[0] == "jobs" and len(relative_path.parts) >= 3:
                    job_id = relative_path.parts[1]
                    if relative_path.parts[2] == "state.yaml":
                        await self._handle_job_deleted(job_id)
                elif relative_path.parts[0] == "nodes" and len(relative_path.parts) >= 2:
                    node_id = relative_path.parts[1].replace(".yaml", "")
                    await self._handle_node_deleted(node_id)
            except Exception as e:
                logger.error(f"Error handling deleted file {file_path}: {e}")

        # Process new/updated files
        initial_count = len(self._processed_files)
        await self._initial_scan()
        new_count = len(self._processed_files) - initial_count

        # Update processed files to match current state
        self._processed_files = current_files

        return {"scanned": new_count, "total_processed": len(self._processed_files)}

    def get_status(self) -> Dict:
        """Get current service status."""
        return {
            "running": self._running,
            "root_path": str(self.root_path),
            "jobs_dir_exists": (self.root_path / "jobs").exists(),
            "nodes_dir_exists": (self.root_path / "nodes").exists(),
            "processed_files_count": len(self._processed_files),
        }
