"""Aesthetic presets: apply, undo, save, list and voice routing, against a fake Mac."""
import json
import subprocess
import tomllib
from pathlib import Path

import pytest

from heymac import aesthetics as ae
from heymac import palettes
from heymac.actions import ActionResult, Mac
from heymac.commands import route
from heymac.config import Config


def done(out="", code=0, err=""):
    return subprocess.CompletedProcess([], code, out, err)


class FakeSystem:
    """A pretend Mac that remembers what was changed."""

    def __init__(self):
        self.wallpaper = "/Users/me/Pictures/old.jpg"
        self.dark = False
        self.dock = {"tilesize": "64", "orientation": "bottom", "autohide": "0"}
        self.accent = None
        self.highlight = None
        self.spotify_running = True
        self.spotify = {"state": "paused", "volume": 80, "playing": None, "shuffle": None}
        self.mac_volume = 50
        self.cmds = []
        self.dock_restarts = 0
        self.shortcuts = []

    def run(self, cmd, timeout=20):
        self.cmds.append(cmd)
        if cmd[:3] == ["defaults", "read", "com.apple.dock"]:
            return done(self.dock[cmd[3]] + "\n")
        if cmd[:3] == ["defaults", "write", "com.apple.dock"]:
            v = cmd[5]
            self.dock[cmd[3]] = {"true": "1", "false": "0"}.get(v, v)
            return done()
        if cmd == ["killall", "Dock"]:
            self.dock_restarts += 1
            return done()
        if cmd[:4] == ["defaults", "read", "-g", "AppleAccentColor"]:
            return done(f"{self.accent}\n") if self.accent is not None else done(code=1)
        if cmd[:4] == ["defaults", "read", "-g", "AppleHighlightColor"]:
            return done(self.highlight) if self.highlight else done(code=1)
        if cmd[:4] == ["defaults", "write", "-g", "AppleAccentColor"]:
            self.accent = int(cmd[5])
            return done()
        if cmd[:4] == ["defaults", "delete", "-g", "AppleAccentColor"]:
            self.accent = None
            return done()
        if cmd[:4] == ["defaults", "write", "-g", "AppleHighlightColor"]:
            self.highlight = cmd[5]
            return done()
        if cmd[:4] == ["defaults", "delete", "-g", "AppleHighlightColor"]:
            self.highlight = None
            return done()
        if cmd[:2] == ["shortcuts", "run"]:
            self.shortcuts.append(cmd[2])
            return done()
        if cmd[:2] == ["open", "-g"]:
            self.spotify_running = True
            return done()
        return done()

    def osa(self, script, *args):
        if script == ae.WALLPAPER_GET:
            return done(self.wallpaper + "\n")
        if script == ae.WALLPAPER_SET:
            self.wallpaper = args[0]
            return done()
        if script == ae.DARK_GET:
            return done("true\n" if self.dark else "false\n")
        if script == ae.DARK_SET:
            self.dark = args[0] == "true"
            return done()
        if script == ae.SPOTIFY_STATE:
            return done(f"{self.spotify['state']}|{self.spotify['volume']}\n")
        if script.startswith('on run argv\n    set theUri'):
            uri, vol, shuf = args
            if vol:
                self.spotify["volume"] = int(vol)
            if shuf:
                self.spotify["shuffle"] = shuf == "true"
            if "play track theUri" in script:
                self.spotify.update(playing=uri, state="playing")
            return done()
        if script == ae.SPOTIFY_RESTORE:
            self.spotify["volume"] = int(args[0])
            if args[1] != "playing":
                self.spotify["state"] = "paused"
            return done()
        if script == "output volume of (get volume settings)":
            return done(f"{self.mac_volume}\n")
        if script.startswith("set volume output volume"):
            self.mac_volume = int(script.split()[-1])
            return done()
        raise AssertionError(f"unexpected AppleScript: {script[:60]}")

    def running(self, process):
        return self.spotify_running

    def sleep(self, s):
        pass

    def post_color_change(self):
        pass


@pytest.fixture
def env(tmp_path):
    pics = tmp_path / "Pictures" / "coffee"
    pics.mkdir(parents=True)
    (pics / "latte.jpg").write_bytes(b"x")
    (pics / "notes.txt").write_text("not an image")
    cursor = tmp_path / "Cursor" / "User" / "settings.json"
    cursor.parent.mkdir(parents=True)
    cursor.write_text('{\n  // my settings\n  "editor.fontSize": 14,\n  "workbench.colorTheme": "Default Dark Modern",\n}\n')
    config = tmp_path / "config.toml"
    config.write_text('[assistant]\nname = "Jarvis"\n')
    presets = {
        "coffee": {"wallpaper": str(pics), "appearance": "dark", "lily": "coffee",
                   "playlist": "https://open.spotify.com/playlist/AbC123?si=xyz", "volume": 25, "shuffle": True,
                   "editor_theme": "Gruvbox Dark Medium", "dock": {"size": 40, "position": "left"},
                   "accent": "orange", "shortcut": "Focus: Study", "open": ["Notes"], "quit": ["Discord"],
                   "system_volume": 30, "message": "Coffee mode on ☕"},
        "rainy_day": {"appearance": "dark", "lily": {"petal": "#5A6B7C", "tip": "#DDE6EE"}},
    }
    sysm = FakeSystem()
    opened, quit_ = [], []
    hooks = []
    eng = ae.Aesthetics(presets, config, state_dir=tmp_path / "state", sys=sysm,
                        open_app=opened.append, quit_app=quit_.append, palette_hook=hooks.append,
                        editors={"cursor": str(cursor), "vscode": str(tmp_path / "Code" / "User" / "settings.json")})
    return dict(eng=eng, sys=sysm, cursor=cursor, config=config, opened=opened, quit=quit_, hooks=hooks, pics=pics)


# ----------------------------------------------------------------- applying
def test_apply_coffee_changes_everything(env):
    eng, s = env["eng"], env["sys"]
    ok, msg = eng.apply("coffee")
    assert ok and msg == "Coffee mode on ☕", msg
    assert s.wallpaper.endswith("latte.jpg")                       # picked an image, skipped the .txt
    assert s.dark is True
    assert s.spotify["playing"] == "spotify:playlist:AbC123"       # link → URI, tracking junk dropped
    assert s.spotify["volume"] == 25 and s.spotify["shuffle"] is True
    assert s.dock["tilesize"] == "40" and s.dock["orientation"] == "left" and s.dock_restarts == 1
    assert s.accent == 1 and s.highlight.endswith("Orange")
    assert s.shortcuts == ["Focus: Study"]
    assert s.mac_volume == 30
    assert env["opened"] == ["Notes"] and env["quit"] == ["Discord"]
    assert env["hooks"] == ["coffee"]                              # lily recolored
    text = env["cursor"].read_text()
    assert '"workbench.colorTheme": "Gruvbox Dark Medium"' in text and "// my settings" in text
    assert env["cursor"].with_name("settings.json.heymac-backup").exists()


def test_fuzzy_names(env):
    eng = env["eng"]
    assert eng.resolve("my coffee aesthetic") == "coffee"
    assert eng.resolve("cofee") == "coffee"                       # misheard
    assert eng.resolve("rainy day vibes") == "rainy_day"
    assert eng.resolve("chrome") is None


def test_unknown_preset_lists_options(env):
    ok, msg = env["eng"].apply("disco")
    assert not ok and "coffee" in msg and "rainy day" in msg


def test_dock_untouched_when_already_set(env):
    eng, s = env["eng"], env["sys"]
    eng.apply("coffee")
    eng.apply("coffee")
    assert s.dock_restarts == 1                                    # no second Dock blink


def test_custom_lily_colors(env):
    env["eng"].apply("rainy day")
    spec = env["hooks"][-1]
    assert json.loads(spec)["petal"] == "#5A6B7C"
    pal = palettes.resolve(spec)
    assert set(pal) >= {"back", "front", "glow", "think", "stamen", "center", "filament"}


def test_partial_failure_is_reported(env):
    eng = env["eng"]
    eng.presets["broken"] = {"wallpaper": "~/nope/missing.jpg", "appearance": "dark"}
    ok, msg = eng.apply("broken")
    assert ok and "except wallpaper" in msg and env["sys"].dark is True


def test_spotify_started_if_closed(env):
    s = env["sys"]
    s.spotify_running = False
    env["eng"].apply("coffee")
    assert ["open", "-g", "-a", "Spotify"] in s.cmds and s.spotify["state"] == "playing"


# ----------------------------------------------------------------- undo
def test_undo_restores_previous_state(env):
    eng, s = env["eng"], env["sys"]
    eng.apply("coffee")
    ok, msg = eng.undo()
    assert ok and "before coffee mode" in msg
    assert s.wallpaper == "/Users/me/Pictures/old.jpg"
    assert s.dark is False
    assert s.dock["tilesize"] == "64" and s.dock["orientation"] == "bottom"
    assert s.accent is None and s.highlight is None
    assert s.spotify["volume"] == 80 and s.spotify["state"] == "paused"   # music stopped again
    assert s.mac_volume == 50
    assert '"workbench.colorTheme": "Default Dark Modern"' in env["cursor"].read_text()
    assert env["hooks"][-1] == "lily"
    assert eng.undo()[0] is False                                  # nothing left to undo


# ----------------------------------------------------------------- save / list
def test_save_current_setup(env):
    eng = env["eng"]
    eng.apply("coffee")
    ok, msg = eng.save("Late Night", {"playlist": "https://open.spotify.com/playlist/ZZZ"})
    assert ok and "late night" in msg
    data = tomllib.loads(env["config"].read_text())
    p = data["presets"]["late_night"]
    assert p["appearance"] == "dark" and p["lily"] == "coffee" and p["accent"] == "orange"
    assert p["editor_theme"] == "Gruvbox Dark Medium" and p["dock"]["position"] == "left"
    assert p["playlist"].endswith("ZZZ")
    assert data["assistant"]["name"] == "Jarvis"                   # rest of the config untouched
    assert eng.resolve("late night") == "late_night"               # usable right away
    assert eng.save("late night")[0] is False                      # no duplicates


def test_describe(env):
    env["eng"].apply("coffee")
    ok, msg = env["eng"].describe()
    assert ok and "coffee (on now)" in msg and "rainy day" in msg


def test_spotify_uri_forms():
    assert ae.spotify_uri("spotify:playlist:abc") == "spotify:playlist:abc"
    assert ae.spotify_uri("https://open.spotify.com/intl-de/album/XYZ?si=1") == "spotify:album:XYZ"
    with pytest.raises(ValueError):
        ae.spotify_uri("https://youtube.com/watch?v=1")


def test_theme_inserted_when_missing(tmp_path):
    f = tmp_path / "settings.json"
    f.write_text('{\n  "editor.fontSize": 14\n}\n')
    ae.set_editor_theme(str(f), "Night Owl")
    assert '"workbench.colorTheme": "Night Owl",' in f.read_text()
    assert ae.get_editor_theme(str(f)) == "Night Owl"


# ----------------------------------------------------------------- voice routing
class RouteMac:
    def __init__(self, presets):
        self.cfg = Config()
        self.cfg.presets = presets
        self.aesthetics = ae.Aesthetics(presets, Path("/tmp/x.toml"))
        self.called = None

    def __getattr__(self, name):
        if name in ("apply_aesthetic", "undo_aesthetic", "list_aesthetics", "save_aesthetic", "set_appearance",
                    "open_app", "add_note", "append_note", "send_to_me", "ask_chatbot"):
            return lambda *a, **k: setattr(self, "called", (name, *a)) or ActionResult(True, "ok")
        raise AttributeError(name)

    def resolve_app(self, name, among=None):
        return "Google Chrome" if "chrome" in name.lower() else None


PRESETS = {"coffee": {}, "matcha": {}, "rainy_day": {}}


@pytest.mark.parametrize("said,expected", [
    ("hey jarvis change my aesthetic to coffee", ("apply_aesthetic", "coffee")),
    ("coffee mode", ("apply_aesthetic", "coffee")),
    ("switch to matcha mode", ("apply_aesthetic", "matcha")),
    ("set the vibe to rainy day", ("apply_aesthetic", "rainy_day")),
    ("put on rainy day vibes", ("apply_aesthetic", "rainy_day")),
    ("it's coffee time", ("apply_aesthetic", "coffee")),
    ("switch to matcha", ("apply_aesthetic", "matcha")),
    ("undo my aesthetic", ("undo_aesthetic",)),
    ("go back to normal", ("undo_aesthetic",)),
    ("what aesthetics do i have", ("list_aesthetics",)),
    ("save my setup as a preset called late night", ("save_aesthetic", "late night")),
    ("turn on dark mode", ("set_appearance", "dark")),
    ("light mode", ("set_appearance", "light")),
])
def test_routes(said, expected):
    mac = RouteMac(dict(PRESETS))
    fn = route(said, mac)
    assert fn is not None, said
    fn()
    assert mac.called == expected


def test_switch_to_an_app_is_not_hijacked():
    mac = RouteMac(dict(PRESETS))
    route("switch to chrome", mac)()
    assert mac.called == ("open_app", "Google Chrome")


def test_unknown_vibe_goes_to_ai():
    assert route("disco mode", RouteMac(dict(PRESETS))) is None


def test_real_mac_wiring(tmp_path):
    cfg = Config()
    cfg.presets = {"coffee": {"appearance": "dark"}}
    cfg.config_path = tmp_path / "config.toml"
    mac = Mac(cfg)
    assert mac.aesthetics.resolve("coffee") == "coffee"
    assert route("coffee mode", mac) is not None

def test_undo_goes_back_to_normal_after_several_switches(env):
    eng, s = env["eng"], env["sys"]
    eng.presets["night"] = {"appearance": "light", "system_volume": 10}
    eng.apply("coffee")
    eng.apply("coffee")                 # running it twice must not make "coffee" the new normal
    eng.apply("night")
    eng.undo()
    assert s.dark is False and s.wallpaper == "/Users/me/Pictures/old.jpg"
    assert s.mac_volume == 50 and s.dock["tilesize"] == "64"
    assert env["hooks"][-1] == "lily"


def test_config_reload_updates_live_object(tmp_path):
    from heymac.config import RESTART_FIELDS, load_config, update_config
    f = tmp_path / "config.toml"
    f.write_text('[presets.coffee]\nappearance = "dark"\n')
    live = load_config(str(f))
    mac = Mac(live)
    assert list(mac.cfg.presets) == ["coffee"]
    f.write_text('[activation]\nhotkey = "cmd+shift+j"\n[presets.coffee]\nappearance = "dark"\n'
                 '[presets.matcha]\nlily = "matcha"\n')
    changed = update_config(live, load_config(str(f)))
    assert {"presets", "hotkey"} <= changed and "hotkey" in RESTART_FIELDS
    assert mac.cfg is live and set(mac.cfg.presets) == {"coffee", "matcha"}   # same object, new values
    mac._aesthetics = None
    assert mac.aesthetics.resolve("matcha") == "matcha"
