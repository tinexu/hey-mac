"""The AI part: Claude Haiku decides which Mac actions to call."""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field

from .actions import ActionResult, Mac
from .config import Config

log = logging.getLogger(__name__)

SYSTEM = """You are {name}, a voice assistant that controls the user's Mac.
The user speaks; their words come from speech recognition, so expect mishearings
(e.g. "cursed her" = Cursor, "crow" = Chrome) and guess the most likely intent.

Rules:
- Act, don't chat. Use tools to do what was asked, then reply in ONE short sentence (max ~15 words).
- Opening a project/folder "in Cursor"/"in VS Code" -> open_in_editor (it finds the project by name itself).
- Looking for a file by (part of) its name -> find_files.
- Looking for a file they DESCRIBE ("the instructions for my cs373 hw2", "that pdf about tax deductions")
  -> smart_find. Give MANY variants, because file names are messy:
     name_terms: ["hw2", "hw 2", "homework2", "homework 2", "assignment2", "a2", "ps2", "cs373 hw2"]
     folder_terms: ["cs373", "cs 373", "cs-373"]   (class / project / topic folders)
     content_terms: ["homework 2", "instructions", "due"]   (words likely INSIDE the document)
     file_types: only if implied (e.g. ["pdf"] for "the pdf").
  Then, unless the top result is obviously right from its name, peek_file the top 2-4 candidates
  and pick the one whose text best fits the description.
- If they asked to open it, open_path the best one and say which file you opened (name only).
  If you're unsure between a few, don't open — say so; the candidates are shown as a numbered list.
- After a search, reply briefly; results are shown to the user as a numbered list automatically.
- "Ask ChatGPT/Claude/Gemini/Perplexity ..." -> ask_chatbot. Don't answer it yourself. Pass their question
  as the prompt, cleaned up (fix obvious mishearings) but not rewritten. If it involves their files
  ("ask Claude to summarize my hw2 instructions", "send my resume to ChatGPT and ask for feedback",
  "ask Gemini what's in my latest screenshot"), find the file(s) first (smart_find / find_files /
  recent_files), then pass file_paths — they get ATTACHED like a normal upload. The prompt should then
  refer to "the attached file". Only use attach_as="text" if the user asks to paste the text in.
- Apple Notes: "take a note ..." -> add_note (text = what they said, cleaned up; give a short title
  if the note is long). "add X to my <name> list/note" -> append_note. "what's on my <name> list?" or
  anything needing a note's contents -> read_note first, then answer from it. Never invent note text.
- "Send/text me ...", "send this to my phone", "AirDrop ..." -> send_to_me. Notes go in text; a file of theirs
  -> find it first, then file_path; "this page/link" -> current_tab; "what I copied" -> clipboard.
  via="airdrop" only if they say AirDrop (it opens a picker they tap), otherwise "messages".
- "open the second one" etc. refers to the numbered list from the previous turn.
- If a search finds nothing, try once more with a shorter or alternative name, then say so.
- Never invent file paths — only use paths returned by tools.
- If the request is unclear or impossible with your tools, say so briefly.
Home folder: {home}. Project folders: {projects}. Time now: {now}."""

TOOLS = [
    {"name": "open_app", "description": "Open/launch/switch to a Mac app by (approximate) name.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "quit_app", "description": "Quit a running app.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "hide_app", "description": "Hide a running app's windows.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "find_files",
     "description": "Search the user's files by name (fuzzy, via Spotlight). Returns up to `limit` paths, best first.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "Key words of the file name, e.g. 'resume' or 'tax 2025'"},
         "kind": {"type": "string", "enum": ["folder", "pdf", "image", "document", "presentation", "spreadsheet", "code"]},
         "search_contents": {"type": "boolean", "description": "Also match words inside documents"},
         "limit": {"type": "integer", "default": 8}},
         "required": ["query"]}},
    {"name": "smart_find",
     "description": "Find files the user describes but can't name. Combines name variants, folder names and "
                    "words inside documents (Spotlight), returns ranked candidates.",
     "input_schema": {"type": "object", "properties": {
         "name_terms": {"type": "array", "items": {"type": "string"},
                        "description": "Many spellings of what might be in the FILE NAME"},
         "folder_terms": {"type": "array", "items": {"type": "string"},
                          "description": "Class/project/topic names that might be FOLDER names"},
         "content_terms": {"type": "array", "items": {"type": "string"},
                           "description": "Distinctive words/phrases likely INSIDE the document"},
         "file_types": {"type": "array", "items": {"type": "string"},
                        "description": "Extensions like pdf, docx, pptx — only if implied"},
         "limit": {"type": "integer", "default": 8}},
         "required": ["name_terms"]}},
    {"name": "peek_file", "description": "Read the first ~1500 characters of a document to check what it is.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "open_path", "description": "Open a file or folder with its default app, or reveal it in Finder.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string"}, "reveal": {"type": "boolean", "description": "Show in Finder instead of opening"}},
         "required": ["path"]}},
    {"name": "find_project", "description": "Find a code project folder by name.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "open_in_editor",
     "description": "Open a folder/project in a code editor (new window). Accepts a path or a project name.",
     "input_schema": {"type": "object", "properties": {
         "path_or_project": {"type": "string"},
         "editor": {"type": "string", "enum": ["cursor", "vscode", "zed", "xcode"], "default": "cursor"},
         "new_window": {"type": "boolean", "default": True}},
         "required": ["path_or_project"]}},
    {"name": "lock_screen", "description": "Lock the Mac.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "sleep", "description": "Put the Mac to sleep.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "set_volume", "description": "Set volume (0-100), change it relatively, or mute/unmute.",
     "input_schema": {"type": "object", "properties": {
         "level": {"type": "integer"}, "change": {"type": "integer"}, "mute": {"type": "boolean"}}}},
    {"name": "media", "description": "Control Spotify/Music playback.",
     "input_schema": {"type": "object", "properties": {
         "command": {"type": "string", "enum": ["play", "pause", "toggle", "next", "previous"]}},
         "required": ["command"]}},
    {"name": "open_url", "description": "Open a website.",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "web_search", "description": "Search Google in the browser.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "ask_chatbot",
     "description": "Open ChatGPT, Claude, Gemini or Perplexity in Chrome, type the prompt and send it.",
     "input_schema": {"type": "object", "properties": {
         "bot": {"type": "string", "enum": ["chatgpt", "claude", "gemini", "perplexity"]},
         "prompt": {"type": "string"},
         "send": {"type": "boolean", "default": True, "description": "false = just fill it in, don't send"},
         "file_paths": {"type": "array", "items": {"type": "string"},
                        "description": "Files to ATTACH (uploaded like drag-and-drop), max 5"},
         "attach_as": {"type": "string", "enum": ["file", "text"], "default": "file",
                       "description": "file = real attachment; text = paste the file's text into the prompt"}},
         "required": ["bot", "prompt"]}},
    {"name": "recent_files", "description": "Newest files of a kind: the latest screenshot, download, image, pdf...",
     "input_schema": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": ["screenshot", "download", "image", "pdf", "document", "any"]},
         "limit": {"type": "integer", "default": 5}}, "required": ["kind"]}},
    {"name": "add_note", "description": "Create a new note in Apple Notes.",
     "input_schema": {"type": "object", "properties": {
         "text": {"type": "string", "description": "The note's text; new lines become new lines in the note"},
         "title": {"type": "string", "description": "Optional title (otherwise the first line is the title)"},
         "folder": {"type": "string", "description": "Optional Notes folder; created if it doesn't exist"}},
         "required": ["text"]}},
    {"name": "append_note", "description": "Add text to the end of an existing Apple Note (found by title; created if missing).",
     "input_schema": {"type": "object", "properties": {
         "note": {"type": "string", "description": "The note's title or part of it, e.g. 'grocery'"},
         "text": {"type": "string"}}, "required": ["note", "text"]}},
    {"name": "read_note", "description": "Read the text of an Apple Note by title.",
     "input_schema": {"type": "object", "properties": {"note": {"type": "string"}}, "required": ["note"]}},
    {"name": "send_to_me",
     "description": "Send something to the user's own phone: iMessage to themselves (default) or AirDrop.",
     "input_schema": {"type": "object", "properties": {
         "text": {"type": "string", "description": "A note/message to send"},
         "file_path": {"type": "string", "description": "A file to send (path from a search)"},
         "current_tab": {"type": "boolean", "description": "Send the page open in Chrome"},
         "clipboard": {"type": "boolean", "description": "Send what's on the clipboard"},
         "via": {"type": "string", "enum": ["messages", "airdrop"], "default": "messages"}}}},
    {"name": "status", "description": "Current time, date and battery.",
     "input_schema": {"type": "object", "properties": {}}},
]


@dataclass
class Reply:
    text: str
    items: list[str] = field(default_factory=list)   # file paths to show as a list
    ok: bool = True


class Brain:
    MAX_STEPS = 8

    def __init__(self, cfg: Config, mac: Mac, client=None):
        self.cfg = cfg
        self.mac = mac
        self._client = client
        self.history: list[dict] = []
        self.last_items: list[str] = []
        self._last_time = 0.0

    @property
    def client(self):
        if self._client is None:
            import anthropic
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise RuntimeError("ANTHROPIC_API_KEY isn't set (put it in .env)")
            self._client = anthropic.Anthropic()
        return self._client

    def remember(self, user_text: str, reply: Reply) -> None:
        """Record a fast-path exchange so follow-ups still have context."""
        self._expire()
        self.history += [{"role": "user", "content": user_text},
                         {"role": "assistant", "content": reply.text}]
        self._last_time = time.time()

    def _expire(self) -> None:
        if time.time() - self._last_time > self.cfg.memory_seconds:
            self.history, self.last_items = [], []
        self.history = self.history[-12:]
        # history must start with a user turn
        while self.history and self.history[0]["role"] != "user":
            self.history.pop(0)

    def _system(self) -> str:
        return SYSTEM.format(
            name=self.cfg.name, home=os.path.expanduser("~"),
            projects=", ".join(self.cfg.project_roots),
            now=time.strftime("%A %Y-%m-%d %H:%M"),
        )

    def run_tool(self, name: str, args: dict) -> ActionResult:
        fn = getattr(self.mac, name, None)
        if fn is None or name not in {t["name"] for t in TOOLS}:
            return ActionResult(False, f"Unknown tool {name}")
        try:
            return fn(**args)
        except Exception as e:  # noqa: BLE001
            log.exception("tool %s failed", name)
            return ActionResult(False, f"{name} error: {e}")

    def ask(self, text: str) -> Reply:
        self._expire()
        user_content = text
        if self.last_items:
            listing = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(self.last_items))
            user_content = f"{text}\n\n(Items currently shown to the user:\n{listing})"
        messages = self.history + [{"role": "user", "content": user_content}]

        items: list[str] = []
        final_text, any_fail = "", False
        for _ in range(self.MAX_STEPS):
            resp = self.client.messages.create(
                model=self.cfg.ai_model, max_tokens=self.cfg.max_tokens,
                system=self._system(), tools=TOOLS, messages=messages,
            )
            messages.append({"role": "assistant", "content": resp.content})
            final_text = " ".join(b.text for b in resp.content if b.type == "text").strip()
            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if resp.stop_reason != "tool_use" or not tool_uses:
                break
            results = []
            for tu in tool_uses:
                r = self.run_tool(tu.name, dict(tu.input))
                log.info("tool %s(%s) -> %s", tu.name, json.dumps(tu.input), r.message)
                any_fail = any_fail or not r.ok
                if r.items and tu.name in ("find_files", "find_project", "smart_find", "recent_files"):
                    items = r.items
                results.append({"type": "tool_result", "tool_use_id": tu.id,
                                "content": r.for_ai(), "is_error": not r.ok})
            messages.append({"role": "user", "content": results})
        else:
            final_text = final_text or "That took too many steps — try rephrasing?"

        reply = Reply(text=final_text or "Done.", items=items, ok=not (any_fail and not items and not final_text))
        # keep only plain-text turns in memory (cheaper, and avoids dangling tool ids)
        self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": reply.text}]
        if items:
            self.last_items = items
        self._last_time = time.time()
        return reply
