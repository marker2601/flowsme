"""The app's Settings window: AI, privacy, recording and business settings in tabs, saved to the settings file
(config.toml), so nobody has to edit it by hand. The API key is kept in the user's environment, not the file."""
from __future__ import annotations

import os
import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import org
from .config import save_config

# What choosing an AI option sets. Anything the person then edits on the tab wins.
PRESETS = {
    "meta": {"provider": "openai_compatible", "base_url": "https://api.meta.ai/v1", "api_key_env": "MODEL_API_KEY",
             "vision_model": "muse-spark-1.3", "text_model": "muse-spark-1.3", "reasoning_effort": "medium",
             "vision_reasoning_effort": "low", "min_output_tokens": 4096, "temperature": 1.0, "grounded_search": True},
    "local": {"provider": "openai_compatible", "base_url": "http://localhost:11434/v1", "api_key_env": "LLM_API_KEY",
              "vision_model": "qwen3.5:4b", "text_model": "llama3.1:8b", "reasoning_effort": "none",
              "vision_reasoning_effort": "", "min_output_tokens": 0, "temperature": 0.2, "grounded_search": False},
    "off": {"provider": "offline"},
}
HOTKEY = re.compile(r"^(ctrl|alt|shift|win)(\+(ctrl|alt|shift|win))*\+[a-z0-9]$", re.I)


def _mode(cfg) -> str:
    if cfg.llm.provider == "offline":
        return "off"
    return "local" if any(h in cfg.llm.base_url for h in ("localhost", "127.0.0.1")) else "meta"


def _microphones() -> list[str]:
    try:
        import sounddevice as sd

        names = [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]
        return list(dict.fromkeys(names))
    except Exception:  # noqa: BLE001 - no audio system: only the default
        return []


def open_settings(app) -> None:
    from .app import app_config, engine_text, saved_key, store_key

    cfg, cfg_path = app_config(app.config_path)
    win, f = app._dialog("FlowSME settings")
    tabs = ttk.Notebook(f)
    tabs.grid(row=0, column=0, sticky="nsew")
    pad = {"padx": 4, "pady": 3}

    # ---- AI ------------------------------------------------------------------------------------------
    ai = ttk.Frame(tabs, padding=12)
    tabs.add(ai, text="AI")
    mode = tk.StringVar(value=_mode(cfg))
    for n, (value, text) in enumerate((("meta", "Meta Muse Spark (Meta Model API): best results"),
                                       ("local", "Local model with Ollama: free, nothing leaves this computer"),
                                       ("off", "Built-in rules only: no AI"))):
        ttk.Radiobutton(ai, text=text, value=value, variable=mode).grid(row=n, column=0, columnspan=3, sticky="w")
    llm = cfg.llm
    text_model, vision_model = tk.StringVar(value=llm.text_model), tk.StringVar(value=llm.vision_model)
    base_url, cap = tk.StringVar(value=llm.base_url), tk.StringVar(value=str(llm.max_cost_usd))
    search = tk.BooleanVar(value=llm.grounded_search)
    rows = [("Model for text", text_model), ("Model for screenshots", vision_model), ("Address", base_url),
            ("Spending limit per report ($, 0 = none)", cap)]
    fields = []
    for n, (label, var) in enumerate(rows, 4):
        ttk.Label(ai, text=label).grid(row=n, column=0, sticky="w", **pad)
        entry = ttk.Entry(ai, textvariable=var, width=34)
        entry.grid(row=n, column=1, sticky="w", **pad)
        fields.append(entry)
    search_box = ttk.Checkbutton(ai, text="Check vendor APIs and tools with the model's own web search (cited)",
                                 variable=search)
    search_box.grid(row=8, column=0, columnspan=3, sticky="w", pady=(4, 0))
    ttk.Separator(ai).grid(row=9, column=0, columnspan=3, sticky="we", pady=10)
    key_env = tk.StringVar(value=llm.api_key_env)
    key_label = ttk.Label(ai, style="Heading.TLabel")
    key_label.grid(row=10, column=0, columnspan=3, sticky="w")
    key_status = ttk.Label(ai, style="Muted.TLabel")
    key_status.grid(row=11, column=0, columnspan=3, sticky="w")
    key = tk.StringVar()
    key_box = ttk.Entry(ai, textvariable=key, show="•", width=34)
    key_box.grid(row=12, column=0, columnspan=2, sticky="w", pady=6)
    keys = ttk.Frame(ai)
    keys.grid(row=12, column=1, sticky="e")

    def show_key() -> None:
        env = key_env.get()
        key_label.config(text="Meta API key" if mode.get() == "meta" else "API key (if your endpoint needs one)")
        key_status.config(text=f"Saved for your Windows user as {env}. "
                               + ("A key is saved." if saved_key(env) else "No key saved yet."))

    def save_key() -> None:
        if key.get().strip():
            store_key(key_env.get(), key.get().strip())
            key.set("")
            show_key()

    def remove_key() -> None:
        if saved_key(key_env.get()) and messagebox.askyesno("FlowSME", "Remove the saved API key?", parent=win):
            store_key(key_env.get(), "")
            show_key()

    ttk.Button(keys, text="Save key", command=save_key).pack(side="left", padx=(6, 0))
    ttk.Button(keys, text="Remove", command=remove_key).pack(side="left", padx=(6, 0))

    def follow_mode(*_) -> None:
        preset = PRESETS[mode.get()]
        if mode.get() != "off" and mode.get() != _mode(cfg):
            text_model.set(preset["text_model"])
            vision_model.set(preset["vision_model"])
            base_url.set(preset["base_url"])
            key_env.set(preset["api_key_env"])
            search.set(preset["grounded_search"])
        on = mode.get() != "off"
        for widget in (*fields, search_box, key_box):
            widget.state(["!disabled"] if on else ["disabled"])
        show_key()
    mode.trace_add("write", follow_mode)
    follow_mode()

    # ---- privacy -------------------------------------------------------------------------------------
    privacy = ttk.Frame(tabs, padding=12)
    tabs.add(privacy, text="Privacy")
    rec = cfg.recorder
    typed, prints = tk.BooleanVar(value=rec.capture_typed_text), tk.BooleanVar(value=rec.fingerprint_values)
    shots, crop = tk.BooleanVar(value=llm.send_screenshots), tk.BooleanVar(value=llm.crop_screenshots)
    for n, (var, text) in enumerate((
            (typed, "Store the text people type (off is recommended: only the number of characters is kept)"),
            (prints, "Fingerprint copied and typed values to prove where they came from (values are never stored)"),
            (shots, "Send screenshots to the AI model (off: steps are described from button and field names)"),
            (crop, "Send only the window being worked in, not the whole screen"))):
        ttk.Checkbutton(privacy, text=text, variable=var).grid(row=n, column=0, columnspan=2, sticky="w", pady=2)
    ttk.Label(privacy, text="Never record these apps (one per line, e.g. keepass.exe)").grid(
        row=4, column=0, sticky="w", pady=(10, 2))
    ttk.Label(privacy, text="...or windows whose title contains (one per line)").grid(
        row=4, column=1, sticky="w", pady=(10, 2), padx=(12, 0))
    apps_box = tk.Text(privacy, width=32, height=8, font=("Segoe UI", 9))
    words_box = tk.Text(privacy, width=32, height=8, font=("Segoe UI", 9))
    apps_box.grid(row=5, column=0, sticky="w")
    words_box.grid(row=5, column=1, sticky="w", padx=(12, 0))
    apps_box.insert("1.0", "\n".join(rec.excluded_apps))
    words_box.insert("1.0", "\n".join(rec.excluded_title_keywords))

    # ---- recording -----------------------------------------------------------------------------------
    recording = ttk.Frame(tabs, padding=12)
    tabs.add(recording, text="Recording")
    mics = ["Windows default"] + _microphones()
    mic = tk.StringVar(value=rec.mic_device or "Windows default")
    pause_key, stop_key = tk.StringVar(value=rec.pause_hotkey), tk.StringVar(value=rec.stop_hotkey)
    width, quality = tk.StringVar(value=str(rec.screenshot_max_width)), tk.StringVar(value=str(rec.screenshot_quality))
    gap = tk.StringVar(value=str(cfg.analysis.idle_gap_seconds))
    ttk.Label(recording, text="Microphone for narration").grid(row=0, column=0, sticky="w", **pad)
    ttk.Combobox(recording, textvariable=mic, values=mics, width=40, state="readonly").grid(
        row=0, column=1, sticky="w", **pad)
    for n, (label, var) in enumerate((("Pause / resume shortcut", pause_key), ("Stop shortcut", stop_key),
                                      ("Screenshot width (pixels)", width), ("Screenshot quality (1-95)", quality),
                                      ("A pause longer than this starts a new task (seconds)", gap)), 1):
        ttk.Label(recording, text=label).grid(row=n, column=0, sticky="w", **pad)
        ttk.Entry(recording, textvariable=var, width=16).grid(row=n, column=1, sticky="w", **pad)
    ttk.Label(recording, style="Muted.TLabel", text="Shortcuts like ctrl+alt+p. New settings apply to the next "
                                                   "recording.").grid(row=6, column=0, columnspan=2, sticky="w",
                                                                      pady=(8, 0))

    # ---- business ------------------------------------------------------------------------------------
    business = ttk.Frame(tabs, padding=12)
    tabs.add(business, text="Business")
    an = cfg.analysis
    runs = tk.StringVar(value=str(an.runs_per_month))
    tools = tk.StringVar(value="; ".join(cfg.customer.tools))
    folder = tk.StringVar(value=str(cfg.sessions_dir))
    for n, (label, var, w) in enumerate((("Default: times a process runs a month", runs, 10),
                                         ("Tools the business already has (separate with ;)", tools, 44))):
        ttk.Label(business, text=label).grid(row=2 * n, column=0, sticky="w", **pad)
        ttk.Entry(business, textvariable=var, width=w).grid(row=2 * n, column=1, sticky="w", **pad)
    seat = org.my_seat(app.home)
    team = None if seat else org.Org(app.home)
    position = tk.StringVar(value=seat["position"] if seat else team.default_position)
    ttk.Label(business, text="My position").grid(row=1, column=0, sticky="w", **pad)
    if seat:
        ttk.Label(business, text=f"{seat['position']} (set by your manager, {seat['team_name']})").grid(
            row=1, column=1, sticky="w", **pad)
    else:
        place = ttk.Frame(business)
        place.grid(row=1, column=1, sticky="w", **pad)
        ttk.Combobox(place, textvariable=position, values=team.position_names(), state="readonly", width=28).pack(
            side="left")
        ttk.Button(place, text="Positions and rates...", command=lambda: (win.destroy(), app.admin("positions"))
                   ).pack(side="left", padx=6)
    ttk.Label(business, style="Muted.TLabel", text="Each report can still change these in its Report options.").grid(
        row=4, column=0, columnspan=2, sticky="w")
    ttk.Separator(business).grid(row=5, column=0, columnspan=2, sticky="we", pady=10)
    ttk.Label(business, text="Recordings folder").grid(row=6, column=0, sticky="w", **pad)
    place = ttk.Frame(business)
    place.grid(row=6, column=1, sticky="w")
    ttk.Entry(place, textvariable=folder, width=34).pack(side="left")
    ttk.Button(place, text="Change...", command=lambda: folder.set(
        filedialog.askdirectory(parent=win, initialdir=folder.get()) or folder.get())).pack(side="left", padx=6)
    ttk.Button(place, text="Open", command=lambda: (Path(folder.get()).mkdir(parents=True, exist_ok=True),
                                                   os.startfile(folder.get()))).pack(side="left")

    def save() -> None:
        try:
            numbers = {"cap": float(cap.get() or 0), "runs": int(float(runs.get())),
                       "width": int(width.get()), "quality": int(quality.get()), "gap": float(gap.get())}
            if numbers["cap"] < 0 or numbers["runs"] < 1 or not 1 <= numbers["quality"] <= 95 \
                    or numbers["width"] < 320 or numbers["gap"] < 5:
                raise ValueError
        except ValueError:
            messagebox.showerror("FlowSME", "Check the numbers: spending limit 0 or more, runs 1 or more, screenshot "
                                            "width 320 or more, quality 1-95, pause 5 seconds or more.", parent=win)
            return
        for hotkey in (pause_key.get(), stop_key.get()):
            if not HOTKEY.match(hotkey.strip()):
                messagebox.showerror("FlowSME", f"'{hotkey}' isn't a shortcut. Use the form ctrl+alt+p.", parent=win)
                return
        if pause_key.get().strip().lower() == stop_key.get().strip().lower():
            messagebox.showerror("FlowSME", "Pause and stop need different shortcuts.", parent=win)
            return
        preset = PRESETS[mode.get()]
        if mode.get() != _mode(cfg):
            for name, value in preset.items():
                setattr(cfg.llm, name, value)
        if mode.get() != "off":
            cfg.llm.provider = "openai_compatible"
            cfg.llm.text_model, cfg.llm.vision_model = text_model.get().strip(), vision_model.get().strip()
            cfg.llm.base_url, cfg.llm.api_key_env = base_url.get().strip(), key_env.get()
            cfg.llm.grounded_search = search.get()
        cfg.llm.max_cost_usd = numbers["cap"]
        cfg.llm.send_screenshots, cfg.llm.crop_screenshots = shots.get(), crop.get()
        r = cfg.recorder
        r.capture_typed_text, r.fingerprint_values = typed.get(), prints.get()
        r.excluded_apps = [x.strip() for x in apps_box.get("1.0", "end").splitlines() if x.strip()]
        r.excluded_title_keywords = [x.strip() for x in words_box.get("1.0", "end").splitlines() if x.strip()]
        r.mic_device = "" if mic.get() == "Windows default" else mic.get()
        r.pause_hotkey, r.stop_hotkey = pause_key.get().strip().lower(), stop_key.get().strip().lower()
        r.screenshot_max_width, r.screenshot_quality = numbers["width"], numbers["quality"]
        cfg.analysis.idle_gap_seconds = numbers["gap"]
        cfg.analysis.runs_per_month = numbers["runs"]
        if team and position.get() != team.default_position:
            team.data["default_position"] = position.get()
            team.save()
        cfg.customer.tools = [t.strip() for t in tools.get().split(";") if t.strip()]
        cfg.sessions_dir = Path(folder.get().strip() or cfg.sessions_dir)
        if typed.get() and not rec.capture_typed_text and not messagebox.askyesno(
                "FlowSME", "Storing typed text keeps everything people type, including personal data. Store it "
                           "anyway?", parent=win):
            return
        save_config(cfg, cfg_path)
        win.destroy()
        app.cfg, _ = app_config(app.config_path)
        app.engine.config(text=engine_text(app.cfg))
        app.refresh()
        print("Settings saved.")

    bar = ttk.Frame(f)
    bar.grid(row=1, column=0, sticky="e", pady=(12, 0))
    ttk.Button(bar, text="Save", style="Accent.TButton", command=save).pack(side="left")
    ttk.Button(bar, text="Cancel", command=win.destroy).pack(side="left", padx=(8, 0))
    win.bind("<Escape>", lambda _: win.destroy())
