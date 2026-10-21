"""Ad-hoc check of the AXML/ARSC label parser against a real device APK.

Pulls three APKs with different packaging styles, then prints the launcher
label the parser recovered for each one. Run from the project root:

    python tests/label_probe.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.apk_meta import extract_icon_bytes, read_app_label  # noqa: E402

ADB = Path(__file__).resolve().parent.parent / "bin" / "adb.exe"

#: package → the APK path ``pm list packages -f`` reports for it
TARGETS = [
    "com.opera.browser",
    "com.grab.merchant",
    "com.wongnai.rms",
]


def apk_paths() -> dict[str, str]:
    out = subprocess.run(
        [str(ADB), "shell", "pm", "list", "packages", "-f", "-3"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    found: dict[str, str] = {}
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("package:"):
            continue
        body = line[len("package:"):]
        path, _, package = body.rpartition("=")
        if package and package not in found:
            found[package] = path.strip()
    return found


def main() -> int:
    if not ADB.exists():
        print("adb.exe not found — skipped")
        return 0

    paths = apk_paths()
    work = Path(tempfile.gettempdir()) / "adsremovedpp_label_probe"
    work.mkdir(parents=True, exist_ok=True)

    failures = 0
    for package in TARGETS:
        remote = paths.get(package)
        if not remote:
            print(f"{package}: not installed — skipped")
            continue
        local = work / f"{package}.apk"
        subprocess.run(
            [str(ADB), "pull", remote, str(local)],
            capture_output=True,
            text=True,
        )
        if not local.exists():
            print(f"{package}: pull failed — skipped")
            continue

        label = read_app_label(local)
        icon = extract_icon_bytes(local)
        status = "OK " if label else "!! "
        if not label:
            failures += 1
        print(f"{status}{package}: label={label!r}  icon={len(icon) if icon else 0} bytes")

    print()
    print("parser OK" if failures == 0 else f"{failures} package(s) had no label")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
