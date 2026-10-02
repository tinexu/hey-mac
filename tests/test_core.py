"""Tests that run anywhere (no Mac, mic, or API key needed):  python -m pytest tests"""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from heymac import actions
from heymac.actions import Mac, fuzzy_best
from heymac.app import pick_from_list
from heymac.brain import Brain
from heymac.commands import clean, route
from heymac.config import Config, load_config
from heymac.hotkey import parse_hotkey

APPS = {n: f"/Applications/{n}.app" for n in [
    "Messages", "Google Chrome", "Cursor", "Visual Studio Code", "Spotify", "Notes", "Safari",
    "System Settings", "Terminal", "Slack", "Finder"]}


@pytest.fixture
def mac(monkeypatch):
    calls = []

    def fake_run(cmd, timeout=10):
        calls.append(cmd)
        out = ""
        if cmd[0] == "osascript" and "background only" in cmd[-1]:
            out = "Finder, Google Chrome, Spotify, Cursor"
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(actions, "_run", fake_run)
    m = Mac(Config())
    m._apps, m._apps_time = dict(APPS), 1e18
    m.calls = calls
    return m


# ---------------------------------------------------------------- clean()
@pytest.mark.parametrize("raw,expected", [
    ("Hey Jarvis, open Messages.", "open Messages"),
    ("can you please quit chrome for me", "quit chrome"),
    ("Lock the computer!", "Lock the computer"),
])
def test_clean(raw, expected):
    assert clean(raw) == expected


# --------------------------------------------------------------- fast path
def test_open_app(mac):
    fn = route("hey jarvis open messages", mac)
    assert fn is not None
    r = fn()
    assert r.ok and r.message == "Opened Messages"
    assert ["open", "-a", "Messages"] in mac.calls


def test_open_alias_and_fuzzy(mac):
    assert route("open chrome", mac)().message == "Opened Google Chrome"
    assert route("open spotfy", mac)().message == "Opened Spotify"      # misheard
    assert route("open vs code", mac)().message == "Opened Visual Studio Code"


def test_quit_uses_running_apps(mac):
    r = route("quit chrome", mac)()
    assert r.ok and r.message == "Quit Google Chrome"
    assert any(c[0] == "osascript" and 'quit app "Google Chrome"' in c[-1] for c in mac.calls)


def test_quit_not_running(mac):
    assert not route("quit slack", mac)().ok


def test_lock(mac):
    assert route("lock the computer", mac)().message == "Locked"
    assert route("Lock.", mac) is not None


def test_volume(mac):
    assert route("set volume to 30", mac)().message == "Volume 30%"
    assert route("mute", mac)().message == "Muted"


@pytest.mark.parametrize("text", [
    "open a new cursor window with my portfolio folder",
    "open my resume",
    "find the tax pdf from last year",
    "open the project called hey mac in cursor",
    "open blorptastic",                     # unknown app → let AI try
])
def test_complex_goes_to_ai(mac, text):
    assert route(text, mac) is None


# -------------------------------------------------------------- list picks
def test_pick_from_list():
    items = ["~/a.pdf", "~/b.pdf", "~/c.pdf"]
    assert pick_from_list("open the second one", items) == (1, False)
    assert pick_from_list("2", items) == (1, False)
    assert pick_from_list("show the last one in finder", items) == (2, True)
    assert pick_from_list("open number 9", items) is None
    assert pick_from_list("open messages", items) is None
    assert pick_from_list("open the first one", []) is None


# ------------------------------------------------------------------ misc
def test_fuzzy_best():
    assert fuzzy_best("cursed her", ["Cursor", "Calendar"], cutoff=0.5)[0] == "Cursor"
    assert fuzzy_best("zzzz", ["Cursor"])[0] is None


def test_hotkey_parse():
    assert parse_hotkey("option+space") == (49, 1 << 19)
    assert parse_hotkey("cmd+shift+j") == (38, (1 << 20) | (1 << 17))
    with pytest.raises(ValueError):
        parse_hotkey("hyper+space")


def test_config_file(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[assistant]\nname = "Friday"\n[aliases]\n"ide" = "Cursor"\n[ai]\nmodel="x"\n')
    cfg = load_config(str(p))
    assert cfg.name == "Friday" and cfg.ai_model == "x"
    assert cfg.aliases["ide"] == "Cursor" and cfg.aliases["chrome"] == "Google Chrome"


def test_find_project(mac, tmp_path):
    (tmp_path / "portfolio-site").mkdir()
    (tmp_path / "hey-mac").mkdir()
    mac.cfg.project_roots = [str(tmp_path)]
    r = mac.find_project("portfolio")
    assert r.ok and r.items[0].endswith("portfolio-site")
    r = mac.find_project("hey mac")
    assert r.ok and r.items[0].endswith("hey-mac")


def test_open_in_editor_falls_back_to_open_a(mac, tmp_path, monkeypatch):
    (tmp_path / "portfolio").mkdir()
    mac.cfg.project_roots = [str(tmp_path)]
    monkeypatch.setattr(actions.shutil, "which", lambda _: None)
    monkeypatch.setattr(actions.os.path, "isfile", lambda _: False)
    r = mac.open_in_editor("portfolio", "cursor")
    assert r.ok, r.message
    assert ["open", "-a", "Cursor", str(tmp_path / "portfolio")] in mac.calls


def test_walk_search(mac, tmp_path):
    (tmp_path / "Docs").mkdir()
    (tmp_path / "Docs" / "Christine_Resume_2026.pdf").write_text("x")
    (tmp_path / "Docs" / "notes.txt").write_text("x")
    mac.cfg.search_roots = [str(tmp_path)]
    r = mac.find_files("resume")          # mdfind is faked → empty → walk fallback
    assert r.ok and r.items[0].endswith("Christine_Resume_2026.pdf")


# ------------------------------------------------------------- AI loop
def _block(**kw):
    return SimpleNamespace(**kw)


class FakeClient:
    """Pretends to be Claude: first asks for a tool, then answers."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.messages = self

    def create(self, **kw):
        self.requests.append(kw)
        return self.script.pop(0)


def test_brain_tool_loop(mac, tmp_path):
    (tmp_path / "portfolio").mkdir()
    mac.cfg.project_roots = [str(tmp_path)]
    client = FakeClient([
        _block(stop_reason="tool_use", content=[
            _block(type="tool_use", id="t1", name="open_in_editor",
                   input={"path_or_project": "portfolio", "editor": "cursor"})]),
        _block(stop_reason="end_turn", content=[_block(type="text", text="Opened portfolio in Cursor.")]),
    ])
    brain = Brain(mac.cfg, mac, client=client)
    reply = brain.ask("open a new cursor window with my portfolio folder")
    assert reply.text == "Opened portfolio in Cursor."
    tool_result = client.requests[1]["messages"][2]["content"][0]
    assert tool_result["type"] == "tool_result" and "OK" in tool_result["content"]
    assert client.requests[0]["model"] == "claude-haiku-4-5"


def test_brain_rejects_unknown_tool(mac):
    brain = Brain(mac.cfg, mac, client=None)
    assert not brain.run_tool("installed_apps", {}).ok      # real method, but not exposed as a tool
    assert not brain.run_tool("rm_rf", {}).ok
