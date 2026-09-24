"""
Atomic YAML read/write utilities for the Scientific Home Cluster.
Provides thread-safe, atomic operations for YAML files with validation.
"""

import os
import yaml
import tempfile
import logging
from typing import Type, TypeVar
from pydantic import BaseModel as PydanticBaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=PydanticBaseModel)


def read_yaml(file_path: str, model_class: Type[T]) -> T:
    """
    Read and validate a YAML file into a Pydantic model.

    Args:
        file_path: Path to the YAML file
        model_class: Pydantic model class to validate against

    Returns:
        Instance of model_class populated with data from YAML file

    Raises:
        FileNotFoundError: If file does not exist
        yaml.YAMLError: If YAML is malformed
        ValidationError: If data doesn't match model schema
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"YAML file not found: {file_path}")

    try:
        with open(file_path, "r") as f:
            data = yaml.safe_load(f)

        if data is None:
            data = {}

        return model_class(**data)
    except yaml.YAMLError as e:
        logger.error(f"Failed to parse YAML file {file_path}: {e}")
        raise
    except Exception as e:
        logger.error(
            f"Failed to validate YAML file {file_path} against {model_class.__name__}: {e}"
        )
        raise


def write_yaml(file_path: str, data: PydanticBaseModel) -> None:
    """
    Write a Pydantic model to a YAML file atomically.

    Args:
        file_path: Path to the YAML file to write
        data: Pydantic model instance to serialize

    Raises:
        IOError: If file cannot be written
        yaml.YAMLError: If data cannot be serialized to YAML
    """
    # Ensure directory exists
    directory = os.path.dirname(file_path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)

    # Serialize data to YAML string
    try:
        # Use dict() method for Pydantic v1, model_dump() for v2
        if hasattr(data, "model_dump"):
            yaml_data = data.model_dump()
        else:
            yaml_data = data.dict()

        yaml_content = yaml.dump(yaml_data, default_flow_style=False, sort_keys=False)
    except Exception as e:
        logger.error(f"Failed to serialize data to YAML: {e}")
        raise

    # Write atomically using temporary file
    try:
        # Create temporary file in same directory
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=directory,
            prefix=os.path.basename(file_path) + ".",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_file.write(yaml_content)
            tmp_file_path = tmp_file.name

        # Atomically replace target file with temporary file
        os.replace(tmp_file_path, file_path)

    except Exception as e:
        # Clean up temporary file on error
        if "tmp_file_path" in locals() and os.path.exists(tmp_file_path):
            try:
                os.unlink(tmp_file_path)
            except OSError:
                pass
        logger.error(f"Failed to write YAML file {file_path}: {e}")
        raise


def update_yaml(file_path: str, model_class: Type[T], update_func) -> T:
    """
    Read a YAML file, update its contents, and write it back atomically.

    Args:
        file_path: Path to the YAML file
        model_class: Pydantic model class for validation
        update_func: Function that takes a model instance and returns updated instance

    Returns:
        Updated model instance

    Raises:
        FileNotFoundError: If file does not exist
        yaml.YAMLError: If YAML is malformed
        ValidationError: If data doesn't match model schema
        IOError: If file cannot be written
    """
    # Read existing data
    current_data = read_yaml(file_path, model_class)

    # Apply updates
    updated_data = update_func(current_data)

    # Write back atomically
    write_yaml(file_path, updated_data)

    return updated_data
