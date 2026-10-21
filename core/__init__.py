"""
core
====

Non-UI logic for ADSremovedPP.

* :mod:`core.adb_manager` — subprocess layer plus the Qt worker bridge
* :mod:`core.updater` — blacklist sync and application self-update
"""

from __future__ import annotations

__all__ = ["adb_manager", "updater"]