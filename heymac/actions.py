"""Everything the assistant can actually do on the Mac.

Each action returns an ActionResult. Nothing here runs arbitrary shell commands
from the AI — only these fixed, safe operations.
"""
from __future__ import annotations

import datetime as _dt
import difflib
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config

APP_DIRS = [
    "/Applications",
    "/Applications/Utilities",
    "/System/Applications",
    "/System/Applications/Utilities",
    os.path.expanduser("~/Applications"),
]

SKIP_DIR_PARTS = {
    "Library", "node_modules", ".git", ".venv", "venv", "__pycache__", ".Trash",
    "Caches", ".cache", "build", "dist", ".next", "Pods",
}

EDITORS = {
    "cursor": ("Cursor", ["cursor", "/Applications/Cursor.app/Contents/Resources/app/bin/cursor"]),
    "vscode": ("Visual Studio Code", ["code", "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"]),
    "zed": ("Zed", ["zed"]),
    "xcode": ("Xcode", []),
}


@dataclass
class ActionResult:
    ok: bool
    message: str
    items: list[str] = field(default_factory=list)   # e.g. file paths found

    def for_ai(self) -> str:
        s = ("OK: " if self.ok else "FAILED: ") + self.message
        if self.items:
            s += "\n" + "\n".join(f"{i + 1}. {p}" for i, p in enumerate(self.items))
        return s


def _run(cmd: list[str], timeout: float = 10) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _osascript(script: str) -> subprocess.CompletedProcess:
    return _run(["osascript", "-e", script])


def _as_str(s: str) -> str:
    """Escape a Python string for use inside an AppleScript string literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def fuzzy_best(query: str, choices: list[str], cutoff: float = 0.6) -> tuple[str | None, float]:
    """Best fuzzy match; favours exact / prefix / substring matches."""
    q = _norm(query)
    if not q or not choices:
        return None, 0.0
    best, best_score = None, 0.0
    for c in choices:
        n = _norm(c)
        if not n:
            continue
        if n == q:
            score = 1.0
        elif n.replace(" ", "") == q.replace(" ", ""):
            score = 0.98
        elif n.startswith(q) or q.startswith(n):
            score = 0.9 * min(len(q), len(n)) / max(len(q), len(n)) + 0.1
        elif q in n.split() or q in n:
            score = 0.8
        else:
            score = difflib.SequenceMatcher(None, q, n).ratio()
        if score > best_score:
            best, best_score = c, score
    return (best, best_score) if best_score >= cutoff else (None, best_score)


class Mac:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._apps: dict[str, str] | None = None   # app name -> .app path
        self._apps_time = 0.0

    # ---------------------------------------------------------------- apps
    def installed_apps(self) -> dict[str, str]:
        if self._apps is None or time.time() - self._apps_time > 600:
            apps: dict[str, str] = {}
            for d in APP_DIRS:
                try:
                    for entry in os.scandir(d):
                        if entry.name.endswith(".app"):
                            apps.setdefault(entry.name[:-4], entry.path)
                except OSError:
                    continue
            self._apps, self._apps_time = apps, time.time()
        return self._apps

    def running_apps(self) -> list[str]:
        r = _osascript('tell application "System Events" to get name of every process whose background only is false')
        if r.returncode != 0:
            return []
        return [n.strip() for n in r.stdout.split(",") if n.strip()]

    def resolve_app(self, spoken: str, among: list[str] | None = None) -> str | None:
        spoken = spoken.strip().rstrip(".!?").strip()
        alias = self.cfg.aliases.get(spoken.lower())
        choices = among if among is not None else list(self.installed_apps())
        if alias:
            if among is None or alias in among:
                return alias
            spoken = alias
        name, _ = fuzzy_best(spoken, choices, cutoff=0.72)
        return name

    def open_app(self, name: str) -> ActionResult:
        app = self.resolve_app(name)
        if not app:
            return ActionResult(False, f"Couldn't find an app called “{name}”.")
        r = _run(["open", "-a", app])
        if r.returncode != 0:
            return ActionResult(False, f"Couldn't open {app}: {r.stderr.strip()}")
        return ActionResult(True, f"Opened {app}")

    def quit_app(self, name: str) -> ActionResult:
        running = self.running_apps()
        app = self.resolve_app(name, among=running) if running else self.resolve_app(name)
        if not app:
            return ActionResult(False, f"“{name}” doesn't seem to be running.")
        r = _osascript(f'quit app "{_as_str(app)}"')
        if r.returncode != 0:
            return ActionResult(False, f"Couldn't quit {app}: {r.stderr.strip()}")
        return ActionResult(True, f"Quit {app}")

    def hide_app(self, name: str) -> ActionResult:
        running = self.running_apps()
        app = self.resolve_app(name, among=running)
        if not app:
            return ActionResult(False, f"“{name}” isn't running.")
        _osascript(f'tell application "System Events" to set visible of process "{_as_str(app)}" to false')
        return ActionResult(True, f"Hid {app}")

    # -------------------------------------------------------------- system
    def lock_screen(self) -> ActionResult:
        # ⌃⌘Q is the system "Lock Screen" shortcut (needs Accessibility permission).
        r = _osascript('tell application "System Events" to keystroke "q" using {control down, command down}')
        if r.returncode != 0:
            _run(["pmset", "displaysleepnow"])
        return ActionResult(True, "Locked")

    def sleep(self) -> ActionResult:
        _run(["pmset", "sleepnow"])
        return ActionResult(True, "Going to sleep")

    def set_volume(self, level: int | None = None, change: int | None = None, mute: bool | None = None) -> ActionResult:
        if mute is not None:
            _osascript(f"set volume output muted {'true' if mute else 'false'}")
            return ActionResult(True, "Muted" if mute else "Unmuted")
        if change is not None:
            r = _osascript("output volume of (get volume settings)")
            try:
                cur = int(r.stdout.strip())
            except ValueError:
                cur = 50
            level = cur + change
        level = max(0, min(100, int(level if level is not None else 50)))
        _osascript(f"set volume output volume {level} without output muted")
        return ActionResult(True, f"Volume {level}%")

    def media(self, command: str) -> ActionResult:
        """play / pause / next / previous in Spotify or Music (whichever is running)."""
        cmd = {"play": "play", "pause": "pause", "toggle": "playpause", "next": "next track",
               "previous": "previous track"}.get(command, "playpause")
        running = self.running_apps()
        for app in ("Spotify", "Music"):
            if app in running:
                _osascript(f'tell application "{app}" to {cmd}')
                return ActionResult(True, f"{app}: {command}")
        return ActionResult(False, "Spotify or Music isn't open.")

    def status(self) -> ActionResult:
        now = _dt.datetime.now().strftime("%-I:%M %p, %A %B %-d")
        batt = _run(["pmset", "-g", "batt"]).stdout
        m = re.search(r"(\d+)%;\s*([a-zA-Z ]+);", batt)
        b = f" · battery {m.group(1)}% ({m.group(2).strip()})" if m else ""
        return ActionResult(True, f"It's {now}{b}")

    def open_url(self, url: str) -> ActionResult:
        if not re.match(r"^[a-z]+://", url):
            url = "https://" + url
        _run(["open", url])
        return ActionResult(True, f"Opened {url}")

    def web_search(self, query: str) -> ActionResult:
        from urllib.parse import quote_plus
        _run(["open", f"https://www.google.com/search?q={quote_plus(query)}"])
        return ActionResult(True, f"Searched the web for “{query}”")

    # --------------------------------------------------------------- files
    def find_files(self, query: str, kind: str | None = None, limit: int = 8,
                   search_contents: bool = False) -> ActionResult:
        """Find files by (fuzzy) name using Spotlight, falling back to a folder walk.

        kind: optional 'folder', 'pdf', 'image', 'document', 'presentation', 'spreadsheet', 'code'
        """
        words = [w for w in re.split(r"[\s_\-.]+", query.strip()) if w]
        if not words:
            return ActionResult(False, "Nothing to search for.")
        home = str(Path.home())

        name_clause = " && ".join(f'kMDItemFSName == "*{_md_escape(w)}*"cd' for w in words)
        kind_clause = _KIND_CLAUSES.get((kind or "").lower())
        md_query = f"({name_clause})" + (f" && ({kind_clause})" if kind_clause else "")
        paths = _mdfind(["-onlyin", home, md_query])

        if search_contents or len(paths) < 3:
            # Spotlight full-text search (matches words inside documents too)
            paths += [p for p in _mdfind(["-onlyin", home, query]) if p not in paths]

        if not paths:
            paths = self._walk_search(words)

        paths = [p for p in paths if not (set(Path(p).parts) & SKIP_DIR_PARTS)]
        ranked = sorted(paths, key=lambda p: _file_rank(p, query), reverse=True)[:limit]
        if not ranked:
            return ActionResult(False, f"No files matching “{query}”.")
        pretty = [p.replace(home, "~", 1) for p in ranked]
        return ActionResult(True, f"Found {len(ranked)} match(es) for “{query}”", pretty)

    def _walk_search(self, words: list[str], max_results: int = 200) -> list[str]:
        out: list[str] = []
        lw = [w.lower() for w in words]
        for root in self.cfg.search_paths:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_PARTS and not d.startswith(".")]
                if dirpath.count(os.sep) - str(root).count(os.sep) > 6:
                    dirnames[:] = []
                for name in filenames + dirnames:
                    n = name.lower()
                    if all(w in n for w in lw):
                        out.append(os.path.join(dirpath, name))
                        if len(out) >= max_results:
                            return out
        return out

    def smart_find(self, name_terms: list[str], content_terms: list[str] | None = None,
                   folder_terms: list[str] | None = None, file_types: list[str] | None = None,
                   limit: int = 8) -> ActionResult:
        """For vague requests: 'the instructions for my cs373 hw2'."""
        from . import smartsearch
        results = smartsearch.smart_find(
            name_terms, content_terms, folder_terms, file_types,
            fallback_roots=[str(p) for p in self.cfg.search_paths], limit=limit)
        if not results:
            return ActionResult(False, "No likely files found.")
        home = str(Path.home())
        items = [p.replace(home, "~", 1) for p, _, _ in results]
        why = "; ".join(f"{i + 1}: matched {', '.join(r) or 'weakly'}" for i, (_, _, r) in enumerate(results))
        return ActionResult(True, f"{len(items)} candidate(s) ({why})", items)

    def peek_file(self, path: str) -> ActionResult:
        """Read the beginning of a file to check what it is."""
        from . import smartsearch
        text = smartsearch.peek_file(path)
        return ActionResult(True, f"Start of {path}:\n{text}")

    def ask_chatbot(self, bot: str, prompt: str, send: bool = True, file_paths: list[str] | None = None,
                    file_path: str | None = None, attach_as: str = "file") -> ActionResult:
        """Open ChatGPT/Claude/Gemini/Perplexity in Chrome, attach files, type the prompt, send."""
        from . import chatbots, smartsearch
        paths = list(file_paths or []) + ([file_path] if file_path else [])
        if paths and attach_as == "text":            # paste the file's text into the prompt instead
            for p in paths:
                prompt += f"\n\n--- {os.path.basename(p)} ---\n{smartsearch.peek_file(p, max_chars=12000)}"
            paths = []
        ok, msg = chatbots.ask(bot, prompt, send=send, files=paths or None)
        return ActionResult(ok, msg)

    def recent_files(self, kind: str = "any", limit: int = 5) -> ActionResult:
        """Newest screenshots / downloads / images / PDFs / documents."""
        from . import smartsearch
        found = smartsearch.recent_files(kind, limit)
        if not found:
            return ActionResult(False, f"No recent {kind} files.")
        home = str(Path.home())
        return ActionResult(True, f"Newest {kind} files (newest first)", [p.replace(home, "~", 1) for p in found])

    def send_to_me(self, text: str | None = None, file_path: str | None = None, current_tab: bool = False,
                   clipboard: bool = False, via: str = "messages") -> ActionResult:
        """Text yourself (iMessage) or AirDrop a note, file, the Chrome page, or the clipboard."""
        from . import sendme
        on_main = None
        try:
            from AppKit import NSApp
            from PyObjCTools import AppHelper
            if NSApp() is not None and NSApp().isRunning():
                on_main = AppHelper.callAfter
        except Exception:  # noqa: BLE001  (not on a Mac / no UI running)
            pass
        handle = sendme.my_handle(self.cfg.my_handle) if via != "airdrop" else ""
        ok, msg = sendme.send_to_me(handle, text, file_path, current_tab, clipboard, via, on_main)
        return ActionResult(ok, msg)

        # --------------------------------------------------------------- notes
    def add_note(self, text: str, title: str | None = None, folder: str = "") -> ActionResult:
        """New note in Apple Notes."""
        from . import notes
        ok, msg = notes.add_note(text, title=title, folder=folder)
        return ActionResult(ok, msg)

    def append_note(self, note: str, text: str) -> ActionResult:
        """Add a line to an existing note (made if it doesn't exist yet)."""
        from . import notes
        ok, msg = notes.append_note(note, text)
        return ActionResult(ok, msg)

    def read_note(self, note: str) -> ActionResult:
        """The text of a note, for questions like "what's on my grocery list?"."""
        from . import notes
        ok, msg = notes.read_note(note)
        return ActionResult(ok, msg)

    def open_path(self, path: str, reveal: bool = False) -> ActionResult:
        p = os.path.expanduser(path)
        if not os.path.exists(p):
            return ActionResult(False, f"{path} doesn't exist.")
        _run(["open", "-R", p] if reveal else ["open", p])
        return ActionResult(True, ("Showed in Finder: " if reveal else "Opened ") + path)

    # ------------------------------------------------------------ projects
    def list_projects(self) -> list[str]:
        out = []
        for root in self.cfg.project_paths:
            try:
                for e in os.scandir(root):
                    if e.is_dir() and not e.name.startswith("."):
                        out.append(e.path)
                        # one level deeper (e.g. ~/code/work/api)
                        try:
                            for e2 in os.scandir(e.path):
                                if e2.is_dir() and not e2.name.startswith(".") and e2.name not in SKIP_DIR_PARTS:
                                    out.append(e2.path)
                        except OSError:
                            pass
            except OSError:
                continue
        return out

    def find_project(self, name: str) -> ActionResult:
        projects = self.list_projects()
        by_name: dict[str, str] = {}
        for p in projects:
            by_name.setdefault(os.path.basename(p), p)
        best, score = fuzzy_best(name, list(by_name), cutoff=0.55)
        if best:
            return ActionResult(True, f"Project “{best}”", [by_name[best]])
        # fall back to Spotlight: any folder in ~ with that name
        words = [w for w in re.split(r"[\s_\-]+", name) if w]
        q = " && ".join(f'kMDItemFSName == "*{_md_escape(w)}*"cd' for w in words)
        folders = _mdfind(["-onlyin", str(Path.home()), f"({q}) && kMDItemContentType == public.folder"])
        folders = [f for f in folders if not (set(Path(f).parts) & SKIP_DIR_PARTS)]
        folders.sort(key=lambda p: _file_rank(p, name), reverse=True)
        if folders:
            return ActionResult(True, f"Folder(s) matching “{name}”", folders[:5])
        return ActionResult(False, f"No project called “{name}” in {', '.join(self.cfg.project_roots)}")

    def open_in_editor(self, path_or_project: str, editor: str = "cursor", new_window: bool = True) -> ActionResult:
        path = os.path.expanduser(path_or_project)
        if not os.path.exists(path):
            found = self.find_project(path_or_project)
            if not found.ok:
                return found
            path = os.path.expanduser(found.items[0])
        editor = editor.lower().replace(" ", "")
        editor = {"code": "vscode", "visualstudiocode": "vscode"}.get(editor, editor)
        app, clis = EDITORS.get(editor, (editor, []))
        for cli in clis:
            exe = shutil.which(cli) or (cli if os.path.isfile(cli) else None)
            if exe:
                args = [exe] + (["-n"] if new_window and editor in ("cursor", "vscode") else []) + [path]
                subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return ActionResult(True, f"Opened {os.path.basename(path)} in {app}")
        r = _run(["open", "-a", app, path])
        if r.returncode != 0:
            return ActionResult(False, f"Couldn't open {app}: {r.stderr.strip()}")
        return ActionResult(True, f"Opened {os.path.basename(path)} in {app}")


# ------------------------------------------------------------------ helpers
_KIND_CLAUSES = {
    "folder": "kMDItemContentType == public.folder",
    "pdf": "kMDItemContentType == com.adobe.pdf",
    "image": "kMDItemContentTypeTree == public.image",
    "document": "kMDItemContentTypeTree == public.content && kMDItemContentType != public.folder",
    "presentation": "kMDItemContentTypeTree == public.presentation",
    "spreadsheet": "kMDItemContentTypeTree == public.spreadsheet",
    "code": "kMDItemContentTypeTree == public.source-code",
}


def _md_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("*", "")


def _mdfind(args: list[str]) -> list[str]:
    try:
        r = _run(["mdfind", *args], timeout=8)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    return [l for l in r.stdout.splitlines() if l.strip()][:300]


def _file_rank(path: str, query: str) -> float:
    name = os.path.basename(path.rstrip("/"))
    stem = os.path.splitext(name)[0]
    _, match = fuzzy_best(query, [stem], cutoff=0)
    try:
        age_days = (time.time() - os.path.getmtime(path)) / 86400
    except OSError:
        age_days = 9999
    recency = 1 / (1 + age_days / 30)             # 1.0 today → 0.5 a month ago
    depth_penalty = min(path.count("/"), 12) * 0.01
    return match * 2 + recency * 0.6 - depth_penalty
