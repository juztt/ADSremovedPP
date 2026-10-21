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

3. **Database publishing** — push the shared blacklist back to GitHub through
   the Contents API so one technician's find benefits every other install.
   :func:`push_blacklist_to_github` is the only function that writes.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from base64 import b64decode, b64encode
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


# ---------------------------------------------------------------------------
# Database publishing (GitHub Contents API)
# ---------------------------------------------------------------------------


@dataclass
class PushOutcome:
    """Result of publishing the blacklist back to GitHub."""

    ok: bool
    message: str
    commit_url: str = ""

    def __bool__(self) -> bool:
        return self.ok


#: Where a PAT can be stashed once the technician has typed it in.
def github_token_file() -> Path:
    """Per-user file holding the Personal Access Token."""
    return config.user_data_dir() / "github_token.txt"


def save_github_token(token: str) -> bool:
    """Persist a PAT to the per-user data directory.

    The file lives under ``%APPDATA%\\ADSremovedPP`` rather than in the repo, so
    it is never bundled into an executable and never committed by accident.
    """
    cleaned = str(token or "").strip()
    if not cleaned:
        return False
    try:
        path = github_token_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(cleaned, encoding="utf-8")
        if sys.platform == "win32":
            import subprocess as _sp

            _sp.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{_current_user()}:F"],
                capture_output=True,
                creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0),
            )
        return True
    except OSError:
        return False


def load_github_token() -> str:
    """Read a previously stored PAT, if any."""
    try:
        return github_token_file().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def clear_github_token() -> None:
    """Forget the stored PAT."""
    try:
        github_token_file().unlink()
    except OSError:
        pass


def _current_user() -> str:
    """Best-effort current user name for the ``icacls`` grant."""
    try:
        import getpass

        return getpass.getuser()
    except Exception:  # noqa: BLE001 - purely cosmetic
        return "*"


def _contents_url(path: str) -> str:
    return (
        f"https://api.github.com/repos/{config.GITHUB_OWNER}/"
        f"{config.GITHUB_REPO}/contents/{path}"
    )


def push_blacklist_to_github(
    rows: list[dict],
    token: str | None = None,
    path: str = "local_db.json",
    branch: str | None = None,
    message: str | None = None,
    timeout: int = config.NETWORK_TIMEOUT,
) -> PushOutcome:
    """Commit ``rows`` to the repository's ``path``.

    Uses the GitHub Contents API, which is the simplest write path available
    without standing up a server:

    1. ``GET`` the current file to learn its blob SHA (and catch a 404, meaning
       the file does not exist yet and must be created instead of replaced).
    2. ``PUT`` the new content together with that SHA — GitHub rejects the
       write if the SHA moved in the meantime, which prevents one technician
       silently overwriting another's edit.

    Returns a :class:`PushOutcome`; never raises, because a failed publish is a
    UI message rather than a crash.
    """
    resolved_token = str(token or load_github_token() or "").strip()
    if not resolved_token:
        return PushOutcome(
            False,
            "ยังไม่ได้ตั้งค่า Personal Access Token — ต้องมีสิทธิ์ repo",
        )

    branch_name = branch or config.GITHUB_BRANCH
    headers = {
        "Authorization": f"Bearer {resolved_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"{config.APP_NAME}/{config.CURRENT_VERSION}",
    }

    body_text = json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
    payload = {
        "message": message or f"อัปเดตรายการต้องระวัง ({len(rows)} รายการ) จาก ADSremovedPP",
        "content": b64encode(body_text.encode("utf-8")).decode("ascii"),
        "branch": branch_name,
    }

    try:
        current = requests.get(_contents_url(path), headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        return PushOutcome(False, f"เชื่อมต่อ GitHub ไม่สำเร็จ: {exc.__class__.__name__}")

    if current.status_code == 200:
        try:
            payload["sha"] = current.json()["sha"]
        except (ValueError, KeyError):
            return PushOutcome(False, "อ่านสถานะไฟล์บน GitHub ไม่สำเร็จ")
        method = "PUT"
    elif current.status_code == 404:
        method = "PUT"  # create; no sha required
    elif current.status_code in {401, 403}:
        detail = ""
        try:
            detail = current.json().get("message", "")
        except ValueError:
            pass
        hint = (
            "Token ไม่มีสิทธิ์เขียน (ต้องเลือก scope 'repo')"
            if "sscope" in detail.lower() or "permission" in detail.lower()
            else detail or "สิทธิ์ไม่เพียงพอ"
        )
        return PushOutcome(False, f"GitHub ไม่อนุญาต: {hint}")
    else:
        return PushOutcome(False, f"GitHub ตอบกลับผิดปกติ ({current.status_code})")

    try:
        response = requests.put(
            _contents_url(path),
            headers=headers,
            data=json.dumps(payload).encode("utf-8"),
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return PushOutcome(False, f"ส่งไฟล์ไม่สำเร็จ: {exc.__class__.__name__}")

    if response.status_code in {200, 201}:
        commit_url = ""
        try:
            commit_url = response.json().get("content", {}).get("html_url", "")
        except ValueError:
            pass
        return PushOutcome(
            True,
            f"ส่งขึ้น GitHub สำเร็จ — {len(rows)} รายการ (ทุกเครื่องจะได้รับในการซิงก์ครั้งถัดไป)",
            commit_url,
        )

    detail = ""
    try:
        body = response.json()
        detail = str(body.get("message", ""))
        if isinstance(body.get("errors"), list) and body["errors"]:
            detail += " — " + "; ".join(str(item.get("message", item)) for item in body["errors"])
    except ValueError:
        detail = response.text[:200]

    if "sha" in detail.lower():
        return PushOutcome(
            False,
            "มีคนแก้ไฟล์นี้พร้อมกัน — กด «โหลดใหม่» แล้วลองอีกครั้ง",
        )
    return PushOutcome(False, f"ส่งไม่สำเร็จ ({response.status_code}): {detail or 'ไม่ทราบสาเหตุ'}")


def fetch_remote_blacklist(timeout: int = config.NETWORK_TIMEOUT) -> tuple[list[dict], str]:
    """Fetch the shared database straight from GitHub's raw endpoint."""
    return load_blacklist(timeout=timeout)


def github_repo_status(timeout: int = config.NETWORK_TIMEOUT) -> tuple[bool, str]:
    """Quick reachability probe for the configured repository."""
    url = f"https://api.github.com/repos/{config.GITHUB_OWNER}/{config.GITHUB_REPO}"
    try:
        response = requests.get(url, timeout=timeout)
    except requests.RequestException as exc:
        return False, f"เชื่อมต่อไม่ได้ ({exc.__class__.__name__})"
    if response.status_code == 200:
        return True, "เชื่อมต่อ GitHub ได้"
    if response.status_code == 404:
        return False, "ไม่พบ repository — ตรวจชื่อผู้ใช้และชื่อ repo"
    return False, f"ตอบกลับ ({response.status_code})"


def describe_push_failure(outcome: PushOutcome) -> str:
    """Turn a failed push into something a technician can act on."""
    if outcome.ok:
        return outcome.message
    lowered = outcome.message.lower()
    if "token" in lowered:
        return (
            f"{outcome.message}\n\n"
            "สร้างได้ที่ github.com/settings/tokens\n"
            "เลือก 'Generate new token (classic)' แล้วติ๊ก scope 'repo'"
        )
    if "โหลดใหม่" in outcome.message:
        return (
            f"{outcome.message}\n\n"
            "เปิดหน้าฐานข้อมูล → กด «↻ โหลดใหม่» แล้วทำรายการของคุณใหม่อีกครั้ง"
        )
    return outcome.message


#: Kept for the auto-updater flow, which waits before swapping the binary.
def wait_for_process_exit(pid: int, timeout: float = 10.0, interval: float = 0.25) -> bool:
    """Poll until ``pid`` disappears or ``timeout`` elapses."""
    import os

    deadline = time.monotonic() + max(timeout, 0.0)
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(max(interval, 0.05))
    return not _pid_alive(pid)


def _pid_alive(pid: int) -> bool:
    import os

    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    except Exception:  # noqa: BLE001 - Windows raises odd variants
        return True
    return True