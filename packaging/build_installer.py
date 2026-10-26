"""Build the Windows app and its installer:

    .venv/Scripts/python.exe packaging/build_installer.py

Writes dist/FlowSME/ (the app) and dist/FlowSME-Setup-<version>.exe, a single-file installer that installs it for
the current user, without administrator rights. Both are made with PyInstaller: uv pip install pyinstaller."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from flowsme import __version__  # noqa: E402

BUILD, DIST = ROOT / "build", ROOT / "dist"
ICON = ROOT / "flowsme" / "assets" / "flowsme.ico"


def build_app() -> Path:
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", "FlowSME",
        "--icon", str(ICON), "--add-data", f"{ICON}{os.pathsep}.",
        # Loaded at run time, so PyInstaller can't see them: input hooks, UI Automation, search engines.
        "--collect-submodules", "pynput", "--collect-all", "uiautomation", "--collect-submodules", "ddgs",
        "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"), "--specpath", str(BUILD),
        str(ROOT / "packaging" / "flowsme_app.py"),
    ], check=True, cwd=ROOT)
    return DIST / "FlowSME"


def build_setup(app_dir: Path) -> Path:
    """FlowSME-Setup-<version>.exe: one file holding the app (zipped) and a small installer window."""
    stage = BUILD / "setup"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    shutil.make_archive(str(stage / "FlowSME"), "zip", root_dir=app_dir)
    (stage / "version.txt").write_text(__version__, encoding="ascii")
    data = [stage / "FlowSME.zip", stage / "version.txt", ROOT / "packaging" / "uninstall.ps1", ICON]
    name = f"FlowSME-Setup-{__version__}"
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed", "--name", name,
        "--icon", str(ICON), *[arg for f in data for arg in ("--add-data", f"{f}{os.pathsep}.")],
        "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller-setup"), "--specpath", str(BUILD),
        str(ROOT / "packaging" / "setup_app.py"),
    ], check=True, cwd=ROOT)
    return DIST / f"{name}.exe"


if __name__ == "__main__":
    app = build_app()
    setup = build_setup(app)
    print(f"App: {app}\nInstaller: {setup} ({setup.stat().st_size / 1e6:.0f} MB)")
