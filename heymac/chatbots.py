"""Send a prompt to ChatGPT / Claude / Gemini / Perplexity in Chrome.

Two ways in:
  * link   — the site accepts ?q=<prompt> (ChatGPT, Claude, Perplexity). We open it, wait for the
             page to load, then press Enter (harmless if the site already sent it).
  * paste  — no link support (Gemini) or the prompt is too long for a link. We put the prompt on
             the clipboard, open the site, paste, press Enter, then restore your clipboard.

Before touching the keyboard we check Chrome is still the front app and on the right site,
so keystrokes never land in some other window if you clicked away.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from urllib.parse import quote, urlparse

log = logging.getLogger(__name__)

BROWSER = "Google Chrome"
MAX_LINK_CHARS = 1800          # longer prompts get pasted instead (links can get truncated)


@dataclass(frozen=True)
class Site:
    name: str
    home: str                  # page to open for the paste method
    link: str | None           # link template with {q}, or None if unsupported
    host: str                  # for "am I on the right page?" checks


SITES = {
    "chatgpt": Site("ChatGPT", "https://chatgpt.com/", "https://chatgpt.com/?q={q}", "chatgpt.com"),
    "claude": Site("Claude", "https://claude.ai/new", "https://claude.ai/new?q={q}", "claude.ai"),
    "gemini": Site("Gemini", "https://gemini.google.com/app", None, "gemini.google.com"),
    "perplexity": Site("Perplexity", "https://www.perplexity.ai/", "https://www.perplexity.ai/search?q={q}",
                       "perplexity.ai"),
}

# How speech recognition tends to spell them
ALIASES = {
    "chatgpt": "chatgpt", "chat gpt": "chatgpt", "chat gbt": "chatgpt", "chatgbt": "chatgpt", "gpt": "chatgpt",
    "chat": "chatgpt", "openai": "chatgpt", "open ai": "chatgpt",
    "claude": "claude", "claud": "claude", "cloud": "claude", "clod": "claude", "clawed": "claude",
    "gemini": "gemini", "jemini": "gemini", "gemeni": "gemini", "google gemini": "gemini", "bard": "gemini",
    "perplexity": "perplexity", "perplexity ai": "perplexity",
}
BOT_PATTERN = "|".join(sorted((re.escape(a) for a in ALIASES), key=len, reverse=True))


def resolve_bot(name: str) -> str | None:
    n = re.sub(r"[^a-z ]", "", name.lower()).strip()
    return ALIASES.get(n) or ALIASES.get(n.replace(" ", ""))


# ------------------------------------------------------------------ mac glue
def _run(cmd: list[str], input: str | None = None, timeout: float = 10) -> subprocess.CompletedProcess:
    env = dict(os.environ, LANG="en_US.UTF-8")          # so pbcopy keeps emoji / accents
    return subprocess.run(cmd, input=input, capture_output=True, text=True, timeout=timeout, env=env)


def _osa(script: str) -> str:
    r = _run(["osascript", "-e", script])
    return r.stdout.strip() if r.returncode == 0 else ""


def _front_app() -> str:
    return _osa('tell application "System Events" to get name of first application process whose frontmost is true')


def _tab_state() -> tuple[bool, str]:
    """(still_loading, url) of Chrome's active tab."""
    out = _osa(f'''tell application "{BROWSER}"
        if (count of windows) = 0 then return "false|"
        set t to active tab of front window
        return ((loading of t) as string) & "|" & (URL of t)
    end tell''')
    loading, _, url = out.partition("|")
    return loading == "true", url


def _wait_for_page(host: str, timeout: float = 12.0, sleep=time.sleep) -> bool:
    deadline = time.time() + timeout
    sleep(0.6)
    while time.time() < deadline:
        loading, url = _tab_state()
        if not loading and host in (urlparse(url).netloc or ""):
            sleep(1.2)          # let the chat box finish setting itself up
            return True
        sleep(0.3)
    return False


def _safe_to_type(host: str) -> bool:
    if _front_app() != BROWSER:
        return False
    _, url = _tab_state()
    return host in (urlparse(url).netloc or "")


def _keystroke_paste():
    _osa('tell application "System Events" to keystroke "v" using command down')


def _keystroke_enter():
    _osa('tell application "System Events" to key code 36')


def _get_clipboard() -> str:
    return _run(["pbpaste"]).stdout


def _set_clipboard(text: str):
    _run(["pbcopy"], input=text)


def _set_clipboard_files(paths: list[str]) -> bool:
    """Put file references on the clipboard, exactly like Finder's Copy — pasting uploads them."""
    try:
        from AppKit import NSPasteboard, NSURL
    except ImportError:
        return False
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    return bool(pb.writeObjects_([NSURL.fileURLWithPath_(os.path.abspath(p)) for p in paths]))


def _upload_wait(paths: list[str]) -> float:
    """Rough time for the site to upload the files before Enter will work."""
    mb = sum(os.path.getsize(p) for p in paths) / 1_000_000
    return min(20.0, 2.5 + 1.2 * len(paths) + 0.6 * mb)


# ------------------------------------------------------------------ main entry
def ask(bot: str, prompt: str, send: bool = True, files: list[str] | None = None,
        sleep=time.sleep) -> tuple[bool, str]:
    """Open the chatbot in Chrome with the prompt (and files attached). Returns (ok, message)."""
    key = resolve_bot(bot) or bot.lower()
    site = SITES.get(key)
    if site is None:
        return False, f"I don't know “{bot}”. Try ChatGPT, Claude, Gemini or Perplexity."
    prompt = prompt.strip()
    if files:
        return _ask_with_files(site, prompt, files, send, sleep)
    if not prompt:
        _run(["open", "-a", BROWSER, site.home])
        return True, f"Opened {site.name}"

    use_link = site.link is not None and len(quote(prompt)) <= MAX_LINK_CHARS * 3 and len(prompt) <= MAX_LINK_CHARS
    if use_link:
        _run(["open", "-a", BROWSER, site.link.format(q=quote(prompt, safe=""))])
        if not send:
            return True, f"Put your prompt into {site.name} — press Enter to send"
        if not _wait_for_page(site.host, sleep=sleep):
            return True, f"Opened {site.name}, but it loaded slowly — press Enter if it didn't send"
        if not _safe_to_type(site.host):
            return True, f"Opened {site.name} with your prompt — didn't press Enter since you switched away"
        _keystroke_enter()
        return True, f"Asked {site.name}"

    # paste method
    saved = _get_clipboard()
    _set_clipboard(prompt)
    restore = True
    try:
        _run(["open", "-a", BROWSER, site.home])
        if not _wait_for_page(site.host, sleep=sleep):
            restore = False
            return True, f"Opened {site.name} slowly — your prompt is on the clipboard, press ⌘V"
        if not _safe_to_type(site.host):
            restore = False
            return True, f"Opened {site.name} — your prompt is on the clipboard (⌘V), since you switched away"
        _keystroke_paste()
        sleep(0.4)
        if send:
            _keystroke_enter()
        sleep(0.6)                        # let the paste finish before restoring the clipboard
    finally:
        if restore and saved:
            _set_clipboard(saved)
    return True, (f"Asked {site.name}" if send else f"Pasted your prompt into {site.name}")


# ------------------------------------------------------------- with files
MAX_FILES = 5


def _ask_with_files(site: Site, prompt: str, files: list[str], send: bool, sleep) -> tuple[bool, str]:
    """Attach files by pasting them (like copying in Finder), then paste the prompt and send."""
    paths = [os.path.expanduser(f) for f in files][:MAX_FILES]
    missing = [p for p in paths if not os.path.isfile(p)]
    if missing:
        return False, f"Can't find {os.path.basename(missing[0])} (folders can't be attached)."
    names = ", ".join(os.path.basename(p) for p in paths)

    saved = _get_clipboard()
    restore = True
    try:
        _run(["open", "-a", BROWSER, site.home])
        if not _wait_for_page(site.host, sleep=sleep):
            return True, f"Opened {site.name} but it loaded slowly — attach {names} yourself"
        for p in paths:                                   # one at a time is most reliable
            if not _safe_to_type(site.host):
                return True, f"Stopped — you switched away from {site.name} before I attached {names}"
            if not _set_clipboard_files([p]):
                return False, "Couldn't put the file on the clipboard."
            _keystroke_paste()
            sleep(1.0)
        if prompt:
            _set_clipboard(prompt)
            if not _safe_to_type(site.host):
                restore = False
                return True, f"Attached {names} — you switched away, so your prompt is on the clipboard (⌘V)"
            _keystroke_paste()
            sleep(0.5)
        if not send:
            return True, f"Attached {names} in {site.name} — press Enter when ready"
        # Enter doesn't work until uploads finish, so press it a few times; on an
        # already-sent (empty) chat box Enter does nothing, so the extra presses are harmless.
        sleep(_upload_wait(paths))
        for extra_wait in (0, 3, 6):
            sleep(extra_wait)
            if not _safe_to_type(site.host):
                return True, f"Attached {names} — press Enter in {site.name} to send"
            _keystroke_enter()
        return True, f"Sent {names} to {site.name}"
    finally:
        sleep(0.3)
        if restore and saved:
            _set_clipboard(saved)
