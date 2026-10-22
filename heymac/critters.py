"""Animal mascots that replace (or sit beside) the lily: cat, bunny, frog, bear, fox, otter, owl.

Everything is drawn with a tiny "pen" interface, so the same drawing code runs on the Mac
(AppKit, in lily.py) and anywhere else (Cairo previews, tests). Coordinates: origin at the
center of the 150×150 window, y pointing UP, roughly ±75 units each way.

They're alive:
  idle       breathing bob, blinks every few seconds
  listening  ears perk up, mouth opens with your voice, glow pulses
  thinking   head tilts, eyes look up, "…" bubble
  done       happy ^^ eyes, sparkles
  error      droopy ears, little sweat drop
"""
from __future__ import annotations

import math

IDLE, LISTENING, THINKING, DONE, ERROR = range(5)

ANIMALS = ("cat", "bunny", "frog", "bear", "fox", "otter", "owl")
DEFAULT_ANIMAL = "cat"
# Which animal shows up when a preset picks a lily palette but no mascot.
MASCOT_FOR_PALETTE = {"lily": "cat", "coffee": "bear", "matcha": "frog", "ocean": "otter",
                      "sakura": "bunny", "midnight": "owl", "sunset": "fox"}

INK = (0.20, 0.15, 0.20)
WHITE = (1.0, 1.0, 1.0)
BLUSH = (1.0, 0.55, 0.62)
MOUTH = (0.55, 0.22, 0.28)

# status glows that stay the same in every palette
DONE_GLOW = (0.40, 0.90, 0.60)
ERROR_GLOW = (1.00, 0.66, 0.25)


def choose(ui_setting: str | None, explicit: str | None, auto: str | None, packs=()) -> str:
    """Which mascot to show.

    ui_setting: [ui] mascot in config: "auto" (follow presets), "lily" (always the flower),
                or an animal name (that animal unless a preset names one explicitly).
    explicit:   set by a preset's `mascot = "…"` or by "change my mascot to a fox".
    auto:       picked from the preset's lily palette (coffee → bear …).
    packs:      names of custom characters (folders in ~/.config/heymac/characters).
    """
    s = (ui_setting or "auto").strip().lower()
    known = set(ANIMALS) | {"lily"} | {str(p).lower() for p in packs}
    if s == "lily":
        return "lily"
    if explicit and explicit.lower() in known:
        return explicit.lower()
    if s in known:
        return s
    if auto in ANIMALS:
        return auto
    return DEFAULT_ANIMAL


def normalize(name: str, packs=None) -> str | None:
    """'a little bunny' -> 'bunny', 'kitty' -> 'cat', 'flower' -> 'lily', 'my mochi' -> 'mochi' (a pack).

    packs: {folder name: Pack} of custom characters; they win over a built-in animal of the same name.
    """
    n = name.strip().lower()
    for prefix in ("a ", "an ", "the ", "my ", "little "):
        n = n.removeprefix(prefix)
    n = n.removesuffix(" character").strip(" .!?")
    from .characters import key
    for folder, pack in (packs or {}).items():
        if key(n) in getattr(pack, "keys", {key(folder)}):
            return folder
    synonyms = {"kitty": "cat", "kitten": "cat", "rabbit": "bunny", "bun": "bunny", "toad": "frog",
                "froggy": "frog", "teddy": "bear", "foxy": "fox", "sea otter": "otter", "owlet": "owl",
                "flower": "lily", "lilly": "lily", "lily": "lily"}
    n = synonyms.get(n, n.rstrip("s") if n.rstrip("s") in ANIMALS else n)
    return n if n in ANIMALS or n == "lily" else None


# ------------------------------------------------------------------ small helpers
def _shade(c, k):
    return tuple(max(0.0, min(1.0, v * k)) for v in c)


def _blink(state, phase):
    return state in (IDLE, LISTENING) and (phase % 3.4) < 0.13


def _face_mode(state, phase):
    if state == DONE:
        return "happy"
    if state == ERROR:
        return "sad"
    if _blink(state, phase):
        return "blink"
    if state == THINKING:
        return "look"
    return "open"


def _eye(pen, x, y, r, mode, color=INK):
    if mode == "blink":
        pen.shape([("M", x - r, y), ("C", x - r * 0.4, y - r * 0.6, x + r * 0.4, y - r * 0.6, x + r, y)],
                  stroke=color, width=max(1.6, r * 0.45))
    elif mode == "happy":
        pen.shape([("M", x - r, y - r * 0.3), ("C", x - r * 0.5, y + r * 0.9, x + r * 0.5, y + r * 0.9,
                                                x + r, y - r * 0.3)], stroke=color, width=max(1.8, r * 0.5))
    else:
        dx, dy = (r * 0.25, r * 0.35) if mode == "look" else (0.0, 0.0)
        rr = r * (0.82 if mode == "sad" else 1.0)
        pen.ellipse(x + dx, y + dy, rr * 0.86, rr, fill=color)
        pen.ellipse(x + dx - rr * 0.32, y + dy + rr * 0.38, rr * 0.34, rr * 0.34, fill=WHITE)
        pen.ellipse(x + dx + rr * 0.3, y + dy - rr * 0.35, rr * 0.16, rr * 0.16, fill=WHITE, alpha=0.8)


def _eyes(pen, gap, y, r, mode, color=INK):
    for sx in (-1, 1):
        _eye(pen, sx * gap, y, r, mode, color)


def _mouth(pen, y, state, level, phase, w=5.0, cat=False):
    if state == LISTENING and level > 0.08:
        h = 1.6 + level * 5.5
        pen.ellipse(0, y - h * 0.6, w * 0.75, h, fill=MOUTH)
        return
    if state == DONE:
        pen.shape([("M", -w * 1.2, y), ("C", -w, y - w * 1.6, w, y - w * 1.6, w * 1.2, y), ("Z",)], fill=MOUTH)
        return
    if state == ERROR:
        pen.shape([("M", -w, y - 2), ("C", -w * 0.4, y + 0.5, w * 0.4, y - 4.5, w, y - 2)],
                  stroke=_shade(MOUTH, 0.9), width=1.6)
        return
    if state == THINKING:
        pen.ellipse(w * 0.6, y - 1.5, 1.9, 1.9, fill=MOUTH)
        return
    if cat:                                        # little "w"
        pen.shape([("M", -w, y), ("C", -w * 0.8, y - 3.5, -w * 0.15, y - 3.5, 0, y - 0.5),
                   ("C", w * 0.15, y - 3.5, w * 0.8, y - 3.5, w, y)], stroke=INK, width=1.5)
    else:
        pen.shape([("M", -w * 0.8, y), ("C", -w * 0.4, y - 3.2, w * 0.4, y - 3.2, w * 0.8, y)],
                  stroke=INK, width=1.6)


def _blush(pen, gap, y, rx=6.0, ry=3.6, alpha=0.55):
    for sx in (-1, 1):
        pen.ellipse(sx * gap, y, rx, ry, fill=BLUSH, alpha=alpha)


def _paws(pen, color, y=-34.0, gap=13.0):
    edge = _shade(color, 0.78)
    for sx in (-1, 1):
        pen.ellipse(sx * gap, y, 9.5, 6.5, fill=color, stroke=edge, width=1.1)
        for t in (-1, 0, 1):                       # toe lines
            pen.shape([("M", sx * gap + t * 3.0, y - 5.5), ("L", sx * gap + t * 3.0, y - 2.5)],
                      stroke=edge, width=0.9)


def _whiskers(pen, x0, y0, length=18.0, alpha=0.45):
    for sx in (-1, 1):
        for k in (-1, 0, 1):
            pen.shape([("M", sx * x0, y0 + k * 3.2), ("L", sx * (x0 + length), y0 + k * 6.5 + 1.5)],
                      stroke=INK, width=0.9, stroke_alpha=alpha)


def _head(pen, rx, ry, color, y=0.0):
    pen.ellipse(0, y, rx, ry, fill=color, stroke=_shade(color, 0.72), width=1.3, stroke_alpha=0.85)


def _tri(pen, a, b, c, fill, stroke=None, width=1.2):
    pen.shape([("M", *a), ("L", *b), ("L", *c), ("Z",)], fill=fill, stroke=stroke, width=width)


# ------------------------------------------------------------------ the animals
def _cat(pen, s, level, phase, perk):
    fur, patch, inner, nose = (0.99, 0.95, 0.89), (0.95, 0.64, 0.36), (1.0, 0.74, 0.80), (0.96, 0.52, 0.62)
    drop = (1 - perk) * 7
    for sx in (-1, 1):
        tip = (sx * (30 + drop * 0.6), 47 - drop)
        _tri(pen, (sx * 33, 10), (sx * 8, 27), tip, fur if sx > 0 else patch, stroke=_shade(fur, 0.7))
        _tri(pen, (sx * 27, 15), (sx * 13, 25), (sx * (28 + drop * 0.5), 39 - drop), inner)
    _head(pen, 37, 31, fur)
    pen.ellipse(-15, 17, 14, 9, fill=patch, alpha=0.95)                 # calico patch
    mode = _face_mode(s, phase)
    _eyes(pen, 13.5, 3, 5.6, mode)
    _blush(pen, 23, -8)
    _tri(pen, (-3.4, -4.5), (3.4, -4.5), (0, -8.2), nose)
    _mouth(pen, -9.5, s, level, phase, w=5.5, cat=True)
    _whiskers(pen, 22, -7)
    _paws(pen, fur)


def _bunny(pen, s, level, phase, perk):
    fur, inner, nose = (1.0, 0.98, 0.98), (1.0, 0.76, 0.84), (0.98, 0.58, 0.70)
    for sx in (-1, 1):
        flop = (1 - perk) * 48 if sx < 0 else (1 - perk) * 10    # left ear flops when not perked
        pen.save()
        pen.translate(sx * 11, 22)
        pen.rotate(-sx * (6 + flop))
        pen.ellipse(0, 22, 8.5, 24, fill=fur, stroke=_shade(fur, 0.75), width=1.2)
        pen.ellipse(0, 22, 4.6, 17, fill=inner)
        pen.restore()
    _head(pen, 34, 30, fur)
    mode = _face_mode(s, phase)
    _eyes(pen, 12.5, 3, 5.6, mode)
    _blush(pen, 21, -8)
    pen.ellipse(0, -5, 3.4, 2.4, fill=nose)
    pen.shape([("M", 0, -7), ("L", 0, -9.5)], stroke=INK, width=1.2)
    _mouth(pen, -10, s, level, phase, w=4.5, cat=True)
    _paws(pen, fur, gap=12)


def _frog(pen, s, level, phase, perk):
    skin, light, pad, pad_dark = (0.55, 0.80, 0.42), (0.83, 0.94, 0.64), (0.33, 0.64, 0.38), (0.24, 0.50, 0.30)
    # lily pad with a notch, and a tiny flower
    pen.shape([("M", 4, -30), ("C", 40, -22, 52, -38, 30, -44), ("C", 10, -50, -34, -50, -46, -40),
               ("C", -54, -32, -30, -22, -4, -30), ("L", 0, -38), ("Z",)], fill=pad, stroke=pad_dark, width=1.2)
    pen.ellipse(-38, -38, 4.5, 3, fill=(1.0, 0.80, 0.88))
    pen.ellipse(-38, -38, 1.6, 1.2, fill=(1.0, 0.92, 0.55))
    lift = perk * 2.5
    for sx in (-1, 1):                                                   # eye bumps
        pen.ellipse(sx * 16, 15 + lift, 13, 12.5, fill=skin, stroke=_shade(skin, 0.72), width=1.3)
    pen.ellipse(0, -8, 41, 26, fill=skin, stroke=_shade(skin, 0.72), width=1.3)
    for sx in (-1, 1):                                                   # cover the seam
        pen.ellipse(sx * 16, 12 + lift, 11.5, 9, fill=skin)
    pen.ellipse(0, -21, 24, 10, fill=light)
    mode = _face_mode(s, phase)
    for sx in (-1, 1):
        if mode in ("open", "look", "sad"):
            pen.ellipse(sx * 16, 16 + lift, 9, 9, fill=WHITE)
        _eye(pen, sx * 16, 16 + lift, 5.2 if mode in ("open", "look", "sad") else 6.5, mode)
    _blush(pen, 27, -8, rx=6.5)
    if s == LISTENING and level > 0.08:
        _mouth(pen, -9, s, level, phase, w=7)
    elif s in (DONE, ERROR, THINKING):
        _mouth(pen, -9, s, level, phase, w=7)
    else:
        pen.shape([("M", -17, -8), ("C", -8, -17, 8, -17, 17, -8)], stroke=INK, width=1.8)


def _bear(pen, s, level, phase, perk):
    fur, muzzle, inner, nose = (0.64, 0.45, 0.30), (0.93, 0.82, 0.67), (0.84, 0.64, 0.48), (0.24, 0.15, 0.11)
    lift = perk * 2
    for sx in (-1, 1):
        pen.ellipse(sx * 25, 23 + lift, 11.5, 11, fill=fur, stroke=_shade(fur, 0.72), width=1.3)
        pen.ellipse(sx * 25, 22 + lift, 6, 5.8, fill=inner)
    _head(pen, 35, 31, fur)
    pen.ellipse(0, -9, 15.5, 11.5, fill=muzzle)
    mode = _face_mode(s, phase)
    _eyes(pen, 14, 6, 5.2, mode)
    _blush(pen, 24, -5, alpha=0.45)
    pen.ellipse(0, -4, 5.6, 4, fill=nose)
    pen.ellipse(-1.6, -2.8, 1.6, 1.0, fill=WHITE, alpha=0.6)
    pen.shape([("M", 0, -8), ("L", 0, -11)], stroke=nose, width=1.3)
    _mouth(pen, -12, s, level, phase, w=4.5, cat=True)
    _paws(pen, fur)


def _fox(pen, s, level, phase, perk):
    fur, cream, dark, inner = (0.97, 0.56, 0.25), (1.0, 0.97, 0.92), (0.33, 0.20, 0.16), (1.0, 0.82, 0.70)
    drop = (1 - perk) * 8
    for sx in (-1, 1):
        tip = (sx * (31 + drop * 0.7), 51 - drop)
        _tri(pen, (sx * 34, 8), (sx * 7, 24), tip, fur, stroke=_shade(fur, 0.7))
        _tri(pen, (sx * 28, 14), (sx * 13, 23), (sx * (29 + drop * 0.6), 40 - drop), inner)
        _tri(pen, (sx * (32 + drop * 0.4), 42 - drop * 0.8), (sx * (24 + drop * 0.4), 44 - drop * 0.8), tip, dark)
    _head(pen, 38, 30, fur)
    for sx in (-1, 1):                                                   # white cheeks
        pen.shape([("M", -sx * 1.5, -3), ("C", sx * 14, 2, sx * 34, -2, sx * 36, -8),
                   ("C", sx * 30, -22, sx * 12, -28, -sx * 1.5, -29), ("Z",)], fill=cream)
    mode = _face_mode(s, phase)
    _eyes(pen, 14, 5, 5.3, mode)
    _blush(pen, 23, -9, alpha=0.45)
    pen.ellipse(0, -6, 4.4, 3.2, fill=dark)
    _mouth(pen, -11, s, level, phase, w=4.5, cat=True)
    _paws(pen, dark, gap=13)


def _otter(pen, s, level, phase, perk):
    fur, face, nose = (0.58, 0.42, 0.31), (0.90, 0.80, 0.68), (0.22, 0.14, 0.11)
    for sx in (-1, 1):
        pen.ellipse(sx * 30, 14 + perk * 2, 7.5, 7, fill=fur, stroke=_shade(fur, 0.72), width=1.2)
        pen.ellipse(sx * 30, 14 + perk * 2, 3.8, 3.5, fill=_shade(fur, 0.75))
    _head(pen, 35, 30, fur)
    pen.ellipse(0, -7, 25, 18, fill=face)
    pen.ellipse(0, 11, 16, 8, fill=face, alpha=0.35)                     # light brow
    mode = _face_mode(s, phase)
    _eyes(pen, 13, 6, 5.2, mode)
    _blush(pen, 21, -5, alpha=0.4)
    pen.ellipse(0, -2, 6.2, 4.3, fill=nose)
    pen.ellipse(-1.8, -0.8, 1.8, 1.1, fill=WHITE, alpha=0.6)
    for sx in (-1, 1):                                                   # whisker dots
        for (dx, dy) in ((9, -6), (12, -9), (8, -10)):
            pen.ellipse(sx * dx, dy, 0.9, 0.9, fill=nose, alpha=0.7)
    _mouth(pen, -12, s, level, phase, w=5, cat=True)
    _whiskers(pen, 15, -8, length=16, alpha=0.35)
    for sx in (-1, 1):                                                   # paws held together
        pen.ellipse(sx * 6.5, -33, 8, 6.5, fill=fur, stroke=_shade(fur, 0.75), width=1.1)


def _owl(pen, s, level, phase, perk):
    body, belly, disc, beak, wing = (0.52, 0.45, 0.72), (0.82, 0.78, 0.94), (0.95, 0.93, 1.0), (0.99, 0.76, 0.32), (0.40, 0.34, 0.60)
    drop = (1 - perk) * 6
    for sx in (-1, 1):                                                   # ear tufts
        _tri(pen, (sx * 30, 18), (sx * 14, 28), (sx * (34 + drop * 0.5), 44 - drop), body, stroke=_shade(body, 0.7))
    for sx in (-1, 1):                                                   # wings
        pen.save()
        pen.translate(sx * 33, -12)
        pen.rotate(sx * (12 + (8 if s == DONE else 0) * math.sin(phase * 9)))
        pen.ellipse(0, 0, 9, 20, fill=wing, stroke=_shade(wing, 0.75), width=1.1)
        pen.restore()
    _head(pen, 35, 35, body, y=-2)
    pen.ellipse(0, -20, 19, 14, fill=belly)
    for (x, y) in ((-8, -16), (0, -14), (8, -16), (-4, -23), (4, -23)):  # feather chevrons
        pen.shape([("M", x - 3, y + 1.5), ("L", x, y - 1), ("L", x + 3, y + 1.5)], stroke=_shade(belly, 0.7), width=1)
    for sx in (-1, 1):
        pen.ellipse(sx * 14.5, 8, 13, 12.5, fill=disc, stroke=_shade(disc, 0.8), width=1)
    mode = _face_mode(s, phase)
    _eyes(pen, 14.5, 8, 6.6, mode)
    _tri(pen, (-4, 1), (4, 1), (0, -7), beak, stroke=_shade(beak, 0.75), width=1)
    if s == LISTENING and level > 0.08:
        pen.ellipse(0, -9, 2.6, 1 + level * 3, fill=MOUTH)


DRAW = {"cat": _cat, "bunny": _bunny, "frog": _frog, "bear": _bear, "fox": _fox, "otter": _otter, "owl": _owl}


# ------------------------------------------------------------------ the whole picture
def glow_color(state, palette):
    if state in (IDLE, LISTENING):
        return palette["glow"]
    if state == THINKING:
        return palette["think"]
    return DONE_GLOW if state == DONE else ERROR_GLOW


def draw(pen, animal: str, state: int, level: float, bloom: float, phase: float, palette: dict,
         character=None) -> None:
    """Draw one frame. bloom 0→1 = popping in; level 0→1 = voice loudness; phase = seconds.

    character: a characters.Pack to draw its pictures instead of a built-in animal.
    """
    if bloom <= 0.02:
        return
    animal = animal if animal in DRAW else DEFAULT_ANIMAL
    if character is not None and character.glow and state in (IDLE, LISTENING):
        palette = {**palette, "glow": character.glow}

    # glow, same feel as the lily
    pulse = 0.5 + 0.5 * math.sin(phase * 2.4)
    strength = {LISTENING: 0.35 + 0.65 * level, THINKING: 0.35 + 0.45 * pulse}.get(state, 0.55)
    pen.glow(0, 0, (50 + 24 * strength) * bloom, glow_color(state, palette), strength)

    # pop in with a little overshoot, then breathe
    scale = 0.35 + 0.65 * bloom + 0.12 * math.sin(math.pi * min(1.0, bloom))
    bob = math.sin(phase * 2.2) * 1.6 + (level * 3.5 if state == LISTENING else 0)
    tilt = {THINKING: math.sin(phase * 1.6) * 9, DONE: math.sin(phase * 7) * 3}.get(state, 0.0)
    perk = {LISTENING: 1.0, DONE: 1.0, THINKING: 0.8, ERROR: 0.1}.get(state, 0.65)
    if character is not None:
        bob = bob if character.bob else 0.0
        tilt = tilt if character.tilt else 0.0
        if state == LISTENING and character.bob:
            scale *= 1 + 0.04 * level                        # pictures can't perk their ears, so they swell

    pen.save()
    pen.translate(0, bob - 2)
    pen.scale(scale, scale)
    pen.rotate(tilt)
    if character is not None:
        size = 108 * character.scale
        pen.image(str(character.image_for(state, level, phase)), 0, 0, size, size)
    else:
        DRAW[animal](pen, state, level, phase, perk)
    pen.restore()

    # extras
    if character is not None and not character.effects:
        return
    if state == THINKING:
        for i in range(3):
            up = max(0.0, math.sin(phase * 5 - i * 0.7)) * 3.5
            pen.ellipse(36 + i * 9, 50 + up, 3.2, 3.2, fill=WHITE, alpha=0.9, stroke=INK, width=0.8, stroke_alpha=0.3)
    elif state == DONE:
        for (x, y, k) in ((40, 34, 1.0), (-42, 22, 0.75), (34, -30, 0.6)):
            r = (4.5 + 1.5 * math.sin(phase * 6 + x)) * k
            pen.shape([("M", x, y + r * 1.6), ("L", x + r * 0.35, y + r * 0.35), ("L", x + r * 1.6, y),
                       ("L", x + r * 0.35, y - r * 0.35), ("L", x, y - r * 1.6), ("L", x - r * 0.35, y - r * 0.35),
                       ("L", x - r * 1.6, y), ("L", x - r * 0.35, y + r * 0.35), ("Z",)],
                      fill=(1.0, 0.92, 0.55), stroke=(0.95, 0.75, 0.3), width=0.8)
    elif state == ERROR:
        pen.shape([("M", 34, 30), ("C", 30, 22, 28, 18, 34, 15), ("C", 40, 18, 38, 22, 34, 30), ("Z",)],
                  fill=(0.70, 0.86, 1.0), stroke=(0.45, 0.65, 0.9), width=1)