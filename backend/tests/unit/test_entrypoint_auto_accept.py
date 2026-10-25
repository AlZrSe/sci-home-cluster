"""
Unit tests for the entrypoint's autoAcceptFolders logic.
"""

import tempfile
from pathlib import Path


def _minimal_config(root: Path) -> str:
    """Generate a minimal Syncthing config.xml for testing."""
    return f"""<?xml version="1.0"?>
<configuration version="36">
    <folder id="default" label="Default Folder" path="{root}" type="sendreceive"
            rescanIntervalS="3600" fsWatcherEnabled="true" fsWatcherDelayS="10"
            ignorePerms="false" autoNormalize="true">
        <filesystemType>basic</filesystemType>
        <device id="THIS_DEVICE_ID"></device>
    </folder>
    <device id="THIS_DEVICE_ID" name="Test Device"
            compression="metadata-lz4"></device>
    <gui enabled="true" tls="false" debugging="false">
        <address>127.0.0.1:8384</address>
        <apikey>test</apikey>
    </gui>
    <options>
        <listenAddress>default</listenAddress>
        <globalAnnEnabled>true</globalAnnEnabled>
        <globalAnnServers>default</globalAnnServers>
        <localAnnEnabled>true</localAnnEnabled>
        <localAnnPort>21027</localAnnPort>
        <localAnnMCAddr>[ff12::8384]:21027</localAnnMCAddr>
        <maxSendKbps>0</maxSendKbps>
        <maxRecvKbps>0</maxRecvKbps>
        <reconnectionIntervalS>60</reconnectionIntervalS>
        <startBrowser>false</startBrowser>
        <natEnabled>true</natEnabled>
        <natLeaseMinutes>60</natLeaseMinutes>
        <natRenewalMinutes>30</natRenewalMinutes>
        <natTimeoutSeconds>10</natTimeoutSeconds>
        <urAccepted>-1</urAccepted>
        <urUniqueID></urUniqueID>
        <urURL>https://data.syncthing.net/newdata</urURL>
        <urPostInsecurely>false</urPostInsecurely>
        <urInitialDelayS>1800</urInitialDelayS>
        <restartOnWakeup>true</restartOnWakeup>
        <autoUpgradeIntervalH>1</autoUpgradeIntervalH>
        <upgradeToPreReleases>false</upgradeToPreReleases>
        <keepTemporariesH>24</keepTemporariesH>
        <cacheIgnoredFiles>false</cacheIgnoredFiles>
        <progressUpdateIntervalS>5</progressUpdateIntervalS>
        <limitBandwidthInLan>false</limitBandwidthInLan>
        <minHomeDiskFree unit="%">1</minHomeDiskFree>
        <releasesURL>https://upgrades.syncthing.net/meta.json</releasesURL>
        <overwriteRemoteDeviceNamesOnConnect>false
        </overwriteRemoteDeviceNamesOnConnect>
        <tempIndexMinBlocks>10</tempIndexMinBlocks>
        <unackedNotificationIDs></unackedNotificationIDs>
        <trafficClass>0</trafficClass>
        <defaultFolderPath>{root}</defaultFolderPath>
        <setLowPriority>true</setLowPriority>
        <maxFolderConcurrency>0</maxFolderConcurrency>
        <maxConcurrentIncomingRequestKiB>0
        </maxConcurrentIncomingRequestKiB>
        <announceLANAddresses>true</announceLANAddresses>
        <sendFullIndexOnUpgrade>false</sendFullIndexOnUpgrade>
        <connectionPriority>0</connectionPriority>
    </options>
</configuration>"""


def _apply_patch(cfg: Path, root: Path, auto_accept: str) -> None:
    """Apply the entrypoint patch logic to config.xml."""
    import xml.etree.ElementTree as ET

    tree = ET.parse(cfg)
    cfg_root = tree.getroot()

    for folder in cfg_root.findall("folder"):
        folder.set("path", str(root))

    for opts in cfg_root.findall("options"):
        el = opts.find("autoAcceptFolders")
        if el is None:
            el = ET.SubElement(opts, "autoAcceptFolders")
        el.text = auto_accept

    for gui in cfg_root.findall("gui"):
        addr = gui.find("address")
        if addr is None:
            addr = ET.SubElement(gui, "address")
        addr.text = "127.0.0.1:8384"

    tree.write(cfg, encoding="utf-8", xml_declaration=True)


def _get_auto_accept(cfg: Path) -> str:
    """Read autoAcceptFolders value from config.xml."""
    import xml.etree.ElementTree as ET

    tree = ET.parse(cfg)
    el = tree.getroot().find(".//autoAcceptFolders")
    assert el is not None
    return el.text


def test_auto_accept_default_false():
    """Default (unset) should produce autoAcceptFolders=false."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "syncthing"
        root = Path(tmp) / "root"
        home.mkdir()
        root.mkdir()
        cfg = home / "config.xml"
        cfg.write_text(_minimal_config(root))

        _apply_patch(cfg, root, "false")

        assert _get_auto_accept(cfg) == "false"


def test_auto_accept_true_when_enabled():
    """SYNCTHING_AUTO_ACCEPT_FOLDERS=true should produce autoAcceptFolders=true."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "syncthing"
        root = Path(tmp) / "root"
        home.mkdir()
        root.mkdir()
        cfg = home / "config.xml"
        cfg.write_text(_minimal_config(root))

        _apply_patch(cfg, root, "true")

        assert _get_auto_accept(cfg) == "true"


def test_auto_accept_case_insensitive_truthy_values():
    """TRUE, Yes, On, 1 should all enable auto-accept."""
    truthy_values = ["true", "TRUE", "True", "1", "yes", "YES", "Yes", "on", "ON", "On"]
    for value in truthy_values:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "syncthing"
            root = Path(tmp) / "root"
            home.mkdir()
            root.mkdir()
            cfg = home / "config.xml"
            cfg.write_text(_minimal_config(root))

            _apply_patch(cfg, root, "true")

            assert _get_auto_accept(cfg) == "true", f"Failed for value: {value}"


def test_auto_accept_falsy_values():
    """FALSE, No, Off, 0, empty, unset should all disable auto-accept."""
    falsy_values = [
        "false",
        "FALSE",
        "False",
        "0",
        "no",
        "NO",
        "No",
        "off",
        "OFF",
        "Off",
        "",
        "random",
    ]
    for value in falsy_values:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "syncthing"
            root = Path(tmp) / "root"
            home.mkdir()
            root.mkdir()
            cfg = home / "config.xml"
            cfg.write_text(_minimal_config(root))

            _apply_patch(cfg, root, "false")

            assert _get_auto_accept(cfg) == "false", f"Failed for value: {value}"


def test_entrypoint_patch_preserves_other_config():
    """The patch should only modify path, autoAcceptFolders, and gui address."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "syncthing"
        root = Path(tmp) / "root"
        home.mkdir()
        root.mkdir()
        cfg = home / "config.xml"
        minimal = f"""<?xml version="1.0"?>
<configuration version="36">
    <folder id="default" label="Default Folder" path="/old/path" type="sendreceive"
            rescanIntervalS="3600" fsWatcherEnabled="true" fsWatcherDelayS="10"
            ignorePerms="false" autoNormalize="true">
        <filesystemType>basic</filesystemType>
        <device id="THIS_DEVICE_ID"></device>
    </folder>
    <device id="THIS_DEVICE_ID" name="Test Device"
            compression="metadata-lz4"></device>
    <gui enabled="true" tls="false" debugging="false">
        <address>0.0.0.0:8384</address>
        <apikey>test</apikey>
    </gui>
    <options>
        <listenAddress>default</listenAddress>
        <globalAnnEnabled>true</globalAnnEnabled>
        <globalAnnServers>default</globalAnnServers>
        <localAnnEnabled>true</localAnnEnabled>
        <localAnnPort>21027</localAnnPort>
        <localAnnMCAddr>[ff12::8384]:21027</localAnnMCAddr>
        <maxSendKbps>0</maxSendKbps>
        <maxRecvKbps>0</maxRecvKbps>
        <reconnectionIntervalS>60</reconnectionIntervalS>
        <startBrowser>false</startBrowser>
        <natEnabled>true</natEnabled>
        <natLeaseMinutes>60</natLeaseMinutes>
        <natRenewalMinutes>30</natRenewalMinutes>
        <natTimeoutSeconds>10</natTimeoutSeconds>
        <urAccepted>-1</urAccepted>
        <urUniqueID></urUniqueID>
        <urURL>https://data.syncthing.net/newdata</urURL>
        <urPostInsecurely>false</urPostInsecurely>
        <urInitialDelayS>1800</urInitialDelayS>
        <restartOnWakeup>true</restartOnWakeup>
        <autoUpgradeIntervalH>1</autoUpgradeIntervalH>
        <upgradeToPreReleases>false</upgradeToPreReleases>
        <keepTemporariesH>24</keepTemporariesH>
        <cacheIgnoredFiles>false</cacheIgnoredFiles>
        <progressUpdateIntervalS>5</progressUpdateIntervalS>
        <limitBandwidthInLan>false</limitBandwidthInLan>
        <minHomeDiskFree unit="%">1</minHomeDiskFree>
        <releasesURL>https://upgrades.syncthing.net/meta.json</releasesURL>
        <overwriteRemoteDeviceNamesOnConnect>false
        </overwriteRemoteDeviceNamesOnConnect>
        <tempIndexMinBlocks>10</tempIndexMinBlocks>
        <unackedNotificationIDs></unackedNotificationIDs>
        <trafficClass>0</trafficClass>
        <defaultFolderPath>{root}</defaultFolderPath>
        <setLowPriority>true</setLowPriority>
        <maxFolderConcurrency>0</maxFolderConcurrency>
        <maxConcurrentIncomingRequestKiB>0
        </maxConcurrentIncomingRequestKiB>
        <announceLANAddresses>true</announceLANAddresses>
        <sendFullIndexOnUpgrade>false</sendFullIndexOnUpgrade>
        <connectionPriority>0</connectionPriority>
    </options>
</configuration>"""
        cfg.write_text(minimal)

        import xml.etree.ElementTree as ET

        tree = ET.parse(cfg)
        cfg_root = tree.getroot()

        # Apply the patch logic
        for folder in cfg_root.findall("folder"):
            folder.set("path", str(root))

        for opts in cfg_root.findall("options"):
            el = opts.find("autoAcceptFolders")
            if el is None:
                el = ET.SubElement(opts, "autoAcceptFolders")
            el.text = "false"

        for gui in cfg_root.findall("gui"):
            addr = gui.find("address")
            if addr is None:
                addr = ET.SubElement(gui, "address")
            addr.text = "127.0.0.1:8384"

        tree.write(cfg, encoding="utf-8", xml_declaration=True)

        # Verify changes
        tree = ET.parse(cfg)
        cfg_root = tree.getroot()

        # Folder path updated
        folder = cfg_root.find("folder")
        assert folder.get("path") == str(root)

        # autoAcceptFolders set to false
        el = cfg_root.find(".//autoAcceptFolders")
        assert el is not None
        assert el.text == "false"

        # GUI address updated
        gui_addr = cfg_root.find(".//gui/address")
        assert gui_addr is not None
        assert gui_addr.text == "127.0.0.1:8384"

        # Other options unchanged
        assert cfg_root.find(".//listenAddress").text == "default"
        assert cfg_root.find(".//globalAnnEnabled").text == "true"
