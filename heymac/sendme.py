"""Send things to yourself: iMessage (fully automatic) or AirDrop (opens the AirDrop picker).

What you can send: a note, a file, the page open in Chrome, or whatever's on your clipboard.

Messages quirk (macOS Sonoma/Sequoia): files sent by script only go through if they live in
~/Pictures, so files are copied to ~/Pictures/Hey Mac Outbox first (cleaned up after a day).
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

OUTBOX = Path.home() / "Pictures" / "Hey Mac Outbox"

SEND_SCRIPT = '''on run argv
    set theHandle to item 1 of argv
    set theKind to item 2 of argv
    set thePayload to item 3 of argv
    tell application "Messages"
        set svc to 1st account whose service type = iMessage
        try
            set p to participant theHandle of svc
        on error
            set p to buddy theHandle of svc
        end try
        if theKind is "file" then
            send (POSIX file thePayload) to p
        else
            send thePayload to p
        end if
    end tell
end run'''

MY_CARD_SCRIPT = '''tell application "Contacts"
    set out to ""
    try
        set out to value of first phone of my card
    end try
    if out is "" then
        try
            set out to value of first email of my card
        end try
    end if
    return out
end tell'''


def _run(cmd: list[str], timeout: float = 20) -> subprocess.CompletedProcess:
    env = dict(os.environ, LANG="en_US.UTF-8")
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)


# ---------------------------------------------------------------- sources
def chrome_tab() -> tuple[str, str]:
    """(title, url) of the page open in Chrome, or ('', '')."""
    r = _run(["osascript", "-e", '''tell application "Google Chrome"
        if (count of windows) = 0 then return ""
        set t to active tab of front window
        return (title of t) & "\n" & (URL of t)
    end tell'''])
    title, _, url = r.stdout.strip().partition("\n")
    return title, url


def clipboard() -> str:
    return _run(["pbpaste"]).stdout


# ------------------------------------------------------------------ who am I
_cached_me: str | None = None


def my_handle(configured: str = "") -> str:
    """Your phone number or Apple ID email: from config, else from your own card in Contacts."""
    global _cached_me
    if configured.strip():
        return configured.strip()
    if _cached_me is None:
        r = _run(["osascript", "-e", MY_CARD_SCRIPT])
        _cached_me = re.sub(r"[^\d+@.\w-]", "", r.stdout.strip()) if r.returncode == 0 else ""
    return _cached_me


# ------------------------------------------------------------------ Messages
def _stage_file(path: str) -> str:
    """Copy into ~/Pictures/Hey Mac Outbox so Messages is allowed to send it."""
    OUTBOX.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - 86400
    for old in OUTBOX.iterdir():                       # tidy up yesterday's copies
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass
    src = Path(os.path.expanduser(path))
    dest = OUTBOX / src.name
    n = 1
    while dest.exists():
        dest = OUTBOX / f"{src.stem} ({n}){src.suffix}"
        n += 1
    shutil.copy2(src, dest)
    return str(dest)


def imessage(handle: str, text: str | None = None, file_path: str | None = None) -> tuple[bool, str]:
    if not handle:
        return False, ("I don't know your number yet — add `me = \"+1…\"` under [send] in your config, "
                       "or set up your own card in Contacts.")
    sent = []
    if text:
        r = _run(["osascript", "-e", SEND_SCRIPT, handle, "text", text])
        if r.returncode != 0:
            return False, f"Messages couldn't send: {r.stderr.strip()[:160]}"
        sent.append("note")
    if file_path:
        p = os.path.expanduser(file_path)
        if not os.path.isfile(p):
            return False, f"{file_path} isn't a file (folders can't be texted — try AirDrop)."
        staged = _stage_file(p)
        r = _run(["osascript", "-e", SEND_SCRIPT, handle, "file", staged], timeout=60)
        if r.returncode != 0:
            return False, f"Messages couldn't send the file: {r.stderr.strip()[:160]}"
        sent.append(os.path.basename(p))
    if not sent:
        return False, "Nothing to send."
    return True, "Texted you " + " + ".join(sent)


# ------------------------------------------------------------------- AirDrop
def airdrop(items: list[str], on_main_thread=None) -> tuple[bool, str]:
    """Opens the AirDrop picker with the items (file paths, web links or text). You tap your phone."""
    def show():
        from AppKit import NSApp, NSSharingService, NSURL
        objs = []
        for it in items:
            p = os.path.expanduser(it)
            if os.path.exists(p):
                objs.append(NSURL.fileURLWithPath_(p))
            elif re.match(r"^https?://", it):
                objs.append(NSURL.URLWithString_(it))
            else:
                objs.append(it)
        svc = NSSharingService.sharingServiceNamed_("com.apple.share.AirDrop.send")
        if svc is None or not svc.canPerformWithItems_(objs):
            log.warning("AirDrop not available for %s", items)
            return
        NSApp.activateIgnoringOtherApps_(True)
        svc.performWithItems_(objs)

    if not items:
        return False, "Nothing to AirDrop."
    if on_main_thread:
        on_main_thread(show)
    else:
        show()
    return True, "AirDrop is open — tap your phone"


# --------------------------------------------------------------------- main
def send_to_me(handle: str, text: str | None = None, file_path: str | None = None,
               current_tab: bool = False, include_clipboard: bool = False,
               via: str = "messages", on_main_thread=None) -> tuple[bool, str]:
    parts = [text.strip()] if text and text.strip() else []
    if current_tab:
        title, url = chrome_tab()
        if not url:
            return False, "Chrome doesn't have a page open."
        parts.append(f"{title}\n{url}" if via == "messages" else url)
    if include_clipboard:
        clip = clipboard().strip()
        if not clip:
            return False, "Your clipboard is empty (or has an image — copy it as a file instead)."
        parts.append(clip)

    if via == "airdrop":
        items = parts + ([file_path] if file_path else [])
        return airdrop(items, on_main_thread)
    return imessage(handle, "\n\n".join(parts) or None, file_path)
