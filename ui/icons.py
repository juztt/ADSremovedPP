"""
ui.icons
========

Icon extraction and rendering for the app table.

Technicians judge an unfamiliar package far faster from its launcher icon than
from a 30-character identifier, so this module pulls real icons off the device.

How it works
------------
1. ``pm path <package>`` tells us where the APK lives.
2. ``adb pull`` copies that APK into a per-package cache folder.
3. The APK is a ZIP; the launcher icon lives under ``res/mipmap-*`` or
   ``res/drawable-*`` and is **compiled Android binary XML** in most builds, not
   a plain PNG. Rather than ship an AXML parser, the module:

   * looks for an *uncompiled* image first (``ic_launcher.png``,
     ``ic_launcher.webp`` and friends) and loads that directly;
   * otherwise falls back to a deterministic colour tile derived from the
     package name, so a row always shows *something* recognisable.

Because every extraction is disk- and time-consuming, results are cached on
disk and an in-process LRU keeps the most recent icons hot while scrolling.

Everything degrades quietly: a missing icon, a corrupt APK or an offline
device must never stop a scan.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPixmap

import config

# ---------------------------------------------------------------------------
# Cache locations
# ---------------------------------------------------------------------------


def icon_cache_dir() -> Path:
    """Folder holding extracted launcher icons."""
    path = config.user_data_dir() / "icons"
    path.mkdir(parents=True, exist_ok=True)
    return path


def apk_cache_dir() -> Path:
    """Folder holding pulled APKs (kept small; only the current batch)."""
    path = config.user_data_dir() / "apk"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_key(package: str, apk_path: str) -> str:
    """Stable filename for a package, changing when its APK path changes."""
    seed = f"{package}|{apk_path}".encode("utf-8", errors="replace")
    return hashlib.sha1(seed).hexdigest()[:20]


# ---------------------------------------------------------------------------
# Deterministic fallback tiles
# ---------------------------------------------------------------------------

#: Twelve hues chosen to stay distinguishable at 34 px and readable in both
#: light and dark palettes.
_TILE_HUES = (
    "#FF6B6B", "#FF9500", "#FFCC00", "#34C759", "#00C7BE", "#30B0C7",
    "#007AFF", "#5856D6", "#AF52DE", "#FF2D55", "#A2845E", "#8E8E93",
)


def _hash_package(package: str) -> int:
    return int(hashlib.md5((package or "x").encode("utf-8", errors="replace")).hexdigest()[:8], 16)


def colour_for_package(package: str) -> str:
    """Stable accent colour for a package — used by the fallback tile."""
    return _TILE_HUES[_hash_package(package) % len(_TILE_HUES)]


def _initial_glyph(package: str) -> str:
    """A single character that hints at the app: ``com.fake.cleaner`` → ``C``."""
    cleaned = (package or "").strip()
    if not cleaned:
        return "?"
    # Use the most "meaningful" segment — the last one is usually the app name.
    parts = [p for p in cleaned.split(".") if p and p not in {"com", "org", "net", "io", "app"}]
    source = parts[-1] if parts else cleaned
    match = next((ch for ch in source if ch.isalnum()), "?")
    return match.upper()


def render_fallback_icon(package: str, size: int = 34) -> QPixmap:
    """Draw a rounded gradient tile with the package's initial.

    Used when the real icon cannot be extracted. Keeping it deterministic means
    the same app always looks the same across rows and sessions.
    """
    colour = QColor(colour_for_package(package))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    lighter = QColor(colour).lighter(135)
    darker = QColor(colour).darker(125)

    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0.0, lighter)
    gradient.setColorAt(1.0, darker)
    painter.setBrush(gradient)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(0, 0, size, size, size * 0.27, size * 0.27)

    font = QFont()
    font.setPixelSize(max(int(size * 0.46), 9))
    font.setBold(True)
    painter.setFont(font)
    # Dark glyph on light tiles, light glyph on dark tiles.
    luminance = 0.299 * colour.red() + 0.587 * colour.green() + 0.114 * colour.blue()
    painter.setPen(QColor("#1C1C1E") if luminance > 150 else QColor("#FFFFFF"))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, _initial_glyph(package))
    painter.end()

    return pixmap


# ---------------------------------------------------------------------------
# APK extraction
# ---------------------------------------------------------------------------

#: Candidate resource paths, best-looking first. Modern builds ship adaptive
#: icons in ``mipmap-anydpi-v26`` (compiled XML — skipped) and raster fallbacks
#: in the density buckets below.
_ICON_BASENAMES = (
    "ic_launcher.png",
    "ic_launcher.webp",
    "ic_launcher.jpg",
    "ic_launcher_round.png",
    "ic_launcher_round.webp",
    "ic_launcher_foreground.png",
    "icon.png",
    "app_icon.png",
)

#: Density buckets ordered from highest to lowest quality.
_MIPMAP_DIRS = (
    "mipmap-xxxhdpi",
    "mipmap-xxhdpi",
    "mipmap-xhdpi",
    "mipmap-hdpi",
    "mipmap-mdpi",
    "mipmap-anydpi-v26",
    "mipmap",
)


def extract_icon_bytes(apk_path: Path) -> bytes | None:
    """Read a launcher icon out of a local APK, or ``None`` when unavailable.

    Only plain raster entries are considered. Android binary XML is skipped on
    purpose — decoding it needs an AXML parser we deliberately do not ship.
    """
    try:
        with zipfile.ZipFile(apk_path) as archive:
            names = set(archive.namelist())
            for directory in _MIPMAP_DIRS:
                for base in _ICON_BASENAMES:
                    candidate = f"res/{directory}/{base}"
                    if candidate in names:
                        data = archive.read(candidate)
                        if _looks_like_image(data):
                            return data
            # Last resort: any res/ image whose name contains "launcher".
            for name in sorted(names):
                lowered = name.lower()
                if not lowered.startswith("res/"):
                    continue
                if "launcher" not in lowered and "/icon" not in lowered:
                    continue
                if not lowered.endswith((".png", ".webp", ".jpg", ".jpeg")):
                    continue
                data = archive.read(name)
                if _looks_like_image(data):
                    return data
    except (zipfile.BadZipFile, OSError, KeyError, ValueError):
        return None
    return None


def _looks_like_image(data: bytes) -> bool:
    """Sniff the file signature so we never hand Qt a compiled XML resource."""
    if not data or len(data) < 4:
        return False
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if data.startswith(b"\xff\xd8\xff"):
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class IconStore:
    """Disk-backed icon cache with a small in-process hot set.

    Usage from the UI::

        store = IconStore()
        store.prime(package, apk_path)        # called off the GUI thread
        icon = store.icon(package, size=34)   # safe on the GUI thread
    """

    def __init__(self, max_memory: int = 400) -> None:
        self._max_memory = max_memory
        self._memory: dict[str, QPixmap] = {}

    # -- extraction --------------------------------------------------------

    def prime(self, package: str, apk_path: str, adb_manager=None, serial: str = "") -> bool:
        """Fetch and cache one icon. Returns ``True`` when a real icon landed.

        Called from a worker thread. Every failure is absorbed; the caller can
        always fall back to :func:`render_fallback_icon`.
        """
        name = (package or "").strip()
        if not name or not apk_path:
            return False

        key = _cache_key(name, apk_path)
        target = icon_cache_dir() / f"{key}.png"
        if target.exists() and target.stat().st_size > 0:
            return True

        if adb_manager is None or not serial:
            return False

        try:
            local_apk = self._pull_apk(adb_manager, serial, name, apk_path)
            if local_apk is None:
                return False
            data = extract_icon_bytes(local_apk)
            if data is None:
                # Keep a copy anyway — a later version may ship a plain PNG.
                self._note_apk_path(name, apk_path)
                return False
            self._write_png(target, data)
            return True
        except Exception:  # noqa: BLE001 - icon extraction is best-effort
            return False

    def _pull_apk(self, adb_manager, serial: str, package: str, apk_path: str) -> Path | None:
        """Copy one APK off the device. Split APKs use the first entry only."""
        destination = apk_cache_dir() / f"{package}.apk"
        if destination.exists() and destination.stat().st_size > 0:
            return destination
        adb_manager.pull(serial, apk_path, destination)
        return destination if destination.exists() else None

    def _note_apk_path(self, package: str, apk_path: str) -> None:
        """Remember a remote APK path so the icon can be re-extracted later."""
        index = icon_cache_dir() / "paths.txt"
        try:
            with index.open("a", encoding="utf-8") as handle:
                handle.write(f"{package}\t{apk_path}\n")
        except OSError:
            pass

    @staticmethod
    def _write_png(target: Path, data: bytes) -> None:
        pixmap = QPixmap()
        if not pixmap.loadFromData(data, _sniff_format(data)):
            return
        scaled = pixmap.scaled(
            QSize(96, 96),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.save(str(target), "PNG")

    # -- rendering ---------------------------------------------------------

    def pixmap(self, package: str, apk_path: str = "", size: int = 34) -> QPixmap:
        """Return the best available pixmap for ``package``."""
        name = (package or "").strip()
        cache_key = f"{name}|{size}"

        hot = self._memory.get(cache_key)
        if hot is not None:
            return hot

        pixmap = QPixmap()
        if name and apk_path:
            candidate = icon_cache_dir() / f"{_cache_key(name, apk_path)}.png"
            if candidate.exists():
                if pixmap.load(str(candidate)):
                    pixmap = pixmap.scaled(
                        size,
                        size,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    self._remember(cache_key, pixmap)
                    return pixmap

        pixmap = render_fallback_icon(name, size)
        self._remember(cache_key, pixmap)
        return pixmap

    def icon(self, package: str, apk_path: str = "", size: int = 34) -> QIcon:
        """:class:`QIcon` wrapper, ready to hand to a Qt item view."""
        return QIcon(self.pixmap(package, apk_path, size))

    def _remember(self, key: str, pixmap: QPixmap) -> None:
        if len(self._memory) >= self._max_memory:
            self._memory.clear()
        self._memory[key] = pixmap

    # -- maintenance -------------------------------------------------------

    @staticmethod
    def clear_cache() -> int:
        """Delete cached icons and pulled APKs; returns files removed."""
        removed = 0
        for folder in (icon_cache_dir(), apk_cache_dir()):
            try:
                for item in folder.iterdir():
                    if item.is_file():
                        item.unlink()
                        removed += 1
            except OSError:
                continue
        return removed

    @staticmethod
    def cache_size_mb() -> float:
        """Total on-disk cache footprint in megabytes."""
        total = 0
        for folder in (icon_cache_dir(), apk_cache_dir()):
            try:
                for item in folder.iterdir():
                    if item.is_file():
                        total += item.stat().st_size
            except OSError:
                continue
        return round(total / (1024 * 1024), 2)

    def remember_paths(self) -> list[tuple[str, str]]:
        """Return ``[(package, apk_path)]`` discovered during earlier runs."""
        index = icon_cache_dir() / "paths.txt"
        pairs: list[tuple[str, str]] = []
        try:
            for line in index.read_text(encoding="utf-8").splitlines():
                parts = line.split("\t", 1)
                if len(parts) == 2 and parts[0] and parts[1]:
                    pairs.append((parts[0], parts[1]))
        except OSError:
            return []
        return pairs


def _sniff_format(data: bytes) -> str:
    """Map a magic number onto the format string ``QPixmap.loadFromData`` wants."""
    if data.startswith(b"\x89PNG"):
        return "PNG"
    if data.startswith(b"\xff\xd8"):
        return "JPEG"
    if data[:4] == b"RIFF":
        return "WEBP"
    return ""


def load_from_stream(data: bytes) -> QPixmap:
    """Decode image bytes into a pixmap without touching the disk."""
    pixmap = QPixmap()
    if data:
        pixmap.loadFromData(data, _sniff_format(data))
    return pixmap


def save_png(pixmap: QPixmap, target: Path) -> bool:
    """Persist a pixmap as PNG, creating parent folders as needed."""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        return bool(pixmap.save(str(target), "PNG"))
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Standalone helpers
# ---------------------------------------------------------------------------


def build_icon_for_entry(entry, size: int = 34) -> QIcon:
    """Convenience wrapper taking an :class:`~core.app_model.AppEntry`."""
    return QIcon(render_fallback_icon(getattr(entry, "package", ""), size))