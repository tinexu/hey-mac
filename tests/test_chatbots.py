"""Chatbot feature: routing + the Chrome automation logic, with all Mac calls faked."""
from urllib.parse import unquote

import pytest

from heymac import chatbots
from heymac.actions import ActionResult
from heymac.commands import route


class FakeMac:
    def __init__(self):
        self.asked = None

    def ask_chatbot(self, bot, prompt, send=True, file_path=None):
        self.asked = (chatbots.resolve_bot(bot), prompt)
        return ActionResult(True, "ok")

    def resolve_app(self, name, among=None):
        return None


@pytest.mark.parametrize("said,bot,prompt", [
    ("Hey Jarvis, ask Claude how to reverse a linked list in Python.", "claude",
     "how to reverse a linked list in Python"),
    ("ask chat gpt to write a haiku about finals", "chatgpt", "write a haiku about finals"),
    ("ask cloud what's the difference between TCP and UDP", "claude", "what's the difference between TCP and UDP"),
    ("open gemini and ask it what the weather's like in Tokyo", "gemini", "what the weather's like in Tokyo"),
    ("search best ramen near purdue on perplexity", "perplexity", "best ramen near purdue"),
    ("tell ChatGPT: explain big O like I'm five", "chatgpt", "explain big O like I'm five"),
])
def test_routes(said, bot, prompt):
    mac = FakeMac()
    fn = route(said, mac)
    assert fn is not None, said
    fn()
    assert mac.asked == (bot, prompt)


@pytest.mark.parametrize("said", [
    "ask claude to summarize my cs373 hw2 instructions",     # needs file search → AI
    "ask chatgpt to fix the bug in this code",
    "open messages",                                           # not a chatbot at all
])
def test_not_fast_path(said):
    mac = FakeMac()
    fn = route(said, mac)
    assert fn is None or mac.asked is None


# ------------------------------------------------------------- automation
@pytest.fixture
def fake_mac_env(monkeypatch):
    state = {"front": "Google Chrome", "url": "", "clip": "my old clipboard", "log": []}

    def run(cmd, input=None, timeout=10):
        import subprocess
        state["log"].append(("run", cmd[0], cmd[-1] if cmd[0] == "open" else input))
        if cmd[:1] == ["open"]:
            state["url"] = cmd[-1]
        if cmd == ["pbpaste"]:
            return subprocess.CompletedProcess(cmd, 0, state["clip"], "")
        if cmd == ["pbcopy"]:
            state["clip"] = input
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(chatbots, "_run", run)
    monkeypatch.setattr(chatbots, "_front_app", lambda: state["front"])
    monkeypatch.setattr(chatbots, "_tab_state", lambda: (False, state["url"]))
    monkeypatch.setattr(chatbots, "_keystroke_enter", lambda: state["log"].append(("key", "enter")))
    monkeypatch.setattr(chatbots, "_keystroke_paste", lambda: state["log"].append(("key", "paste", state["clip"])))
    return state


def keys(state):
    return [e[1:] for e in state["log"] if e[0] == "key"]


def test_claude_link_then_enter(fake_mac_env):
    ok, msg = chatbots.ask("claude", "how do I center a div?", sleep=lambda s: None)
    assert ok and msg == "Asked Claude"
    url = fake_mac_env["url"]
    assert url.startswith("https://claude.ai/new?q=") and unquote(url.split("q=")[1]) == "how do I center a div?"
    assert keys(fake_mac_env) == [("enter",)]


def test_gemini_pastes_and_restores_clipboard(fake_mac_env):
    ok, msg = chatbots.ask("gemini", "plan a 3 day trip to Chicago ✈️", sleep=lambda s: None)
    assert ok and msg == "Asked Gemini"
    assert fake_mac_env["url"] == "https://gemini.google.com/app"
    assert keys(fake_mac_env) == [("paste", "plan a 3 day trip to Chicago ✈️"), ("enter",)]
    assert fake_mac_env["clip"] == "my old clipboard"


def test_long_prompt_uses_paste_even_for_chatgpt(fake_mac_env):
    ok, _ = chatbots.ask("chatgpt", "x" * 5000, sleep=lambda s: None)
    assert ok and fake_mac_env["url"] == "https://chatgpt.com/"
    assert keys(fake_mac_env)[0][0] == "paste"


def test_no_typing_if_you_switched_apps(fake_mac_env):
    fake_mac_env["front"] = "Messages"                     # you clicked away while it loaded
    ok, msg = chatbots.ask("gemini", "hello", sleep=lambda s: None)
    assert keys(fake_mac_env) == []                        # nothing typed into Messages!
    assert "clipboard" in msg and fake_mac_env["clip"] == "hello"   # prompt left for you to paste

    fake_mac_env["log"].clear()
    ok, msg = chatbots.ask("claude", "hello", sleep=lambda s: None)
    assert keys(fake_mac_env) == [] and "switched away" in msg


def test_unknown_bot():
    ok, msg = chatbots.ask("siri", "hi")
    assert not ok


def test_text_mode_puts_file_text_in_prompt(monkeypatch, tmp_path):
    from heymac.actions import Mac
    from heymac.config import Config
    f = tmp_path / "hw2.md"
    f.write_text("Problem 1: prove the lemma.")
    sent = {}
    monkeypatch.setattr(chatbots, "ask", lambda bot, prompt, send=True, files=None:
                        sent.update(prompt=prompt, files=files) or (True, "ok"))
    Mac(Config()).ask_chatbot("claude", "Summarize this", file_paths=[str(f)], attach_as="text")
    assert "Problem 1: prove the lemma." in sent["prompt"] and sent["files"] is None


def test_default_is_real_attachment(monkeypatch, tmp_path):
    from heymac.actions import Mac
    from heymac.config import Config
    f = tmp_path / "resume.pdf"
    f.write_bytes(b"%PDF")
    sent = {}
    monkeypatch.setattr(chatbots, "ask", lambda bot, prompt, send=True, files=None:
                        sent.update(prompt=prompt, files=files) or (True, "ok"))
    Mac(Config()).ask_chatbot("chatgpt", "Give feedback on the attached resume", file_paths=[str(f)])
    assert sent == {"prompt": "Give feedback on the attached resume", "files": [str(f)]}


# ------------------------------------------------------- attachment automation
@pytest.fixture
def files_env(fake_mac_env, monkeypatch):
    def set_files(paths):
        fake_mac_env["clip"] = ("FILES", tuple(paths))
        return True
    monkeypatch.setattr(chatbots, "_set_clipboard_files", set_files)
    return fake_mac_env


def test_attach_files_then_prompt_then_enter(files_env, tmp_path):
    a = tmp_path / "resume.pdf"; a.write_bytes(b"x" * 1000)
    b = tmp_path / "shot.png"; b.write_bytes(b"x" * 1000)
    ok, msg = chatbots.ask("claude", "What do you think?", files=[str(a), str(b)], sleep=lambda s: None)
    assert ok and msg == "Sent resume.pdf, shot.png to Claude"
    assert files_env["url"] == "https://claude.ai/new"            # opened plain page, not ?q=
    k = keys(files_env)
    assert k[0] == ("paste", ("FILES", (str(a),)))                   # file 1 attached
    assert k[1] == ("paste", ("FILES", (str(b),)))                   # file 2 attached
    assert k[2] == ("paste", "What do you think?")                   # then the prompt
    assert k[3:] == [("enter",)] * 3                                 # Enter until the upload is done
    assert files_env["clip"] == "my old clipboard"                   # clipboard restored


def test_attach_stops_if_you_switch_away(files_env, tmp_path):
    a = tmp_path / "resume.pdf"; a.write_bytes(b"x")
    files_env["front"] = "Notes"
    ok, msg = chatbots.ask("chatgpt", "hi", files=[str(a)], sleep=lambda s: None)
    assert keys(files_env) == [] and "switched away" in msg


def test_missing_file(files_env):
    ok, msg = chatbots.ask("gemini", "hi", files=["/nope/x.pdf"], sleep=lambda s: None)
    assert not ok and "x.pdf" in msg


@pytest.mark.parametrize("said", [
    "ask chatgpt what's in my latest screenshot",
    "ask claude to give feedback on my resume",
    "ask gemini to explain my downloads",
])
def test_file_prompts_go_to_ai(said):
    mac = FakeMac()
    fn = route(said, mac)
    assert fn is None or mac.asked is None


def test_recent_files(tmp_path):
    import os
    import time
    from heymac.smartsearch import recent_files
    dl = tmp_path / "Downloads"; dl.mkdir()
    old = dl / "old.pdf"; old.write_text("x"); os.utime(old, (time.time() - 5000, time.time() - 5000))
    new = dl / "new.pdf"; new.write_text("x")
    (dl / ".DS_Store").write_text("")
    assert recent_files("download", home=str(tmp_path)) == [str(new), str(old)]
    desk = tmp_path / "Desktop"; desk.mkdir()
    shot = desk / "Screenshot 2026-09-30 at 10.00.00.png"; shot.write_text("x")
    (desk / "notes.txt").write_text("x")
    assert recent_files("screenshot", home=str(tmp_path), mdfind=lambda q, h: []) == [str(shot)]
