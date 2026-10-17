"""Loads config.toml (user copy overrides defaults)."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

CONFIG_LOCATIONS = [
    Path.cwd() / "config.toml",
    Path.home() / ".config" / "heymac" / "config.toml",
]

DEFAULT_ALIASES = {
    "chrome": "Google Chrome",
    "messages": "Messages",
    "imessage": "Messages",
    "texts": "Messages",
    "vs code": "Visual Studio Code",
    "vscode": "Visual Studio Code",
    "terminal": "Terminal",
    "settings": "System Settings",
}


@dataclass
class Config:
    name: str = "Jarvis"
    speak_replies: bool = False
    memory_seconds: int = 120
    ui_style: str = "lily"
    ui_corner: str = "bottom-right"

    wake_word: str = "hey_jarvis"
    wake_threshold: float = 0.5
    hotkey: str = "option+space"
    enable_wake_word: bool = True

    stt_model: str = "mlx-community/whisper-large-v3-turbo"
    language: str = "en"
    input_device: str = ""
    silence_seconds: float = 1.0
    max_seconds: float = 12.0
    vocabulary: list[str] = field(default_factory=list)

    my_handle: str = ""              # your phone/Apple ID for "text me"

    ai_model: str = "claude-haiku-4-5"
    max_tokens: int = 600

    project_roots: list[str] = field(default_factory=lambda: [
        "~/code", "~/Projects", "~/Developer", "~/Documents/GitHub", "~/dev", "~/src"])
    search_roots: list[str] = field(default_factory=lambda: ["~/Desktop", "~/Documents", "~/Downloads"])
    aliases: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ALIASES))
    presets: dict[str, dict] = field(default_factory=dict)       # [presets.<name>] aesthetics
    config_path: Path = field(default_factory=lambda: Path.home() / ".config" / "heymac" / "config.toml")

    @property
    def project_paths(self) -> list[Path]:
        return [p for p in (Path(os.path.expanduser(r)) for r in self.project_roots) if p.is_dir()]

    @property
    def search_paths(self) -> list[Path]:
        return [p for p in (Path(os.path.expanduser(r)) for r in self.search_roots) if p.is_dir()]


def load_config(path: str | None = None) -> Config:
    cfg = Config()
    candidates = [Path(path)] if path else CONFIG_LOCATIONS
    data: dict = {}
    for p in candidates:
        if p.is_file():
            with open(p, "rb") as f:
                data = tomllib.load(f)
            cfg.config_path = p
            break
    if path and not data:
        cfg.config_path = Path(path)

    a = data.get("assistant", {})
    act = data.get("activation", {})
    sp = data.get("speech", {})
    ai = data.get("ai", {})
    fl = data.get("files", {})

    cfg.name = a.get("name", cfg.name)
    cfg.speak_replies = a.get("speak_replies", cfg.speak_replies)
    cfg.memory_seconds = a.get("memory_seconds", cfg.memory_seconds)
    ui = data.get("ui", {})
    cfg.ui_style = ui.get("style", cfg.ui_style)
    cfg.ui_corner = ui.get("corner", cfg.ui_corner)
    cfg.wake_word = act.get("wake_word", cfg.wake_word)
    cfg.wake_threshold = act.get("wake_threshold", cfg.wake_threshold)
    cfg.hotkey = act.get("hotkey", cfg.hotkey)
    cfg.enable_wake_word = act.get("enable_wake_word", cfg.enable_wake_word)
    cfg.stt_model = sp.get("model", cfg.stt_model)
    cfg.language = sp.get("language", cfg.language)
    cfg.input_device = sp.get("input_device", cfg.input_device)
    cfg.silence_seconds = sp.get("silence_seconds", cfg.silence_seconds)
    cfg.max_seconds = sp.get("max_seconds", cfg.max_seconds)
    cfg.vocabulary = sp.get("vocabulary", cfg.vocabulary)
    cfg.ai_model = ai.get("model", cfg.ai_model)
    cfg.max_tokens = ai.get("max_tokens", cfg.max_tokens)
    cfg.project_roots = fl.get("project_roots", cfg.project_roots)
    cfg.search_roots = fl.get("search_roots", cfg.search_roots)
    cfg.my_handle = str(data.get("send", {}).get("me", cfg.my_handle))
    cfg.presets = {str(k): dict(v) for k, v in data.get("presets", {}).items() if isinstance(v, dict)}
    cfg.aliases.update({k.lower(): v for k, v in data.get("aliases", {}).items()})
    return cfg



# Settings that are only read when the app starts (everything else applies on reload).
RESTART_FIELDS = {"hotkey": "hotkey", "wake_word": "wake word", "enable_wake_word": "wake word on/off",
                  "input_device": "microphone", "ui_style": "UI style"}


def update_config(live: Config, new: Config) -> set[str]:
    """Copy every setting from `new` into the config object the app is already using.

    Updating in place (instead of swapping objects) means the mic, speech, AI and actions,
    which all hold a reference to `live`, see the new values right away. Returns what changed.
    """
    changed = set()
    for f in fields(Config):
        if getattr(live, f.name) != getattr(new, f.name):
            setattr(live, f.name, getattr(new, f.name))
            changed.add(f.name)
    return changed
