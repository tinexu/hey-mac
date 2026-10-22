"""Custom characters: a folder of pictures becomes your corner buddy.

  ~/.config/heymac/characters/mochi/
      idle.png          required: how it looks most of the time
      listening.png     optional: while you talk          (falls back to idle)
      thinking.png      optional: while the AI works       (falls back to idle)
      done.png          optional: when it worked           (falls back to idle)
      error.png         optional: when something failed    (falls back to idle)
      blink.png         optional: shown for a split second every few seconds
      talking.png       optional: swapped with listening.png when you're loud, like a moving mouth
      character.toml    optional: name, size, glow color, which effects to use

Square pictures with a transparent background look best (around 512×512 PNG). hey-mac adds the
bobbing, tilting, glow, "…" bubble, sparkles and sweat drop on top, like the built-in animals.
Then say "change your character to mochi", or set mascot = "mochi" in [ui] or in a preset.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

IDLE, LISTENING, THINKING, DONE, ERROR = range(5)
STATE_NAMES = {IDLE: "idle", LISTENING: "listening", THINKING: "thinking", DONE: "done", ERROR: "error"}
EXTRA_NAMES = ("blink", "talking")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".heic", ".webp", ".gif", ".tiff", ".tif")
ROOT = Path.home() / ".config" / "heymac" / "characters"
SETTINGS = {"name", "scale", "glow", "bob", "tilt", "effects"}


class PackError(ValueError):
    pass


def key(name: str) -> str:
    """'Space Cat', 'space_cat', 'space-cat' -> 'space cat' (how names are compared)."""
    return re.sub(r"[\s_-]+", " ", str(name).strip().lower()).strip(" .!?")


def _hex(c: str) -> tuple[float, float, float]:
    c = str(c).strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", c):
        raise PackError(f'glow should look like "#FFB6C1", not "{c}"')
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))


@dataclass
class Pack:
    name: str                                   # the folder name, e.g. "mochi"
    path: Path
    images: dict[str, Path]                     # "idle", "listening", …, "blink", "talking"
    display: str = ""                           # character.toml name, e.g. "Mochi"
    scale: float = 1.0
    glow: tuple[float, float, float] | None = None
    bob: bool = True
    tilt: bool = True
    effects: bool = True
    warnings: list[str] = field(default_factory=list)

    @property
    def keys(self) -> set[str]:
        return {key(self.name), key(self.display)} - {""}

    def image_for(self, state: int, level: float = 0.0, phase: float = 0.0) -> Path:
        imgs = self.images
        if state in (IDLE, LISTENING) and "blink" in imgs and (phase % 3.4) < 0.13:
            return imgs["blink"]
        if state == LISTENING and level > 0.15 and "talking" in imgs and int(phase * 8) % 2 == 0:
            return imgs["talking"]
        return imgs.get(STATE_NAMES.get(state, "idle")) or imgs["idle"]


def load(folder: Path) -> Pack:
    """Read one character folder. Raises PackError with a message a person can act on."""
    folder = Path(folder)
    if not folder.is_dir():
        raise PackError(f"{folder} isn't a folder")
    images: dict[str, Path] = {}
    for f in sorted(folder.iterdir()):
        stem, ext = f.stem.lower(), f.suffix.lower()
        if ext in IMAGE_EXT and (stem in STATE_NAMES.values() or stem in EXTRA_NAMES) and stem not in images:
            images[stem] = f
    if "idle" not in images:
        raise PackError(f"“{folder.name}” needs an idle picture (idle.png)")
    pack = Pack(name=folder.name, path=folder, images=images, display=folder.name)
    settings_file = folder / "character.toml"
    if settings_file.exists():
        try:
            s = tomllib.loads(settings_file.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise PackError(f"“{folder.name}/character.toml” has a typo: {e}") from None
        unknown = set(s) - SETTINGS
        if unknown:
            pack.warnings.append(f"ignored unknown setting(s): {', '.join(sorted(unknown))}")
        pack.display = str(s.get("name", pack.display)).strip() or pack.display
        try:
            pack.scale = max(0.4, min(1.6, float(s.get("scale", 1.0))))
        except (TypeError, ValueError):
            pack.warnings.append("scale should be a number like 1.0")
        if "glow" in s:
            pack.glow = _hex(s["glow"])
        pack.bob, pack.tilt, pack.effects = (bool(s.get(k, True)) for k in ("bob", "tilt", "effects"))
    missing = [n for n in STATE_NAMES.values() if n not in images and n != "idle"]
    if missing:
        pack.warnings.append(f"uses idle for: {', '.join(missing)}")
    return pack


def discover(root: Path | None = None) -> tuple[dict[str, Pack], list[str]]:
    """All valid packs (by folder name) and a list of problems with the broken ones."""
    root = Path(root or ROOT)
    packs: dict[str, Pack] = {}
    problems: list[str] = []
    if not root.is_dir():
        return packs, problems
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        try:
            p = load(folder)
        except PackError as e:
            problems.append(str(e))
            continue
        packs[p.name] = p
    return packs, problems


def find(name: str, root: Path | None = None) -> Pack | None:
    """'mochi' / 'Mochi' / 'space cat' (folder space_cat) → its Pack, else None."""
    k = key(name)
    if not k:
        return None
    packs, _ = discover(root)
    for p in packs.values():
        if k in p.keys:
            return p
    return None


def report(root: Path | None = None) -> str:
    """Human-readable summary for `heymac --characters`."""
    root = Path(root or ROOT)
    packs, problems = discover(root)
    lines = [f"Characters folder: {root}"]
    if not packs and not problems:
        lines.append("  (none yet: make a folder there with an idle.png in it)")
    for p in packs.values():
        extra = f" “{p.display}”" if p.display != p.name else ""
        lines.append(f"  ✓ {p.name}{extra}: {', '.join(sorted(p.images))}")
        lines += [f"      note: {w}" for w in p.warnings]
    lines += [f"  ✗ {msg}" for msg in problems]
    return "\n".join(lines)


TEMPLATE = """# Settings for this character (all optional).
name = "{display}"      # what it's called; you can also say the folder name
scale = 1.0             # 0.4 to 1.6: make it smaller or bigger in the corner
# glow = "#FFB6C1"      # glow color while idle/listening (default: your aesthetic's color)
bob = true              # gently bob up and down
tilt = true             # tilt its head while thinking, wiggle when done
effects = true          # "…" while thinking, sparkles when done, sweat drop on errors

# Pictures in this folder (square, transparent PNG around 512x512 looks best):
#   idle.png (required), listening.png, thinking.png, done.png, error.png,
#   blink.png (flashes every few seconds), talking.png (alternates with listening while you talk)
"""


def new_pack(display: str, root: Path | None = None) -> Path:
    """Make ~/.config/heymac/characters/<name>/ with a commented character.toml."""
    display = str(display).strip()
    folder_name = re.sub(r"[^\w-]+", "_", key(display)).strip("_")
    if not folder_name:
        raise ValueError("Give the character a name, e.g. --new-character Mochi")
    folder = Path(root or ROOT) / folder_name
    if folder.exists():
        raise ValueError(f"{folder} already exists")
    folder.mkdir(parents=True)
    (folder / "character.toml").write_text(TEMPLATE.format(display=display.replace('"', "'")), encoding="utf-8")
    return folder