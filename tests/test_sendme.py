"""Send-to-myself: routing + Messages/AirDrop logic with all Mac calls faked."""
import subprocess

import pytest

from heymac import sendme
from heymac.actions import ActionResult
from heymac.commands import route


class FakeMac:
    def __init__(self):
        self.sent = None

    def send_to_me(self, **kw):
        self.sent = kw
        return ActionResult(True, "ok")

    def resolve_app(self, name, among=None):
        return None


@pytest.mark.parametrize("said,expected", [
    ("text me a note: buy milk", {"text": "Buy milk", "via": "messages"}),
    ("send myself a reminder to call mom at 5", {"text": "Reminder: call mom at 5", "via": "messages"}),
    ("Hey Jarvis, send me this page.", {"current_tab": True, "via": "messages"}),
    ("send this link to my phone", {"current_tab": True, "via": "messages"}),
    ("text myself my clipboard", {"clipboard": True, "via": "messages"}),
    ("send me what I copied", {"clipboard": True, "via": "messages"}),
    ("airdrop this page to my phone", {"current_tab": True, "via": "airdrop"}),
    ("airdrop my clipboard", {"clipboard": True, "via": "airdrop"}),
])
def test_routes(said, expected):
    mac = FakeMac()
    fn = route(said, mac)
    assert fn is not None, said
    fn()
    assert mac.sent == expected


@pytest.mark.parametrize("said", [
    "send me my cs101 hw2 instructions",       # a file → AI finds it first
    "text myself resume.pdf",
    "airdrop my resume to my phone",
    "text mom I'm running late",               # not to me
])
def test_goes_to_ai(said):
    mac = FakeMac()
    fn = route(said, mac)
    assert fn is None or mac.sent is None


# ------------------------------------------------------------ Messages logic
@pytest.fixture
def fake(monkeypatch, tmp_path):
    calls = []

    def run(cmd, timeout=20):
        calls.append(cmd)
        out = ""
        if "Google Chrome" in cmd[-1]:
            out = "Big-O cheat sheet\nhttps://example.com/bigo"
        if cmd == ["pbpaste"]:
            out = "copied thing"
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(sendme, "_run", run)
    monkeypatch.setattr(sendme, "OUTBOX", tmp_path / "Pictures" / "Hey Mac Outbox")
    return calls


def sends(calls):
    """(kind, payload) of every Messages send."""
    return [(c[4], c[5]) for c in calls if len(c) == 6 and c[0] == "osascript"]


def test_note(fake):
    ok, msg = sendme.send_to_me("+15551234567", text="Buy milk")
    assert ok and msg == "Texted you note"
    assert sends(fake) == [("text", "Buy milk")]
    assert fake[0][3] == "+15551234567"


def test_tab_and_clipboard(fake):
    sendme.send_to_me("me@icloud.com", current_tab=True)
    sendme.send_to_me("me@icloud.com", include_clipboard=True)
    assert sends(fake) == [("text", "Big-O cheat sheet\nhttps://example.com/bigo"), ("text", "copied thing")]


def test_file_is_copied_to_pictures_first(fake, tmp_path):
    f = tmp_path / "Documents" / "hw2.pdf"
    f.parent.mkdir()
    f.write_text("pdf")
    ok, msg = sendme.send_to_me("+15551234567", file_path=str(f))
    assert ok and "hw2.pdf" in msg
    kind, payload = sends(fake)[0]
    assert kind == "file" and "Pictures/Hey Mac Outbox/hw2.pdf" in payload
    assert open(payload).read() == "pdf"
    # sending again doesn't overwrite the first copy
    sendme.send_to_me("+15551234567", file_path=str(f))
    assert sends(fake)[1][1].endswith("hw2 (1).pdf")


def test_no_handle_explains(fake):
    ok, msg = sendme.send_to_me("", text="hi")
    assert not ok and "config" in msg


def test_folder_cant_be_texted(fake, tmp_path):
    ok, msg = sendme.send_to_me("+1555", file_path=str(tmp_path))
    assert not ok and "AirDrop" in msg


def test_airdrop_hands_items_to_main_thread(fake):
    ran = []
    ok, msg = sendme.send_to_me("", current_tab=True, via="airdrop", on_main_thread=lambda fn: ran.append(fn))
    assert ok and "AirDrop" in msg and len(ran) == 1      # UI work scheduled on the main thread
    assert not sends(fake)                                # nothing went through Messages