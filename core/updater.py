"""
core.updater
============

Two independent jobs that happen to share a file:

1. **Database sync** — keep the malware/adware blacklist fresh.
   :func:`load_blacklist` tries the remote URL first and silently degrades to
   the bundled JSON when the network is unavailable.

2. **Application auto-update** — compare the running version against a remote
   manifest, offer the download, then hand the swap over to a temporary
   ``updater.bat``. A PyInstaller one-file executable physically cannot replace
   itself while running, which is exactly why the batch file exists.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

import config

#: Keys accepted in the remote version manifest.
_VERSION_KEYS = ("version", "latest", "tag_name", "VERSION")

#: Accepted shapes for a blacklist entry.
_REQUIRED_ENTRY_KEYS = {"package"}


# ---------------------------------------------------------------------------
# Database sync
# ---------------------------------------------------------------------------


def _normalise_entry(raw: object) -> dict | None:
    """Coerce one blacklist row into our canonical dictionary shape."""
    if not isinstance(raw, dict):
        return None
    package = str(raw.get("package", "")).strip()
    if not package:
        return None
    return {
        "package": package,
        "appName": str(raw.get("appName", "") or raw.get("app_name", "") or "").strip(),
        "type": str(raw.get("type", "") or "").strip(),
        "risk": str(raw.get("risk", "") or "").strip(),
    }


def _parse_blacklist(payload: object) -> list[dict]:
    """Accept either a bare list or a ``{"entries": [...]}`` wrapper."""
    if isinstance(payload, dict):
        for key in ("entries", "blacklist", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                payload = value
                break
        else:
            return []
    if not isinstance(payload, list):
        return []

    rows: list[dict] = []
    seen: set[str] = set()
    for item in payload:
        entry = _normalise_entry(item)
        if entry is None or entry["package"] in seen:
            continue
        seen.add(entry["package"])
        rows.append(entry)
    return rows


def load_local_blacklist() -> list[dict]:
    """Read the bundled fallback database from disk."""
    for candidate in (config.LOCAL_DB_PORTABLE, config.LOCAL_DB_RESOURCE):
        try:
            if candidate.exists():
                data = json.loads(candidate.read_text(encoding="utf-8"))
                rows = _parse_blacklist(data)
                if rows:
                    return rows
        except (OSError, json.JSONDecodeError):
            continue
    return []


def load_blacklist(
    url: str = config.BLACKLIST_URL,
    timeout: int = config.NETWORK_TIMEOUT,
    on_status: Callable[[str], None] | None = None,
) -> tuple[list[dict], str]:
    """Fetch the blacklist, falling back to the local copy.

    Returns ``(entries, origin)`` where ``origin`` is one of
    ``"remote"``, ``"local"`` or ``"empty"`` so the UI can be honest with the
    user about which data is being used.
    """

    def _say(message: str) -> None:
        if on_status:
            on_status(message)

    if url:
        try:
            _say("กำลังดาวน์โหลดรายการต้องระวังจากอินเทอร์เน็ต...")
            response = requests.get(
                url,
                timeout=timeout,
                headers={"User-Agent": f"{config.APP_NAME}/{config.CURRENT_VERSION}"},
            )
            response.raise_for_status()
            rows = _parse_blacklist(response.json())
            if rows:
                _say(f"ดาวน์โหลดสำเร็จ — {len(rows)} รายการ")
                return rows, "remote"
            _say("ไฟล์ที่ดาวน์โหลดมาไม่มีข้อมูล — ใช้ไฟล์ในเครื่องแทน")
        except requests.RequestException as exc:
            _say(f"ดาวน์โหลดไม่สำเร็จ ({exc.__class__.__name__}) — ใช้ไฟล์ในเครื่องแทน")
        except (json.JSONDecodeError, ValueError):
            _say("ไฟล์ที่ดาวน์โหลดมาผิดรูปแบบ — ใช้ไฟล์ในเครื่องแทน")
    else:
        _say("ยังไม่ได้ตั้งค่า URL ของฐานข้อมูล — ใช้ไฟล์ในเครื่อง")

    local = load_local_blacklist()
    if local:
        _say(f"ใช้รายการต้องระวังจากเครื่อง — {len(local)} รายการ")
        return local, "local"
    return [], "empty"


# ---------------------------------------------------------------------------
# Version check
# ---------------------------------------------------------------------------


@dataclass
class UpdateInfo:
    version: str
    download_url: str
    notes: str = ""
    asset_name: str = ""


def parse_version(text: str) -> tuple[int, ...]:
    """Turn ``"v1.2.3"`` into ``(1, 2, 3)`` for reliable comparison."""
    cleaned = str(text or "").strip().lstrip("vV")
    parts: list[int] = []
    for chunk in cleaned.split("."):
        digits = ""
        for char in chunk:
            if char.isdigit():
                digits += char
            else:
                break
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _pick_download_url(payload: dict, fallback: str) -> str:
    """Prefer an explicit download_url, else the first binary asset."""
    explicit = str(payload.get("download_url") or payload.get("url") or "").strip()
    if explicit:
        return explicit

    assets = payload.get("assets")
    if isinstance(assets, list):
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name", "")).lower()
            if name.endswith(".exe"):
                browser_url = str(asset.get("browser_download_url", "")).strip()
                if browser_url:
                    return browser_url
    return fallback


def check_for_update(
    url: str = config.VERSION_URL,
    current: str = config.CURRENT_VERSION,
    timeout: int = config.NETWORK_TIMEOUT,
    fallback_download: str = config.UPDATE_EXE_URL,
) -> UpdateInfo | None:
    """Return update details when a newer release exists, else ``None``.

    Never raises: an offline machine simply gets ``None``.
    """
    if not url:
        return None
    try:
        response = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": f"{config.APP_NAME}/{current}"},
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, json.JSONDecodeError, ValueError):
        return None

    if not isinstance(payload, dict):
        return None

    latest_text = ""
    for key in _VERSION_KEYS:
        value = payload.get(key)
        if value:
            latest_text = str(value)
            break
    if not latest_text:
        return None

    if parse_version(latest_text) <= parse_version(current):
        return None

    download_url = _pick_download_url(payload, fallback_download)
    if not download_url:
        return None

    return UpdateInfo(
        version=latest_text.strip(),
        download_url=download_url,
        notes=str(payload.get("notes") or payload.get("body") or "").strip(),
        asset_name=str(payload.get("asset_name") or f"{config.APP_NAME}.exe"),
    )


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

ProgressCB = Callable[[int, int], None]  # bytes downloaded, total bytes


def download_file(
    url: str,
    destination: Path,
    timeout: int = config.NETWORK_TIMEOUT,
    on_progress: ProgressCB | None = None,
) -> Path:
    """Stream ``url`` to ``destination`` with a progress callback."""
    destination.parent.mkdir(parents=True, exist_ok=True)

    with requests.get(url, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        total = int(response.headers.get("Content-Length") or 0)
        received = 0
        with destination.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                handle.write(chunk)
                received += len(chunk)
                if on_progress:
                    on_progress(received, total)
    return destination


# ---------------------------------------------------------------------------
# Self-replacement
# ---------------------------------------------------------------------------


def build_updater_script(
    old_exe: Path,
    new_exe: Path,
    script_path: Path,
    relaunch: bool = True,
) -> Path:
    """Write the batch file that performs the swap.

    A PyInstaller one-file app unpacks itself into a temp folder and holds a
    lock on its own binary, so the replacement has to happen from outside.
    The script:

    1. waits for the old process to release its lock,
    2. deletes the old executable,
    3. renames the freshly downloaded one into place,
    4. relaunches the app,
    5. attempts to delete itself.
    """
    script_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "setlocal enableextensions",
        "title " + config.APP_NAME + " Updater",
        "echo [" + config.APP_NAME + "] กำลังอัปเดต...",
        "",
        "rem รอให้โปรแกรมเก่าปิดสนิทก่อนลบไฟล์",
        "timeout /t 3 /nobreak >nul",
        "",
        f'set "OLD={old_exe}"',
        f'set "NEW={new_exe}"',
        "",
        "rem ถ้ายังลบไม่ได้ ให้ลองอีก 5 ครั้ง หน่วงครั้งละ 1 วินาที",
        "set /a ATTEMPT=0",
        ":WAIT_FREE",
        "if exist \"%OLD%\" del /f /q \"%OLD%\" >nul 2>&1",
        "if not exist \"%OLD%\" goto COPY_NEW",
        "set /a ATTEMPT+=1",
        "if %ATTEMPT% GEQ 5 goto FAILED",
        "timeout /t 1 /nobreak >nul",
        "goto WAIT_FREE",
        "",
        ":COPY_NEW",
        'if not exist "%NEW%" goto FAILED',
        "move /f /y \"%NEW%\" \"%OLD%\" >nul 2>&1",
        "if exist \"%NEW%\" goto FAILED",
        "echo [OK] อัปเดตสำเร็จ",
        "",
    ]

    if relaunch:
        lines += [
            'start "" "%OLD%"',
            "timeout /t 2 /nobreak >nul",
            "",
        ]

    lines += [
        "rem พยายามลบตัวเอง (ล้มเหลวได้โดยไม่กระทบอะไร)",
        'del /f /q "%~f0" >nul 2>&1',
        "exit /b 0",
        "",
        ":FAILED",
        'echo [ERROR] อัปเดตไม่สำเร็จ กรุณาปิดโปรแกรมแล้วลองใหม่',
        'del /f /q "%~f0" >nul 2>&1',
        "pause",
        "exit /b 1",
        "",
    ]

    # Write as UTF-8 with BOM-less bytes so `chcp 65001` renders Thai correctly
    # and the .bat is not mangled by the OEM code page.
    script_path.write_bytes("\n".join(lines).encode("utf-8"))
    return script_path


def launch_updater(script_path: Path) -> None:
    """Run the generated batch file detached from this process."""
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            ["cmd.exe", "/c", str(script_path)],
            creationflags=creationflags,
            cwd=str(script_path.parent),
        )
        return

    # Non-Windows fallback: do the swap with Python directly.
    raise NotImplementedError("Auto-update เฉพาะบน Windows เท่านั้น")


def stage_download(info: UpdateInfo, filename: str | None = None) -> Path:
    """Download an update into the staging directory and return its path."""
    target_name = filename or info.asset_name or f"{config.APP_NAME}.exe"
    destination = config.download_staging_dir() / target_name
    download_file(info.download_url, destination)
    return destination


def manual_update_paths() -> tuple[Path, Path]:
    """Return ``(current_exe, staging_dir)`` for manual inspection."""
    current = Path(sys.executable).resolve() if getattr(sys, "frozen", False) else None
    return current or config.app_dir(), config.download_staging_dir()


# ---------------------------------------------------------------------------
# Convenience used by the UI
# ---------------------------------------------------------------------------


def describe_update(info: UpdateInfo) -> str:
    """Human-readable summary shown inside the update dialog."""
    parts = [f"พบเวอร์ชันใหม่: {info.version}", f"เวอร์ชันปัจจุบัน: {config.CURRENT_VERSION}"]
    if info.notes:
        parts.append("")
        parts.append(info.notes)
    parts.append("")
    parts.append("โปรแกรมจะปิดและเปิดใหม่ให้อัตโนมัติ")
    return "\n".join(parts)


def clear_staging() -> None:
    """Remove any leftover partial downloads."""
    staging = config.download_staging_dir()
    try:
        for item in staging.iterdir():
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
    except OSError:
        pass


def temp_script_path() -> Path:
    """Location for the throwaway updater batch file."""
    return Path(tempfile.gettempdir()) / f"{config.APP_NAME}_updater.bat"