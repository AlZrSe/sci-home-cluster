"""
Integration tests for Syncthing autoAcceptFolders behaviour.
"""

import pytest
import xml.etree.ElementTree as ET

from backend.tests.compose.conftest import COMPOSE_PYTESTMARK

pytestmark = COMPOSE_PYTESTMARK


@pytest.mark.compose
def test_auto_accept_default_false_in_container(compose_stack):
    """Fresh container should have autoAcceptFolders=false in config.xml."""
    stack = compose_stack

    # Wait for container to be healthy
    stack.wait_healthy()

    # Read config.xml from the container's SYNCTHING_HOME
    exec_result = stack.exec("cat", "/app/data/syncthing/config.xml")
    assert exec_result.exit_code == 0
    config_xml = exec_result.output.decode()

    root = ET.fromstring(config_xml)
    el = root.find(".//autoAcceptFolders")
    assert el is not None, "autoAcceptFolders element should exist"
    assert el.text == "false", f"Expected false, got {el.text}"


@pytest.mark.compose
def test_auto_accept_true_when_env_set(compose_stack_with_auto_accept):
    """Container with SYNCTHING_AUTO_ACCEPT_FOLDERS=true should have
    autoAcceptFolders=true.
    """
    stack = compose_stack_with_auto_accept

    stack.wait_healthy()

    exec_result = stack.exec("cat", "/app/data/syncthing/config.xml")
    assert exec_result.exit_code == 0
    config_xml = exec_result.output.decode()

    root = ET.fromstring(config_xml)
    el = root.find(".//autoAcceptFolders")
    assert el is not None
    assert el.text == "true"


@pytest.mark.compose
def test_warning_logged_when_auto_accept_enabled(compose_stack_with_auto_accept):
    """Container with SYNCTHING_AUTO_ACCEPT_FOLDERS=true should log WARNING."""
    stack = compose_stack_with_auto_accept

    logs = stack.logs()
    assert "WARNING: SYNCTHING_AUTO_ACCEPT_FOLDERS=true" in logs
    assert "issue #160" in logs.lower()


@pytest.mark.compose
def test_marker_preserves_config_on_restart(compose_stack):
    """Restarting container should not change autoAcceptFolders
    (marker protects it).
    """
    stack = compose_stack

    # First, verify initial state (false)
    exec_result = stack.exec("cat", "/app/data/syncthing/config.xml")
    config_xml = exec_result.output.decode()
    root = ET.fromstring(config_xml)
    el = root.find(".//autoAcceptFolders")
    assert el.text == "false"

    # Restart
    stack.restart()
    stack.wait_healthy()

    # Verify unchanged
    exec_result = stack.exec("cat", "/app/data/syncthing/config.xml")
    config_xml = exec_result.output.decode()
    root = ET.fromstring(config_xml)
    el = root.find(".//autoAcceptFolders")
    assert el.text == "false"


@pytest.mark.compose
def test_marker_deletion_reapplies_patch(compose_stack):
    """Removing .shc-configured and restarting re-applies the patch."""
    stack = compose_stack

    # Verify initial state (false)
    exec_result = stack.exec("cat", "/app/data/syncthing/config.xml")
    config_xml = exec_result.output.decode()
    root = ET.fromstring(config_xml)
    el = root.find(".//autoAcceptFolders")
    assert el.text == "false"

    # Delete marker and restart
    stack.exec("rm", "/app/data/syncthing/.shc-configured")
    stack.restart()
    stack.wait_healthy()

    # Verify patch reapplied with current default (false)
    exec_result = stack.exec("cat", "/app/data/syncthing/config.xml")
    config_xml = exec_result.output.decode()
    root = ET.fromstring(config_xml)
    el = root.find(".//autoAcceptFolders")
    assert el.text == "false"

    # Marker should be recreated
    exec_result = stack.exec("test", "-f", "/app/data/syncthing/.shc-configured")
    assert exec_result.exit_code == 0


@pytest.mark.compose
def test_gui_address_set_to_loopback(compose_stack):
    """GUI address should be set to 127.0.0.1:8384."""
    stack = compose_stack
    stack.wait_healthy()

    exec_result = stack.exec("cat", "/app/data/syncthing/config.xml")
    assert exec_result.exit_code == 0
    config_xml = exec_result.output.decode()

    root = ET.fromstring(config_xml)
    gui_addr = root.find(".//gui/address")
    assert gui_addr is not None
    assert gui_addr.text == "127.0.0.1:8384"


@pytest.mark.compose
def test_folder_path_set_to_syncthing_root(compose_stack):
    """Folder path should be set to SYNCTHING_ROOT (/tmp/syncthing)."""
    stack = compose_stack
    stack.wait_healthy()

    exec_result = stack.exec("cat", "/app/data/syncthing/config.xml")
    assert exec_result.exit_code == 0
    config_xml = exec_result.output.decode()

    root = ET.fromstring(config_xml)
    folder = root.find(".//folder")
    assert folder is not None
    assert folder.get("path") == "/tmp/syncthing"
