"""
core.gas_reporter
=================

Client module that talks to the Google Apps Script backend backing ADSremovedPP.

Two responsibilities, kept in separate classes so the UI can mix and match:

* :class:`GASClient` — a tiny pure-Python wrapper around the GAS WebApp URL.
  Synchronous, returns parsed JSON or a ``GASResponse`` describing an offline /
  error case. Never raises.
* :class:`GASReporterWorker` — a ``QObject`` that owns the ``QThread`` required
  to keep the UI responsive while ``requests`` is in flight. The window calls
  :meth:`submit` once per uninstall batch and listens to two signals:
  :attr:`reported` (one entry per successfully shipped package) and
  :attr:`failed` (the failure summary string the UI should show in the log).

The configuration helpers (:func:`get_gas_url`, :func:`set_gas_url`,
:func:`get_reporter`, :func:`set_reporter`) read and write ``%APPDATA%
\\ADSremovedPP\\gas_config.json`` so the technician only types the WebApp URL
once.
"""

from __future__ import annotations

import json
import socket
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import requests
from PySide6.QtCore import QObject, QThread, Signal, Slot

import config

#: How long any single HTTP call is allowed to take before we give up.
DEFAULT_TIMEOUT: int = 12

#: User-Agent sent with every request — helps in the GAS Execution log.
_USER_AGENT = f"{config.APP_NAME}/{config.CURRENT_VERSION} (GASClient)"


# ---------------------------------------------------------------------------
# Persistent config (per-user file, not bundled with the exe)
# ---------------------------------------------------------------------------


def _config_path() -> Path:
    return config.user_data_dir() / "gas_config.json"


@dataclass
class GASConfig:
    """The values the technician types into the GAS setup dialog."""

    url: str = ""
    reporter: str = ""
    admin_token: str = ""
    extra: dict = field(default_factory=dict)

    def is_configured(self) -> bool:
        return bool(self.url.strip())

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "reporter": self.reporter,
            "admin_token": self.admin_token,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, payload: dict | None) -> "GASConfig":
        if not isinstance(payload, dict):
            return cls()
        return cls(
            url=str(payload.get("url", "") or ""),
            reporter=str(payload.get("reporter", "") or ""),
            admin_token=str(payload.get("admin_token", "") or ""),
            extra=dict(payload.get("extra", {}) or {}),
        )


def load_gas_config() -> GASConfig:
    """Read the persisted GAS configuration, returning defaults if absent."""
    path = _config_path()
    try:
        if not path.exists():
            return _default_gas_config()
        data = json.loads(path.read_text(encoding="utf-8"))
        return GASConfig.from_dict(data)
    except (OSError, json.JSONDecodeError):
        return _default_gas_config()


def save_gas_config(cfg: GASConfig) -> bool:
    """Persist the GAS configuration to ``%APPDATA%\\ADSremovedPP\\gas_config.json``."""
    try:
        path = _config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except OSError:
        return False


def _default_gas_config() -> GASConfig:
    """Best-effort reporter name (machine / branch) the first time we run."""
    name = socket.gethostname() or ""
    return GASConfig(reporter=name)


def get_gas_url() -> str:
    """Shortcut: just the configured GAS WebApp URL."""
    return load_gas_config().url


def set_gas_url(url: str) -> bool:
    cfg = load_gas_config()
    cfg.url = str(url or "").strip()
    return save_gas_config(cfg)


def get_reporter() -> str:
    return load_gas_config().reporter


def set_reporter(value: str) -> bool:
    cfg = load_gas_config()
    cfg.reporter = str(value or "").strip() or socket.gethostname() or ""
    return save_gas_config(cfg)


def get_admin_token() -> str:
    return load_gas_config().admin_token


def set_admin_token(value: str) -> bool:
    cfg = load_gas_config()
    cfg.admin_token = str(value or "").strip()
    return save_gas_config(cfg)


# ---------------------------------------------------------------------------
# Synchronous client
# ---------------------------------------------------------------------------


@dataclass
class GASResponse:
    """Result of a single GAS call. Never raises from the client."""

    ok: bool
    message: str
    status_code: int = 0
    payload: dict | None = None
    entries: list = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok

    @classmethod
    def from_exception(cls, exc: Exception) -> "GASResponse":
        return cls(
            ok=False,
            message=f"{exc.__class__.__name__}: {exc}",
            status_code=0,
        )


class GASClient:
    """Lightweight synchronous wrapper around a GAS WebApp URL.

    Two entrypoints:

    * :meth:`fetch_approved` — ``GET ?action=get_approved``
    * :meth:`report_uninstall` — ``POST`` with the JSON payload described in the
      backend's :file:`Code.gs` (``packages`` + device metadata).

    The class never raises; every failure mode is returned as a
    :class:`GASResponse`. This keeps the worker layer free of ``try/except``
    plumbing on every call site.
    """

    def __init__(self, url: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> None:
        # ``None`` means "use whatever is configured"; an explicit string is
        # taken literally. An empty string therefore stays empty instead of
        # silently falling back to the configured backend — the caller asked
        # for "no endpoint" and must get "no endpoint".
        if url is None:
            self.url = get_gas_url().strip()
        else:
            self.url = str(url).strip()
        self.timeout = timeout

    # -- low-level ---------------------------------------------------------

    def _endpoint(self, query: str = "") -> str:
        base = self.url.rstrip("/")
        if not base:
            raise RuntimeError("ยังไม่ได้ตั้งค่า GAS WebApp URL")
        return f"{base}{query}"

    def _get(self, query: str) -> GASResponse:
        if not self.url:
            return GASResponse(ok=False, message="ยังไม่ได้ตั้งค่า GAS URL")
        try:
            response = requests.get(
                self._endpoint(query),
                timeout=self.timeout,
                headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            )
            return self._parse(response)
        except requests.RequestException as exc:
            return GASResponse.from_exception(exc)

    def _post(self, body: dict, query: str = "") -> GASResponse:
        if not self.url:
            return GASResponse(ok=False, message="ยังไม่ได้ตั้งค่า GAS URL")
        try:
            response = requests.post(
                self._endpoint(query),
                json=body,
                timeout=self.timeout,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
            return self._parse(response)
        except requests.RequestException as exc:
            return GASResponse.from_exception(exc)

    @staticmethod
    def _parse(response: requests.Response) -> GASResponse:
        try:
            payload = response.json()
        except ValueError:
            payload = None

        if response.ok and isinstance(payload, dict) and payload.get("ok"):
            entries = []
            if isinstance(payload.get("entries"), list):
                entries = list(payload["entries"])
            return GASResponse(
                ok=True,
                message=str(payload.get("message") or "OK"),
                status_code=response.status_code,
                payload=payload,
                entries=entries,
            )

        message = "GAS ตอบกลับไม่สำเร็จ"
        if isinstance(payload, dict):
            message = str(payload.get("error") or payload.get("message") or message)
        elif response.text:
            message = response.text[:200]
        return GASResponse(
            ok=False,
            message=message,
            status_code=response.status_code,
            payload=payload if isinstance(payload, dict) else None,
        )

    # -- public API --------------------------------------------------------

    def ping(self) -> GASResponse:
        """Round-trip check used by the UI to verify the URL is reachable."""
        return self._get("?action=ping")

    def fetch_approved(self) -> GASResponse:
        """Pull the latest Approved_DB list — drops ``local_db.json`` entirely."""
        return self._get("?action=get_approved")

    def report_uninstall(
        self,
        packages: Iterable[dict],
        *,
        device_model: str = "",
        os_version: str = "",
        reporter: str = "",
    ) -> GASResponse:
        """Ship a batch of uninstall events to the backend.

        ``packages`` is an iterable of dicts; each dict may contain ``package``,
        ``appName``, ``category``, ``riskLevel`` and ``source``. Missing values
        are tolerated — the backend fills in defaults.
        """
        body = {
            "packages": list(packages),
            "deviceModel": str(device_model or "").strip(),
            "osVersion": str(os_version or "").strip(),
            "reporter": str(reporter or get_reporter() or "").strip(),
            "submittedAt": _now_iso(),
        }
        return self._post(body)

    def push_approved(self, rows: Iterable[dict]) -> GASResponse:
        """Write the technician's edited blacklist straight into ``Approved_DB``.

        This is the "save my own entries" path used by the Database page. It
        bypasses the pending queue on purpose: the person editing the table
        *is* the admin, so their rows go live immediately.
        """
        body = {
            "action": "push_approved",
            "entries": list(rows),
            "submittedAt": _now_iso(),
        }
        return self._post(body)

    def delete_approved(self, package: str) -> GASResponse:
        """Remove one package from ``Approved_DB``."""
        body = {"action": "delete_approved", "package": str(package or "").strip()}
        return self._post(body)

    # -- compatibility ----------------------------------------------------

    def fetch_blacklist(self) -> GASResponse:
        """Alias kept so :file:`updater.py` stays terse."""
        return self.fetch_approved()


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Local ISO-8601 timestamp with timezone offset."""
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")


class GASReporterWorker:
    """Owns a parentless ``QThread`` and posts uninstall events off the GUI.

    Why a dedicated thread rather than ``QThreadPool``? — the same pattern
    is used elsewhere in the project: ``MainWindow._spawned_threads`` already
    drains all known threads on ``closeEvent``. Reusing that contract keeps
    shutdown deterministic and the silence-from-the-OS clean.

    Usage::

        worker = GASReporterWorker()
        worker.reported.connect(self._on_reported)
        worker.failed.connect(self._on_failed)
        worker.submit(packages, device_model=..., os_version=...)
    """

    def __init__(self, url: str | None = None, parent: object | None = None) -> None:
        self._lock = threading.Lock()
        self._busy = False

        # Parent ownership trick: the QObject **must** live on the worker
        # thread, and the thread must **not** be parented to the window.
        # Create them here, on the calling thread, and moveToThread in a sec.
        self._thread = QThread()
        self._task = _ReporterTask(url=url)
        self._task.moveToThread(self._thread)

        # Re-expose the task signals on this façade.
        self.reported = self._task.reported
        self.failed = self._task.failed
        self.log_line = self._task.log_line

        self._task.finished.connect(self._on_finished)
        self._thread.start()

    # -- public API --------------------------------------------------------

    def submit(
        self,
        packages: Iterable[dict],
        *,
        device_model: str = "",
        os_version: str = "",
        reporter: str = "",
    ) -> bool:
        """Enqueue one batch. Returns ``False`` if a previous batch is in flight.

        The payload crosses the thread boundary as a **JSON string**, not as a
        Python list. PySide6 has no ``QMetaType`` for ``list``, so
        ``Q_ARG(list, items)`` raises

            RuntimeError: qArgDataFromPyType: Unable to find a QMetaType for "list".

        on every call — the slot never ran and the uninstall report was never
        sent, even with a perfectly configured backend. A JSON string maps to
        the built-in ``QString`` meta type, so it travels safely.
        """
        from PySide6.QtCore import QMetaObject, Qt, Q_ARG

        with self._lock:
            if self._busy:
                return False
            self._busy = True

        items = list(packages)
        payload = json.dumps(
            {
                "packages": items,
                "deviceModel": str(device_model or ""),
                "osVersion": str(os_version or ""),
                "reporter": str(reporter or ""),
            },
            ensure_ascii=False,
        )
        QMetaObject.invokeMethod(
            self._task,
            "run",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(str, payload),
        )
        return True

    def is_busy(self) -> bool:
        with self._lock:
            return self._busy

    def shutdown(self) -> None:
        """Drain the worker politely (used by ``MainWindow.closeEvent``)."""
        thread = self._thread
        if thread is None:
            return
        thread.quit()
        thread.wait(4000)
        self._thread = None

    # -- internal ----------------------------------------------------------

    def _on_finished(self) -> None:
        with self._lock:
            self._busy = False


class _ReporterTask(QObject):
    """QObject body of :class:`GASReporterWorker` — lives on the worker thread."""

    reported = Signal(list)               # list[(package, outcome)]
    failed = Signal(str)                  # human-readable error
    log_line = Signal(str, str)           # level, message
    finished = Signal()                   # one batch complete (any outcome)

    def __init__(self, url: str | None = None) -> None:
        super().__init__()
        self._client = GASClient(url=url)

    @Slot(str)
    def run(self, payload: str) -> None:
        """Execute one batch.

        ``payload`` is a JSON string (see :meth:`GASReporterWorker.submit` for
        why it is not a Python object).

        The ``@Slot(str)`` decorator has to sit **inside the class body** —
        PySide6 builds the meta-object when the class is created, so a later
        ``_ReporterTask.run = Slot(str)(...)`` assignment is ignored and
        ``invokeMethod`` reports "No such method _ReporterTask::run(QString)".
        """
        try:
            try:
                request = json.loads(payload)
            except (TypeError, ValueError) as exc:
                self.failed.emit(f"GAS: payload เสียรูปแบบ ({exc})")
                self.reported.emit([])
                return

            packages = request.get("packages") or []
            device_model = str(request.get("deviceModel") or "")
            os_version = str(request.get("osVersion") or "")
            reporter = str(request.get("reporter") or "")

            if not packages:
                self.log_line.emit("INFO", "GAS: ไม่มีรายการให้ส่ง")
                self.reported.emit([])
                return

            self.log_line.emit(
                "INFO",
                f"GAS: กำลังส่งรายการถอนการติดตั้ง {len(packages)} รายการ",
            )

            response = self._client.report_uninstall(
                packages,
                device_model=device_model,
                os_version=os_version,
                reporter=reporter,
            )
            if not response:
                self.failed.emit(f"GAS ส่งไม่สำเร็จ: {response.message}")
                self.reported.emit([])
                return

            details = []
            logged = 0
            queued = 0
            if isinstance(response.payload, dict):
                raw_details = response.payload.get("details")
                if isinstance(raw_details, list):
                    for item in raw_details:
                        if isinstance(item, dict):
                            outcome = str(item.get("outcome") or "")
                            if outcome == "logged":
                                logged += 1
                            elif outcome == "queued":
                                queued += 1
                            details.append(
                                (str(item.get("package") or ""), outcome)
                            )
            self.reported.emit(details)
            self.log_line.emit(
                "OK",
                f"GAS: ส่งเรียบร้อย — บันทึกสถิติ {logged} · รออนุมัติ {queued}",
            )
        except Exception as exc:  # noqa: BLE001 - last-resort safety net
            self.failed.emit(f"GAS worker ล้มเหลว: {exc}")
            self.reported.emit([])
        finally:
            self.finished.emit()


__all__ = [
    "DEFAULT_TIMEOUT",
    "GASClient",
    "GASConfig",
    "GASReporterWorker",
    "GASResponse",
    "get_admin_token",
    "get_gas_url",
    "get_reporter",
    "load_gas_config",
    "save_gas_config",
    "set_admin_token",
    "set_gas_url",
    "set_reporter",
]
