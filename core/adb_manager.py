"""
core.adb_manager
================

Everything that talks to ``adb`` lives here.

The module is split in two halves on purpose:

* :class:`AdbManager` — plain Python, no Qt. Owns subprocess handling, device
  probing and command parsing. Unit-testable without a GUI.
* :class:`AdbWorker` — a Qt worker object that runs :class:`AdbManager` on a
  background thread and re-emits the results as signals, so the UI never
  blocks.

Threading contract
------------------
Exactly one :class:`AdbWorker` lives on exactly one ``QThread``. The main
window requests work by emitting ``task_requested``; the worker dispatches it
on its own thread. Because the manager serialises access to the adb server
with a lock, concurrent task requests queue up safely instead of interleaving.
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from PySide6.QtCore import QObject, Signal, Slot

import config

# ---------------------------------------------------------------------------
# Data carriers
# ---------------------------------------------------------------------------


@dataclass
class DeviceRecord:
    """One entry from ``adb devices -l``."""

    serial: str
    state: str = "unknown"          # device | unauthorized | offline | unknown
    model: str = ""
    product: str = ""
    device: str = ""
    transport_id: str = ""

    @property
    def is_ready(self) -> bool:
        return self.state == "device"

    @property
    def display_name(self) -> str:
        if self.model:
            return self.model.replace("_", " ")
        return self.serial

    @property
    def state_label(self) -> str:
        return {
            "device": "พร้อมใช้งาน",
            "unauthorized": "ยังไม่อนุญาต",
            "offline": "ออฟไลน์",
        }.get(self.state, self.state)


@dataclass
class DeviceInfo:
    """Aggregated device telemetry shown on the home screen."""

    serial: str = ""
    brand: str = ""
    manufacturer: str = ""
    model: str = ""
    device: str = ""
    android_release: str = ""
    sdk_int: str = ""
    abi: str = ""
    battery_level: int | None = None
    battery_status: str = ""
    battery_health: str = ""
    battery_temperature: str = ""
    storage_free_gb: float | None = None
    storage_total_gb: float | None = None

    @property
    def title(self) -> str:
        parts = [p for p in (self.brand, self.model) if p and p.lower() != "unknown"]
        return " ".join(parts) if parts else (self.serial or "ไม่ทราบรุ่น")

    @property
    def battery_state(self) -> str:
        return {
            2: "กำลังชาร์จ",
            3: "ไม่ได้ชาร์จ",
            4: "ไม่เสียบสาย",
            5: "เต็ม",
        }.get(self.battery_status, "ไม่ทราบ")


@dataclass
class PackageRecord:
    """One installed package plus everything the blacklist can tell us."""

    package: str
    apk_path: str = ""
    is_system: bool = False
    is_enabled: bool = True
    app_name: str = ""
    threat_type: str = ""
    risk: str = ""

    @property
    def display_name(self) -> str:
        return self.app_name or self.package

    @property
    def is_threat(self) -> bool:
        return bool(self.risk)


@dataclass
class UninstallOutcome:
    package: str
    success: bool
    message: str
    forced: bool = False


class AdbError(RuntimeError):
    """Raised when adb is missing, times out, or returns a failure."""


# ---------------------------------------------------------------------------
# AdbManager — pure logic layer
# ---------------------------------------------------------------------------


class AdbManager:
    """Thread-safe wrapper around the adb executable.

    All public methods may raise :class:`AdbError`; callers inside the worker
    translate that into a user-facing message.
    """

    def __init__(self, adb_path: Path | str | None = None) -> None:
        self.adb_path = Path(adb_path) if adb_path else config.find_adb()
        self._lock = threading.Lock()
        self._server_started = False

    # -- low level ---------------------------------------------------------

    @property
    def available(self) -> bool:
        return bool(self.adb_path) and Path(self.adb_path).exists()

    def ensure_available(self) -> Path:
        if not self.available:
            raise AdbError(
                "ไม่พบ adb.exe — กรุณาวางไฟล์ไว้ที่โฟลเดอร์ bin/ "
                "หรือติดตั้ง Android platform-tools"
            )
        return Path(self.adb_path)

    def _run(
        self,
        args: Sequence[str],
        timeout: float = config.ADB_COMMAND_TIMEOUT,
    ) -> str:
        """Execute one adb command and return stdout.

        The lock serialises access: adb's local server is a single shared
        resource and interleaving ``adb`` invocations from several threads
        produces garbled output.
        """
        adb = self.ensure_available()
        cmd = [str(adb), *[str(a) for a in args]]

        try:
            with self._lock:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    creationflags=_creation_flags(),
                )
        except FileNotFoundError as exc:
            raise AdbError(f"เรียกใช้ adb ไม่สำเร็จ: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise AdbError(
                f"คำสั่ง adb ทำงานเกิน {timeout:.0f} วินาที — "
                "ลองถอดสาย USB แล้วเสียบใหม่"
            ) from exc

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()

        if proc.returncode != 0:
            raise AdbError(_clean_adb_error(stderr or stdout))
        return stdout

    def run_shell(self, serial: str, command: str, timeout: float = config.ADB_COMMAND_TIMEOUT) -> str:
        """Run a single shell command on the device."""
        return self._run(["-s", serial, "shell", command], timeout=timeout)

    def getprop(self, serial: str, key: str) -> str:
        return self._run(["-s", serial, "shell", "getprop", key], timeout=15).strip()

    # -- server lifecycle --------------------------------------------------

    def start_server(self, timeout: float = 20.0) -> str:
        out = self._run(["start-server"], timeout=timeout)
        self._server_started = True
        return out

    def kill_server(self) -> None:
        try:
            self._run(["kill-server"], timeout=10)
        except AdbError:
            pass  # killing is advisory; the server may not be running
        finally:
            self._server_started = False

    # -- device discovery --------------------------------------------------

    def list_devices(self) -> list[DeviceRecord]:
        output = self._run(["devices", "-l"], timeout=20)
        records: list[DeviceRecord] = []
        for raw in output.splitlines():
            line = raw.strip()
            if not line or line.lower().startswith("list of devices"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            serial, state = parts[0], parts[1]
            extras: dict[str, str] = {}
            for token in parts[2:]:
                if ":" in token:
                    key, _, value = token.partition(":")
                    extras[key] = value
            records.append(
                DeviceRecord(
                    serial=serial,
                    state=state,
                    model=extras.get("model", ""),
                    product=extras.get("product", ""),
                    device=extras.get("device", ""),
                    transport_id=extras.get("transport_id", ""),
                )
            )
        return records

    def probe_devices(
        self,
        retries: int = config.DEVICE_PROBE_RETRIES,
        interval_ms: int = config.DEVICE_PROBE_INTERVAL_MS,
        on_attempt: Callable[[int, int], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> DeviceRecord | None:
        """Start the server if needed and poll for an authorised device."""
        try:
            self.start_server()
        except AdbError:
            pass  # server may already be running; listing still works

        for attempt in range(1, max(retries, 1) + 1):
            if should_stop and should_stop():
                return None
            if on_attempt:
                on_attempt(attempt, retries)
            try:
                records = self.list_devices()
            except AdbError:
                records = []

            ready = [r for r in records if r.is_ready]
            if ready:
                return ready[0]

            if any(r.state == "unauthorized" for r in records):
                raise AdbError(
                    "มือถือยังไม่อนุญาตการเชื่อมต่อ — "
                    "ไปที่มือถือแล้วกด 'อนุญาต' ในหน้าต่าง RSA ที่ปรากฏขึ้น"
                )

            if attempt < retries:
                time.sleep(max(interval_ms, 0) / 1000.0)
        return None

    # -- device telemetry --------------------------------------------------

    def get_device_info(self, serial: str) -> DeviceInfo:
        props = {
            "brand": "ro.product.brand",
            "manufacturer": "ro.product.manufacturer",
            "model": "ro.product.model",
            "device": "ro.product.device",
            "android_release": "ro.build.version.release",
            "sdk_int": "ro.build.version.sdk",
            "abi": "ro.product.cpu.abi",
        }
        info = DeviceInfo(serial=serial)
        for attr, key in props.items():
            try:
                setattr(info, attr, self.getprop(serial, key))
            except AdbError:
                setattr(info, attr, "")

        if not info.model:
            try:
                info.model = self.getprop(serial, "ro.product.vendor.model")
            except AdbError:
                info.model = ""

        self._read_battery(serial, info)
        self._read_storage(serial, info)
        return info

    def _read_battery(self, serial: str, info: DeviceInfo) -> None:
        """Parse ``dumpsys battery``.

        Some OEM skins report a bogus level through dumpsys, so fall back to
        the sysfs capacity node before giving up.
        """
        try:
            raw = self.run_shell(serial, "dumpsys battery", timeout=20)
        except AdbError:
            raw = ""

        def _grab(pattern: str) -> str:
            match = re.search(pattern, raw, re.IGNORECASE | re.MULTILINE)
            return match.group(1).strip() if match else ""

        level_text = _grab(r"^\s*level:\s*(\d+)")
        status_text = _grab(r"^\s*status:\s*(\d+)")
        health_text = _grab(r"^\s*health:\s*(\d+)")
        temp_text = _grab(r"^\s*temperature:\s*(\d+)")

        if level_text:
            info.battery_level = int(level_text)
        if status_text and status_text.isdigit():
            info.battery_status = status_text
        if health_text:
            info.battery_health = health_text
        if temp_text and temp_text.isdigit():
            celsius = int(temp_text) / 10.0
            info.battery_temperature = f"{celsius:.1f}°C"

        if info.battery_level is None:
            try:
                fallback = self.run_shell(
                    serial,
                    "cat /sys/class/power_supply/battery/capacity 2>/dev/null",
                    timeout=15,
                ).strip()
                if fallback.isdigit():
                    info.battery_level = int(fallback)
            except AdbError:
                pass

    def _read_storage(self, serial: str, info: DeviceInfo) -> None:
        try:
            raw = self.run_shell(serial, "df /data", timeout=20)
        except AdbError:
            return
        for line in raw.splitlines():
            parts = line.split()
            # Filesystem 1K-blocks Used Available Use% Mounted-on
            if len(parts) >= 5 and parts[-1].startswith("/"):
                try:
                    total_kb = int(parts[1])
                    avail_kb = int(parts[3])
                except ValueError:
                    continue
                info.storage_total_gb = round(total_kb / 1024 / 1024, 1)
                info.storage_free_gb = round(avail_kb / 1024 / 1024, 1)
                break

    # -- package listing ---------------------------------------------------

    def list_packages(self, serial: str, third_party_only: bool = False) -> list[PackageRecord]:
        """List installed packages.

        ``pm list packages -f`` yields ``package:/path/to.apk=com.name`` which
        gives us the APK location for free, letting us tell user-installed
        apps apart from system apps without a second round-trip.
        """
        args = ["-s", serial, "shell", "pm", "list", "packages", "-f"]
        if third_party_only:
            args.append("-3")
        output = self._run(args, timeout=60)

        records: list[PackageRecord] = []
        for line in output.splitlines():
            line = line.strip()
            if not line.startswith("package:"):
                continue
            body = line[len("package:"):]
            if "=" in body:
                apk_path, _, package = body.rpartition("=")
            else:
                apk_path, package = "", body
            package = package.strip()
            if not package:
                continue
            records.append(
                PackageRecord(
                    package=package,
                    apk_path=apk_path.strip(),
                    is_system=apk_path.startswith("/system/") or apk_path.startswith("/vendor/"),
                )
            )
        records.sort(key=lambda r: r.package)
        return records

    def list_disabled_packages(self, serial: str) -> set[str]:
        try:
            output = self._run(["-s", serial, "shell", "pm", "list", "packages", "-d"], timeout=45)
        except AdbError:
            return set()
        return {
            line.strip()[len("package:"):].strip()
            for line in output.splitlines()
            if line.strip().startswith("package:")
        }

    def get_installer(self, serial: str, package: str) -> str:
        """Return the installer package recorded for ``package``.

        Returns an empty string when unknown. Used by the risk heuristics in
        the UI layer.
        """
        try:
            output = self._run(
                ["-s", serial, "shell", "pm", "list", "packages", "-i", package],
                timeout=20,
            )
        except AdbError:
            return ""
        for line in output.splitlines():
            if "installer=" in line:
                return line.split("installer=", 1)[1].strip()
        return ""

    # -- mutation ----------------------------------------------------------

    def uninstall(self, serial: str, package: str, force: bool = False) -> UninstallOutcome:
        """Remove ``package``.

        ``force`` maps to ``pm uninstall --user 0``, which hides a system app
        for the current user without touching the system partition — the app
        comes back on a factory reset and, crucially, this is the only route
        that works for pre-installed bloatware.
        """
        if force:
            try:
                out = self._run(
                    ["-s", serial, "shell", "pm", "uninstall", "--user", "0", package],
                    timeout=60,
                )
            except AdbError as exc:
                return UninstallOutcome(package, False, str(exc), forced=True)
            success = "Success" in out or "Failure" not in out
            return UninstallOutcome(
                package,
                success,
                out or ("สำเร็จ" if success else "ไม่สำเร็จ"),
                forced=True,
            )

        try:
            out = self._run(["-s", serial, "uninstall", package], timeout=60)
        except AdbError as exc:
            message = str(exc)
            # "DELETE_FAILED_INTERNAL_ERROR" on a pre-installed app means we
            # should retry through the --user 0 path rather than give up.
            if "DELETE_FAILED_INTERNAL_ERROR" in message or "not installed for" in message:
                return self.uninstall(serial, package, force=True)
            return UninstallOutcome(package, False, message)

        success = "Success" in out
        return UninstallOutcome(package, success, out or ("สำเร็จ" if success else "ไม่สำเร็จ"))

    def clear_app_data(self, serial: str, package: str) -> UninstallOutcome:
        try:
            out = self._run(
                ["-s", serial, "shell", "pm", "clear", package],
                timeout=90,
            )
        except AdbError as exc:
            return UninstallOutcome(package, False, str(exc))
        success = "Success" in out
        return UninstallOutcome(package, success, out or ("สำเร็จ" if success else "ไม่สำเร็จ"))

    def open_settings(self, serial: str, action: str) -> str:
        """Launch a settings screen on the device."""
        return self._run(
            ["-s", serial, "shell", "am", "start", "-a", f"android.settings.{action}"],
            timeout=30,
        )

    def reboot(self, serial: str, target: str = "") -> str:
        args = ["-s", serial, "reboot"]
        if target:
            args.append(target)
        return self._run(args, timeout=30)


# ---------------------------------------------------------------------------
# AdbWorker — Qt bridge
# ---------------------------------------------------------------------------


class AdbWorker(QObject):
    """Runs :class:`AdbManager` off the GUI thread and re-emits results.

    The main window never calls manager methods directly. It emits
    ``task_requested`` and receives results through the signals below.
    """

    task_requested = Signal(str, object)          # task name, payload dict
    log_line = Signal(str, str)                   # level, message
    devices_ready = Signal(list)                  # list[DeviceRecord]
    device_info_ready = Signal(dict)              # dict of DeviceInfo fields
    packages_ready = Signal(list, str)            # list[PackageRecord], origin tab
    scan_finished = Signal(list, int)             # list[PackageRecord] threats, total scanned
    uninstall_results = Signal(list)               # list[UninstallOutcome]
    action_result = Signal(str, bool, str)        # label, ok, message
    task_failed = Signal(str)                     # human readable error
    task_done = Signal(str)                       # task name

    def __init__(self, manager: AdbManager, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.manager = manager
        self._stop_requested = False

    # -- public API used by the window ------------------------------------

    def request(self, task: str, payload: dict | None = None) -> None:
        """Thread-safe way to queue work from the GUI thread."""
        self.task_requested.emit(task, payload or {})

    @Slot(str, object)
    def _dispatch(self, task: str, payload: object) -> None:
        handler: Callable[[dict], None] | None = getattr(self, f"_task_{task}", None)
        if handler is None:
            self.task_failed.emit(f"ไม่รู้จักงานที่สั่ง: {task}")
            self.task_done.emit(task)
            return
        data = payload if isinstance(payload, dict) else {}
        try:
            handler(data)
        except AdbError as exc:
            self.task_failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - surface anything unexpected
            self.task_failed.emit(f"เกิดข้อผิดพลาดที่ไม่คาดคิด: {exc}")
        finally:
            self.task_done.emit(task)

    # -- helpers used by several tasks -------------------------------------

    def _require_device(self, serial: str) -> str:
        if not serial:
            raise AdbError("ยังไม่ได้เชื่อมต่อมือถือ — กดปุ่มรีเฟรชก่อน")
        return serial

    def _log(self, level: str, message: str) -> None:
        self.log_line.emit(level, message)

    # -- tasks --------------------------------------------------------------

    def _task_probe(self, payload: dict) -> None:
        serial_hint = payload.get("serial", "")
        self._log("INFO", "กำลังตรวจสอบการเชื่อมต่อมือถือ...")

        def _attempt(current: int, total: int) -> None:
            self._log("INFO", f"  ตรวจหามือถือครั้งที่ {current}/{total}")

        try:
            record = self.manager.probe_devices(
                on_attempt=_attempt,
                should_stop=lambda: self._stop_requested,
            )
        except AdbError:
            # An unauthorized device is a user-action problem, not a failure.
            self.devices_ready.emit([])
            raise

        if record is None:
            self.devices_ready.emit([])
            self._log("WARN", "ไม่พบมือถือ — ตรวจว่าเปิด USB Debugging แล้วหรือยัง")
            return

        self.devices_ready.emit([record])
        self._log("OK", f"พบมือถือ: {record.display_name} ({record.serial})")
        if serial_hint and serial_hint != record.serial:
            self._log("INFO", f"  อุปกรณ์เดิมคือ {serial_hint}")

        info = self.manager.get_device_info(record.serial)
        payload_out = {
            "serial": info.serial,
            "brand": info.brand,
            "manufacturer": info.manufacturer,
            "model": info.model,
            "device": info.device,
            "android_release": info.android_release,
            "sdk_int": info.sdk_int,
            "abi": info.abi,
            "battery_level": info.battery_level,
            "battery_status": info.battery_status,
            "battery_state": info.battery_state,
            "battery_health": info.battery_health,
            "battery_temperature": info.battery_temperature,
            "storage_free_gb": info.storage_free_gb,
            "storage_total_gb": info.storage_total_gb,
            "title": info.title,
        }
        self.device_info_ready.emit(payload_out)
        self._log("OK", f"อ่านข้อมูลเครื่องสำเร็จ: {info.title} / Android {info.android_release}")

    def _task_list_packages(self, payload: dict) -> None:
        serial = self._require_device(payload.get("serial", ""))
        third_party = bool(payload.get("third_party_only", False))
        origin = payload.get("origin", "packages")
        self._log("INFO", "กำลังดึงรายชื่อแอปจากมือถือ...")
        records = self.manager.list_packages(serial, third_party_only=third_party)
        self.packages_ready.emit(records, origin)
        self._log("OK", f"พบ {len(records)} แอป")

    def _task_scan(self, payload: dict) -> None:
        serial = self._require_device(payload.get("serial", ""))
        blacklist: list[dict] = payload.get("blacklist") or []
        self._log("INFO", f"เริ่มสแกนด้วยรายการต้องระวัง {len(blacklist)} รายการ")

        records = self.manager.list_packages(serial)
        index: dict[str, dict] = {}
        for entry in blacklist:
            pkg = str(entry.get("package", "")).strip()
            if pkg:
                index[pkg] = entry

        threats: list[PackageRecord] = []
        total = len(records)
        for position, record in enumerate(records, start=1):
            if self._stop_requested:
                self._log("WARN", "ผู้ใช้ยกเลิกการสแกน")
                break
            hit = index.get(record.package)
            if hit is not None:
                record.app_name = str(hit.get("appName", "") or "")
                record.threat_type = str(hit.get("type", "") or "")
                record.risk = str(hit.get("risk", "") or "")
                threats.append(record)
                self._log(
                    "WARN",
                    f"พบรายการต้องระวัง: {record.display_name} "
                    f"({record.risk or 'ไม่ระบุ'}) — {record.package}",
                )
            if total and position % 25 == 0:
                self._log("INFO", f"  ตรวจแล้ว {position}/{total} แอป")

        threats.sort(key=lambda r: (config.RISK_ORDER.get(r.risk, 9), r.package))
        self.scan_finished.emit(threats, total)
        self._log(
            "OK" if not threats else "WARN",
            f"สแกนเสร็จ — พบรายการต้องระวัง {len(threats)} รายการ จากทั้งหมด {total} แอป",
        )

    def _task_uninstall(self, payload: dict) -> None:
        serial = self._require_device(payload.get("serial", ""))
        packages: Iterable[str] = payload.get("packages") or []
        force = bool(payload.get("force", False))
        label = payload.get("label", "แอป")

        targets = [p for p in packages if p]
        if not targets:
            self.task_failed.emit("ไม่ได้เลือกแอปที่จะลบ")
            return

        self._log("INFO", f"กำลังถอนการติดตั้ง {label} จำนวน {len(targets)} รายการ")
        results: list[UninstallOutcome] = []
        for package in targets:
            if self._stop_requested:
                self._log("WARN", "ผู้ใช้ยกเลิกระหว่างดำเนินการ")
                break
            outcome = self.manager.uninstall(serial, package, force=force)
            results.append(outcome)
            if outcome.success:
                mode = " (ถอนแบบบังคับ)" if outcome.forced else ""
                self._log("OK", f"ลบ {package} สำเร็จ{mode}")
            else:
                self._log("ERROR", f"ลบ {package} ไม่สำเร็จ: {outcome.message}")

        self.uninstall_results.emit(results)

    def _task_clear_data(self, payload: dict) -> None:
        serial = self._require_device(payload.get("serial", ""))
        packages: Iterable[str] = payload.get("packages") or []
        label = payload.get("label", "แอป")
        targets = [p for p in packages if p]
        if not targets:
            self.task_failed.emit("ไม่ได้เลือกแอปที่จะล้างข้อมูล")
            return

        self._log("INFO", f"กำลังล้างข้อมูล {label} จำนวน {len(targets)} รายการ")
        results: list[UninstallOutcome] = []
        for package in targets:
            outcome = self.manager.clear_app_data(serial, package)
            results.append(outcome)
            level = "OK" if outcome.success else "ERROR"
            self._log(level, f"ล้างข้อมูล {package}: {outcome.message}")
        self.uninstall_results.emit(results)

    def _task_open_settings(self, payload: dict) -> None:
        serial = self._require_device(payload.get("serial", ""))
        action = str(payload.get("action", ""))
        label = str(payload.get("label", action))
        if not action:
            self.task_failed.emit("ไม่ได้ระบุหน้าตั้งค่าที่ต้องการเปิด")
            return
        try:
            self.manager.open_settings(serial, action)
        except AdbError as exc:
            self.action_result.emit(label, False, str(exc))
            return
        self.action_result.emit(label, True, f"เปิดหน้า{label}บนมือถือแล้ว")

    def _task_reboot(self, payload: dict) -> None:
        serial = self._require_device(payload.get("serial", ""))
        target = str(payload.get("target", ""))
        label = str(payload.get("label", "ปิด-เปิดเครื่อง"))
        try:
            self.manager.reboot(serial, target)
        except AdbError as exc:
            self.action_result.emit(label, False, str(exc))
            return
        note = {
            "": "สั่งปิด-เปิดเครื่องปกติแล้ว มือถือจะเปิดใหม่ในอีก 10–20 วินาที",
            "recovery": "สั่งเข้าโหมด Recovery แล้ว",
            "bootloader": "สั่งเข้าโหมด Bootloader/Fastboot แล้ว — ถ้าค้างให้กดปุ่มเปิดเครื่องค้างไว้",
        }.get(target, f"สั่ง {label} แล้ว")
        self.action_result.emit(label, True, note)

    def _task_kill_server(self, payload: dict) -> None:
        self.manager.kill_server()
        self._log("INFO", "ปิดเซิร์ฟเวอร์ ADB เรียบร้อย")


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def _creation_flags() -> int:
    """Hide the console window that would otherwise flash on Windows."""
    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _clean_adb_error(raw: str) -> str:
    """Translate common adb failures into actionable Thai messages."""
    text = (raw or "").strip()
    lowered = text.lower()

    if "no devices/emulators found" in lowered or "device not found" in lowered:
        return "ไม่พบมือถือ — เชื่อมสาย USB และเปิด USB Debugging อีกครั้ง"
    if "device unauthorized" in lowered:
        return "มือถือยังไม่อนุญาต — กด 'อนุญาต' ในหน้าต่าง RSA บนมือถือ"
    if "device offline" in lowered:
        return "การเชื่อมต่อเป็นออฟไลน์ — ถอดสาย USB แล้วเสียบใหม่"
    if "more than one device" in lowered:
        return "เสียบมือถือหลายเครื่อง — ถอดเครื่องที่ไม่ต้องการออก"
    if "cannot connect to daemon" in lowered:
        return "เซิร์ฟเวอร์ ADB ไม่ทำงาน — ปิดโปรแกรมอื่นที่อาจใช้พอร์ต 5037 แล้วลองใหม่"
    if "unauthorized" in lowered:
        return "มือถือยังไม่อนุญาตการเชื่อมต่อ"

    return text or "คำสั่ง adb ล้มเหลวโดยไม่ทราบสาเหตุ"