"""
ADSremovedPP — Central Configuration
=====================================

Single source of truth for application constants: version numbers, remote
endpoints, filesystem paths and tuning constants.

Import this module instead of hard-coding values anywhere else in the project.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Application identity
# ---------------------------------------------------------------------------

APP_NAME = "ADSremovedPP"
APP_TITLE = "ADSremovedPP — Android ADB Cleaner & Repair Utility"
CURRENT_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Remote endpoints
#
# Replace the placeholder owner/repo with your own GitHub repository so the
# auto-updater and the remote blacklist can actually resolve. Everything still
# works offline while these remain unset — see updater.py for the fallbacks.
# ---------------------------------------------------------------------------

GITHUB_OWNER = "juzzt"
GITHUB_REPO = "ADSremovedPP"
GITHUB_BRANCH = "main"

RAW_BASE_URL = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}"

#: Remote blacklist (JSON array) consulted on every scan.
BLACKLIST_URL = f"{RAW_BASE_URL}/local_db.json"

#: Remote version manifest (JSON object with a "version" key).
VERSION_URL = f"{RAW_BASE_URL}/version.json"

#: Release asset used by the self-updater. Falls back to the repo root exe
#: name when no dedicated release asset exists.
UPDATE_EXE_URL = (
    f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/"
    f"latest/download/{APP_NAME}.exe"
)

#: Seconds to wait on any network request before falling back to local data.
NETWORK_TIMEOUT = 10

# ---------------------------------------------------------------------------
# Filesystem layout
# ---------------------------------------------------------------------------


def resource_root() -> Path:
    """Directory holding bundled read-only resources.

    Under PyInstaller's one-file mode this is the temporary ``_MEIPASS``
    extraction directory; during normal development it is the project root.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def app_dir() -> Path:
    """Writable directory containing the running executable.

    The self-updater needs this because it must be able to replace the binary
    on disk, which is impossible inside the temporary PyInstaller folder.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def user_data_dir() -> Path:
    """Per-user writable directory for settings and downloaded updates."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_file() -> Path:
    return user_data_dir() / "settings.json"


def log_file() -> Path:
    return user_data_dir() / "adsremovedpp.log"


def download_staging_dir() -> Path:
    """Where the freshly downloaded installer is parked before the swap."""
    path = user_data_dir() / "update"
    path.mkdir(parents=True, exist_ok=True)
    return path


#: Bundled fallback blacklist, shipped inside the executable.
LOCAL_DB_RESOURCE = resource_root() / "local_db.json"

#: Optional on-disk copy of the blacklist sitting next to the executable.
LOCAL_DB_PORTABLE = app_dir() / "local_db.json"

# ---------------------------------------------------------------------------
# ADB discovery
# ---------------------------------------------------------------------------

ADB_RELATIVE_PATH = Path("bin") / ("adb.exe" if sys.platform == "win32" else "adb")


def find_adb() -> Path | None:
    """Locate ``adb`` using, in order:

    1. ``ADSREMOVEDPP_ADB`` environment variable (escape hatch for odd setups)
    2. the bundled ``bin/`` directory
    3. ``adb`` on ``PATH``
    4. a couple of common Android SDK locations

    Returns ``None`` when nothing is found so callers can surface a clear,
    actionable message instead of an opaque crash.
    """
    override = os.environ.get("ADSREMOVEDPP_ADB")
    if override:
        candidate = Path(override)
        if candidate.exists():
            return candidate

    bundled = resource_root() / ADB_RELATIVE_PATH
    if bundled.exists():
        return bundled

    bundled_next_to_exe = app_dir() / ADB_RELATIVE_PATH
    if bundled_next_to_exe.exists():
        return bundled_next_to_exe

    on_path = shutil.which("adb.exe" if sys.platform == "win32" else "adb")
    if on_path:
        return Path(on_path)

    for env_var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        sdk = os.environ.get(env_var)
        if not sdk:
            continue
        candidate = Path(sdk) / "platform-tools" / ADB_RELATIVE_PATH.name
        if candidate.exists():
            return candidate

    return None


# ---------------------------------------------------------------------------
# Behaviour tuning
# ---------------------------------------------------------------------------

#: Default timeout for a single adb invocation, in seconds.
ADB_COMMAND_TIMEOUT = 60.0

#: Timeout for potentially long ``adb pull``-style transfers, in seconds.
ADB_TRANSFER_TIMEOUT = 300.0

#: Number of device-detection retries before the UI reports "not found".
DEVICE_PROBE_RETRIES = 5

#: Delay between device-detection retries, in milliseconds.
DEVICE_PROBE_INTERVAL_MS = 1200

#: Number of recent log lines kept in the on-screen log panel.
LOG_PANEL_MAX_BLOCKS = 5000

#: Google's Play Store package id — treated as a trusted installer.
PLAY_STORE_PACKAGE = "com.android.vending"

#: Installer package names considered legitimate for their respective OEM.
TRUSTED_INSTALLERS = frozenset(
    {
        PLAY_STORE_PACKAGE,
        "com.sec.android.app.samsungapps",   # Galaxy Store
        "com.huawei.appmarket",               # AppGallery
        "com.xiaomi.market",                  # GetApps
        "com.oppo.market",                    # OPPO Store
        "com.bbk.appstore",                   # vivo / iTools
        "com.amazon.venezia",                 # Amazon Appstore
        "org.fdroid.fdroid",                  # F-Droid
        "com.google.android.feedback",        # system feedback app
        "com.android.packageinstaller",       # on-device sideload flow
    }
)

#: Severity ordering used for sorting and colour mapping.
RISK_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}

#: Colour tokens (must mirror ui/styles.py) keyed by risk level.
RISK_COLORS = {
    "Critical": "#ff5c5c",
    "High": "#ff8a5c",
    "Medium": "#f5c623",
    "Low": "#5cc8ff",
    "Safe": "#3ecf8e",
}