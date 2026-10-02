"""Global hotkey via a Quartz event tap (swallows the key so ⌥Space doesn't type anything).

Needs Accessibility permission for your terminal / Python
(System Settings → Privacy & Security → Accessibility).
"""
from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger(__name__)

KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11,
    "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21,
    "6": 22, "5": 23, "9": 25, "7": 26, "8": 28, "0": 29, "o": 31, "u": 32, "i": 34, "p": 35,
    "return": 36, "l": 37, "j": 38, "k": 40, ";": 41, ",": 43, "/": 44, "n": 45, "m": 46,
    ".": 47, "tab": 48, "space": 49, "`": 50, "f13": 105, "f14": 107, "f15": 113,
}
MODS = {"cmd": 1 << 20, "command": 1 << 20, "option": 1 << 19, "opt": 1 << 19, "alt": 1 << 19,
        "ctrl": 1 << 18, "control": 1 << 18, "shift": 1 << 17}
ALL_MODS = (1 << 20) | (1 << 19) | (1 << 18) | (1 << 17)


def parse_hotkey(spec: str) -> tuple[int, int]:
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    key = parts[-1]
    if key not in KEYCODES:
        raise ValueError(f"Unknown hotkey key '{key}'. Options: {', '.join(KEYCODES)}")
    mods = 0
    for p in parts[:-1]:
        if p not in MODS:
            raise ValueError(f"Unknown modifier '{p}'. Use cmd, option, ctrl, shift.")
        mods |= MODS[p]
    return KEYCODES[key], mods


class Hotkey:
    def __init__(self, spec: str, handler: Callable[[], None]):
        self.keycode, self.mods = parse_hotkey(spec)
        self.handler = handler
        self._tap = None
        self._cb = None   # keep a reference so it isn't garbage-collected

    def install(self) -> bool:
        """Call on the main thread after NSApplication exists. Returns False if permission is missing."""
        import Quartz

        def callback(proxy, etype, event, refcon):
            if etype in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
                Quartz.CGEventTapEnable(self._tap, True)
                return event
            if etype == Quartz.kCGEventKeyDown:
                code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
                flags = Quartz.CGEventGetFlags(event) & ALL_MODS
                if code == self.keycode and flags == self.mods:
                    if not Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat):
                        try:
                            self.handler()
                        except Exception:  # noqa: BLE001
                            log.exception("hotkey handler failed")
                    return None          # swallow it
            return event

        self._cb = callback
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap, Quartz.kCGEventTapOptionDefault,
            Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown), callback, None)
        if self._tap is None:
            log.warning("Hotkey needs Accessibility permission (System Settings → Privacy & Security → Accessibility).")
            return False
        source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetMain(), source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        self._source = source
        return True
