"""Fast path: simple commands handled instantly without calling the AI.

Anything this can't handle confidently returns None and goes to the AI instead.
"""
from __future__ import annotations

import re
from typing import Callable

from .actions import ActionResult, Mac

FILLER = re.compile(
    r"^(?:(?:hey|hi|ok|okay|yo)\s+\w+[,.!]?\s+)?"          # "hey jarvis,"
    r"(?:(?:can|could|would) you\s+|please\s+|(?:i want to|i wanna|go)\s+)*",
    re.I,
)
TRAILING = re.compile(r"(?:\s+(?:please|for me|now|thanks|thank you))+$", re.I)

# If these appear the request is more than "open <app>" → let the AI handle it.
COMPLEX_HINTS = re.compile(r"\b(with|in|and|from|folder|file|project|the one|that|my)\b", re.I)


def clean(text: str) -> str:
    t = text.strip().strip("\"'").strip()
    t = re.sub(r"[.!?]+$", "", t)
    t = FILLER.sub("", t, count=1)
    t = TRAILING.sub("", t)
    return t.strip()


def route(text: str, mac: Mac) -> Callable[[], ActionResult] | None:
    """Return a zero-arg function that performs the command, or None."""
    t = clean(text)
    low = t.lower()

    if re.fullmatch(r"(lock|lock (the |my )?(screen|computer|mac|laptop))", low):
        return mac.lock_screen
    if re.fullmatch(r"(go to sleep|sleep|put (the |my )?(computer|mac|laptop) to sleep)", low):
        return mac.sleep
    if re.fullmatch(r"(what time is it|what'?s the time|time|battery|what'?s my battery|how much battery.*)", low):
        return mac.status

    if re.fullmatch(r"(mute|mute (the )?(sound|volume|audio))", low):
        return lambda: mac.set_volume(mute=True)
    if re.fullmatch(r"unmute( (the )?(sound|volume|audio))?", low):
        return lambda: mac.set_volume(mute=False)
    m = re.fullmatch(r"(?:set )?(?:the )?volume (?:to )?(\d{1,3})(?: ?%| percent)?", low)
    if m:
        return lambda: mac.set_volume(level=int(m.group(1)))
    if re.fullmatch(r"(turn (it|the volume) up|volume up|louder)", low):
        return lambda: mac.set_volume(change=+15)
    if re.fullmatch(r"(turn (it|the volume) down|volume down|quieter)", low):
        return lambda: mac.set_volume(change=-15)

    if re.fullmatch(r"(pause|pause (the )?music|stop (the )?music)", low):
        return lambda: mac.media("pause")
    if re.fullmatch(r"(play|resume|play (the )?music|resume (the )?music)", low):
        return lambda: mac.media("play")
    if re.fullmatch(r"(next|skip|next (song|track)|skip (this )?(song|track))", low):
        return lambda: mac.media("next")

    ae = aesthetics_route(t, mac)
    if ae:
        return ae

    nt = notes_route(t, mac)
    if nt:
        return nt

    sm = sendme_route(t, mac)
    if sm:
        return sm

    chat = chatbot_route(t, mac)
    if chat:
        return chat

    m = re.fullmatch(r"(?:open|launch|start|pull up|bring up|switch to|go to)\s+(?:up\s+)?(.+)", t, re.I)
    if m and not COMPLEX_HINTS.search(m.group(1)):
        target = re.sub(r"^(the|my)\s+", "", m.group(1), flags=re.I)
        target = re.sub(r"\s+app$", "", target, flags=re.I)
        app = mac.resolve_app(target)
        if app:
            return lambda: mac.open_app(app)
        return None

    m = re.fullmatch(r"(?:quit|close|exit|kill|force quit)\s+(.+)", t, re.I)
    if m:
        target = re.sub(r"^(the|my|out of)\s+", "", m.group(1), flags=re.I)
        target = re.sub(r"\s+app$", "", target, flags=re.I)
        if not COMPLEX_HINTS.search(target):
            return lambda: mac.quit_app(target)

    m = re.fullmatch(r"hide\s+(.+)", t, re.I)
    if m:
        return lambda: mac.hide_app(m.group(1))

    return None


# ------------------------------------------------------------ chatbots
from .chatbots import BOT_PATTERN  # noqa: E402

# Prompts that mention one of your files need the AI (to find the file first).
NEEDS_FILE = re.compile(
    r"\b(my|this|that)\b.*\b(file|pdf|doc|document|notes|instructions|homework|hw\s?\d*|assignment|"
    r"essay|resume|slides|syllabus|lab|project|code|screenshot|screen shot|image|photo|picture|download|"
    r"downloads|spreadsheet|paper|report)s?\b", re.I)

CHAT_PATTERNS = [
    # ask claude how to reverse a list / tell chatgpt to write a haiku
    rf"(?:ask|tell|have|go ask|message)\s+(?P<bot>{BOT_PATTERN})[,:]?\s+(?:to\s+)?(?P<q>.+)",
    # open gemini and ask it what ...
    rf"(?:open|go to|pull up|launch)\s+(?P<bot>{BOT_PATTERN})\s+and\s+(?:ask|tell|type|say|send|search)"
    rf"(?:\s+it)?[,:]?\s+(?:to\s+)?(?P<q>.+)",
    # search best ramen near me on perplexity
    rf"(?:ask|search|look up|search for)\s+(?P<q>.+?)\s+(?:on|in|with|using)\s+(?P<bot>{BOT_PATTERN})",
]


def chatbot_route(t: str, mac: Mac):
    for pat in CHAT_PATTERNS:
        m = re.fullmatch(pat, t, re.I)
        if m:
            q = m.group("q").strip()
            if NEEDS_FILE.search(q):
                return None                      # let the AI find the file first
            bot = m.group("bot")
            return lambda: mac.ask_chatbot(bot, q)
    return None


# ------------------------------------------------------------ send to myself
ME = r"(?:me|myself|my phone|my iphone)"
TAB = r"(?:this|the|my)?\s*(?:page|tab|link|website|site|url|article)"
SEND_PATTERNS = [
    # airdrop this page to my phone / airdrop my clipboard
    (rf"air\s?drop\s+(?P<what>{TAB}|(?:my |the )?clipboard)(?:\s+to\s+{ME})?", "airdrop"),
    # send me this page / text myself this link / send this tab to my phone
    (rf"(?:send|text|message|imessage)\s+{ME}\s+(?P<what>{TAB}|(?:my |the )?clipboard|what i copied)", "messages"),
    (rf"(?:send|text)\s+(?P<what>{TAB}|(?:my |the )?clipboard|what i copied)\s+to\s+{ME}", "messages"),
    # text me a note: buy milk / send myself a reminder to call mom
    (rf"(?:send|text|message)\s+{ME}\s+(?:a\s+)?(?P<kind>note|message|text|reminder)?\s*(?:saying|that says|to say)?[,:]?\s*(?P<note>.+)",
     "messages"),
]


def sendme_route(t: str, mac: Mac):
    for pat, via in SEND_PATTERNS:
        m = re.fullmatch(pat, t, re.I)
        if not m:
            continue
        what = (m.groupdict().get("what") or "").lower()
        if "clipboard" in what or "copied" in what:
            return lambda: mac.send_to_me(clipboard=True, via=via)
        if what:
            return lambda: mac.send_to_me(current_tab=True, via=via)
        note = m.group("note").strip()
        if NEEDS_FILE.search(note) or re.search(r"\.(pdf|docx?|pptx?|png|jpe?g|txt|zip)\b", note, re.I):
            return None                        # probably a file → the AI finds it first
        if (m.groupdict().get("kind") or "").lower() == "reminder":
            note = "Reminder: " + re.sub(r"^(to|that)\s+", "", note, flags=re.I)
        else:
            note = note[:1].upper() + note[1:]
        return lambda: mac.send_to_me(text=note, via=via)
    return None


# ------------------------------------------------------------ Apple Notes
NOTE_PATTERNS = [
    # take a note: call the dentist / make a note that the quiz moved / jot down ...
    ("new", r"(?:take|make|write|jot down|create|start)\s+(?:a\s+|me\s+a\s+)?(?:new\s+|quick\s+)?note"
            r"(?:\s+(?:that|saying|to say|about|in notes))?[,:]?\s+(?P<text>.+)"),
    ("new", r"(?:note|new note)[,:]\s*(?P<text>.+)"),
    ("new", r"(?:jot down|note down|write down)[,:]?\s+(?P<text>.+)"),
    ("new", r"(?:write|put|save|add)\s+(?P<text>.+?)\s+(?:in|to|into)\s+(?:my\s+|the\s+)?notes(?:\s+app)?"),
    # add eggs to my grocery list / add "read ch 4" to the cs101 note
    ("append", r"(?:add|put|write)\s+(?P<text>.+?)\s+(?:to|on|in|onto)\s+(?:my\s+|the\s+)?"
               r"(?P<note>[\w' -]+?)\s+(?:note|list)"),
]


def notes_route(t: str, mac: Mac):
    for kind, pat in NOTE_PATTERNS:
        m = re.fullmatch(pat, t, re.I)
        if not m:
            continue
        text = m.group("text").strip().strip("\"'“”")
        if not text:
            return None
        if kind == "append":
            note = m.group("note").strip()
            return lambda: mac.append_note(note, text[:1].upper() + text[1:])
        return lambda: mac.add_note(text[:1].upper() + text[1:])
    return None


# ------------------------------------------------------------ aesthetics
VIBE = r"(?:aesthetic|vibe|theme|preset|mode|look|setup)"
AESTHETIC_PATTERNS = [
    # change my aesthetic to coffee / set the vibe to matcha
    rf"(?:change|switch|set|turn|make)\s+(?:my\s+|the\s+)?{VIBE}\s+(?:back\s+)?(?:to|into|over to)\s+(?P<name>.+)",
    # switch to coffee mode / activate the matcha aesthetic / put on rainy day vibes
    rf"(?:switch to|go to|activate|enable|start|put on|turn on|use|do|apply|go)\s+(?:my\s+|the\s+)?(?P<name>.+?)\s+{VIBE}s?",
    # coffee mode / coffee aesthetic / coffee vibes / it's coffee time
    rf"(?:it'?s\s+)?(?P<name>.+?)\s+(?:{VIBE}s?|time)",
    # switch to coffee (only if "coffee" is one of your presets)
    r"(?:switch|change)\s+(?:it\s+)?(?:to|into)\s+(?P<name>.+)",
]
UNDO_AESTHETIC = re.compile(
    rf"(?:undo|revert|reset|go back|switch back|change back)\s+(?:to\s+)?(?:my\s+|the\s+)?(?:old\s+|previous\s+|normal\s+)?{VIBE}s?"
    r"|(?:go\s+|switch\s+|change\s+|put\s+it\s+)?back\s+to\s+(?:normal|how it was|before|my old (?:setup|look))"
    rf"|undo\s+(?:that\s+)?{VIBE}\s+change", re.I)
LIST_AESTHETICS = re.compile(
    r"(?:what|which|list|show)(?:\s+me)?(?:\s+(?:are|is))?(?:\s+my)?\s+(?:aesthetics|presets|vibes|modes)(?:\s+do\s+i\s+have)?",
    re.I)
SAVE_AESTHETIC = re.compile(
    rf"save\s+(?:my\s+|this\s+|the\s+)?(?:current\s+)?(?:{VIBE}|desktop|vibes)\s+as\s+(?:a\s+|an\s+)?(?:new\s+)?"
    rf"(?:{VIBE}\s+)?(?:called\s+|named\s+)?(?P<name>.+)", re.I)


ANIMAL_WORDS = (r"(?:cat|kitty|kitten|bunny|rabbit|frog|froggy|toad|bear|teddy|fox|otter|sea otter|owl|owlet|"
                r"lily|lilly|flower)s?")
MASCOT = re.compile(
    # be a fox / turn into a bunny / make jarvis an owl / change my mascot to a frog / switch back to the lily
    rf"(?:(?:can you\s+)?(?:be|become|turn into|change into)"
    rf"|(?:make|turn)\s+(?:yourself|you|\w+)(?:\s+into)?"
    rf"|(?:change|switch|set)\s+(?:your|my|the)\s+(?:mascot|animal|buddy|character|avatar|critter)\s+(?:to|into)"
    rf"|(?:(?:switch|go)\s+)?back\s+to)\s+(?:a\s+|an\s+|the\s+|my\s+)?(?:little\s+|cute\s+)?(?P<animal>{ANIMAL_WORDS})"
    rf"(?:\s+(?:again|now|please|mode))?", re.I)


NORMAL_LOOK = re.compile(r"(?:my\s+|the\s+)?(?:old\s+)?(?:normal|default|regular|usual|original|og|basic|plain|"
                         r"old|previous|standard)(?:\s+(?:one|again))?", re.I)


def _is_preset(mac, name: str) -> bool:
    """Someone could really have a preset called "default", so check before treating it as undo."""
    presets = getattr(getattr(mac, "cfg", None), "presets", {}) or {}
    key = name.strip().lower().replace(" ", "_")
    return key in presets


def aesthetics_route(t: str, mac: Mac):
    low = t.lower().strip()
    m = MASCOT.fullmatch(low)
    if m:
        animal = m.group("animal")
        return lambda: mac.set_mascot(animal)
    if UNDO_AESTHETIC.fullmatch(low):
        return mac.undo_aesthetic
    if LIST_AESTHETICS.fullmatch(low):
        return mac.list_aesthetics
    m = SAVE_AESTHETIC.fullmatch(t)
    if m:
        name = m.group("name")
        return lambda: mac.save_aesthetic(name)
    if re.fullmatch(r"(?:turn on |switch to |go |enable |use )?(dark|light)\s+mode(?: on)?", low):
        mode = "dark" if "dark" in low else "light"
        if not getattr(mac, "cfg", None) or mode not in getattr(mac.cfg, "presets", {}):
            return lambda: mac.set_appearance(mode)
    presets = getattr(getattr(mac, "cfg", None), "presets", {}) or {}
    for pat in AESTHETIC_PATTERNS:          # "normal aesthetic" / "change my aesthetic to default" = undo
        m = re.fullmatch(pat, t, re.I)
        if m and NORMAL_LOOK.fullmatch(m.group("name").strip(" .!?")) and not _is_preset(mac, m.group("name")):
            return mac.undo_aesthetic
    if not presets:
        return None
    for pat in AESTHETIC_PATTERNS:
        m = re.fullmatch(pat, t, re.I)
        if m:
            key = mac.aesthetics.resolve(m.group("name"))
            if key:                       # only take over when it names one of YOUR presets
                return lambda: mac.apply_aesthetic(key)
    return None