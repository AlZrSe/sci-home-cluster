# Tests Directory

This directory contains all tests for the Scientific Home Cluster platform.

## Test Types

- **Unit Tests**: Tests for individual components and functions
- **Integration Tests**: Tests for interactions between components
- **End-to-End Tests**: Tests for complete user workflows

## Running Tests

To run the full test suite:
```bash
pytest
```

To run tests with coverage:
```bash
pytest --cov=backend --cov=agent --cov=cli
```

## Test Structure

The tests are organized to match the project structure:
- `backend/tests/` - Backend-specific tests
- `agent/tests/` - Agent-specific tests (to be created)
- `cli/tests/` - CLI-specific tests (to be created)
- Shared tests can go in this root tests/ directory