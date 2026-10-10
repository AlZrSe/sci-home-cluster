"""
Path utilities for the Scientific Home Cluster.
Provides helper functions for working with file paths in a cross-platform way.
"""

import os
from pathlib import Path
from typing import Union, Optional


def normalize_path(path: Union[str, Path]) -> Path:
    """
    Normalize a path to a Path object, resolving ~ and making absolute if needed.

    Args:
        path: Path string or Path object

    Returns:
        Normalized Path object
    """
    path_obj = Path(path)

    # Expand user home directory (~)
    if path_obj == Path("~"):
        path_obj = Path.home()
    elif str(path_obj).startswith("~/"):
        path_str = str(path_obj)
        path_obj = Path.home() / path_str[2:]

    # Make absolute if it's not already
    if not path_obj.is_absolute():
        path_obj = path_obj.resolve()

    return path_obj


def ensure_directory(path: Union[str, Path]) -> Path:
    """
    Ensure a directory exists, creating it if necessary.

    Args:
        path: Directory path

    Returns:
        Path object for the directory
    """
    path_obj = normalize_path(path)
    path_obj.mkdir(parents=True, exist_ok=True)
    return path_obj


def get_syncthing_root() -> Optional[Path]:
    """
    Get the Syncthing root directory from environment variable.

    Returns:
        Path to Syncthing root if SYNCTHING_ROOT is set, None otherwise
    """
    syncthing_root = os.environ.get("SYNCTHING_ROOT")
    if syncthing_root:
        return normalize_path(syncthing_root)
    return None


def get_jobs_directory() -> Optional[Path]:
    """
    Get the jobs directory within the Syncthing folder.

    Returns:
        Path to jobs directory if Syncthing root is configured, None otherwise
    """
    syncthing_root = get_syncthing_root()
    if syncthing_root:
        return syncthing_root / "jobs"
    return None


def get_nodes_directory() -> Optional[Path]:
    """
    Get the nodes directory within the Syncthing folder.

    Returns:
        Path to nodes directory if Syncthing root is configured, None otherwise
    """
    syncthing_root = get_syncthing_root()
    if syncthing_root:
        return syncthing_root / "nodes"
    return None


def get_job_directory(job_id: str) -> Optional[Path]:
    """
    Get the directory for a specific job within the Syncthing folder.

    Args:
        job_id: Job identifier

    Returns:
        Path to job directory if Syncthing root is configured, None otherwise
    """
    jobs_dir = get_jobs_directory()
    if jobs_dir:
        return jobs_dir / job_id
    return None


def get_job_state_file(job_id: str) -> Optional[Path]:
    """
    Get the path to a job's state.yaml file.

    Args:
        job_id: Job identifier

    Returns:
        Path to state.yaml file if Syncthing root is configured, None otherwise
    """
    job_dir = get_job_directory(job_id)
    if job_dir:
        return job_dir / "state.yaml"
    return None


def get_node_state_file(node_id: str) -> Optional[Path]:
    """
    Get the path to a node's state.yaml file.

    Args:
        node_id: Node identifier

    Returns:
        Path to state.yaml file if Syncthing root is configured, None otherwise
    """
    nodes_dir = get_nodes_directory()
    if nodes_dir:
        return nodes_dir / f"{node_id}.yaml"
    return None


def is_within_syncthing_root(path: Union[str, Path]) -> bool:
    """
    Check if a path is within the Syncthing root directory.

    Args:
        path: Path to check

    Returns:
        True if path is within Syncthing root, False otherwise
    """
    syncthing_root = get_syncthing_root()
    if not syncthing_root:
        return False

    try:
        path_obj = normalize_path(path)
        return syncthing_root in path_obj.parents or path_obj == syncthing_root
    except Exception:
        return False
