"""
core.app_model
==============

Pure-Python data models and heuristics shared by the whole application.

Nothing in here imports Qt or talks to ``adb`` — the module is deliberately
usable from plain unit tests. Its job is to answer three questions:

1. **Where did this app come from?** — :func:`classify_installer` turns the raw
   ``installer=`` string reported by ``pm list packages -i`` into a
   human-readable verdict (Play Store, an OEM store, sideloaded, unknown).
2. **How dangerous is it?** — :func:`risk_rank`, :func:`sort_key` and the
   permission severity table below.
3. **What did the technician decide?** — :class:`FlagStore` persists per-user
   overrides ("I want to keep this one") next to the shared blacklist so that
   one machine's opinion never leaks into every other install.

Installer classification
------------------------
``adb shell pm list packages -i`` reports one of three things per package:

===========================  ==========================================
``installer=com.android.vending``  trusted — Google Play
``installer=null``                  sideloaded / unknown provenance
``installer=com.sec.android...``    OEM store — trusted if we know it
===========================  ==========================================

A ``null`` installer is the single most useful signal a technician gets: an app
that arrived by tapping a link in a text message has no installer recorded, and
that is exactly the population adware lives in.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

import config

# ---------------------------------------------------------------------------
# Installer classification
# ---------------------------------------------------------------------------

#: Known OEM / alternative store packages mapped to a short display label.
#: Anything not listed here is treated as an unknown third-party installer.
KNOWN_STORES: dict[str, str] = {
    "com.android.vending": "Play Store",
    "com.google.android.feedback": "Google",
    "com.sec.android.app.samsungapps": "Galaxy Store",
    "com.samsung.android.app.one.store": "Galaxy Store",
    "com.huawei.appmarket": "AppGallery",
    "com.xiaomi.market": "GetApps",
    "com.oppo.market": "OPPO Store",
    "com.bbk.appstore": "vivo Store",
    "com.coloros.oppoguardelf": "OPPO",
    "com.amazon.venezia": "Amazon Appstore",
    "com.microsoft.windowsstore": "Microsoft Store",
    "org.fdroid.fdroid": "F-Droid",
    "com.android.packageinstaller": "ติดตั้งเอง",
    "com.owncloudmarketplace": "OwnCloud",
    "com.samsung.android.vending": "Galaxy Store",
    "com.google.android.packageinstaller": "Google",
    "com.android.vending.billing": "Play Store",
}


@dataclass(frozen=True)
class SourceVerdict:
    """Outcome of inspecting a package's installer.

    ``kind`` is one of ``play`` / ``store`` / ``side`` / ``unknown`` / ``system``
    and drives both the badge colour and the sorting priority.
    """

    kind: str
    label: str
    trusted: bool
    #: Sort weight — lower is "more trustworthy".
    rank: int = 50

    @property
    def token(self) -> str:
        """Colour token key understood by :mod:`ui.styles`."""
        return {
            "play": "src_play",
            "store": "src_store",
            "system": "src_system",
            "side": "src_side",
            "unknown": "src_unknown",
        }.get(self.kind, "src_unknown")

    @property
    def is_suspicious(self) -> bool:
        """True for sideloaded or unrecognised installers."""
        return self.kind in {"side", "unknown"}


SYSTEM_SOURCE = SourceVerdict(kind="system", label="ระบบ", trusted=True, rank=0)
PLAY_SOURCE = SourceVerdict(kind="play", label="Play Store", trusted=True, rank=1)
SIDE_SOURCE = SourceVerdict(kind="side", label="Sideload", trusted=False, rank=90)
UNKNOWN_SOURCE = SourceVerdict(kind="unknown", label="ไม่ทราบ", trusted=False, rank=80)


def classify_installer(installer: str | None, *, is_system: bool = False) -> SourceVerdict:
    """Turn a raw installer package name into a :class:`SourceVerdict`.

    ``installer`` is whatever ``pm list packages -i`` printed after the
    ``installer=`` prefix. An empty string, ``null`` and ``None`` all mean the
    package has no recorded installer.
    """
    raw = str(installer or "").strip()

    if raw.lower() in {"null", "none", ""}:
        # A pre-installed system app without an installer is still a system app;
        # report it as such rather than accusing it of being sideloaded.
        if is_system:
            return SYSTEM_SOURCE
        return SIDE_SOURCE

    if raw in KNOWN_STORES:
        label = KNOWN_STORES[raw]
        if raw == config.PLAY_STORE_PACKAGE:
            return PLAY_SOURCE
        # Play Protect's own feedback helper is a special case of "Play".
        if raw == "com.google.android.feedback":
            return SourceVerdict(kind="play", label="Google", trusted=True, rank=2)
        return SourceVerdict(kind="store", label=label, trusted=True, rank=5)

    if is_system:
        # Many OEM system components ship without an installer or with a
        # vendor-private one. They are pre-installed, not sideloaded.
        return SYSTEM_SOURCE

    return UNKNOWN_SOURCE


# ---------------------------------------------------------------------------
# Permission severity
# ---------------------------------------------------------------------------

#: Permissions that let an app display content over other apps. These are the
#: permissions behind the "ad pops up over everything" behaviour technicians
#: complain about most.
OVERLAY_PERMISSIONS = frozenset(
    {
        "android.permission.SYSTEM_ALERT_WINDOW",
        "android.permission.INTERNAL_SYSTEM_WINDOW",
        "android.permission.HIDE_OVERLAY_PERMISSION",
    }
)

#: Permissions that let an app bring itself back after a reboot or be stopped.
PERSISTENCE_PERMISSIONS = frozenset(
    {
        "android.permission.RECEIVE_BOOT_COMPLETED",
        "android.permission.BOOT_COMPLETED",
        "android.permission.FOREGROUND_SERVICE",
        "android.permission.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS",
        "android.permission.DISABLE_KEYGUARD",
        "android.permission.WAKE_LOCK",
    }
)

#: Permissions that let an app install other packages — a strong PUA signal.
INSTALLER_PERMISSIONS = frozenset(
    {
        "android.permission.REQUEST_INSTALL_PACKAGES",
        "android.permission.INSTALL_PACKAGES",
    }
)

#: Permissions that touch personal data. Grouped so the UI can group them.
SENSITIVE_PERMISSIONS = frozenset(
    {
        "android.permission.READ_SMS",
        "android.permission.RECEIVE_SMS",
        "android.permission.SEND_SMS",
        "android.permission.READ_CONTACTS",
        "android.permission.WRITE_CONTACTS",
        "android.permission.READ_CALL_LOG",
        "android.permission.CAMERA",
        "android.permission.RECORD_AUDIO",
        "android.permission.ACCESS_FINE_LOCATION",
        "android.permission.ACCESS_COARSE_LOCATION",
        "android.permission.ACCESS_BACKGROUND_LOCATION",
        "android.permission.READ_PHONE_STATE",
        "android.permission.READ_PHONE_NUMBERS",
        "android.permission.READ_EXTERNAL_STORAGE",
        "android.permission.WRITE_EXTERNAL_STORAGE",
    }
)

ADMIN_PERMISSIONS = frozenset({"android.permission.BIND_DEVICE_ADMIN"})


@dataclass(frozen=True)
class PermissionVerdict:
    """A single permission plus how alarming it looks on screen."""

    name: str
    short: str
    severity: str  # "critical" | "warn" | "normal"

    @property
    def token(self) -> str:
        return {
            "critical": "risk_critical",
            "warn": "risk_medium",
            "normal": "risk_low",
        }.get(self.severity, "risk_low")


def classify_permission(raw: str) -> PermissionVerdict:
    """Grade one permission string."""
    name = raw.strip()
    short = name.split(".")[-1] if name else ""

    if name in OVERLAY_PERMISSIONS:
        severity = "critical"
    elif name in INSTALLER_PERMISSIONS or name in ADMIN_PERMISSIONS:
        severity = "critical"
    elif name in PERSISTENCE_PERMISSIONS:
        severity = "warn"
    elif name in SENSITIVE_PERMISSIONS:
        severity = "warn"
    else:
        severity = "normal"

    return PermissionVerdict(name=name, short=short, severity=severity)


def summarise_permissions(permissions: Sequence[str]) -> dict[str, int]:
    """Return counts per severity, plus the number of critical names."""
    counts = {"critical": 0, "warn": 0, "normal": 0}
    critical: list[str] = []
    for raw in permissions:
        verdict = classify_permission(raw)
        counts[verdict.severity] = counts.get(verdict.severity, 0) + 1
        if verdict.severity == "critical":
            critical.append(verdict.short)
    counts["total"] = len(permissions)
    counts["critical_names"] = critical
    return counts


# ---------------------------------------------------------------------------
# Risk helpers
# ---------------------------------------------------------------------------


def risk_rank(risk: str) -> int:
    """Numeric rank for a risk label; unknown values sort last."""
    return config.RISK_ORDER.get(str(risk or "").strip().title(), 9)


def sort_key(record: "AppEntry") -> tuple:
    """Ordering used by the scan table: most dangerous and most suspicious first."""
    return (
        risk_rank(record.risk),
        0 if record.source.is_suspicious else 1,
        record.display_name.lower(),
    )


# ---------------------------------------------------------------------------
# App entry
# ---------------------------------------------------------------------------


@dataclass
class AppEntry:
    """One installed application as the UI wants to see it."""

    package: str
    apk_path: str = ""
    is_system: bool = False
    is_enabled: bool = True
    installer: str = ""

    # blacklist metadata
    app_name: str = ""
    threat_type: str = ""
    risk: str = ""

    # detail-panel metadata
    version_name: str = ""
    apk_size_bytes: int = 0
    first_install_ms: int = 0
    last_update_ms: int = 0
    permissions: list[str] = field(default_factory=list)

    # presentation
    icon_path: str = ""
    source: SourceVerdict = UNKNOWN_SOURCE

    # local override
    user_flag: str = ""  # "" | "safe" | "danger"

    # runtime
    running: bool = False
    is_foreground: bool = False
    drawing_overlay: bool = False

    def __post_init__(self) -> None:
        if not self.source.kind or self.source.kind == UNKNOWN_SOURCE.kind:
            if self.source is UNKNOWN_SOURCE:
                self.source = classify_installer(self.installer, is_system=self.is_system)

    # -- presentation helpers ---------------------------------------------

    @property
    def display_name(self) -> str:
        return self.app_name or self.package

    @property
    def in_blacklist(self) -> bool:
        return bool(self.risk)

    @property
    def is_flagged_danger(self) -> bool:
        """Blacklisted, or explicitly marked dangerous by the technician."""
        return bool(self.risk) or self.user_flag == "danger"

    @property
    def is_whitelisted(self) -> bool:
        """Explicitly marked safe, or a member of the trusted installer set."""
        return self.user_flag == "safe" or self.source.trusted

    @property
    def effective_risk(self) -> str:
        """Risk shown in the table, after local overrides are applied."""
        if self.user_flag == "safe":
            return "Safe"
        if self.user_flag == "danger":
            return self.risk or "High"
        return self.risk or ""

    @property
    def size_label(self) -> str:
        if self.apk_size_bytes <= 0:
            return "—"
        mb = self.apk_size_bytes / (1024 * 1024)
        if mb < 1:
            return f"{self.apk_size_bytes / 1024:.0f} KB"
        if mb < 1024:
            return f"{mb:.1f} MB"
        return f"{mb / 1024:.2f} GB"

    @property
    def install_date_label(self) -> str:
        if self.first_install_ms <= 0:
            return "—"
        import datetime as _dt

        moment = _dt.datetime.fromtimestamp(self.first_install_ms / 1000.0)
        return moment.strftime("%d %b %Y")

    @property
    def update_date_label(self) -> str:
        if self.last_update_ms <= 0:
            return "—"
        import datetime as _dt

        moment = _dt.datetime.fromtimestamp(self.last_update_ms / 1000.0)
        return moment.strftime("%d %b %Y")

    @property
    def permission_summary(self) -> dict:
        return summarise_permissions(self.permissions)

    def describe_risk(self) -> str:
        """One-line explanation used in tooltips."""
        parts: list[str] = []
        if self.threat_type:
            parts.append(f"ประเภท {self.threat_type}")
        if self.risk:
            parts.append(f"ความเสี่ยง {self.risk}")
        if self.source.is_suspicious:
            parts.append(f"ติดตั้งแบบ {self.source.label}")
        if self.drawing_overlay:
            parts.append("กำลังวาดทับหน้าจอ")
        return " · ".join(parts) or "ไม่พบข้อมูลเพิ่มเติม"


# ---------------------------------------------------------------------------
# Blacklist database editing
# ---------------------------------------------------------------------------


@dataclass
class DbEntry:
    """A single row of ``local_db.json`` as edited in the Database Manager."""

    package: str = ""
    app_name: str = ""
    threat_type: str = ""
    risk: str = "Medium"
    note: str = ""
    added_by: str = ""
    added_at: str = ""

    THREAT_TYPES = ("Adware", "Junk", "PUA", "Scam", "Spyware", "Trojan", "Other")
    RISK_LEVELS = ("Critical", "High", "Medium", "Low")

    @classmethod
    def from_dict(cls, raw: object) -> "DbEntry | None":
        if not isinstance(raw, dict):
            return None
        package = str(raw.get("package", "")).strip()
        if not package:
            return None
        risk = str(raw.get("risk", "") or "Medium").strip().title()
        return cls(
            package=package,
            app_name=str(raw.get("appName", "") or raw.get("app_name", "") or "").strip(),
            threat_type=str(raw.get("type", "") or raw.get("threat_type", "") or "").strip(),
            risk=risk if risk in cls.RISK_LEVELS else "Medium",
            note=str(raw.get("note", "") or "").strip(),
            added_by=str(raw.get("added_by", "") or "").strip(),
            added_at=str(raw.get("added_at", "") or "").strip(),
        )

    def to_dict(self) -> dict:
        """Serialise back to the canonical on-disk shape.

        Only the four original keys are emitted by default; optional metadata is
        included when present so hand-written notes survive a round trip.
        """
        payload: dict = {
            "package": self.package.strip(),
            "appName": self.app_name.strip(),
            "type": self.threat_type.strip(),
            "risk": self.risk.strip().title(),
        }
        if self.note.strip():
            payload["note"] = self.note.strip()
        if self.added_by.strip():
            payload["added_by"] = self.added_by.strip()
        if self.added_at.strip():
            payload["added_at"] = self.added_at.strip()
        return payload

    def validate(self) -> str:
        """Return an empty string when valid, else a Thai error message."""
        if not self.package.strip():
            return "ต้องระบุชื่อแพ็คเกจ (package)"
        if not _is_valid_package(self.package.strip()):
            return "ชื่อแพ็คเกจไม่ถูกต้อง — ต้องมีอย่างน้อย 2 ส่วนคั่นด้วยจุด เช่น com.example.app"
        if self.risk.strip().title() not in DbEntry.RISK_LEVELS:
            return "ระดับความเสี่ยงไม่ถูกต้อง"
        return ""

    def clone(self) -> "DbEntry":
        return DbEntry(
            package=self.package,
            app_name=self.app_name,
            threat_type=self.threat_type,
            risk=self.risk,
            note=self.note,
            added_by=self.added_by,
            added_at=self.added_at,
        )


def _is_valid_package(name: str) -> bool:
    """Conservative check: at least one dot, no spaces, only sane characters."""
    if " " in name or "\t" in name:
        return False
    if name.count(".") < 1:
        return False
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._")
    return all(char in allowed for char in name)


def normalise_database(rows: Iterable[object]) -> list[dict]:
    """Clean a raw JSON payload into sorted, de-duplicated database rows."""
    cleaned: list[DbEntry] = []
    seen: set[str] = set()
    for raw in rows:
        entry = DbEntry.from_dict(raw)
        if entry is None or entry.package in seen:
            continue
        seen.add(entry.package)
        cleaned.append(entry)
    cleaned.sort(key=lambda item: (risk_rank(item.risk), item.package.lower()))
    return [entry.to_dict() for entry in cleaned]


# ---------------------------------------------------------------------------
# Local per-user overrides
# ---------------------------------------------------------------------------


class FlagStore:
    """Persist the technician's own keep/remove decisions for this machine.

    Two buckets are kept:

    * ``safe``   — packages the user asked not to be flagged again
    * ``danger`` — packages manually marked dangerous even if the shared
      blacklist has no opinion

    The file lives in the per-user application-data directory so it never
    travels with the executable and never reaches the shared GitHub database.
    """

    FILENAME = "flags.json"

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else (config.user_data_dir() / self.FILENAME)
        self._safe: set[str] = set()
        self._danger: set[str] = set()
        self.load()

    # -- persistence -------------------------------------------------------

    def load(self) -> None:
        self._safe.clear()
        self._danger.clear()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        for key in raw.get("safe", []) or []:
            if isinstance(key, str) and key.strip():
                self._safe.add(key.strip())
        for key in raw.get("danger", []) or []:
            if isinstance(key, str) and key.strip():
                self._danger.add(key.strip())

    def save(self) -> None:
        payload = {
            "safe": sorted(self._safe),
            "danger": sorted(self._danger),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    # -- queries -----------------------------------------------------------

    def flag_of(self, package: str) -> str:
        name = str(package or "").strip()
        if not name:
            return ""
        # "safe" wins: an explicit keep should silence any earlier danger mark.
        if name in self._safe:
            return "safe"
        if name in self._danger:
            return "danger"
        return ""

    def safe_packages(self) -> list[str]:
        return sorted(self._safe)

    def danger_packages(self) -> list[str]:
        return sorted(self._danger)

    @property
    def count(self) -> int:
        return len(self._safe) + len(self._danger)

    # -- mutations ---------------------------------------------------------

    def mark_safe(self, package: str) -> None:
        name = str(package or "").strip()
        if not name:
            return
        self._safe.add(name)
        self._danger.discard(name)
        self.save()

    def mark_danger(self, package: str) -> None:
        name = str(package or "").strip()
        if not name:
            return
        self._danger.add(name)
        self._safe.discard(name)
        self.save()

    def clear_flag(self, package: str) -> None:
        name = str(package or "").strip()
        self._safe.discard(name)
        self._danger.discard(name)
        self.save()

    def toggle_safe(self, package: str) -> str:
        """Flip between "keep" and "no opinion"; returns the new state."""
        name = str(package or "").strip()
        if self.flag_of(name) == "safe":
            self.clear_flag(name)
            return ""
        self.mark_safe(name)
        return "safe"

    def toggle_danger(self, package: str) -> str:
        name = str(package or "").strip()
        if self.flag_of(name) == "danger":
            self.clear_flag(name)
            return ""
        self.mark_danger(name)
        return "danger"

    def apply_to(self, entries: Iterable[AppEntry]) -> None:
        """Stamp the stored overrides onto freshly scanned entries."""
        for entry in entries:
            entry.user_flag = self.flag_of(entry.package)


# ---------------------------------------------------------------------------
# Live monitor snapshot
# ---------------------------------------------------------------------------


@dataclass
class LiveSnapshot:
    """One sample of what the device is doing right now."""

    timestamp: str = ""
    foreground: str = ""
    foreground_label: str = ""
    focus_window: str = ""
    overlays: list[tuple[str, str]] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    processes: list[str] = field(default_factory=list)
    permission_warners: list[str] = field(default_factory=list)

    @property
    def overlay_packages(self) -> list[str]:
        return [pkg for pkg, _kind in self.overlays]

    @property
    def has_overlay(self) -> bool:
        return bool(self.overlays)

    def describe_overlay(self) -> str:
        if not self.overlays:
            return "— ไม่มี —"
        first = self.overlays[0][0]
        if len(self.overlays) == 1:
            return first
        return f"{first} และอีก {len(self.overlays) - 1} รายการ"

    def process_count(self) -> int:
        return len(self.processes)

    def suspicious_overlay(self, known_bad: Iterable[str] = ()) -> list[str]:
        """Overlays whose package is on the shared blacklist."""
        bad = {str(p) for p in known_bad if p}
        return [pkg for pkg in self.overlay_packages if pkg in bad]