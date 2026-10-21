"""
core.device_monitor
===================

Live inspection of what an Android device is doing *right now*.

Why this exists
---------------
A package-name blacklist finds adware that is merely *installed*. It cannot tell
a technician which app is currently drawing a full-screen ad over the home
screen, which service is being woken every thirty minutes, or which app holds
the overlay permission and is actively using it.

This module answers those questions with plain ``adb shell`` calls:

============================  =============================================
Foreground activity           ``dumpsys activity activities`` → ``mResumedActivity``
Focused window                ``dumpsys window`` → ``mCurrentFocus`` / ``mFocusedApp``
Overlay windows               ``dumpsys window windows`` → ``TYPE_APPLICATION_OVERLAY``
Running services              ``dumpsys activity services`` → ``ServiceRecord``
Running processes             ``ps -A``
Battery / storage             ``dumpsys battery`` / ``df /data``

Parsing rules
-------------
``dumpsys`` output differs between OEM skins, so every extractor takes the
*last* plausible match and tolerates missing fields rather than raising. A
device that refuses one of these dumps still produces a usable snapshot.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .adb_manager import AdbError, AdbManager
from .app_model import OVERLAY_PERMISSIONS, LiveSnapshot

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

_RE_RESUMED = re.compile(r"mResumedActivity[^\n]*?\s([A-Za-z0-9_.]+)/(?:[\w.]+)", re.MULTILINE)
_RE_RESUMED_ALT = re.compile(r"mFocusedApp[^\n]*?\s([A-Za-z0-9_.]+)/([\w.]+)", re.MULTILINE)
_RE_CURRENT_FOCUS = re.compile(r"mCurrentFocus[=:\s]+Window\{[^}]*\s([A-Za-z0-9_.]+)/([\w.]+)\}")
_RE_FOCUSED_APP = re.compile(r"mFocusedApp[=:\s]+AppWindowToken\{[^}]*\s([A-Za-z0-9_.]+)/([\w.]+)\}")
_RE_OVERLAY_PACKAGE = re.compile(r"Window\{[^}]*\s([A-Za-z0-9_.]+)/[\w.]+\}", re.MULTILINE)
_RE_OVERLAY_LINE = re.compile(
    r"Window\{[^\n]*?\s([A-Za-z0-9_.]+)/[\w.]+[^\n]*?\}\s+([A-Za-z0-9_.]+/[A-Za-z0-9_.]+)"
)
_RE_SERVICE_PKG = re.compile(r"ServiceRecord\{[0-9a-fA-F]+\s+([A-Za-z0-9_.]+)/")
_RE_ACTIVITY_ALIVE = re.compile(r"ActivityRecord\{[0-9a-fA-F]+\s+([A-Za-z0-9_.]+)/")

#: Overlay window types worth reporting. ``TYPE_APPLICATION_OVERLAY`` is the
#: permission-backed one that can cover other apps; the others are system UI
#: that is always on screen and would be noise.
_OVERLAY_TYPES = ("TYPE_APPLICATION_OVERLAY", "TYPE_SYSTEM_ALERT", "TYPE_SYSTEM_OVERLAY")


def _short_timestamp() -> str:
    return time.strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------


class DeviceMonitor:
    """Runs the live-inspection probes against one device.

    Holds no state between calls beyond the manager reference, so a technician
    can point the same instance at different phones if they swap cables.
    """

    def __init__(self, manager: AdbManager) -> None:
        self.manager = manager

    # -- individual probes -------------------------------------------------

    def foreground_activity(self, serial: str) -> tuple[str, str]:
        """Return ``(package, label)`` for the resumed activity.

        Falls back through ``mResumedActivity`` → ``mFocusedApp`` because some
        Android versions only expose the latter to non-root shells.
        """
        raw = self._safe_shell(serial, "dumpsys activity activities", timeout=30)
        if not raw:
            return "", ""

        match = _RE_RESUMED.search(raw)
        if match:
            package = match.group(1)
            activity = match.group(0).split("/", 1)[-1].split(" ")[0].strip()
            return package, self._read_app_label(serial, package) or activity

        match = _RE_RESUMED_ALT.search(raw)
        if match:
            package = match.group(1)
            return package, self._read_app_label(serial, package) or match.group(2)

        return "", ""

    def focused_window(self, serial: str) -> tuple[str, str]:
        """Return ``(package, window_class)`` for the focused window."""
        raw = self._safe_shell(serial, "dumpsys window windows", timeout=35)
        if not raw:
            return "", ""

        for regex in (_RE_CURRENT_FOCUS, _RE_FOCUSED_APP):
            match = regex.search(raw)
            if match:
                return match.group(1), match.group(2)

        # Some builds only print a single-line summary; grab the first
        # ``pkg/class`` token pair that mentions a window.
        match = _RE_OVERLAY_LINE.search(raw)
        if match:
            return match.group(1), match.group(2).split("/", 1)[-1]
        return "", ""

    def overlay_windows(self, serial: str) -> list[tuple[str, str]]:
        """Return ``[(package, window_type)]`` for overlay-capable windows.

        Only windows whose type is overlay-ish are reported — a plain activity
        window is on screen but cannot draw over anything.
        """
        raw = self._safe_shell(serial, "dumpsys window windows", timeout=35)
        if not raw:
            return []

        found: list[tuple[str, str]] = []
        seen: set[str] = set()

        # Each Window block starts with "Window{...} <type>" — walk the blocks.
        for block in re.split(r"\n\s*Window\{", raw):
            if not any(token in block for token in _OVERLAY_TYPES):
                continue
            package_match = re.search(r"\s([A-Za-z0-9_.]+)/[\w.]+", block)
            if not package_match:
                continue
            package = package_match.group(1)
            if package in seen:
                continue
            type_match = re.search(r"(TYPE_[A-Z_]+)", block)
            window_type = type_match.group(1) if type_match else "TYPE_APPLICATION_OVERLAY"
            seen.add(package)
            found.append((package, window_type))

        return found

    def running_services(self, serial: str) -> list[str]:
        """Packages that currently own at least one live ``ServiceRecord``."""
        raw = self._safe_shell(serial, "dumpsys activity services", timeout=35)
        if not raw:
            return []
        ordered: list[str] = []
        seen: set[str] = set()
        for package in _RE_SERVICE_PKG.findall(raw):
            if package not in seen:
                seen.add(package)
                ordered.append(package)
        return ordered

    def running_processes(self, serial: str) -> list[str]:
        """Packages with at least one live process.

        ``ps -A -o NAME`` is used first because the column layout differs between
        Android versions; the plain ``ps -A`` output is the fallback.
        """
        raw = self._safe_shell(serial, "ps -A -o NAME", timeout=25)
        if raw and "PID" not in raw.upper():
            return self._packages_from_ps(raw)

        raw = self._safe_shell(serial, "ps -A", timeout=25)
        if raw:
            return self._packages_from_ps(raw)
        return []

    @staticmethod
    def _packages_from_ps(raw: str) -> list[str]:
        """Extract package names from ``ps`` output.

        The column layout differs between Android versions and between the
        ``ps -A -o NAME`` and plain ``ps -A`` forms, so the header line and the
        owner column are both detected rather than assumed.
        """
        ordered: list[str] = []
        seen: set[str] = set()

        for line in raw.splitlines():
            parts = line.split()
            if not parts:
                continue

            # Header row emitted by `ps -A` (USER PID PPID ... NAME).
            if parts[0].upper() == "USER" and len(parts) > 1 and parts[1].upper() == "PID":
                continue

            # `ps -A` puts the process name last, after the numeric columns.
            if parts[0].isdigit() or parts[0].startswith("u0_") or parts[0] in {"root", "system", "shell"}:
                candidate = parts[-1]
            else:
                # `ps -A -o NAME` prints only the name column.
                candidate = parts[0]

            candidate = candidate.strip()
            if not candidate or not re.fullmatch(r"[A-Za-z0-9_.:]+", candidate):
                continue

            # Skip platform daemons: no dot means a binary, not an app package.
            if "." not in candidate.split(":")[0]:
                continue

            base = candidate.split(":", 1)[0]
            if base in seen:
                continue
            seen.add(base)
            ordered.append(base)

        return ordered

    def overlay_permission_holders(self, serial: str) -> list[str]:
        """Packages that hold ``SYSTEM_ALERT_WINDOW`` and are currently enabled.

        This is a *capability* query, not an activity query — the app may not be
        drawing anything right now. It is still the best shortlist of "who could
        pop an ad over everything".
        """
        raw = self._safe_shell(serial, "appops get SYSTEM_ALERT_WINDOW allow", timeout=30)
        if not raw:
            return []
        holders: list[str] = []
        seen: set[str] = set()
        for line in raw.splitlines():
            match = re.search(r"\b([A-Za-z0-9_.]+)\b", line)
            if not match:
                continue
            package = match.group(1)
            if package in seen or package in {"No", "operations", "Uid"}:
                continue
            if package.count(".") < 1:
                continue
            seen.add(package)
            holders.append(package)
        return holders

    # -- composed snapshot -------------------------------------------------

    def snapshot(self, serial: str, known_bad: set[str] | None = None) -> LiveSnapshot:
        """Run every probe and assemble one :class:`LiveSnapshot`.

        Individual probe failures are swallowed: a snapshot with three of four
        fields filled is far more useful than an exception.
        """
        shot = LiveSnapshot(timestamp=_short_timestamp())
        bad = known_bad or set()

        try:
            shot.foreground, shot.foreground_label = self.foreground_activity(serial)
        except AdbError:
            pass

        try:
            focus_pkg, _window = self.focused_window(serial)
            if not shot.foreground:
                shot.foreground = focus_pkg
        except AdbError:
            pass

        try:
            shot.overlays = self.overlay_windows(serial)
        except AdbError:
            pass

        try:
            shot.services = self.running_services(serial)
        except AdbError:
            pass

        try:
            shot.processes = self.running_processes(serial)
        except AdbError:
            pass

        try:
            holders = self.overlay_permission_holders(serial)
            shot.permission_warners = [pkg for pkg in holders if pkg in bad]
        except AdbError:
            pass

        return shot

    # -- utility -----------------------------------------------------------

    def _safe_shell(self, serial: str, command: str, timeout: float = 30) -> str:
        """Run a shell command, returning an empty string on any failure."""
        try:
            return self.manager.run_shell(serial, command, timeout=timeout)
        except AdbError:
            return ""

    def _read_app_label(self, serial: str, package: str) -> str:
        """Best-effort human label for a package.

        Android has no shell command that reliably returns the localised app
        name, so the package id itself is used. The scan table already carries
        friendly names for anything on the blacklist.
        """
        return package or ""


# ---------------------------------------------------------------------------
# Threat rules
# ---------------------------------------------------------------------------


@dataclass
class ThreatSignal:
    """One actionable observation about the device's current state."""

    level: str          # "OK" | "WARN" | "ERROR"
    title: str
    detail: str
    packages: list[str] = field(default_factory=list)

    @property
    def has_action(self) -> bool:
        return bool(self.packages)


def evaluate_snapshot(
    shot: LiveSnapshot,
    blacklist: dict[str, str],
    overlay_capable: set[str] | None = None,
) -> list[ThreatSignal]:
    """Turn a raw snapshot into human-readable warnings.

    ``blacklist`` maps package → risk label; ``overlay_capable`` is the set of
    packages that hold ``SYSTEM_ALERT_WINDOW`` so a *capable but idle* app can be
    distinguished from one actively drawing.
    """
    signals: list[ThreatSignal] = []
    capable = overlay_capable or set()

    # 1. An app on the blacklist is drawing over everything right now.
    bad_overlays = [pkg for pkg in shot.overlay_packages if pkg in blacklist]
    if bad_overlays:
        risk = max(
            (blacklist.get(pkg, "High") for pkg in bad_overlays),
            key=lambda value: {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}.get(value, 9),
        )
        signals.append(
            ThreatSignal(
                level="ERROR",
                title="มีแอปอันตรายกำลังวาดทับหน้าจอ",
                detail=(
                    f"พบ {len(bad_overlays)} แอปที่อยู่ในรายการต้องระวัง "
                    f"กำลังทับหน้าจออยู่ (ความเสี่ยงสูงสุด: {risk})"
                ),
                packages=bad_overlays,
            )
        )

    # 2. An unknown app holds the overlay permission and is currently running.
    idle_suspicious = [
        pkg
        for pkg in shot.overlay_packages
        if pkg not in blacklist and pkg not in capable
    ]
    if idle_suspicious:
        signals.append(
            ThreatSignal(
                level="WARN",
                title="แอปที่ไม่รู้จักกำลังวาดทับหน้าจอ",
                detail=(
                    f"พบ {len(idle_suspicious)} แอปที่ไม่ได้อยู่ในฐานข้อมูล "
                    "แต่กลับวาดทับหน้าจออยู่"
                ),
                packages=idle_suspicious,
            )
        )

    # 3. A blacklisted app is running even without an overlay right now.
    running_bad = [pkg for pkg in shot.processes if pkg in blacklist]
    if running_bad and not bad_overlays:
        signals.append(
            ThreatSignal(
                level="WARN",
                title="มีแอปต้องระวังกำลังทำงานอยู่",
                detail=f"พบ {len(running_bad)} แอปจากรายการต้องระวังที่ยังรันอยู่ในเบื้องหลัง",
                packages=running_bad,
            )
        )

    # 4. Nothing alarming.
    if not signals:
        signals.append(
            ThreatSignal(
                level="OK",
                title="ไม่พบพฤติกรรมน่าสงสัย",
                detail=f"กำลังตรวจสอบ {len(shot.processes)} โปรเซส — ไม่มีแอปต้องระวังที่กำลังทำงาน",
                packages=[],
            )
        )

    return signals


def force_stop_targets() -> tuple[str, ...]:
    """Packages that must never be force-stopped by the monitor."""
    return (
        "com.android.systemui",
        "com.android.shell",
        "android",
        "system",
    )


def is_protected(package: str) -> bool:
    """True when force-stopping ``package`` would be reckless."""
    name = str(package or "").strip()
    if name in force_stop_targets():
        return True
    # Anything without a dot is a platform binary rather than an app package.
    return "." not in name


# ---------------------------------------------------------------------------
# Overlay permission catalogue (documentation helper)
# ---------------------------------------------------------------------------

#: Exposed so the UI can explain *why* a package is being watched.
DANGEROUS_OVERLAY_PERMISSIONS = tuple(sorted(OVERLAY_PERMISSIONS))