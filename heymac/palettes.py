"""Color themes for the lily, so a preset can recolor it ("coffee" = latte petals, amber glow).

A palette is a dict of RGB tuples (0..1):
  back / front : (base, tip) gradient for the three back and three front petals
  glow         : glow while idle/listening
  think        : glow while thinking
  stamen, filament, center : the flower's middle

Use a built-in name, or define your own in config with three hex colors:
  lily = { petal = "#8B5A3C", tip = "#F5E6D3", glow = "#D4A373" }
"""
from __future__ import annotations

import json
import re

RGB = tuple[float, float, float]

PALETTES: dict[str, dict] = {
    "lily": dict(back=((0.90, 0.40, 0.63), (1.00, 0.84, 0.92)), front=((0.97, 0.52, 0.72), (1.00, 0.97, 0.98)),
                 glow=(1.00, 0.38, 0.72), think=(0.60, 0.45, 1.00),
                 stamen=(0.78, 0.36, 0.14), filament=(0.95, 0.90, 0.75), center=(0.80, 0.92, 0.55)),
    "coffee": dict(back=((0.42, 0.26, 0.16), (0.86, 0.72, 0.56)), front=((0.60, 0.40, 0.24), (0.97, 0.90, 0.80)),
                   glow=(0.88, 0.58, 0.28), think=(0.72, 0.48, 0.32),
                   stamen=(0.30, 0.17, 0.08), filament=(0.98, 0.93, 0.85), center=(0.95, 0.86, 0.70)),
    "matcha": dict(back=((0.36, 0.52, 0.28), (0.80, 0.88, 0.66)), front=((0.50, 0.66, 0.36), (0.95, 0.98, 0.88)),
                   glow=(0.55, 0.82, 0.40), think=(0.42, 0.64, 0.52),
                   stamen=(0.45, 0.33, 0.18), filament=(0.97, 0.98, 0.90), center=(0.98, 0.95, 0.80)),
    "ocean": dict(back=((0.12, 0.38, 0.62), (0.66, 0.85, 0.95)), front=((0.22, 0.55, 0.80), (0.92, 0.97, 1.00)),
                  glow=(0.25, 0.65, 0.95), think=(0.42, 0.45, 0.95),
                  stamen=(0.95, 0.80, 0.40), filament=(0.95, 0.98, 1.00), center=(1.00, 0.95, 0.75)),
    "sakura": dict(back=((0.93, 0.62, 0.72), (1.00, 0.90, 0.93)), front=((0.98, 0.74, 0.82), (1.00, 0.98, 0.99)),
                   glow=(1.00, 0.66, 0.80), think=(0.80, 0.62, 0.95),
                   stamen=(0.85, 0.45, 0.55), filament=(1.00, 0.95, 0.96), center=(0.98, 0.92, 0.60)),
    "midnight": dict(back=((0.20, 0.16, 0.42), (0.55, 0.50, 0.85)), front=((0.32, 0.26, 0.62), (0.82, 0.80, 0.98)),
                     glow=(0.50, 0.42, 1.00), think=(0.30, 0.55, 0.95),
                     stamen=(0.92, 0.86, 0.55), filament=(0.90, 0.90, 1.00), center=(0.95, 0.95, 1.00)),
    "sunset": dict(back=((0.90, 0.36, 0.24), (1.00, 0.78, 0.55)), front=((0.98, 0.50, 0.32), (1.00, 0.92, 0.78)),
                   glow=(1.00, 0.52, 0.28), think=(0.92, 0.40, 0.55),
                   stamen=(0.55, 0.20, 0.15), filament=(1.00, 0.95, 0.85), center=(1.00, 0.88, 0.45)),
}
DEFAULT = "lily"


def from_hex(h: str) -> RGB:
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        raise ValueError(f"Not a hex color: #{h}")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _scale(c: RGB, k: float) -> RGB:
    return tuple(max(0.0, min(1.0, v * k)) for v in c)


def _mix(a: RGB, b: RGB, t: float) -> RGB:
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def custom(petal: str, tip: str, glow: str | None = None) -> dict:
    """Build a full palette from two or three hex colors."""
    p, t = from_hex(petal), from_hex(tip)
    g = from_hex(glow) if glow else _mix(p, t, 0.3)
    return dict(back=(_scale(p, 0.82), _scale(t, 0.92)), front=(p, t), glow=g,
                think=_mix(g, (0.60, 0.45, 1.00), 0.45), stamen=_scale(p, 0.5), filament=t, center=_mix(t, g, 0.3))


def encode(spec) -> str:
    """What gets passed to the UI thread: a palette name or a small JSON string."""
    if isinstance(spec, dict):
        return json.dumps({k: str(v) for k, v in spec.items()})
    return str(spec or DEFAULT).strip().lower()


def is_valid(spec) -> bool:
    try:
        resolve(spec, strict=True)
        return True
    except (KeyError, ValueError, TypeError):
        return False


def resolve(spec, strict: bool = False) -> dict:
    """Name, {petal,tip,glow} dict, or an encoded string -> full palette dict."""
    if isinstance(spec, str) and spec.strip().startswith("{"):
        spec = json.loads(spec)
    if isinstance(spec, dict) or (hasattr(spec, "keys") and not isinstance(spec, str)):
        d = {str(k): str(v) for k, v in dict(spec).items()}
        if "petal" not in d or "tip" not in d:
            if strict:
                raise KeyError("custom lily colors need at least petal and tip")
            return PALETTES[DEFAULT]
        return custom(d["petal"], d["tip"], d.get("glow"))
    name = str(spec or DEFAULT).strip().lower()
    if name in PALETTES:
        return PALETTES[name]
    if strict:
        raise KeyError(f"Unknown lily palette “{name}”. Built-in: {', '.join(PALETTES)}")
    return PALETTES[DEFAULT]
