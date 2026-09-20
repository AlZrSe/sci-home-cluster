# Scientific Home Cluster — Shared Schemas & File Operations Specification

## Overview

**Purpose**: Create shared Pydantic schemas for job definitions, state, metrics, and nodes. Implement cross-platform file locking and atomic YAML read/write operations to ensure data consistency across distributed agents.

**Scope**: This specification covers the creation of shared data models that will be used by both the backend API and worker agents, along with file operation utilities for safely reading/writing YAML state files in the Syncthing shared folder. It does not cover the Syncthing sync service itself (issue #4) or the persistent database layer (issue #3).

## User Stories

- As a backend developer, I want strongly typed schemas so that I can validate data at runtime and catch errors early
- As a backend developer, I want shared schemas between API and agents so that data structures remain consistent across services
- As a system developer, I want cross-platform file locking so that multiple agents can safely update state files without corruption
- As a developer, I want atomic YAML operations so that we don't get corrupted files during concurrent access or system crashes
- As a developer, I want metrics summarization so that we don't store excessive raw data while maintaining useful insights
- As a developer, I want clear separation of concerns so that schemas can evolve independently of storage mechanisms
- As a tester, I want well-defined interfaces so that I can write comprehensive unit and integration tests
- As a DevOps engineer, I want predictable file formats so that I can troubleshoot and monitor the system effectively

## Acceptance Criteria

### Schema Definitions
- [ ] Pydantic models: JobSpec, JobState, JobResources, JobExecution, JobPaths, JobRetry, JobMetrics, NodeSpec, GPUMetric, CPUMetric
- [ ] All models use Pydantic v2 with proper validation and serialization
- [ ] Models include appropriate field constraints, defaults, and examples
- [ ] JobStatus enum is defined and used consistently across all relevant models
- [ ] Models support JSON/YAML serialization for Syncthing integration

### File Operations
- [ ] Cross-platform file locking mechanism using portalocker (with fcntl/msvcrt fallback)
- [ ] Atomic YAML read functions that handle file locking and validation
- [ ] Atomic YAML write functions that write to temporary file then atomically rename
- [ ] Functions properly handle IO errors, permission issues, and invalid YAML
- [ ] Read functions return validated Pydantic models or raise descriptive exceptions
- [ ] Write functions accept Pydantic models and serialize them to YAML

### Metrics Summarization
- [ ] Sliding window metrics summarization for GPU and CPU metrics
- [ ] 30s rolling window for min/max/avg calculations of GPU memory, utilization, temperature
- [ ] Rolling window for CPU usage percent
- [ ] Functions to add new metrics samples and retrieve current summaries
- [ ] Memory-efficient implementation that doesn't store excessive historical data

### Testing & Quality
- [ ] Unit tests for all schemas covering validation, serialization, and edge cases (>90% coverage)
- [ ] Unit tests for all file operations including success cases and error conditions
- [ ] Unit tests for metrics summarization logic with various data patterns
- [ ] Integration tests verifying schema compatibility between API models and shared models
- [ ] No lint errors (ruff check) and no type errors (mypy strict mode)
- [ ] Code follows project formatting standards (ruff format)

## Technical Notes

### Schema Design
- Use Pydantic v2 BaseSettings for models that need environment variable support
- Use plain BaseModel for data transfer objects
- Implement custom validators where needed for complex validation logic
- Provide factory methods for creating default instances
- Consider using RootModel for list-based responses if needed
- Ensure all models are JSON serializable for API responses

### File Locking Implementation
- Use portalocker library as primary cross-platform locking mechanism
- Implement fallback to fcntl (Unix) and msvcrt (Windows) if portalocker unavailable
- Use context managers (with statement) for automatic lock release
- Implement timeout mechanisms to prevent indefinite blocking
- Log lock acquisition/waiting times for monitoring and debugging

### Atomic File Operations
- Write to temporary file in same directory as target file
- Use os.replace() for atomic rename operation (available on Windows and Unix)
- Handle cross-device moves gracefully if temporary directory differs
- Ensure temporary files are cleaned up on failure
- Use file descriptors properly to prevent resource leaks

### Metrics Summarization
- Implement circular buffer or deque with fixed size for sliding window
- Calculate statistics incrementally when possible for performance
- Provide methods to reset summarization windows
- Handle edge cases like empty windows or single samples
- Consider thread-safety if summarization will be used concurrently

### Integration Considerations
- Shared schemas should be importable by both backend and agent services
- File operation utilities should be stateless where possible
- Consider dependency injection for file system operations to improve testability
- Document expected file naming conventions and directory structures
- Ensure schemas align with Syncthing folder structure expectations

## Directory Structure

After implementation, the shared module structure will be:

```
shared/
├── __init__.py              # Package exports
├── schemas/                 # Pydantic models
│   ├── __init__.py
│   ├── job_spec.py          # JobSpec model
│   ├── job_state.py         # JobState model
│   ├── job_resources.py     # JobResources model
│   ├── job_execution.py     # JobExecution model
│   ├── job_paths.py         # JobPaths model
│   ├── job_retry.py         # JobRetry model
│   ├── job_metrics.py       # JobMetrics model
│   ├── node_spec.py         # NodeSpec model
│   ├── gpu_metric.py        # GPUMetric model
│   ├── cpu_metric.py        # CPUMetric model
│   └── job_status.py        # JobStatus enum
├── file_ops/                # File operation utilities
│   ├── __init__.py
│   ├── locking.py           # Cross-platform file locking
│   ├── yaml_utils.py        # Atomic YAML read/write
│   └── path_utils.py        # Path resolution and validation
└── metrics/                 # Metrics summarization
    ├── __init__.py
    ├── sliding_window.py    # Sliding window implementation
    └── summarizer.py        # Metrics summarization logic
```

## Dependencies

### Runtime Dependencies
- `portalocker`: ^2.0.0 (for cross-platform file locking)
- `pydantic`: ^2.0.0 (already required by backend)

### Development Dependencies
- `pytest`: ^8.0.0 (already required)
- `pytest-asyncio`: ^0.23.0 (already required)
- Note: No additional development dependencies beyond what's already specified

## Test Scenarios

### Schema Tests
1. **JobSpec Validation**
   - Test that valid job specifications parse correctly
   - Test that missing required fields raise validation errors
   - Test that field constraints (min/max, regex) are enforced
   - Test that JSON/YAML serialization works correctly
   - Test that default values are applied correctly

2. **JobState Validation**
   - Test that all JobStatus enum values are accepted
   - Test that datetime fields handle various formats correctly
   - Test that optional fields work when None is provided
   - Test that computed properties (if any) return correct values

3. **NodeSpec Validation**
   - Test that GPUInfo lists validate correctly
   - Test that empty GPU lists are allowed
   - Test that hostname validation works as expected
   - Test that memory and CPU values are positive

### File Operation Tests
1. **Locking Mechanism**
   - Test that locks can be acquired and released
   - Test that multiple processes cannot acquire the same lock simultaneously
   - Test that locks are released even when exceptions occur
   - Test that timeout mechanisms work correctly
   - Test behavior on different platforms (Windows/Linux/macOS)

2. **YAML Read Functions**
   - Test that valid YAML files are parsed into correct Pydantic models
   - Test that invalid YAML raises appropriate exceptions
   - Test that missing files raise file not found errors
   - Test that permission errors are handled gracefully
   - Test that file locking prevents concurrent reads during writes

3. **YAML Write Functions**
   - Test that Pydantic models are correctly serialized to YAML
   - Test that written files can be read back and validate correctly
   - Test that write operations are atomic (no partial files visible)
   - Test that existing files are properly overwritten
   - Test that directory creation works when needed

### Metrics Summarization Tests
1. **Sliding Window**
   - Test that window maintains correct size
   - Test that oldest samples are dropped when window is full
   - Test that min/max/avg calculations are correct for known data sets
   - Test edge cases: empty window, single sample, all same values

2. **Summarizer**
   - Test that adding samples updates summaries correctly
   - Test that summaries can be reset to initial state
   - Test that summarizers work independently for different metrics
   - Test performance with large numbers of samples

### Integration Tests
1. **Schema Compatibility**
   - Test that shared schemas are compatible with API schemas
   - Test that data can flow from API → shared schemas → file storage → shared schemas → API
   - Test that versioning strategies work if schemas evolve

2. **End-to-End File Operations**
   - Test that a job can be created via API, saved to Syncthing folder, read by agent, updated, and synced back
   - Test that concurrent access from multiple agents doesn't cause data corruption
   - Test that system crashes during write operations don't leave corrupt files

## Implementation Notes

### Schema Organization
- Keep schemas focused and cohesive - each file should contain related models
- Use forward references sparingly and ensure they resolve correctly
- Consider separating frequently changing schemas from stable ones
- Document any intentional differences between shared schemas and API schemas

### Error Handling
- Define custom exception types for file operation errors where appropriate
- Provide meaningful error messages that include file paths and operation context
- Distinguish between different types of errors (validation, IO, permission, etc.)
- Consider whether errors should be fatal or recoverable based on context

### Performance Considerations
- Minimize schema validation overhead where performance is critical
- Consider caching compiled regex patterns if used in validators
- Optimize file operations for common case (no contention)
- Use efficient data structures for metrics summarization

### Security Considerations
- Validate file paths to prevent directory traversal attacks
- Consider file size limits to prevent DoS via massive files
- Ensure that temporary files don't contain sensitive data longer than necessary
- Review file permissions on created files and directories

### Configuration & Customization
- Allow configuration of file locking timeouts via environment variables
- Consider making metrics window size configurable
- Provide ways to override default file paths for testing or deployment flexibility
- Document any environment variables that affect behavior

## References

- [PROCESS.md](./PROCESS.md) - Development workflow
- [openapi.yaml](./openapi.yaml) - API contract that the backend must implement
- [AGENTS.md](./AGENTS.md) - Agent instructions and project structure
- [backend-setup-spec.md](./backend-setup-spec.md) - Backend project setup specification
- [in-memory-store-spec.md](./in-memory-store-spec.md) - In-memory store specification (for comparison)
- [002-shared-schemas-file-ops.md](./.github/ISSUE_TEMPLATE/002-shared-schemas-file-ops.md) - Original issue template

---
*Specification ready for grooming. Move issue #2 to `groomed` once this document is created and reviewed.*