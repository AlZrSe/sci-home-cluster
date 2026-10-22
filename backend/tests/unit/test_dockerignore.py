"""
Test that .dockerignore correctly excludes runtime-state files from build context.
This ensures no databases, shared memory files, or signing keys would be shipped
in a Docker image (regression test for issue #131).
"""

import fnmatch
import os
from pathlib import Path

import pytest


def parse_dockerignore(path: Path) -> list[str]:
    """Parse .dockerignore file and return list of patterns."""
    patterns = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                patterns.append(line)
    return patterns


def _match_recursive(path: str, suffix: str) -> bool:
    """Match path against a **/ suffix pattern."""
    parts = path.split("/")
    suffix_stripped = suffix.rstrip("/")
    for part in parts:
        if fnmatch.fnmatch(part, suffix_stripped):
            return True
    return False


def _match_directory(path: str, dir_pattern: str) -> bool:
    """Match path against a directory pattern."""
    parts = path.split("/")
    for part in parts:
        if fnmatch.fnmatch(part, dir_pattern):
            return True
    return False


def _match_path_with_dir(path: str, pattern: str) -> bool:
    """Match path against a pattern containing directory separator."""
    if fnmatch.fnmatch(path, pattern):
        return True
    parts = path.split("/")
    for i in range(len(parts)):
        subpath = "/".join(parts[i:])
        if fnmatch.fnmatch(subpath, pattern):
            return True
    return False


def _match_simple(path: str, pattern: str) -> bool:
    """Match path against a simple name pattern."""
    parts = path.split("/")
    for part in parts:
        if fnmatch.fnmatch(part, pattern):
            return True
    return False


def docker_match(path: str, pattern: str) -> bool:
    """
    Simulate Docker's .dockerignore pattern matching.
    Docker matches patterns against each PATH COMPONENT.
    Patterns with **/ match recursively.
    """
    # Normalize path - only remove leading ./ prefix
    if path.startswith("./"):
        path = path[2:]

    if pattern.startswith("**/"):
        return _match_recursive(path, pattern[3:])
    elif pattern.endswith("/"):
        return _match_directory(path, pattern.rstrip("/"))
    elif "/" in pattern:
        return _match_path_with_dir(path, pattern)
    else:
        return _match_simple(path, pattern)


def matches_any_pattern(path: str, patterns: list[str], is_dir: bool = False) -> bool:
    """Check if a path matches any dockerignore pattern."""
    test_path = path + "/" if is_dir else path
    for pattern in patterns:
        if docker_match(test_path, pattern):
            return True
    return False


class TestDockerignore:
    """Tests for .dockerignore pattern coverage."""

    @pytest.fixture(scope="class")
    def dockerignore_patterns(self):
        """Load patterns from .dockerignore."""
        repo_root = Path(__file__).parent.parent.parent.parent
        dockerignore_path = repo_root / ".dockerignore"
        assert dockerignore_path.exists(), ".dockerignore not found"
        return parse_dockerignore(dockerignore_path)

    @pytest.fixture(scope="class")
    def repo_root(self):
        """Repository root path."""
        return Path(__file__).parent.parent.parent.parent

    def test_runtime_state_directories_excluded(self, dockerignore_patterns, repo_root):
        """Verify runtime-state directories are excluded by .dockerignore."""
        runtime_dirs = ("data", ".shc", "syncthing")

        for root, dirs, _ in os.walk(repo_root):
            if ".git" in root.split(os.sep):
                continue

            rel_root = os.path.relpath(root, repo_root)
            if rel_root == ".":
                rel_root = ""

            for d in dirs:
                if d in runtime_dirs:
                    full_path = os.path.join(rel_root, d) if rel_root else d
                    assert matches_any_pattern(
                        full_path, dockerignore_patterns, is_dir=True
                    ), (
                        f"Runtime directory {full_path} not "
                        f"excluded by .dockerignore"
                    )

    def test_runtime_state_files_excluded(self, dockerignore_patterns, repo_root):
        """Verify runtime-state files are excluded by .dockerignore."""
        runtime_extensions = (
            ".db",
            ".db-shm",
            ".db-wal",
            ".sqlite",
            ".sqlite3",
        )

        for root, _, files in os.walk(repo_root):
            if ".git" in root.split(os.sep):
                continue

            rel_root = os.path.relpath(root, repo_root)
            if rel_root == ".":
                rel_root = ""

            for f in files:
                if f.endswith(runtime_extensions):
                    full_path = os.path.join(rel_root, f) if rel_root else f
                    assert matches_any_pattern(
                        full_path, dockerignore_patterns, is_dir=False
                    ), f"Runtime file {full_path} not " f"excluded by .dockerignore"

    def test_must_exclude_patterns_present(self, dockerignore_patterns):
        """Verify critical exclusion patterns are present in .dockerignore."""
        required_patterns = [
            ("**/data/", "**/data/"),
            ("**/.shc/", "**/.shc/"),
            ("**/syncthing/", "**/syncthing/"),
            ("**/*.db", "**/*.db"),
            ("**/*.db-shm", "**/*.db-shm"),
            ("**/*.db-wal", "**/*.db-wal"),
            ("**/*.sqlite", "**/*.sqlite"),
            ("**/*.sqlite3", "**/*.sqlite3"),
        ]

        for _desc, pattern in required_patterns:
            found = any(
                p == pattern or p.endswith(pattern.lstrip("**/"))
                for p in dockerignore_patterns
            )
            assert found, f"Required pattern '{pattern}' not found in .dockerignore"

    def test_no_database_files_in_backend(self, repo_root):
        """Verify no stray database files exist in backend/ or backend/tests/unit/."""
        exts = ("*.db", "*.db-shm", "*.db-wal", "*.sqlite", "*.sqlite3")
        backend_db_files = []
        unit_db_files = []

        for ext in exts:
            backend_db_files.extend(repo_root.glob(f"backend/{ext}"))
            unit_db_files.extend(repo_root.glob(f"backend/tests/unit/{ext}"))

        assert (
            not backend_db_files
        ), f"Stray database files in backend/: {backend_db_files}"
        assert (
            not unit_db_files
        ), f"Stray database files in backend/tests/unit/: {unit_db_files}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
