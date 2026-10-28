"""Build the Windows apps and their installers:

    .venv/Scripts/python.exe packaging/build_installer.py            the full FlowSME (for you)
    .venv/Scripts/python.exe packaging/build_installer.py --client   FlowSME Recorder (for customers)

Writes dist/<app>/ and dist/<app>-Setup-<version>.exe, a single-file installer that installs it for the current
user, without administrator rights. Both are made with PyInstaller: uv pip install pyinstaller.

The customers' app is checked after the build: if any FlowSME module beyond the recorder, the client and the
connectors (sending a finished workflow to the customer's tool) is in it, the build fails. The analysis, prompts,
knowledge base and automation builders must only ever be on the server."""
from __future__ import annotations

import json
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
MARKED = ROOT / "flowsme" / "assets" / "marked.min.js"
# Loaded at run time, so PyInstaller can't see them: input hooks and UI Automation.
RECORDER_HIDDEN = ["--collect-submodules", "pynput", "--collect-all", "uiautomation", "--collect-all", "sv_ttk"]
CLIENT_MODULES = {"flowsme", "flowsme.config", "flowsme.recorder", "flowsme.session", "flowsme.connect",
                  "flowsme.client", "flowsme.client.app", "flowsme.client.admin", "flowsme.client.account",
                  "flowsme.client.cloud"}
RECORDER_PRODUCT = {"name": "FlowSME Recorder", "exe": "FlowSMERecorder.exe", "folder": "FlowSME Recorder",
                    "data": "FlowSME Client",
                    "about": "FlowSME Recorder records how you work. Your team's FlowSME service turns the recording "
                             "into the process map, what to automate and the minutes saved."}


def server_only_modules() -> list[str]:
    """Every FlowSME module the customers' app must not contain."""
    found = []
    for path in (ROOT / "flowsme").rglob("*.py"):
        parts = path.relative_to(ROOT).with_suffix("").parts
        name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        if name not in CLIENT_MODULES and not name.startswith("flowsme.client"):
            found.append(name)
    return sorted(found)


def build_app() -> Path:
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", "FlowSME",
        "--icon", str(ICON), "--add-data", f"{ICON}{os.pathsep}.", *RECORDER_HIDDEN, "--collect-submodules", "ddgs",
        "--add-data", f"{MARKED}{os.pathsep}flowsme/assets",  # the report pages' Markdown renderer
        "--exclude-module", "flowsme.server", "--exclude-module", "flowsme.client",
        "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"), "--specpath", str(BUILD),
        str(ROOT / "packaging" / "flowsme_app.py"),
    ], check=True, cwd=ROOT)
    return DIST / "FlowSME"


def build_client() -> Path:
    excludes = [a for m in server_only_modules() + ["fastapi", "uvicorn", "starlette", "openai", "ddgs", "primp"]
                for a in ("--exclude-module", m)]
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", "FlowSMERecorder",
        "--icon", str(ICON), "--add-data", f"{ICON}{os.pathsep}.", *RECORDER_HIDDEN, *excludes,
        "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller-client"), "--specpath", str(BUILD),
        str(ROOT / "packaging" / "flowsme_client.py"),
    ], check=True, cwd=ROOT)
    app = DIST / "FlowSMERecorder"
    check_client(app / "FlowSMERecorder.exe")
    return app


def check_client(exe: Path) -> None:
    """Fail the build if the customers' app holds any FlowSME code beyond the recorder and the client."""
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(exe))
    pyz = archive.open_embedded_archive(next(n for n in archive.toc if n.endswith(".pyz") or "PYZ" in n))
    bundled = {n for n in pyz.toc if n == "flowsme" or n.startswith("flowsme.")}
    extra = bundled - CLIENT_MODULES
    if extra:
        raise SystemExit(f"The customers' app contains server code: {', '.join(sorted(extra))}")
    print(f"Checked: the customers' app contains only {', '.join(sorted(bundled))}")


def build_setup(app_dir: Path, name: str, product: dict | None = None) -> Path:
    """<name>-<version>.exe: one file holding the app (zipped) and a small installer window."""
    stage = BUILD / f"setup-{name}"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    shutil.make_archive(str(stage / "FlowSME"), "zip", root_dir=app_dir)
    (stage / "version.txt").write_text(__version__, encoding="ascii")
    data = [stage / "FlowSME.zip", stage / "version.txt", ROOT / "packaging" / "uninstall.ps1", ICON]
    if product:
        (stage / "product.json").write_text(json.dumps(product), encoding="utf-8")
        data.append(stage / "product.json")
    setup = f"{name}-{__version__}"
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed", "--name", setup,
        "--icon", str(ICON), *[arg for f in data for arg in ("--add-data", f"{f}{os.pathsep}.")],
        "--distpath", str(DIST), "--workpath", str(BUILD / f"pyinstaller-{name}"), "--specpath", str(BUILD),
        str(ROOT / "packaging" / "setup_app.py"),
    ], check=True, cwd=ROOT)
    return DIST / f"{setup}.exe"


if __name__ == "__main__":
    if "--client" in sys.argv:
        app = build_client()
        setup = build_setup(app, "FlowSME-Recorder-Setup", RECORDER_PRODUCT)
    else:
        app = build_app()
        setup = build_setup(app, "FlowSME-Setup")
    print(f"App: {app}\nInstaller: {setup} ({setup.stat().st_size / 1e6:.0f} MB)")
