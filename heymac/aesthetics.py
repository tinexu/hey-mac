"""Aesthetic presets: "hey jarvis, coffee mode".

A preset lives in config.toml and can change any of these (all optional):

  [presets.coffee]
  wallpaper     = "~/Pictures/Aesthetics/coffee.jpg"   # a file, or a folder (picks one at random)
  appearance    = "dark"                               # "light" or "dark"
  playlist      = "https://open.spotify.com/playlist/…" # Spotify link or spotify: URI
  volume        = 25                                   # Spotify volume 0-100
  shuffle       = true
  system_volume = 40                                   # Mac output volume 0-100
  lily          = "coffee"                             # colors: a palette name or {petal, tip, glow}
  mascot        = "bear"                               # cat, bunny, frog, bear, fox, otter, owl, or "lily"
                                                       # (leave it out: coffee=bear, matcha=frog, ocean=otter,
                                                       #  sakura=bunny, midnight=owl, sunset=fox, lily=cat)
  editor_theme  = "Gruvbox Dark Medium"                # Cursor + VS Code (or cursor_theme / vscode_theme)
  dock          = { size = 40, position = "left", autohide = true }
  accent        = "orange"                             # macOS accent color (some apps update after reopening)
  shortcut      = "Focus: Study"                       # run an Apple Shortcut (Focus, lights, Night Shift…)
  open          = ["Notes", "Spotify"]                 # apps to open
  quit          = ["Discord"]                          # apps to quit
  message       = "Coffee mode on ☕"

Before applying, the current state of everything the preset touches is saved, so
"undo my aesthetic" puts it back. "save my setup as a preset called rainy day" writes
the current wallpaper, appearance, editor theme, dock, accent and lily into config.toml.
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from . import critters, palettes

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".tiff", ".tif", ".gif", ".bmp"}
EDITOR_SETTINGS = {
    "cursor": "~/Library/Application Support/Cursor/User/settings.json",
    "vscode": "~/Library/Application Support/Code/User/settings.json",
}
EDITOR_NAMES = {"cursor": "Cursor", "vscode": "VS Code"}
ACCENTS = {"graphite": -1, "red": 0, "orange": 1, "yellow": 2, "green": 3, "blue": 4, "purple": 5, "pink": 6,
           "multicolor": None}
HIGHLIGHTS = {
    "graphite": "0.847059 0.847059 0.862745 Graphite", "red": "1.000000 0.733333 0.721569 Red",
    "orange": "1.000000 0.874510 0.701961 Orange", "yellow": "1.000000 0.937255 0.690196 Yellow",
    "green": "0.752941 0.964706 0.678431 Green", "blue": "0.698039 0.843137 1.000000 Blue",
    "purple": "0.968627 0.831373 1.000000 Purple", "pink": "1.000000 0.749020 0.823529 Pink",
}
KNOWN_KEYS = {"wallpaper", "appearance", "playlist", "volume", "shuffle", "system_volume", "lily", "mascot", "editor_theme",
              "cursor_theme", "vscode_theme", "dock", "accent", "shortcut", "open", "quit", "message"}
FILLER = re.compile(r"\b(?:my|the|a|an|aesthetic|aesthetics|mode|vibe|vibes|preset|theme|look|setup)\b", re.I)

THEME_RE = re.compile(r'("workbench\.colorTheme"\s*:\s*)"((?:[^"\\]|\\.)*)"')

WALLPAPER_SET = '''on run argv
    tell application "System Events" to tell every desktop to set picture to (item 1 of argv)
end run'''
WALLPAPER_GET = 'tell application "System Events" to return picture of desktop 1'
DARK_SET = '''on run argv
    tell application "System Events" to tell appearance preferences to set dark mode to ((item 1 of argv) is "true")
end run'''
DARK_GET = 'tell application "System Events" to tell appearance preferences to return dark mode'
SPOTIFY_PLAY = '''on run argv
    set theUri to item 1 of argv
    set vol to item 2 of argv
    set shuf to item 3 of argv
    tell application "Spotify"
        if vol is not "" then set sound volume to (vol as integer)
        if shuf is "true" then set shuffling to true
        if shuf is "false" then set shuffling to false
        play track theUri
    end tell
end run'''
SPOTIFY_STATE = 'tell application "Spotify" to return ((player state as text) & "|" & (sound volume as text))'
SPOTIFY_RESTORE = '''on run argv
    tell application "Spotify"
        set sound volume to ((item 1 of argv) as integer)
        if (item 2 of argv) is not "playing" then pause
    end tell
end run'''


# ------------------------------------------------------------------ system access
class System:
    """Everything that touches the Mac goes through here, so tests can swap it out."""

    def run(self, cmd: list[str], timeout: float = 20) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                                  env=dict(os.environ, LANG="en_US.UTF-8"))
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return subprocess.CompletedProcess(cmd, 1, "", str(e))

    def osa(self, script: str, *args: str) -> subprocess.CompletedProcess:
        return self.run(["osascript", "-e", script, *args])

    def running(self, process: str) -> bool:
        return self.run(["pgrep", "-x", process]).returncode == 0

    def sleep(self, s: float) -> None:
        time.sleep(s)

    def post_color_change(self) -> None:
        """Nudge running apps to pick up a new accent color."""
        try:
            from Foundation import NSDistributedNotificationCenter
            c = NSDistributedNotificationCenter.defaultCenter()
            for name in ("AppleColorPreferencesChangedNotification", "AppleAquaColorVariantChanged"):
                c.postNotificationName_object_userInfo_deliverImmediately_(name, None, None, True)
        except Exception:  # noqa: BLE001
            pass


def _err(r: subprocess.CompletedProcess) -> str:
    e = (r.stderr or "").strip()
    if "-1743" in e or "Not authorized" in e:
        return "blocked by macOS (allow it under Privacy & Security → Automation)"
    return e[:120] or "failed"


# ------------------------------------------------------------------ helpers
def spotify_uri(link: str) -> str:
    """https://open.spotify.com/playlist/ID?si=… → spotify:playlist:ID (URIs pass through)."""
    link = link.strip()
    if link.startswith("spotify:"):
        return link
    u = urlparse(link)
    if "spotify.com" in u.netloc:
        parts = [p for p in u.path.split("/") if p and not p.startswith("intl-")]
        if len(parts) >= 2 and parts[-2] in ("playlist", "album", "track", "artist", "show", "episode"):
            return f"spotify:{parts[-2]}:{parts[-1]}"
    raise ValueError(f"Not a Spotify link: {link}")


def pick_wallpaper(path: str, rng=random) -> str:
    p = Path(os.path.expanduser(path))
    if p.is_dir():
        images = sorted(f for f in p.iterdir() if f.suffix.lower() in IMAGE_EXT and not f.name.startswith("."))
        if not images:
            raise FileNotFoundError(f"No images in {path}")
        return str(rng.choice(images))
    if not p.is_file():
        raise FileNotFoundError(f"{path} doesn't exist")
    return str(p)


def display_name(key: str) -> str:
    return key.replace("_", " ").strip()


def toml_str(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{toml_key(k)} = {toml_str(x)}" for k, x in v.items()) + " }"
    if isinstance(v, list):
        return "[" + ", ".join(toml_str(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)        # a valid TOML basic string


def toml_key(k: str) -> str:
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else json.dumps(k, ensure_ascii=False)


# ------------------------------------------------------------------ editor settings
def get_editor_theme(path: str) -> str | None:
    p = Path(os.path.expanduser(path))
    if not p.is_file():
        return None
    m = THEME_RE.search(p.read_text(encoding="utf-8", errors="replace"))
    return json.loads(f'"{m.group(2)}"') if m else ""


def set_editor_theme(path: str, theme: str) -> tuple[bool, str]:
    """Edit just the colorTheme line so comments and everything else in settings.json stay."""
    p = Path(os.path.expanduser(path))
    if not p.parent.is_dir():
        return False, "not installed"
    text = p.read_text(encoding="utf-8") if p.is_file() else "{}"
    backup = p.with_name("settings.json.heymac-backup")
    if p.is_file() and not backup.exists():
        shutil.copy2(p, backup)
    value = json.dumps(theme, ensure_ascii=False)
    if THEME_RE.search(text):
        new = THEME_RE.sub(lambda m: m.group(1) + value, text, count=1)
    else:
        brace = text.find("{")
        if brace < 0:
            return False, "settings.json looks broken, left it alone"
        rest = text[brace + 1:]
        comma = "," if rest.strip() and not rest.strip().startswith("}") else ""
        new = text[:brace + 1] + f'\n    "workbench.colorTheme": {value}{comma}' + rest
    p.write_text(new, encoding="utf-8")
    return True, theme


# ------------------------------------------------------------------ the engine
@dataclass
class Step:
    label: str
    ok: bool
    detail: str = ""


@dataclass
class Aesthetics:
    presets: dict[str, dict]
    config_path: Path
    state_dir: Path = field(default_factory=lambda: Path.home() / ".config" / "heymac")
    sys: System = field(default_factory=System)
    open_app: Callable[[str], object] | None = None
    quit_app: Callable[[str], object] | None = None
    palette_hook: Callable[[str], None] | None = None      # recolors the lily on the UI thread
    mascot_hook: Callable[[str], None] | None = None       # swaps the animal on the UI thread
    mascot_setting: str = "auto"                           # [ui] mascot in config.toml
    editors: dict[str, str] = field(default_factory=lambda: dict(EDITOR_SETTINGS))

    # ---------------------------------------------------------------- names
    def names(self) -> list[str]:
        return [display_name(k) for k in self.presets]

    def resolve(self, spoken: str) -> str | None:
        """'my coffee aesthetic' -> 'coffee' (the config key), fuzzy."""
        from .actions import fuzzy_best
        cleaned = re.sub(r"\s+", " ", FILLER.sub(" ", spoken)).strip(" .!?")
        by_display = {display_name(k).lower(): k for k in self.presets}
        if not cleaned or not by_display:
            return None
        best, _ = fuzzy_best(cleaned, list(by_display), cutoff=0.75)
        return by_display.get(best) if best else None

    # ---------------------------------------------------------------- state
    @property
    def _state_file(self) -> Path:
        return self.state_dir / "aesthetic_state.json"

    def state(self) -> dict:
        try:
            return json.loads(self._state_file.read_text())
        except (OSError, ValueError):
            return {}

    def _save_state(self, **changes) -> None:
        s = self.state()
        s.update(changes)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(json.dumps(s, indent=2))

    def current_lily(self) -> str:
        return self.state().get("lily", palettes.DEFAULT)

    def current_mascot(self) -> str:
        """The animal to show right now: an explicit pick, else the one that goes with the colors."""
        st = self.state()
        lily = str(st.get("lily", palettes.DEFAULT))
        auto = None if lily.startswith("{") else critters.MASCOT_FOR_PALETTE.get(lily)
        return critters.choose(self.mascot_setting, st.get("mascot"), auto)

    def _push_mascot(self) -> None:
        if self.mascot_hook:
            self.mascot_hook(self.current_mascot())

    # ---------------------------------------------------------------- capture current state
    def capture(self, keys: set[str]) -> dict:
        snap: dict = {}
        if "wallpaper" in keys:
            r = self.sys.osa(WALLPAPER_GET)
            if r.returncode == 0 and r.stdout.strip():
                snap["wallpaper"] = r.stdout.strip()
        if "appearance" in keys:
            r = self.sys.osa(DARK_GET)
            if r.returncode == 0:
                snap["appearance"] = "dark" if r.stdout.strip() == "true" else "light"
        if keys & {"editor_theme", "cursor_theme", "vscode_theme"}:
            themes = {ed: get_editor_theme(path) for ed, path in self.editors.items()}
            snap["editor_themes"] = {ed: t for ed, t in themes.items() if t is not None}
        if "dock" in keys:
            dock = {}
            for key, name, kind in (("size", "tilesize", int), ("position", "orientation", str),
                                    ("autohide", "autohide", bool)):
                r = self.sys.run(["defaults", "read", "com.apple.dock", name])
                if r.returncode == 0:
                    v = r.stdout.strip()
                    dock[key] = (v == "1") if kind is bool else (int(float(v)) if kind is int else v)
            snap["dock"] = dock
        if "accent" in keys:
            r = self.sys.run(["defaults", "read", "-g", "AppleAccentColor"])
            snap["accent"] = int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip().lstrip("-").isdigit() else None
            r = self.sys.run(["defaults", "read", "-g", "AppleHighlightColor"])
            snap["highlight"] = r.stdout.strip() if r.returncode == 0 else None
        if "lily" in keys:
            snap["lily"] = self.current_lily()
        if keys & {"lily", "mascot"}:
            snap["mascot"] = self.state().get("mascot")        # None = "follow the colors"
        if keys & {"playlist", "volume"} and self.sys.running("Spotify"):
            r = self.sys.osa(SPOTIFY_STATE)
            if r.returncode == 0 and "|" in r.stdout:
                st, vol = r.stdout.strip().split("|", 1)
                snap["spotify"] = {"state": st, "volume": int(float(vol))}
        if "system_volume" in keys:
            r = self.sys.osa("output volume of (get volume settings)")
            if r.returncode == 0 and r.stdout.strip().isdigit():
                snap["system_volume"] = int(r.stdout.strip())
        return snap

    # ---------------------------------------------------------------- the individual changes
    def _wallpaper(self, path: str) -> Step:
        try:
            chosen = pick_wallpaper(path)
        except FileNotFoundError as e:
            return Step("wallpaper", False, str(e))
        r = self.sys.osa(WALLPAPER_SET, chosen)
        return Step("wallpaper", r.returncode == 0, os.path.basename(chosen) if r.returncode == 0 else _err(r))

    def _appearance(self, mode: str) -> Step:
        mode = str(mode).lower()
        if mode not in ("dark", "light"):
            return Step("appearance", False, f"“{mode}” should be light or dark")
        r = self.sys.osa(DARK_SET, "true" if mode == "dark" else "false")
        return Step(f"{mode} mode", r.returncode == 0, "" if r.returncode == 0 else _err(r))

    def _lily(self, spec) -> Step:
        if not palettes.is_valid(spec):
            return Step("lily colors", False, f"unknown palette {spec!r}")
        code = palettes.encode(spec)
        self._save_state(lily=code)
        if self.palette_hook:
            self.palette_hook(code)
        return Step("lily colors", True)

    def _mascot(self, name) -> Step:
        n = critters.normalize(str(name))
        if not n:
            return Step("mascot", False, f"pick one of: {', '.join(critters.ANIMALS)}, lily")
        self._save_state(mascot=n)
        return Step("mascot", True, n)

    def set_mascot(self, spoken: str) -> tuple[bool, str]:
        """'make jarvis a fox' / 'back to the lily' / 'auto'."""
        if spoken.strip().lower() in ("auto", "default", "normal", "match", "match my aesthetic"):
            self._save_state(mascot=None)
            self._push_mascot()
            return True, f"Okay, I'll match your aesthetic ({self.current_mascot()} right now)."
        step = self._mascot(spoken)
        if not step.ok:
            return False, f"I can be a {', '.join(critters.ANIMALS[:-1])} or {critters.ANIMALS[-1]} (or the lily)."
        self._push_mascot()
        if step.detail != "lily" and self.current_mascot() == "lily":
            return True, 'Saved, but your config has [ui] mascot = "lily". Change it to "auto" to see animals.'
        return True, "Back to the lily." if step.detail == "lily" else f"Ta-da, I'm a {step.detail} now."

    def _editor(self, preset: dict) -> list[Step]:
        steps = []
        for ed, path in self.editors.items():
            theme = preset.get(f"{ed}_theme", preset.get("editor_theme"))
            if not theme:
                continue
            ok, detail = set_editor_theme(path, theme)
            if detail == "not installed" and f"{ed}_theme" not in preset:
                continue                                   # don't complain about editors you don't use
            steps.append(Step(f"{EDITOR_NAMES[ed]} theme", ok, "" if ok else detail))
        return steps

    def _dock(self, dock: dict, restart: bool = True) -> Step:
        writes = []
        if "size" in dock:
            writes.append(["tilesize", "-int", str(max(16, min(128, int(dock["size"]))))])
        if "position" in dock:
            pos = str(dock["position"]).lower()
            if pos not in ("left", "bottom", "right"):
                return Step("dock", False, "position should be left, bottom or right")
            writes.append(["orientation", "-string", pos])
        if "autohide" in dock:
            writes.append(["autohide", "-bool", "true" if dock["autohide"] else "false"])
        changed = False
        for w in writes:
            cur = self.sys.run(["defaults", "read", "com.apple.dock", w[0]])
            want = {"true": "1", "false": "0"}.get(w[2], w[2])
            if cur.returncode != 0 or cur.stdout.strip() != want:
                self.sys.run(["defaults", "write", "com.apple.dock", *w])
                changed = True
        if changed and restart:
            self.sys.run(["killall", "Dock"])
        return Step("dock", True, "" if changed else "already set")

    def _accent(self, color) -> Step:
        name = str(color).lower()
        if name not in ACCENTS:
            return Step("accent color", False, f"use one of: {', '.join(ACCENTS)}")
        self._write_accent(ACCENTS[name], HIGHLIGHTS.get(name))
        return Step("accent color", True, "some apps update after reopening")

    def _write_accent(self, value: int | None, highlight: str | None) -> None:
        if value is None:
            self.sys.run(["defaults", "delete", "-g", "AppleAccentColor"])
        else:
            self.sys.run(["defaults", "write", "-g", "AppleAccentColor", "-int", str(value)])
        if highlight:
            self.sys.run(["defaults", "write", "-g", "AppleHighlightColor", "-string", highlight])
        else:
            self.sys.run(["defaults", "delete", "-g", "AppleHighlightColor"])
        self.sys.post_color_change()

    def _spotify(self, preset: dict) -> Step:
        try:
            uri = spotify_uri(str(preset["playlist"])) if preset.get("playlist") else ""
        except ValueError as e:
            return Step("music", False, str(e))
        if not self.sys.running("Spotify"):
            self.sys.run(["open", "-g", "-a", "Spotify"])          # -g: don't steal focus
            for _ in range(16):
                self.sys.sleep(0.5)
                if self.sys.running("Spotify"):
                    break
            self.sys.sleep(2.5)                                     # let it finish starting
        vol = str(int(preset["volume"])) if "volume" in preset else ""
        shuf = "" if "shuffle" not in preset else ("true" if preset["shuffle"] else "false")
        if not uri:                                                 # just a volume change
            r = self.sys.osa(SPOTIFY_PLAY.replace("play track theUri", ""), "", vol, shuf)
            return Step("Spotify volume", r.returncode == 0, "" if r.returncode == 0 else _err(r))
        r = self.sys.osa(SPOTIFY_PLAY, uri, vol, shuf)
        if r.returncode != 0:                                       # Spotify sometimes needs a moment more
            self.sys.sleep(2)
            r = self.sys.osa(SPOTIFY_PLAY, uri, vol, shuf)
        return Step("music", r.returncode == 0, "" if r.returncode == 0 else _err(r))

    def _shortcut(self, name: str) -> Step:
        r = self.sys.run(["shortcuts", "run", str(name)], timeout=60)
        return Step(f"shortcut “{name}”", r.returncode == 0, "" if r.returncode == 0 else _err(r))

    # ---------------------------------------------------------------- apply / undo
    def apply(self, spoken: str) -> tuple[bool, str]:
        key = self.resolve(spoken) if spoken not in self.presets else spoken
        if not key:
            have = ", ".join(self.names()) or "none yet"
            return False, f"No aesthetic called “{spoken}”. You have: {have}."
        preset = dict(self.presets[key])
        unknown = set(preset) - KNOWN_KEYS
        # "Undo" means back to normal: keep the state from before the FIRST aesthetic in a row,
        # and only add things this preset touches that the earlier ones didn't.
        st = self.state()
        prev = st.get("undo") if st.get("active") else None
        before = dict(prev["before"]) if prev else {}
        fresh = self.capture(set(preset))
        for k, v in fresh.items():
            if k == "editor_themes":
                merged = dict(fresh[k])
                merged.update(before.get(k, {}))
                before[k] = merged
            else:
                before.setdefault(k, v)
        self._save_state(undo={"preset": key, "time": time.time(), "before": before})

        steps: list[Step] = []
        if "mascot" in preset:
            steps.append(self._mascot(preset["mascot"]))
        elif "lily" in preset:
            self._save_state(mascot=None)                 # follow the preset's colors (coffee → bear)
        if "lily" in preset:
            steps.append(self._lily(preset["lily"]))
        if "lily" in preset or "mascot" in preset:
            self._push_mascot()
        if "appearance" in preset:
            steps.append(self._appearance(preset["appearance"]))
        if "wallpaper" in preset:
            steps.append(self._wallpaper(str(preset["wallpaper"])))
        if "accent" in preset:
            steps.append(self._accent(preset["accent"]))
        steps += self._editor(preset)
        if isinstance(preset.get("dock"), dict):
            steps.append(self._dock(preset["dock"]))
        if "system_volume" in preset:
            r = self.sys.osa(f"set volume output volume {max(0, min(100, int(preset['system_volume'])))}")
            steps.append(Step("Mac volume", r.returncode == 0))
        for app in preset.get("quit", []):
            if self.quit_app:
                self.quit_app(app)
        for app in preset.get("open", []):
            if self.open_app:
                self.open_app(app)
        if preset.get("shortcut"):
            steps.append(self._shortcut(preset["shortcut"]))
        if preset.get("playlist") or "volume" in preset or "shuffle" in preset:
            steps.append(self._spotify(preset))

        self._save_state(active=key)
        failed = [s for s in steps if not s.ok]
        headline = preset.get("message") or f"{display_name(key).title()} mode on"
        if unknown:
            failed.append(Step("config", False, f"unknown setting(s) ignored: {', '.join(sorted(unknown))}"))
        if not failed:
            return True, headline
        problems = "; ".join(f"{s.label}: {s.detail}" for s in failed)
        return len(failed) < len(steps) or not steps, f"{headline}, except {problems}"

    def undo(self) -> tuple[bool, str]:
        undo = self.state().get("undo")
        if not undo:
            return True, "You're already on your normal look."
        b = undo.get("before", {})
        steps: list[Step] = []
        if "wallpaper" in b:
            r = self.sys.osa(WALLPAPER_SET, b["wallpaper"])
            steps.append(Step("wallpaper", r.returncode == 0, "" if r.returncode == 0 else _err(r)))
        if "appearance" in b:
            steps.append(self._appearance(b["appearance"]))
        for ed, theme in b.get("editor_themes", {}).items():
            if ed in self.editors and theme:
                ok, d = set_editor_theme(self.editors[ed], theme)
                steps.append(Step(f"{EDITOR_NAMES.get(ed, ed)} theme", ok, "" if ok else d))
        if b.get("dock"):
            steps.append(self._dock(b["dock"]))
        if "accent" in b:
            self._write_accent(b["accent"], b.get("highlight"))
            steps.append(Step("accent color", True))
        if "mascot" in b:
            self._save_state(mascot=b["mascot"])
        if "lily" in b:
            steps.append(self._lily(b["lily"]))
        if "lily" in b or "mascot" in b:
            self._push_mascot()
        if "spotify" in b and self.sys.running("Spotify"):
            r = self.sys.osa(SPOTIFY_RESTORE, str(b["spotify"]["volume"]), b["spotify"]["state"])
            steps.append(Step("music", r.returncode == 0))
        if "system_volume" in b:
            self.sys.osa(f"set volume output volume {b['system_volume']}")
        self._save_state(undo=None, active=None)
        failed = [s for s in steps if not s.ok]
        name = display_name(undo.get("preset", ""))
        if failed:
            return True, f"Undid {name} mode, except " + "; ".join(f"{s.label}: {s.detail}" for s in failed)
        return True, f"Back to how things were before {name} mode"

    # ---------------------------------------------------------------- save / list
    def save(self, name: str, overrides: dict | None = None) -> tuple[bool, str]:
        name = re.sub(r"\s+", " ", name.strip().strip("\"'“”.")).lower()
        name = re.sub(r"^(?:called|named)\s+", "", name)
        if not name:
            return False, "What should the preset be called?"
        key = name.replace(" ", "_")
        if key in self.presets or self.resolve(name) == key:
            return False, f"You already have a “{name}” aesthetic. Pick another name, or edit it in config.toml."
        snap = self.capture({"wallpaper", "appearance", "editor_theme", "dock", "accent", "lily"})
        preset: dict = {}
        if snap.get("wallpaper"):
            preset["wallpaper"] = snap["wallpaper"]
        if snap.get("appearance"):
            preset["appearance"] = snap["appearance"]
        themes = [t for t in snap.get("editor_themes", {}).values() if t]
        if themes:
            preset["editor_theme"] = themes[0]
        if snap.get("dock"):
            preset["dock"] = snap["dock"]
        accent = next((n for n, v in ACCENTS.items() if v == snap.get("accent")), None)
        if accent and snap.get("accent") is not None:
            preset["accent"] = accent
        lily = snap.get("lily")
        if lily and lily != palettes.DEFAULT:
            preset["lily"] = json.loads(lily) if lily.startswith("{") else lily
        if snap.get("mascot"):
            preset["mascot"] = snap["mascot"]
        preset.update({k: v for k, v in (overrides or {}).items() if k in KNOWN_KEYS and v not in (None, "")})

        block = f"\n[presets.{toml_key(key)}]\n" + "".join(f"{k} = {toml_str(v)}\n" for k, v in preset.items())
        path = Path(os.path.expanduser(str(self.config_path)))
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        new = old.rstrip("\n") + "\n" + block if old else block.lstrip("\n")
        try:
            tomllib.loads(new)                                   # never write a broken config
        except tomllib.TOMLDecodeError as e:
            return False, f"Couldn't save the preset safely ({e})."
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new, encoding="utf-8")
        self.presets[key] = preset
        tip = "" if "playlist" in preset else " Add a playlist link to it in your config if you want music."
        return True, f"Saved “{name}” ({', '.join(preset) or 'empty'}).{tip}"

    def describe(self) -> tuple[bool, str]:
        if not self.presets:
            return False, "No aesthetics yet. Add one under [presets.<name>] in config.toml."
        active = self.state().get("active")
        lines = []
        for k, p in self.presets.items():
            parts = [p.get("appearance"), "music" if p.get("playlist") else None,
                     f"lily: {p['lily']}" if isinstance(p.get("lily"), str) else None,
                     f"mascot: {p['mascot']}" if p.get("mascot") else None]
            mark = " (on now)" if k == active else ""
            lines.append(f"{display_name(k)}{mark}" + (f": {', '.join(x for x in parts if x)}" if any(parts) else ""))
        return True, "Your aesthetics: " + " · ".join(lines)