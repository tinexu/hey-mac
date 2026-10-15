"""Write to (and read from) Apple Notes.

  add_note      -> a new note ("take a note: call the dentist tomorrow")
  append_note   -> add a line to an existing note ("add eggs to my grocery list")
  find_notes    -> notes whose title matches, newest first
  read_note     -> the text of a note, so the AI can answer "what's on my grocery list?"

Talks to Notes with AppleScript. Text is passed as arguments (never pasted into the
script), so quotes, emoji and newlines can't break anything. macOS asks once for
permission to control Notes. Locked (password-protected) notes can't be edited.
"""
from __future__ import annotations

import html
import os
import re
import subprocess
from dataclasses import dataclass

DELETED_FOLDERS = {"recently deleted", "zuletzt gelöscht", "supprimés récemment"}

ADD_SCRIPT = '''on run argv
    set theBody to item 1 of argv
    set theFolder to item 2 of argv
    tell application "Notes"
        if theFolder is "" then
            try
                set f to default folder of default account
            on error
                set f to first folder of default account
            end try
        else
            tell default account
                try
                    set f to first folder whose name is theFolder
                on error
                    set f to make new folder with properties {name:theFolder}
                end try
            end tell
        end if
        set n to make new note at f with properties {body:theBody}
        return (id of n) & tab & (name of n) & tab & (name of f)
    end tell
end run'''

# One line per note whose title contains the query: id, title, folder, modified (ISO).
FIND_SCRIPT = '''on run argv
    set q to item 1 of argv
    set out to ""
    tell application "Notes"
        repeat with n in (notes whose name contains q)
            set folderName to ""
            try
                set folderName to name of container of n
            end try
            set out to out & (id of n) & tab & (name of n) & tab & folderName & tab & ¬
                ((modification date of n) as «class isot» as string) & linefeed
        end repeat
    end tell
    return out
end run'''

APPEND_SCRIPT = '''on run argv
    set theId to item 1 of argv
    set addition to item 2 of argv
    tell application "Notes"
        set n to note id theId
        set body of n to (body of n) & addition
        return name of n
    end tell
end run'''

READ_SCRIPT = '''on run argv
    tell application "Notes" to return plaintext of note id (item 1 of argv)
end run'''


@dataclass
class NoteRef:
    id: str
    title: str
    folder: str
    modified: str          # ISO 8601, sorts correctly as text


def _osascript(script: str, *args: str, timeout: float = 20) -> subprocess.CompletedProcess:
    env = dict(os.environ, LANG="en_US.UTF-8")
    return subprocess.run(["osascript", "-e", script, *args], capture_output=True, text=True,
                          timeout=timeout, env=env)


def _error(r: subprocess.CompletedProcess) -> str:
    err = r.stderr.strip()
    if "-1743" in err or "Not authorized" in err:
        return ("macOS is blocking access to Notes. Allow it in System Settings → Privacy & Security "
                "→ Automation → (Terminal or Hey Mac) → Notes.")
    if "locked" in err.lower() or "-10003" in err:
        return "That note is locked with a password, so I can't edit it."
    return f"Notes error: {err[:160] or 'unknown'}"


# ------------------------------------------------------------------ formatting
def to_html(text: str) -> str:
    """Plain text -> the HTML Notes stores. Each line becomes its own line in the note."""
    lines = text.strip().splitlines() or [""]
    return "".join(f"<div>{html.escape(line) or '<br>'}</div>" for line in lines)


def make_body(text: str, title: str | None = None) -> str:
    """Notes uses the first line as the note's title."""
    text = text.strip()
    rest = text
    if not title:
        first, _, rest = text.partition("\n")
        if len(first) > 60:                       # long one-liner: short title + full text below
            title = re.split(r"(?<=[.!?])\s|,\s", first, maxsplit=1)[0][:60].rstrip(" ,.")
            rest = text
        else:
            title, rest = first, rest
    head = f"<div><h1>{html.escape(title)}</h1></div>"
    return head + (to_html(rest) if rest.strip() else "")


# ------------------------------------------------------------------ actions
def add_note(text: str, title: str | None = None, folder: str = "", run=_osascript) -> tuple[bool, str]:
    if not text.strip() and not title:
        return False, "Nothing to write."
    r = run(ADD_SCRIPT, make_body(text, title), folder)
    if r.returncode != 0:
        return False, _error(r)
    _, name, where = (r.stdout.strip().split("\t") + ["", "", ""])[:3]
    return True, f"Saved a note “{name}”" + (f" in {where}" if where else "")


def find_notes(query: str, run=_osascript) -> list[NoteRef]:
    r = run(FIND_SCRIPT, query.strip())
    if r.returncode != 0:
        raise RuntimeError(_error(r))
    found = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 4 and parts[2].strip().lower() not in DELETED_FOLDERS:
            found.append(NoteRef(*[p.strip() for p in parts[:4]]))
    return found


def best_match(query: str, notes: list[NoteRef]) -> NoteRef | None:
    """Exact title first, then titles starting with the query, then most recently edited."""
    if not notes:
        return None
    q = query.strip().lower()

    def score(n: NoteRef):
        t = n.title.strip().lower()
        return (t == q, t.startswith(q), n.modified)
    return max(notes, key=score)


def _note_query(name: str) -> str:
    """'my grocery list' -> 'grocery'; 'the cs373 notes' -> 'cs373'."""
    q = re.sub(r"^(?:my|the|a)\s+", "", name.strip(), flags=re.I)
    q = re.sub(r"\s+(?:note|notes|list)$", "", q, flags=re.I)
    return q.strip() or name.strip()


def append_note(note: str, text: str, create_if_missing: bool = True, run=_osascript) -> tuple[bool, str]:
    if not text.strip():
        return False, "Nothing to add."
    query = _note_query(note)
    try:
        target = best_match(query, find_notes(query, run=run))
    except RuntimeError as e:
        return False, str(e)
    if target is None:
        if not create_if_missing:
            return False, f"No note called “{query}”."
        title = note.strip()
        title = re.sub(r"^(?:my|the)\s+", "", title, flags=re.I)
        title = title[:1].upper() + title[1:]
        ok, msg = add_note(text, title=title, run=run)
        return ok, (f"No “{query}” note yet, so I made one and added it" if ok else msg)
    r = run(APPEND_SCRIPT, target.id, to_html(text))
    if r.returncode != 0:
        return False, _error(r)
    return True, f"Added to “{target.title}”"


def read_note(note: str, run=_osascript, max_chars: int = 3000) -> tuple[bool, str]:
    query = _note_query(note)
    try:
        target = best_match(query, find_notes(query, run=run))
    except RuntimeError as e:
        return False, str(e)
    if target is None:
        return False, f"No note called “{query}”."
    r = run(READ_SCRIPT, target.id)
    if r.returncode != 0:
        return False, _error(r)
    return True, f"“{target.title}”:\n{r.stdout.strip()[:max_chars]}"