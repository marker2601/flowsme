"""Settings: built-in defaults, overridden by config.toml."""
from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class RecorderConfig:
    screenshot_max_width: int = 1280
    screenshot_quality: int = 70
    min_screenshot_interval: float = 0.4
    # False = keystrokes are counted and shortcuts logged, but the text you type is never stored.
    capture_typed_text: bool = False
    # Store a salted one-way fingerprint of copied and typed values (never the values), so the analysis can
    # prove "the Vendor typed here is the Vendor copied there". The salt is never saved.
    fingerprint_values: bool = True
    # While one of these apps or titles is in front: no screenshot, no title, no text, no UI element.
    excluded_apps: list[str] = field(default_factory=lambda: [
        "1password.exe", "keepass.exe", "keepassxc.exe", "bitwarden.exe", "lastpass.exe",
        "credentialuibroker.exe", "consent.exe", "lockapp.exe", "pickerhost.exe",
    ])
    excluded_title_keywords: list[str] = field(default_factory=lambda: [
        "password", "sign in", "log in", "login", "inprivate", "incognito", "windows security",
    ])
    pause_hotkey: str = "ctrl+alt+p"
    stop_hotkey: str = "ctrl+alt+q"
    # Narration: record the microphone too, so you can explain what you do and why while you work.
    # Mono 16-bit at 16 kHz, the format Muse Voice Transcribe expects. Pausing records silence.
    narrate: bool = False
    audio_rate: int = 16000
    mic_device: str = ""  # microphone name (or part of it); empty = the Windows default


@dataclass
class LLMConfig:
    # "offline" (heuristics only) or "openai_compatible" (Ollama, Meta Llama API, Groq, OpenRouter...)
    provider: str = "offline"
    base_url: str = "http://localhost:11434/v1"
    api_key_env: str = "LLM_API_KEY"
    vision_model: str = "llama3.2-vision"
    text_model: str = "llama3.1:8b"
    send_screenshots: bool = True
    # Send only the window being worked in (recorded from this version on), not the whole screen: other
    # windows never reach the model, and smaller images cost less.
    crop_screenshots: bool = True
    max_vision_steps: int = 300
    temperature: float = 0.2
    timeout: float = 120.0
    # Sent as reasoning_effort when set; "none" stops 'thinking' models (Qwen, DeepSeek...) on Ollama
    # from spending the token budget on reasoning. Leave empty for Llama.
    reasoning_effort: str = ""
    # Screenshot model on a different endpoint than the text model (e.g. local vision + hosted text).
    # Empty = same as base_url / api_key_env.
    vision_base_url: str = ""
    vision_api_key_env: str = ""
    # Requests in flight at once, and consecutive screenshots sent together in one request
    # (1 = one screenshot per request, needed for models that accept a single image).
    concurrency: int = 4
    screenshots_per_call: int = 3
    vision_image_width: int = 1024
    # Reasoning effort for the screenshot model only (empty = same as reasoning_effort). Describing a
    # screenshot needs little thought; recommendations deserve more.
    vision_reasoning_effort: str = ""
    # Reasoning models count their thinking as output: never ask for fewer output tokens than this.
    min_output_tokens: int = 0
    # Use the model's own web search with citations (Meta Model API: Responses API web_search tool)
    # instead of DuckDuckGo, when the endpoint supports it.
    grounded_search: bool = False
    # Meta "contributor" models may use what you send for training: refused unless you opt in here.
    allow_contributor: bool = False
    # How much search context the model pulls in per web search (low / medium / high): low costs least.
    search_context_size: str = "low"
    search_reasoning_effort: str = ""  # empty = same as reasoning_effort
    # Web searches one grounded question may run (each is billed). 0 = the model decides.
    max_searches_per_call: int = 3
    # Stop paying AI calls once a single run's estimated cost reaches this many dollars (0 = no cap).
    max_cost_usd: float = 0.0
    transcribe_model: str = "muse-voice-transcribe-1.0"


@dataclass
class AnalysisConfig:
    idle_gap_seconds: float = 60.0
    runs_per_month: int = 20
    hourly_cost: float = 30.0
    currency: str = "$"
    # The position whose rate hourly_cost came from (the app's Admin panel keeps positions and rates), and
    # whether money appears at all: an employee's app shows hours only, the rates stay with the manager.
    position: str = ""
    show_money: bool = True
    show_ai_usage: bool = True  # the AI cost line in reports and logs (off on the FlowSME server)
    web_search: bool = True
    max_recommended_tasks: int = 5


@dataclass
class CustomerConfig:
    # Tools the customer already has: catalog names ("Microsoft 365", "Power BI", "n8n") or any other
    # application described by what it can do ("MyERP: database, reporting").
    tools: list[str] = field(default_factory=list)


@dataclass
class Config:
    sessions_dir: Path = Path("sessions")
    recorder: RecorderConfig = field(default_factory=RecorderConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    customer: CustomerConfig = field(default_factory=CustomerConfig)


def _apply(section_name: str, section, values: dict) -> None:
    known = {f.name for f in fields(section)}
    for key, value in values.items():
        if key not in known:
            raise SystemExit(f"Unknown setting '{key}' in [{section_name}] of config.toml")
        setattr(section, key, value)


def load_config(path: str | Path | None = None) -> Config:
    cfg = Config()
    if path is None:
        path = Path("config.toml")
        if not path.exists():
            return cfg
    path = Path(path)
    if not path.exists():
        raise SystemExit(f"Config file not found: {path}")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if "extends" in data:
        # Start from another config file (e.g. your privacy settings) and override only what this one sets.
        base = path.parent / data.pop("extends")
        cfg = load_config(base) if base.exists() else cfg
    if "sessions_dir" in data:
        cfg.sessions_dir = Path(data.pop("sessions_dir"))
    for name in ("recorder", "llm", "analysis", "customer"):
        if name in data:
            _apply(name, getattr(cfg, name), data.pop(name))
    if data:
        raise SystemExit(f"Unknown section(s) in config.toml: {', '.join(data)}")
    return cfg


def _toml(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)  # JSON string escapes are valid TOML basic strings


def save_config(cfg: Config, path: str | Path) -> None:
    """Write every setting to `path` as TOML (the app's Settings window saves through this)."""
    path = Path(path)
    sessions = Path(cfg.sessions_dir)
    try:  # keep the recordings folder relative to the settings file when it sits beside it
        sessions = sessions.resolve().relative_to(path.resolve().parent)
    except ValueError:
        pass
    lines = ["# FlowSME settings, saved by the app's Settings window. Every setting is listed here.",
             f"sessions_dir = {_toml(sessions.as_posix())}", ""]
    for name in ("recorder", "llm", "analysis", "customer"):
        section = getattr(cfg, name)
        lines.append(f"[{name}]")
        lines += [f"{f.name} = {_toml(getattr(section, f.name))}" for f in fields(section)]
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
