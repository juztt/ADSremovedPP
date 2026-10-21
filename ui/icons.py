"""
ui.icons
========

Compatibility shim.

The icon/label extraction used to live here, which put a hard dependency from
``core.adb_extended`` (the background worker) up into the ``ui`` package — and
in the version that shipped, the import was simply missing, so every launcher
icon silently fell back to a coloured letter tile.

The implementation now lives in :mod:`core.icon_store` (and the APK parsing in
:mod:`core.apk_meta`), which puts the dependency in the right direction:

    core.adb_extended  ──▶  core.icon_store  ──▶  core.apk_meta
    ui.pages_scan      ──▶  core.icon_store

This module re-exports the public names so existing imports keep working.
"""

from __future__ import annotations

from core.apk_meta import extract_icon_bytes, read_app_label
from core.icon_store import (
    IconStore,
    _cache_key,
    _initial_glyph,
    apk_cache_dir,
    build_icon_for_entry,
    colour_for_package,
    icon_cache_dir,
    load_from_stream,
    render_fallback_icon,
    save_png,
)

__all__ = [
    "IconStore",
    "apk_cache_dir",
    "build_icon_for_entry",
    "colour_for_package",
    "extract_icon_bytes",
    "icon_cache_dir",
    "load_from_stream",
    "read_app_label",
    "render_fallback_icon",
    "save_png",
]
