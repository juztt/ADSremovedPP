"""
core.adb_extended
=================

Additions on top of :class:`~core.adb_manager.AdbManager` for v0.2.

The original manager answers "which packages are installed" and "remove this
one". The features agreed for 0.2 need more:

* **Installer provenance** — who installed the app, read in one round trip with
  ``pm list packages -f -i`` instead of one query per package.
* **App detail** — version, install dates, requested permissions and on-device
  APK size, used by the detail panel.
* **Force stop** — kill a running process without uninstalling anything, which is
  the fastest way to silence an app that is currently drawing an overlay.
* **Icon fetching** — pull an APK so the launcher icon can be extracted.

Rather than growing the tested core module, this file subclasses it and adds a
matching worker. The subclass is a drop-in replacement: it inherits every
method and signal of the base classes.
"""

from __future__ import annotations

import re
import subprocess
import sys
from typing import Iterable, Sequence

from PySide6.QtCore import QObject, Signal, Slot

import config
from .adb_manager import AdbError, AdbManager, AdbWorker, DeviceRecord, UninstallOutcome
from .app_model import AppEntry, LiveSnapshot, classify_installer

# ---------------------------------------------------------------------------
# Parsing regexes
# ---------------------------------------------------------------------------

#: ``pm list packages -f -i`` emits lines such as
#: ``package:/data/app/~~Ab==/com.x.y-1/base.apk=com.x.y installer=com.android.vending``
#: APK paths legitimately contain ``=`` (obfuscated ``~~hash==`` folder names),
#: so the package name is taken from the *last* ``=`` and the installer is
#: whatever follows the ``installer=`` marker.
_RE_INSTALLER = re.compile(r"\binstaller=(\S*)")

#: ``dumpsys package`` fields.
_RE_VERSION_NAME = re.compile(r"versionName=(\S+)")
_RE_VERSION_CODE = re.compile(r"versionCode=(\d+)")
_RE_FIRST_INSTALL = re.compile(r"firstInstallTime=(.+)")
_RE_LAST_UPDATE = re.compile(r"lastUpdateTime=(.+)")
_RE_PERMISSIONS_HEADER = re.compile(r"requested permissions:\s*$", re.MULTILINE)
_RE_PERMISSION_LINE = re.compile(r"^\s*(android\.permission\.[A-Z0-9_]+)\s*$", re.MULTILINE)
_RE_APK_SPLIT = re.compile(r"^\s*(\S+)\.apk=", re.MULTILINE)

#: ``getprop``-free timestamp parsing: ``2024-07-12 08:31:04``
_RE_DEVICE_TIME = re.compile(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})")

#: Permissions that Android auto-grants and never lists as "requested" because
#: they are implied by the install. Filtering them keeps the panel honest.
_IMPLIED_PERMISSIONS = frozenset(
    {
        "android.permission.INTERNET",
        "android.permission.ACCESS_NETWORK_STATE",
        "android.permission.ACCESS_WIFI_STATE",
        "android.permission.FOREGROUND_SERVICE",
        "android.permission.WAKE_LOCK",
        "android.permission.RECEIVE_BOOT_COMPLETED",
    }
)


# ---------------------------------------------------------------------------
# Time helper
# ---------------------------------------------------------------------------


def _parse_device_time(text: str) -> int:
    """Convert a ``dumpsys`` timestamp into milliseconds since the epoch."""
    import datetime as _dt

    match = _RE_DEVICE_TIME.search(text or "")
    if not match:
        return 0
    year, month, day, hour, minute, second = (int(part) for part in match.groups())
    try:
        moment = _dt.datetime(year, month, day, hour, minute, second)
    except ValueError:
        return 0
    return int(moment.timestamp() * 1000)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class ExtendedAdbManager(AdbManager):
    """``AdbManager`` plus the v0.2 probing surface."""

    # -- file transfer -----------------------------------------------------

    def pull(self, serial: str, remote_path: str, local_path) -> None:
        """Copy one file off the device.

        Used by the icon pipeline to fetch an APK. Raises :class:`AdbError` on
        failure so the caller can fall back to a placeholder tile.
        """
        import pathlib

        destination = pathlib.Path(local_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._lock:
                proc = subprocess.run(
                    [str(self.ensure_available()), "-s", serial, "pull", remote_path, str(destination)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=config.ADB_TRANSFER_TIMEOUT,
                    creationflags=_no_window(),
                )
        except FileNotFoundError as exc:
            raise AdbError(f"เรียกใช้ adb ไม่สำเร็จ: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise AdbError("ดึงไฟล์จากมือถือเกินเวลาที่กำหนด") from exc

        if proc.returncode != 0:
            message = (proc.stderr or proc.stdout or "").strip()
            raise AdbError(message or "ดึงไฟล์จากมือถือไม่สำเร็จ")

    def apk_size_on_device(self, serial: str, package: str) -> int:
        """Return the APK size in bytes without transferring the file.

        ``pm path`` gives the location; ``stat -c %s`` reads the size. Older
        images without ``stat`` fall back to parsing ``ls -l``.
        """
        try:
            listing = self._run(["-s", serial, "shell", "pm", "path", package], timeout=25)
        except AdbError:
            return 0

        path = ""
        for line in listing.splitlines():
            cleaned = line.strip()
            if cleaned.startswith("package:"):
                path = cleaned[len("package:"):].strip()
                break
        if not path:
            return 0

        for template in ("stat -c %s {p}", "ls -l {p}"):
            command = template.format(p=_shell_quote(path))
            try:
                raw = self.run_shell(serial, command, timeout=20)
            except AdbError:
                continue
            value = _parse_size(raw)
            if value > 0:
                return value
        return 0

    # -- package listing with provenance -----------------------------------

    def list_packages_detailed(self, serial: str, third_party_only: bool = False) -> list[AppEntry]:
        """List packages *with* their installer in a single adb round trip.

        ``pm list packages -f -i`` returns the APK path, the package name and the
        installer package together. One call for the whole device replaces the
        hundreds of per-package queries the old code would have needed.
        """
        args = ["-s", serial, "shell", "pm", "list", "packages", "-f", "-i"]
        if third_party_only:
            args.append("-3")
        output = self._run(args, timeout=90)

        entries: list[AppEntry] = []
        for line in output.splitlines():
            line = line.strip()
            if not line.startswith("package:"):
                continue

            body = line[len("package:"):]
            if "=" not in body:
                continue
            # APK paths may contain "=", so split on the last one.
            apk_path, _, package = body.rpartition("=")
            package = package.strip()
            if not package:
                continue

            installer_match = _RE_INSTALLER.search(body)
            installer = installer_match.group(1) if installer_match else ""

            apk_path = apk_path.strip()
            is_system = apk_path.startswith("/system/") or apk_path.startswith("/vendor/")

            entries.append(
                AppEntry(
                    package=package,
                    apk_path=apk_path,
                    is_system=is_system,
                    installer=installer,
                    source=classify_installer(installer, is_system=is_system),
                )
            )

        entries.sort(key=lambda entry: entry.package.lower())
        return entries

    def list_installers(self, serial: str) -> dict[str, str]:
        """Return ``{package: installer}`` for every installed package."""
        return {entry.package: entry.installer for entry in self.list_packages_detailed(serial)}

    # -- per-app detail ----------------------------------------------------

    def get_app_details(self, serial: str, package: str) -> AppEntry:
        """Collect everything the detail panel shows about one package."""
        entry = AppEntry(package=package)

        raw = ""
        try:
            raw = self.run_shell(serial, f"dumpsys package {_shell_quote(package)}", timeout=40)
        except AdbError:
            raw = ""

        if raw:
            match = _RE_VERSION_NAME.search(raw)
            if match:
                entry.version_name = match.group(1)
            match = _RE_VERSION_CODE.search(raw)
            if match:
                entry.version_name = entry.version_name or f"code {match.group(1)}"
            entry.first_install_ms = _parse_device_time(_first_match(_RE_FIRST_INSTALL, raw))
            entry.last_update_ms = _parse_device_time(_first_match(_RE_LAST_UPDATE, raw))
            entry.permissions = _extract_permissions(raw)

        # Provenance and size are cheap and frequently what the user came for.
        try:
            entry.installer = self.get_installer(serial, package)
        except AdbError:
            entry.installer = ""

        try:
            path_out = self._run(["-s", serial, "shell", "pm", "path", package], timeout=25)
            for line in path_out.splitlines():
                cleaned = line.strip()
                if cleaned.startswith("package:"):
                    entry.apk_path = cleaned[len("package:"):].strip()
                    break
        except AdbError:
            pass

        entry.is_system = entry.apk_path.startswith("/system/") or entry.apk_path.startswith("/vendor/")
        entry.source = classify_installer(entry.installer, is_system=entry.is_system)
        entry.apk_size_bytes = self.apk_size_on_device(serial, package)
        return entry

    # -- runtime control ---------------------------------------------------

    def force_stop(self, serial: str, package: str) -> UninstallOutcome:
        """Kill a running app immediately without touching its installation.

        ``am force-stop`` is the quickest way to silence an app that is drawing
        an overlay; the technician can then decide whether to uninstall it.
        """
        try:
            self._run(
                ["-s", serial, "shell", "am", "force-stop", _shell_quote(package)],
                timeout=30,
            )
        except AdbError as exc:
            return UninstallOutcome(package, False, str(exc))
        return UninstallOutcome(package, True, "หยุดแอปแล้ว")

    def kill_process(self, serial: str, pid: int) -> UninstallOutcome:
        """Send SIGKILL to one PID. Used when force-stop is not enough."""
        try:
            self.run_shell(serial, f"kill -9 {int(pid)}", timeout=15)
        except (AdbError, ValueError) as exc:
            return UninstallOutcome(str(pid), False, str(exc))
        return UninstallOutcome(str(pid), True, "ยกเลิกโปรเซสแล้ว")

    def is_running(self, serial: str, package: str) -> bool:
        """Cheap liveness probe — ``pidof`` when available, ``ps`` otherwise."""
        try:
            raw = self.run_shell(serial, f"pidof {_shell_quote(package)}", timeout=12)
            if raw.strip():
                return True
        except AdbError:
            pass
        try:
            raw = self.run_shell(serial, "ps -A -o NAME", timeout=20)
        except AdbError:
            return False
        return package in raw

    def enable_package(self, serial: str, package: str) -> UninstallOutcome:
        """Re-enable a package previously hidden with ``--user 0``."""
        try:
            out = self._run(
                ["-s", serial, "shell", "pm", "install-existing", package],
                timeout=60,
            )
        except AdbError as exc:
            return UninstallOutcome(package, False, str(exc))
        success = "Success" in out or not out
        return UninstallOutcome(package, success, out or "เปิดใช้งานแล้ว")

    def set_standby_bucket(self, serial: str, package: str, bucket: str) -> UninstallOutcome:
        """Put an app in ``rare``/``frequent``/``active`` standby bucket.

        Restricting the bucket starves a background adware of the wake-ups it
        relies on, without uninstalling anything — a reversible middle ground a
        technician can offer a customer who is not ready to remove an app.
        """
        allowed = {"active", "working_set", "frequent", "rare"}
        if bucket not in allowed:
            return UninstallOutcome(package, False, f"โหมดไม่ถูกต้อง (ต้องเป็น {sorted(allowed)})")
        try:
            self._run(
                ["-s", serial, "shell", "am", "set-standby-bucket", _shell_quote(package), bucket],
                timeout=25,
            )
        except AdbError as exc:
            return UninstallOutcome(package, False, str(exc))
        return UninstallOutcome(package, True, f"ตั้งโหมดพื้นหลังเป็น {bucket} แล้ว")

    def get_standby_bucket(self, serial: str, package: str) -> str:
        """Read a package's current standby bucket (``unknown`` when absent)."""
        try:
            raw = self.run_shell(
                serial,
                f"am get-standby-bucket {_shell_quote(package)}",
                timeout=20,
            )
        except AdbError:
            return "unknown"
        token = raw.strip().split()
        return token[-1] if token else "unknown"


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


class ExtendedAdbWorker(AdbWorker):
    """``AdbWorker`` plus the v0.2 tasks and their result signals."""

    packages_detailed_ready = Signal(list)                # list[AppEntry]
    app_details_ready = Signal(object)                     # AppEntry
    live_snapshot_ready = Signal(object)                   # LiveSnapshot
    live_signals_ready = Signal(list)                      # list[ThreatSignal]
    force_stop_results = Signal(list)                      # list[UninstallOutcome]
    icons_primed = Signal(int, int)                        # found, requested
    db_loaded = Signal(list, str)                          # rows, origin
    db_pushed = Signal(bool, str, str)                     # ok, message, url
    update_found = Signal(object)                          # dict
    update_ready = Signal(str, str)                        # local path, message

    # -- tasks -------------------------------------------------------------

    def _task_scan_detailed(self, payload: dict) -> None:
        """List every package with its installer and match against the blacklist."""
        serial = self._require_device(payload.get("serial", ""))
        blacklist: list[dict] = payload.get("blacklist") or []
        flags: dict[str, str] = payload.get("flags") or {}
        include_clean: bool = bool(payload.get("include_clean", False))
        package_filter = str(payload.get("package_filter", "") or "")

        self._log("INFO", "กำลังอ่านรายชื่อแอปพร้อมแหล่งที่ติดตั้ง...")

        entries = self.manager.list_packages_detailed(serial)
        total = len(entries)

        index: dict[str, dict] = {}
        for item in blacklist:
            pkg = str(item.get("package", "")).strip()
            if pkg:
                index[pkg] = item

        matched: list[AppEntry] = []
        for position, entry in enumerate(entries, start=1):
            if self._stop_requested:
                self._log("WARN", "ผู้ใช้ยกเลิกการสแกน")
                break

            if package_filter:
                needle = package_filter.lower()
                if needle not in entry.package.lower() and needle not in entry.display_name.lower():
                    continue

            entry.user_flag = flags.get(entry.package, "")
            hit = index.get(entry.package)
            if hit is not None:
                entry.app_name = str(hit.get("appName", "") or "")
                entry.threat_type = str(hit.get("type", "") or "")
                entry.risk = str(hit.get("risk", "") or "")
            elif entry.user_flag == "danger":
                entry.risk = entry.risk or "High"
            elif entry.user_flag == "safe":
                entry.risk = ""
            elif not include_clean:
                continue

            matched.append(entry)

            if total and position % 60 == 0:
                self._log("INFO", f"  ตรวจแล้ว {position}/{total} แอป")

        matched.sort(key=lambda item: (config.RISK_ORDER.get(item.risk, 9), 0 if item.source.is_suspicious else 1, item.display_name.lower()))

        self.packages_detailed_ready.emit(matched)
        self._log(
            "OK" if matched else "INFO",
            f"อ่านรายชื่อเสร็จ — {len(matched)} รายการ จากทั้งหมด {total} แอป",
        )

    def _task_app_details(self, payload: dict) -> None:
        serial = self._require_device(payload.get("serial", ""))
        package = str(payload.get("package", "")).strip()
        if not package:
            self.task_failed.emit("ไม่ได้ระบุแพ็คเกจที่ต้องการดูรายละเอียด")
            return

        self._log("INFO", f"กำลังอ่านรายละเอียด {package}...")
        entry = self.manager.get_app_details(serial, package)
        entry.user_flag = (payload.get("flags") or {}).get(package, "")

        blacklist: list[dict] = payload.get("blacklist") or []
        for item in blacklist:
            if str(item.get("package", "")) == package:
                entry.app_name = str(item.get("appName", "") or entry.app_name)
                entry.threat_type = str(item.get("type", "") or "")
                entry.risk = str(item.get("risk", "") or "")
                break

        self.app_details_ready.emit(entry)
        self._log("OK", f"ได้รายละเอียด {package} แล้ว")

    def _task_live_snapshot(self, payload: dict) -> None:
        serial = self._require_device(payload.get("serial", ""))
        blacklist: list[dict] = payload.get("blacklist") or []

        from .device_monitor import DeviceMonitor, evaluate_snapshot

        monitor = DeviceMonitor(self.manager)
        shot = monitor.snapshot(serial)
        self.live_snapshot_ready.emit(shot)

        index = {
            str(item.get("package", "")): str(item.get("risk", "High"))
            for item in blacklist
            if str(item.get("package", ""))
        }
        capable = set(shot.permission_warners) | set(shot.overlay_packages)
        self.live_signals_ready.emit(evaluate_snapshot(shot, index, capable))

    def _task_force_stop(self, payload: dict) -> None:
        serial = self._require_device(payload.get("serial", ""))
        packages: Iterable[str] = payload.get("packages") or []

        from .device_monitor import is_protected

        targets = [p for p in packages if p]
        if not targets:
            self.task_failed.emit("ไม่ได้เลือกแอปที่จะหยุด")
            return

        self._log("INFO", f"กำลังสั่งหยุด {len(targets)} แอป")
        results: list[UninstallOutcome] = []
        for package in targets:
            if is_protected(package):
                message = "ข้ามไป — เป็นส่วนประกอบของระบบ"
                self._log("WARN", f"{package}: {message}")
                results.append(UninstallOutcome(package, False, message))
                continue
            outcome = self.manager.force_stop(serial, package)
            results.append(outcome)
            level = "OK" if outcome.success else "ERROR"
            self._log(level, f"หยุด {package}: {outcome.message}")

        self.force_stop_results.emit(results)

    def _task_restrict(self, payload: dict) -> None:
        serial = self._require_device(payload.get("serial", ""))
        packages: Iterable[str] = payload.get("packages") or []
        bucket = str(payload.get("bucket", "rare"))

        targets = [p for p in packages if p]
        if not targets:
            self.task_failed.emit("ไม่ได้เลือกแอปที่จะจำกัดการทำงานเบื้องหลัง")
            return

        self._log("INFO", f"กำลังจำกัดการทำงานเบื้องหลัง {len(targets)} แอป เป็นโหมด {bucket}")
        results: list[UninstallOutcome] = []
        for package in targets:
            outcome = self.manager.set_standby_bucket(serial, package, bucket)
            results.append(outcome)
            level = "OK" if outcome.success else "ERROR"
            self._log(level, f"{package}: {outcome.message}")

        self.force_stop_results.emit(results)

    def _task_prime_icons(self, payload: dict) -> None:
        """Pull APKs and extract launcher icons for the listed packages.

        Runs on the worker thread so the table can fill icons in progressively
        rather than freezing the GUI. ``limit`` keeps one scan from dragging a
        hundred megabytes across the cable.
        """
        serial = self._require_device(payload.get("serial", ""))
        entries: Sequence[AppEntry] = payload.get("entries") or ()
        limit = int(payload.get("limit", 24) or 24)

        store = IconStore()
        found = 0
        attempted = 0

        for entry in entries[:limit]:
            if self._stop_requested:
                self._log("WARN", "หยุดดึงไอคอนก่อนเวลา")
                break
            attempted += 1
            if store.prime(entry.package, entry.apk_path, self.manager, serial):
                found += 1

        self.icons_primed.emit(found, attempted)
        self._log("INFO", f"ดึงไอคอนจริงได้ {found}/{attempted} รายการ (ที่เหลือใช้ไอคอนสำรอง)")

    # -- database and application maintenance ------------------------------

    def _task_db_load(self, payload: dict) -> None:
        """Fetch the shared blacklist, remote first then local."""
        from . import updater

        rows, origin = updater.load_blacklist(on_status=self._log)
        self.db_loaded.emit(rows, origin)

    def _task_db_push(self, payload: dict) -> None:
        """Publish the edited blacklist back to GitHub."""
        from . import updater

        rows = payload.get("rows") or []
        outcome = updater.push_blacklist_to_github(rows)
        self.db_pushed.emit(outcome.ok, outcome.message, outcome.commit_url)

    def _task_check_update(self, payload: dict) -> None:
        """Compare the running version with the remote manifest."""
        from . import updater

        info = updater.check_for_update()
        if info is None:
            self._log("INFO", "เป็นเวอร์ชันล่าสุดแล้ว — ไม่พบรุ่นใหม่")
            self.update_found.emit({})
            return
        self._log("INFO", f"พบเวอร์ชันใหม่ {info.version}")
        self.update_found.emit(
            {
                "version": info.version,
                "notes": info.notes,
                "download_url": info.download_url,
                "asset_name": info.asset_name,
            }
        )

    def _task_download_update(self, payload: dict) -> None:
        """Download the advertised release and schedule the swap."""
        import config as _config

        from . import updater

        info_raw = payload.get("info") or {}
        if not info_raw:
            self._log("WARN", "ไม่พบข้อมูลเวอร์ชันใหม่")
            return

        class _Info:
            version = str(info_raw.get("version", ""))
            download_url = str(info_raw.get("download_url", ""))
            notes = str(info_raw.get("notes", ""))
            asset_name = str(info_raw.get("asset_name", ""))

        target_name = _Info.asset_name or f"{_config.APP_NAME}.exe"
        destination = _config.download_staging_dir() / target_name

        self._log("INFO", f"กำลังดาวน์โหลด {_Info.version}...")
        try:
            updater.download_file(_Info.download_url, destination)
        except Exception as exc:  # noqa: BLE001 - requests raises a broad family
            self._log("ERROR", f"ดาวน์โหลดไม่สำเร็จ: {exc}")
            self.update_ready.emit("", f"ดาวน์โหลดไม่สำเร็จ: {exc}")
            return

        self.update_ready.emit(str(destination), f"ดาวน์โหลดเสร็จแล้ว: {destination.name}")

    # -- device maintenance ------------------------------------------------

    def _task_wipe_cache(self, payload: dict) -> None:
        serial = self._require_device(payload.get("serial", ""))
        self._log("INFO", "กำลังล้างแคชระบบ…")
        try:
            out = self.manager._run(
                ["-s", serial, "shell", "pm", "trim-caches", "999999999999"],
                timeout=120,
            )
        except AdbError as exc:
            self._log("ERROR", f"ล้างแคชไม่สำเร็จ: {exc}")
            return
        self._log("OK", out or "ล้างแคชเรียบร้อย")

    def _task_restart_apps(self, payload: dict) -> None:
        """Force-stop every third-party app — the classic "unstick the phone" move."""
        serial = self._require_device(payload.get("serial", ""))
        self._log("INFO", "กำลังรีสตาร์ทแอปที่กำลังทำงาน…")

        from .device_monitor import DeviceMonitor, is_protected

        monitor = DeviceMonitor(self.manager)
        packages = monitor.running_processes(serial)

        from .app_model import KNOWN_STORES

        targets = [
            pkg
            for pkg in packages
            if not is_protected(pkg)
            and pkg.count(".") >= 1
            and pkg not in {"android", "com.android.shell"}
        ]
        stopped = 0
        for package in targets:
            if self._stop_requested:
                break
            outcome = self.manager.force_stop(serial, package)
            if outcome.success:
                stopped += 1
        self._log("OK", f"รีสตาร์ทแล้ว {stopped} แอป (ข้ามส่วนประกอบระบบ)")

    def _task_start_server(self, payload: dict) -> None:
        self._log("INFO", "กำลังเปิดเซิร์ฟเวอร์ ADB ใหม่…")
        self.manager.kill_server()
        out = self.manager.start_server()
        self._log("OK", out.strip() or "เปิดเซิร์ฟเวอร์ ADB แล้ว")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _no_window() -> int:
    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _shell_quote(value: str) -> str:
    """Single-quote a value for the device shell."""
    return "'" + str(value).replace("'", "'\\''") + "'"


def _parse_size(raw: str) -> int:
    """Extract a byte count from ``stat`` or ``ls -l`` output."""
    text = (raw or "").strip()
    if not text:
        return 0

    # `stat -c %s` prints the bare number.
    first = text.split()[0] if text.split() else ""
    if first.isdigit():
        return int(first)

    # `ls -l` puts the size in field 5 (index 4).
    match = re.search(r"^\S+\s+\d+\s+\S+\s+\S+\s+(\d+)\s", text, re.MULTILINE)
    if match:
        return int(match.group(1))
    return 0


def _first_match(regex: re.Pattern[str], raw: str) -> str:
    match = regex.search(raw or "")
    return match.group(1).strip() if match else ""


def _extract_permissions(dumpsys: str) -> list[str]:
    """Pull the ``requested permissions`` block out of ``dumpsys package``.

    Everything after the header belongs to the list until a line that is not a
    permission-shaped token, so a permission may legitimately span the search.
    """
    header = _RE_PERMISSIONS_HEADER.search(dumpsys or "")
    if not header:
        return []

    tail = dumpsys[header.end():]
    found: list[str] = []
    seen: set[str] = set()
    for line in tail.splitlines():
        match = _RE_PERMISSION_LINE.match(line)
        if match:
            name = match.group(1)
            if name not in seen:
                seen.add(name)
                found.append(name)
            continue
        # A blank line ends the block.
        if not line.strip():
            if found:
                break
    return [name for name in found if name not in _IMPLIED_PERMISSIONS]


__all__ = [
    "AdbError",
    "AdbManager",
    "AdbWorker",
    "AppEntry",
    "DeviceRecord",
    "ExtendedAdbManager",
    "ExtendedAdbWorker",
    "LiveSnapshot",
    "UninstallOutcome",
    "parse_device_time",
]


def parse_device_time(text: str) -> int:
    """Public alias for the timestamp parser."""
    return _parse_device_time(text)