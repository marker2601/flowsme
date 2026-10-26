"""FlowSME Setup (FlowSME-Setup-<version>.exe): installs the app for the current Windows user, without administrator
rights. Program: %LOCALAPPDATA%\\Programs\\FlowSME. Recordings and settings: %LOCALAPPDATA%\\FlowSME, kept on upgrade.

FlowSME-Setup.exe /S installs silently (no window, FlowSME is not started): exit code 0 when it worked."""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import winreg
import zipfile
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk

HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
VERSION = (HERE / "version.txt").read_text(encoding="ascii").strip()
DEST = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "FlowSME"
DATA = Path(os.environ["LOCALAPPDATA"]) / "FlowSME"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\FlowSME"
NO_WINDOW = 0x08000000

SHORTCUTS = r"""
$shell = New-Object -ComObject WScript.Shell
$folders = @([Environment]::GetFolderPath('Programs'))
if ($env:FLOWSME_DESKTOP -eq '1') { $folders += [Environment]::GetFolderPath('Desktop') }
foreach ($folder in $folders) {
    $link = $shell.CreateShortcut((Join-Path $folder 'FlowSME.lnk'))
    $link.TargetPath = $env:FLOWSME_EXE
    $link.WorkingDirectory = $env:FLOWSME_DIR
    $link.IconLocation = $env:FLOWSME_EXE + ',0'
    $link.Description = 'Record your work and find what to automate'
    $link.Save()
}
"""


def flowsme_running() -> bool:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq FlowSME.exe", "/NH"], capture_output=True, text=True,
                         creationflags=NO_WINDOW).stdout
    return "FlowSME.exe" in out


def install(desktop: bool, progress=lambda done, total: None) -> Path:
    if flowsme_running():
        raise RuntimeError("FlowSME is open. Close it (stop any recording first), then run Setup again.")
    if DEST.exists():
        shutil.rmtree(DEST)  # program files only: recordings live in DATA
    DEST.mkdir(parents=True)
    with zipfile.ZipFile(HERE / "FlowSME.zip") as archive:
        members = archive.infolist()
        for n, member in enumerate(members, 1):
            archive.extract(member, DEST)
            if n % 25 == 0 or n == len(members):
                progress(n, len(members))
    shutil.copy(HERE / "uninstall.ps1", DEST)
    exe = DEST / "FlowSME.exe"
    subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", SHORTCUTS],
                   env={**os.environ, "FLOWSME_EXE": str(exe), "FLOWSME_DIR": str(DEST),
                        "FLOWSME_DESKTOP": "1" if desktop else "0"},
                   check=True, capture_output=True, creationflags=NO_WINDOW)
    size_kb = sum(p.stat().st_size for p in DEST.rglob("*") if p.is_file()) // 1024
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:  # listed in Settings > Apps
        for name, value in (("DisplayName", "FlowSME"), ("DisplayVersion", VERSION), ("Publisher", "FlowSME"),
                            ("DisplayIcon", str(exe)), ("InstallLocation", str(DEST)),
                            ("UninstallString", "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle "
                                                f'Hidden -File "{DEST / "uninstall.ps1"}"'),
                            ("QuietUninstallString", "powershell.exe -NoProfile -ExecutionPolicy Bypass -File "
                                                     f'"{DEST / "uninstall.ps1"}" /quiet')):
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        for name, value in (("EstimatedSize", size_kb), ("NoModify", 1), ("NoRepair", 1)):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)
    return exe


class SetupWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.exe: Path | None = None
        root.title("FlowSME Setup")
        root.resizable(False, False)
        frame = ttk.Frame(root, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"Install FlowSME {VERSION}", style="Title.TLabel").pack(anchor="w")
        self.text = ttk.Label(frame, wraplength=int(430 * self.scale), justify="left", text=(
            "FlowSME records how you work, maps the process and shows what to automate and the minutes saved.\n\n"
            f"It installs for your Windows user only, no administrator rights needed, in:\n{DEST}\n\n"
            f"Your recordings and settings are kept in {DATA}."))
        self.text.pack(anchor="w", pady=(10, 12))
        self.desktop = tk.BooleanVar(value=True)
        self.check = ttk.Checkbutton(frame, text="Add a desktop shortcut", variable=self.desktop)
        self.check.pack(anchor="w")
        self.bar = ttk.Progressbar(frame, mode="determinate", maximum=100)
        self.bar.pack(fill="x", pady=(14, 0))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(16, 0))
        self.close_btn = ttk.Button(buttons, text="Cancel", command=root.destroy)
        self.close_btn.pack(side="right")
        self.main_btn = ttk.Button(buttons, text="Install", style="Accent.TButton", command=self.start)
        self.main_btn.pack(side="right", padx=(0, 8))
        root.bind("<Return>", lambda _: self.main_btn.invoke())

    @property
    def scale(self) -> float:
        return self.root.winfo_fpixels("1i") / 96

    def start(self) -> None:
        self.main_btn.state(["disabled"])
        self.close_btn.state(["disabled"])
        self.check.state(["disabled"])
        self.text.config(text="Installing...")
        self.state = {"value": 0.0, "result": None}  # written by the install thread, read here
        threading.Thread(target=self._install, args=(self.desktop.get(),), daemon=True).start()
        self._poll()

    def _install(self, desktop: bool) -> None:
        def progress(done: int, total: int) -> None:
            self.state["value"] = 100 * done / total

        try:
            self.state["result"] = (install(desktop, progress), None)
        except Exception as exc:  # noqa: BLE001 - show any failure to the person installing
            self.state["result"] = (None, exc)

    def _poll(self) -> None:  # Tk may only be touched from this thread
        self.bar.config(value=self.state["value"])
        if self.state["result"] is None:
            self.root.after(100, self._poll)
        else:
            self._done(*self.state["result"])

    def _done(self, exe: Path | None, error: Exception | None) -> None:
        self.close_btn.state(["!disabled"])
        self.close_btn.config(text="Close")
        if error:
            self.text.config(text=f"FlowSME could not be installed:\n\n{error}")
            self.main_btn.config(text="Try again")
            self.main_btn.state(["!disabled"])
            self.check.state(["!disabled"])
            return
        self.exe = exe
        self.bar.config(value=100)
        self.text.config(text="FlowSME is installed. Find it in the Start menu"
                              + (" and on your desktop." if self.desktop.get() else ".")
                              + "\n\nTo remove it later: Settings > Apps > FlowSME.")
        self.main_btn.config(text="Open FlowSME", command=self.launch)
        self.main_btn.state(["!disabled"])

    def launch(self) -> None:
        if self.exe:
            subprocess.Popen([str(self.exe)], cwd=str(DEST))
        self.root.destroy()


def main() -> None:
    if "/S" in sys.argv[1:] or "/s" in sys.argv[1:]:
        try:
            install(desktop="/nodesktop" not in sys.argv)
        except Exception:  # noqa: BLE001 - silent mode: the exit code is the answer
            sys.exit(1)
        sys.exit(0)
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    root = tk.Tk()
    if (HERE / "flowsme.ico").exists():
        root.iconbitmap(default=str(HERE / "flowsme.ico"))
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    for name in ("TkDefaultFont", "TkTextFont"):
        tkfont.nametofont(name).configure(family="Segoe UI", size=10)
    style.configure("Title.TLabel", font=("Segoe UI", 15, "bold"))
    style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"), padding=(12, 4))
    SetupWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
