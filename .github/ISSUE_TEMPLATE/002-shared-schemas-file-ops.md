# Shared Schemas & File Operations

## Description
Create shared Pydantic schemas for job definitions, state, metrics, and nodes. Implement cross-platform file locking and atomic YAML read/write operations.

## User Stories
- As a backend developer, I want strongly typed schemas so that I can validate data at runtime
- As a system developer, I want cross-platform file locking so that multiple agents can safely update state files
- As a developer, I want atomic YAML operations so that we don't get corrupted files during concurrent access
- As a developer, I want metrics summarization so that we don't store excessive raw data

## Acceptance Criteria
- [ ] Pydantic models: JobSpec, JobState, JobResources, JobExecution, JobPaths, JobRetry, MetricEntry, GPUMetric, NodeSpec
- [ ] Cross-platform file locking using portalocker (with fcntl/msvcrt fallback)
- [ ] Atomic YAML read/write functions with validation
- [ ] Constants module with JobStatus enum, default paths, and intervals
- [ ] Metrics summarization: 30s rolling min/max/avg for GPU memory, utilization, temperature; CPU usage percent
- [ ] Unit tests for all schemas, file operations, and summarization logic (>90% coverage)
- [ ] No lint or type errors

## Technical Notes
- Use Pydantic v2 for validation
- File locking should work on Windows, Linux, and macOS
- Metrics summarization should use a sliding window approach
- Consider using Python's dataclasses for simple constants
- All functions should be pure where possible for easy testing