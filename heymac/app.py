"""Wires everything together: mic → Whisper → (fast path | Claude) → Mac actions → UI."""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .actions import Mac
from .brain import Brain, Reply
from .commands import clean, route
from .config import RESTART_FIELDS, Config, load_config, update_config

log = logging.getLogger(__name__)

ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
            "seventh": 7, "eighth": 8, "last": -1, "one": 1, "two": 2, "three": 3, "four": 4,
            "five": 5, "six": 6, "seven": 7, "eight": 8}


def pick_from_list(text: str, items: list[str]) -> tuple[int, bool] | None:
    """'open the second one' / '2' / 'show 3 in finder' → (index, reveal)."""
    if not items:
        return None
    t = clean(text).lower()
    m = re.fullmatch(
        r"(?:(open|show|reveal)\s+)?(?:the\s+)?(?:number\s+)?(\d|first|second|third|fourth|fifth|sixth|"
        r"seventh|eighth|last|one|two|three|four|five|six|seven|eight)(?:\s+one)?(?:\s+in finder)?", t)
    if not m:
        return None
    word = m.group(2)
    n = int(word) if word.isdigit() else ORDINALS[word]
    idx = len(items) - 1 if n == -1 else n - 1
    if not 0 <= idx < len(items):
        return None
    reveal = m.group(1) in ("show", "reveal") or "finder" in t
    return idx, reveal


class Assistant:
    def __init__(self, cfg: Config):
        from PyObjCTools import AppHelper
        from .audio import Microphone
        from .stt import Transcriber
        from .ui import MenuBar, SpotlightBar

        self.cfg = cfg
        self.main = AppHelper.callAfter         # run a function on the UI thread
        self.mac = Mac(cfg)
        self.brain = Brain(cfg, self.mac)
        self.stt = Transcriber(cfg)
        self.worker = ThreadPoolExecutor(max_workers=1)
        self.stt_ready = threading.Event()

        if cfg.ui_style == "bar":
            self.bar = SpotlightBar.alloc().initWithName_onSubmit_onOpenItem_onTyping_(
                cfg.name, self.on_typed, self.on_open_item, self.on_typing)
        else:
            from .lily import LilyOverlay
            self.bar = LilyOverlay.alloc().initWithName_corner_onSubmit_onOpenItem_onTyping_onDismiss_(
                cfg.name, cfg.ui_corner, self.on_typed, self.on_open_item, self.on_typing, self.on_dismiss)
        self.menubar = MenuBar.alloc().initWithActions_(self.menu_actions())
        if hasattr(self.bar, "setPalette_"):            # lily: recolor live + remember the last palette
            self.mac.palette_hook = lambda spec: self.main(self.bar.setPalette_, spec)
            self.mac.mascot_hook = lambda name: self.main(self.bar.setMascot_, name)
            try:
                self.bar.setPalette_(self.mac.aesthetics.current_lily())
                self.bar.setMascot_(self.mac.aesthetics.current_mascot())
            except Exception:  # noqa: BLE001
                log.exception("couldn't restore lily colors / mascot")
        self.mic = Microphone(cfg, on_wake=self.on_wake, on_level=self.on_level,
                              on_utterance=self.on_utterance)

    # ------------------------------------------------------------ startup
    def start(self) -> None:
        from .hotkey import Hotkey
        self.hotkey = Hotkey(self.cfg.hotkey, self.on_hotkey)
        hotkey_ok = self.hotkey.install()
        self.mic.start()
        threading.Thread(target=self.watch_config, name="config-watch", daemon=True).start()
        self.menubar.setStatus_("Loading speech model… (first run downloads ~1.5 GB)")

        def warm():
            try:
                self.stt.warm_up()
            except Exception as e:  # noqa: BLE001
                log.exception("speech model failed to load")
                self.main(self.menubar.setStatus_, f"Speech model error: {e}")
                return
            self.stt_ready.set()
            bits = []
            if self.cfg.enable_wake_word and self.mic._wake_model is not None:
                bits.append(f"say “{self.cfg.wake_word.replace('_', ' ')}”")
            bits.append(f"press {self.cfg.hotkey}" if hotkey_ok else "hotkey needs Accessibility permission")
            log.info("✅ Ready — %s", " or ".join(bits))
            self.main(self.menubar.setStatus_, "Ready — " + " or ".join(bits))
        threading.Thread(target=warm, daemon=True).start()

    # ------------------------------------------------------ input events
    def on_hotkey(self) -> None:                       # main thread
        if self.bar.isVisible() and self.mic.recording:
            self.mic.cancel_recording()
            self.bar.hide()
            return
        self._chime("Tink")
        self.bar.showListening_(True)
        self.mic.start_recording()

    def on_wake(self) -> None:                         # audio thread
        self._chime("Tink")
        self.main(self.bar.showListening_, False)

    def on_level(self, level: float) -> None:          # audio thread
        self.main(self.bar.setLevel_, level)

    def on_typing(self) -> None:                       # main thread
        if self.mic.recording:
            self.mic.cancel_recording()

    def on_typed(self, text: str) -> None:             # main thread
        self.mic.cancel_recording()
        self.bar.showThinking_("")
        self.worker.submit(self._handle, text)

    def on_utterance(self, audio) -> None:             # audio thread
        if audio is None:
            self.main(self.bar.showResult_items_ok_, "Didn't hear anything.", [], False)
            return
        self.main(self.bar.showThinking_, "Transcribing…")
        self.worker.submit(self._transcribe_and_handle, audio)

    # ------------------------------------------------------ menu + config reload
    def menu_actions(self) -> list:
        cfg = self.cfg
        vibes = [(f"Aesthetic: {k.replace('_', ' ').title()}", (lambda key=k: self.run_action(
                     lambda: self.mac.apply_aesthetic(key)))) for k in cfg.presets]
        if vibes:
            vibes += [("Undo last aesthetic", lambda: self.run_action(self.mac.undo_aesthetic)), None]
        return [
            (f"Talk to {cfg.name}  ({cfg.hotkey})", self.on_hotkey),
            None,
            *vibes,
            ("Open config file", self.open_config),
            ("Reload config", lambda: self.reload_config(auto=False)),
            ("Quit", self.quit),
        ]

    def _config_mtime(self) -> float:
        try:
            return os.path.getmtime(self.cfg.config_path)
        except OSError:
            return 0.0

    def watch_config(self) -> None:
        """Background thread: reload a couple of seconds after config.toml is saved."""
        import time
        last = self._config_mtime()
        while True:
            time.sleep(2)
            now = self._config_mtime()
            if now and now != last:
                time.sleep(0.5)                       # let the editor finish writing
                last = self._config_mtime()
                self.main(self.reload_config, True)

    def reload_config(self, auto: bool = False) -> None:     # main thread
        try:
            new = load_config(str(self.cfg.config_path))
        except Exception as e:  # noqa: BLE001  (usually a TOML typo)
            log.warning("config not reloaded: %s", e)
            self.menubar.setStatus_("Config has a typo, still using the old one")
            self.bar.showListening_(False)
            self.bar.showResult_items_ok_(f"Config not reloaded, there's a typo: {str(e)[:120]}", [], False)
            return
        changed = update_config(self.cfg, new)
        self.mac._aesthetics = None                       # rebuild with the new presets
        if "mascot" in changed and hasattr(self.bar, "setMascot_"):
            self.bar.setMascot_(self.mac.aesthetics.current_mascot())
        if "ui_corner" in changed and hasattr(self.bar, "corner"):
            self.bar.corner = self.cfg.ui_corner
        self.menubar.set_actions(self.menu_actions())
        needs_restart = [RESTART_FIELDS[c] for c in changed if c in RESTART_FIELDS]
        log.info("config reloaded (%s changed)", ", ".join(sorted(changed)) or "nothing")
        if auto and not changed:
            return
        msg = "Config reloaded" if changed else "Config reloaded (no changes)"
        if needs_restart:
            msg += f". Restart the app for: {', '.join(needs_restart)}"
        self.bar.showListening_(False)
        self.bar.showResult_items_ok_(msg, [], True)

    def run_action(self, fn) -> None:                 # main thread (menu click)
        """Run a Mac action in the background and show its result in the lily/bar."""
        self.bar.showListening_(False)
        self.bar.showThinking_("")

        def work():
            try:
                r = fn()
                self.main(self.bar.showResult_items_ok_, r.message, list(r.items or []), r.ok)
            except Exception as e:  # noqa: BLE001
                log.exception("menu action failed")
                self.main(self.bar.showResult_items_ok_, f"Something went wrong: {e}", [], False)
        self.worker.submit(work)

    def on_dismiss(self) -> None:                      # main thread (lily clicked)
        self.mic.cancel_recording()

    def on_open_item(self, path: str, reveal: bool) -> None:
        self.mac.open_path(path, reveal=reveal)

    # --------------------------------------------------------- handling
    def _transcribe_and_handle(self, audio) -> None:
        self.stt_ready.wait(timeout=300)
        try:
            text = self.stt.transcribe(audio)
        except Exception as e:  # noqa: BLE001
            log.exception("transcription failed")
            self.main(self.bar.showResult_items_ok_, f"Speech recognition failed: {e}", [], False)
            return
        log.info("heard: %r", text)
        if not text:
            self.main(self.bar.showResult_items_ok_, "Didn't catch that — try again?", [], False)
            return
        self.main(self.bar.setQuery_, text)
        self._handle(text)

    def _handle(self, text: str) -> None:              # worker thread
        try:
            reply = self._decide(text)
        except Exception as e:  # noqa: BLE001
            log.exception("command failed")
            reply = Reply(f"Something went wrong: {e}", ok=False)
        self.main(self.bar.showResult_items_ok_, reply.text, reply.items, reply.ok)
        if self.cfg.speak_replies and reply.text:
            subprocess.Popen(["say", reply.text])

    def _decide(self, text: str) -> Reply:
        # 1. "open the second one" when a list is showing
        pick = pick_from_list(text, self.brain.last_items)
        if pick:
            idx, reveal = pick
            r = self.mac.open_path(self.brain.last_items[idx], reveal=reveal)
            reply = Reply(r.message, ok=r.ok)
            self.brain.remember(text, reply)
            return reply

        # 2. instant commands (no AI, no cost)
        action = route(text, self.mac)
        if action:
            r = action()
            reply = Reply(r.message, r.items, r.ok)
            self.brain.remember(text, reply)
            return reply

        # 3. everything else → Claude
        self.main(self.bar.showThinking_, "Thinking…")
        return self.brain.ask(text)

    # ------------------------------------------------------------ misc
    def _chime(self, name: str) -> None:
        try:
            from AppKit import NSSound
            s = NSSound.soundNamed_(name)
            if s:
                s.setVolume_(0.3)
                s.play()
        except Exception:  # noqa: BLE001
            pass

    def open_config(self) -> None:
        path = Path.home() / ".config" / "heymac" / "config.toml"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            example = Path(__file__).resolve().parent.parent / "config.example.toml"
            path.write_text(example.read_text() if example.exists() else "")
        subprocess.Popen(["open", "-t", str(path)])

    def quit(self) -> None:
        from AppKit import NSApp
        self.mic.stop()
        NSApp.terminate_(None)


def run(cfg: Config) -> None:
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
    from PyObjCTools import AppHelper

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)   # no Dock icon
    assistant = Assistant(cfg)
    assistant.start()
    log.info("%s is running. Menu bar icon: waveform. Ctrl+C to quit.", cfg.name)
    AppHelper.runEventLoop(installInterrupt=True)
    del assistant