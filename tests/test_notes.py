"""Apple Notes: routing, HTML formatting and the AppleScript calls, with Notes faked."""
import subprocess

import pytest

from heymac import notes
from heymac.actions import ActionResult
from heymac.commands import route


class FakeMac:
    def __init__(self):
        self.called = None

    def add_note(self, text, title=None, folder=""):
        self.called = ("add", text)
        return ActionResult(True, "ok")

    def append_note(self, note, text):
        self.called = ("append", note, text)
        return ActionResult(True, "ok")

    def send_to_me(self, **kw):
        self.called = ("send", kw)
        return ActionResult(True, "ok")

    def resolve_app(self, name, among=None):
        return None


@pytest.mark.parametrize("said,expected", [
    ("take a note: call the dentist tomorrow", ("add", "Call the dentist tomorrow")),
    ("hey jarvis, make a note that the cs373 quiz moved to friday",
     ("add", "The cs373 quiz moved to friday")),
    ("jot down ideas for the club meeting", ("add", "Ideas for the club meeting")),
    ("note: buy a birthday card for jess", ("add", "Buy a birthday card for jess")),
    ("write remember to email the TA in my notes", ("add", "Remember to email the TA")),
    ("add eggs to my grocery list", ("append", "grocery", "Eggs")),
    ("add read chapter 4 to the cs373 note", ("append", "cs373", "Read chapter 4")),
])
def test_routes(said, expected):
    mac = FakeMac()
    fn = route(said, mac)
    assert fn is not None, said
    fn()
    assert mac.called == expected


def test_text_me_a_note_still_goes_to_messages():
    mac = FakeMac()
    route("text me a note: buy milk", mac)()
    assert mac.called[0] == "send"


# ------------------------------------------------------------ formatting
def test_html_is_escaped_and_lines_kept():
    assert notes.to_html('a < b & "c"\nsecond') == "<div>a &lt; b &amp; &quot;c&quot;</div><div>second</div>"


def test_short_text_becomes_the_title():
    assert notes.make_body("Call the dentist") == "<div><h1>Call the dentist</h1></div>"


def test_long_text_gets_short_title_and_full_body():
    body = notes.make_body("The quiz moved to Friday, so study chapters 3 and 4 on Wednesday night after the club meeting ends")
    assert body.startswith("<div><h1>The quiz moved to Friday</h1></div>")
    assert "after the club meeting ends" in body


# ------------------------------------------------------------ AppleScript calls
class FakeNotes:
    """Pretends to be osascript talking to Notes."""

    def __init__(self, notes_list=None, fail=None):
        self.notes = notes_list or []      # (id, title, folder, modified)
        self.calls = []
        self.fail = fail

    def __call__(self, script, *args, timeout=20):
        self.calls.append((script, args))
        if self.fail:
            return subprocess.CompletedProcess([], 1, "", self.fail)
        if script is notes.ADD_SCRIPT:
            return subprocess.CompletedProcess([], 0, "x-new\tNew note\tNotes\n", "")
        if script is notes.FIND_SCRIPT:
            q = args[0].lower()
            out = "".join("\t".join(n) + "\n" for n in self.notes if q in n[1].lower())
            return subprocess.CompletedProcess([], 0, out, "")
        if script is notes.APPEND_SCRIPT:
            title = next(n[1] for n in self.notes if n[0] == args[0])
            return subprocess.CompletedProcess([], 0, title, "")
        if script is notes.READ_SCRIPT:
            return subprocess.CompletedProcess([], 0, "Grocery list\nmilk\neggs\n", "")
        raise AssertionError("unexpected script")


NOTES = [
    ("x-1", "Grocery list", "Notes", "2026-10-01T09:00:00"),
    ("x-2", "Old grocery ideas", "Notes", "2026-10-10T09:00:00"),
    ("x-3", "Grocery list", "Recently Deleted", "2026-10-14T09:00:00"),
]


def test_add_note_passes_text_as_arguments():
    fake = FakeNotes()
    ok, msg = notes.add_note('Quiz "moved" & stuff', run=fake)
    assert ok and "Saved a note" in msg
    script, args = fake.calls[0]
    assert script is notes.ADD_SCRIPT
    assert "&quot;moved&quot; &amp;" in args[0] and args[1] == ""      # escaped, default folder


def test_append_picks_exact_title_and_skips_deleted():
    fake = FakeNotes(NOTES)
    ok, msg = notes.append_note("my grocery list", "Eggs", run=fake)
    assert ok and msg == "Added to “Grocery list”"
    script, args = fake.calls[-1]
    assert script is notes.APPEND_SCRIPT and args == ("x-1", "<div>Eggs</div>")


def test_append_creates_note_when_missing():
    fake = FakeNotes([])
    ok, msg = notes.append_note("packing list", "Charger", run=fake)
    assert ok and "made one" in msg
    script, args = fake.calls[-1]
    assert script is notes.ADD_SCRIPT and "<h1>Packing list</h1>" in args[0] and "Charger" in args[0]


def test_read_note():
    ok, msg = notes.read_note("grocery list", run=FakeNotes(NOTES))
    assert ok and "milk" in msg and msg.startswith("“Grocery list”")


def test_permission_error_is_explained():
    ok, msg = notes.add_note("hi", run=FakeNotes(fail="execution error: Not authorized to send Apple events to Notes. (-1743)"))
    assert not ok and "Automation" in msg


def test_note_query_cleanup():
    assert notes._note_query("my grocery list") == "grocery"
    assert notes._note_query("the CS373 notes") == "CS373"
