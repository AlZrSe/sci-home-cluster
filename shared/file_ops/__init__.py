"""
File operations package for the Scientific Home Cluster.
Provides cross-platform file locking, atomic YAML operations, and path utilities.
"""

from .locking import file_lock, shared_lock
from .yaml_utils import read_yaml, write_yaml, update_yaml
from .path_utils import (
    normalize_path,
    ensure_directory,
    get_syncthing_root,
    get_jobs_directory,
    get_nodes_directory,
    get_job_directory,
    get_job_state_file,
    get_node_state_file,
    is_within_syncthing_root,
)

__all__ = [
    "file_lock",
    "shared_lock",
    "read_yaml",
    "write_yaml",
    "update_yaml",
    "normalize_path",
    "ensure_directory",
    "get_syncthing_root",
    "get_jobs_directory",
    "get_nodes_directory",
    "get_job_directory",
    "get_job_state_file",
    "get_node_state_file",
    "is_within_syncthing_root",
]
