"""
ui
==

Presentation layer for ADSremovedPP.

Module map
----------
``styles``          colour tokens + the generated Qt stylesheet
``widgets``         reusable presentation pieces (cards, pills, delegates)
``icons``           launcher-icon extraction with a disk cache
``log_panel``       the collapsible activity log
``pages_scan``      device strip, blacklist table, bulk actions, app detail
``pages_live``      foreground / overlay / process monitor
``pages_tools``     utility tiles and application maintenance
``pages_database``  blacklist editor, local save and GitHub publish
``main_window``     sidebar shell that wires every page to the ADB worker

Import order matters only in one respect: ``styles`` must be importable without
instantiating a ``QApplication``, because ``main.py`` calls ``apply_theme``
immediately after constructing one.
"""

from __future__ import annotations

__all__ = [
    "styles",
    "widgets",
    "icons",
    "log_panel",
    "pages_scan",
    "pages_live",
    "pages_tools",
    "pages_database",
    "main_window",
]