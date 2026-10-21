"""
core.icon_store
===============

Disk-backed cache for launcher icons **and** app labels.

Why this lives in ``core`` and not ``ui``
-----------------------------------------
The ADB worker pulls APKs off the device in a background thread so the table
can fill in progressively. That thread lives in :mod:`core.adb_extended`, and
it needs the icon extractor — but the extractor used to live in
:mod:`ui.icons`, which meant ``core`` had to reach upwards into the UI layer
(and, in the version that shipped, it simply forgot the import, so every
icon silently fell back to a coloured letter tile).

The dependency now points the right way: ``core`` owns the extraction, ``ui``
imports from ``core``.

How it works
------------
1. ``pm path <package>`` tells us where the APK lives.
2. ``adb pull`` copies that APK into a per-package cache folder.
3. The APK is a ZIP. The launcher icon lives under ``res/mipmap-*`` or
   ``res/drawable-*`` and the app label lives in ``AndroidManifest.xml``
   (binary XML) possibly pointing at ``resources.arsc``. Both are decoded by
   :mod:`core.apk_meta`.
4. Results are cached on disk (PNG + a sidecar ``.label`` file) and a small
   in-process LRU keeps the most recent entries hot while scrolling.

Everything degrades quietly: a missing icon, a corrupt APK or an offline
device must never stop a scan.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPixmap

import config
from .apk_meta import extract_icon_bytes, read_app_label

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


def _sniff_format(data: bytes) -> str:
    """Map a magic number onto the format string ``QPixmap.loadFromData`` wants."""
    if data.startswith(b"\x89PNG"):
        return "PNG"
    if data.startswith(b"\xff\xd8"):
        return "JPEG"
    if data[:4] == b"RIFF":
        return "WEBP"
    return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class IconStore:
    """Disk-backed icon + label cache with a small in-process hot set.

    Usage from the UI::

        store = IconStore()
        store.prime(package, apk_path, manager, serial)   # off the GUI thread
        icon = store.pixmap(package, apk_path, 34)         # safe on the GUI thread
        label = store.label(package, apk_path)             # "" when unknown
    """

    def __init__(self, max_memory: int = 400) -> None:
        self._max_memory = max_memory
        self._memory: dict[str, QPixmap] = {}
        self._labels: dict[str, str] = {}

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
        label_file = icon_cache_dir() / f"{key}.label"

        # The label is cheap to read from the sidecar and may already exist
        # even when the icon failed (e.g. an adaptive icon with no raster).
        if label_file.exists() and not self._labels.get(name):
            try:
                self._labels[name] = label_file.read_text(encoding="utf-8").strip()
            except OSError:
                pass

        if target.exists() and target.stat().st_size > 0:
            return True

        if adb_manager is None or not serial:
            return False

        try:
            local_apk = self._pull_apk(adb_manager, serial, name, apk_path)
            if local_apk is None:
                return False

            # The label is read from the same APK we already pulled — no extra
            # round trip, no extra download.
            if not label_file.exists():
                label = read_app_label(local_apk)
                if label:
                    try:
                        label_file.write_text(label, encoding="utf-8")
                    except OSError:
                        pass
                    self._labels[name] = label

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

    def label(self, package: str, apk_path: str = "") -> str:
        """Return the cached app label for ``package`` (``""`` when unknown).

        Reads the sidecar written by :meth:`prime`; never touches the device.
        """
        name = (package or "").strip()
        if not name:
            return ""
        cached = self._labels.get(name)
        if cached:
            return cached
        if not apk_path:
            return ""
        label_file = icon_cache_dir() / f"{_cache_key(name, apk_path)}.label"
        try:
            if label_file.exists():
                value = label_file.read_text(encoding="utf-8").strip()
                self._labels[name] = value
                return value
        except OSError:
            pass
        return ""

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


# ---------------------------------------------------------------------------
# Standalone helpers
# ---------------------------------------------------------------------------


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


def build_icon_for_entry(entry, size: int = 34) -> QIcon:
    """Convenience wrapper taking an :class:`~core.app_model.AppEntry`."""
    return QIcon(render_fallback_icon(getattr(entry, "package", ""), size))
