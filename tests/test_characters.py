"""Custom characters: folders of pictures."""
from pathlib import Path

import pytest

from heymac import characters as ch
from heymac import critters, palettes
from heymac.commands import route
from tests.test_aesthetics import PRESETS, RouteMac, env  # noqa: F401  (pytest fixture)
from tests.test_critters import RecordingPen

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "characters" / "ghost"


def make_pack(root: Path, name: str, files=("idle.png",), toml: str | None = None) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    for f in files:
        (folder / f).write_bytes(b"\x89PNG")
    if toml is not None:
        (folder / "character.toml").write_text(toml)
    return folder


# ----------------------------------------------------------------- loading
def test_example_ghost_is_complete():
    p = ch.load(EXAMPLE)
    assert set(p.images) == {"idle", "listening", "thinking", "done", "error", "blink", "talking"}
    assert p.display == "Boo" and p.glow is not None and not p.warnings


def test_minimal_pack_falls_back_to_idle(tmp_path):
    p = ch.load(make_pack(tmp_path, "mochi"))
    for state in range(5):
        assert p.image_for(state).name == "idle.png"
    assert "uses idle for" in p.warnings[0]


def test_state_pictures_blink_and_talking(tmp_path):
    files = ("idle.png", "Listening.PNG", "thinking.jpg", "done.webp", "blink.png", "talking.png", "notes.txt")
    p = ch.load(make_pack(tmp_path, "mochi", files))
    assert p.image_for(ch.THINKING, phase=1.0).name == "thinking.jpg"
    assert p.image_for(ch.DONE).name == "done.webp"
    assert p.image_for(ch.ERROR).name == "idle.png"
    assert p.image_for(ch.IDLE, phase=3.45).name == "blink.png"                 # blinks every 3.4 s
    assert p.image_for(ch.LISTENING, level=0.8, phase=0.5).name == "talking.png"
    assert p.image_for(ch.LISTENING, level=0.8, phase=0.65).name == "Listening.PNG"   # mouth flaps
    assert p.image_for(ch.LISTENING, level=0.0, phase=0.5).name == "Listening.PNG"    # quiet = mouth shut


def test_settings(tmp_path):
    p = ch.load(make_pack(tmp_path, "space_cat", toml='name = "Space Cat"\nscale = 9\nglow = "#ff0080"\n'
                                                       'tilt = false\nsparkle = true\n'))
    assert p.display == "Space Cat" and p.scale == 1.6 and p.glow == (1.0, 0.0, 128 / 255)
    assert p.tilt is False and p.bob is True
    assert any("sparkle" in w for w in p.warnings)
    assert p.keys == {"space cat"}


@pytest.mark.parametrize("files,toml,problem", [
    (("listening.png",), None, "needs an idle picture"),
    (("idle.png",), "name = ", "has a typo"),
    (("idle.png",), 'glow = "pink"', "#FFB6C1"),
])
def test_broken_packs_are_explained(tmp_path, files, toml, problem):
    make_pack(tmp_path, "bad", files, toml)
    make_pack(tmp_path, "good")
    packs, problems = ch.discover(tmp_path)
    assert list(packs) == ["good"]
    assert problem in problems[0]


def test_find_and_report(tmp_path):
    make_pack(tmp_path, "space_cat", toml='name = "Cosmo"')
    assert ch.find("space cat", tmp_path).name == "space_cat"
    assert ch.find("Cosmo", tmp_path).name == "space_cat"
    assert ch.find("dragon", tmp_path) is None
    assert ch.find("anything", tmp_path / "missing") is None
    text = ch.report(tmp_path)
    assert "✓ space_cat “Cosmo”" in text


def test_new_pack(tmp_path):
    folder = ch.new_pack("Mochi Bun", tmp_path)
    assert folder.name == "mochi_bun" and (folder / "character.toml").exists()
    assert 'name = "Mochi Bun"' in (folder / "character.toml").read_text()
    with pytest.raises(ValueError, match="already exists"):
        ch.new_pack("mochi bun", tmp_path)
    with pytest.raises(ValueError, match="name"):
        ch.new_pack("  ", tmp_path)
    (folder / "idle.png").write_bytes(b"x")
    assert ch.load(folder).display == "Mochi Bun"                           # the template is valid TOML


# ----------------------------------------------------------------- drawing
class ImagePen(RecordingPen):
    def __init__(self):
        super().__init__()
        self.images = []

    def image(self, path, x, y, w, h, alpha=1.0):
        self.images.append(Path(path).name)
        for px, py in ((x - w / 2, y - h / 2), (x + w / 2, y + h / 2)):
            self._pt(px, py)


@pytest.mark.parametrize("state", range(5))
def test_pack_draws_inside_the_window(state):
    pack = ch.load(EXAMPLE)
    pen = ImagePen()
    critters.draw(pen, "ghost", state, 0.9, 1.0, 1.0, palettes.resolve("lily"), character=pack)
    assert pen.images == [pack.image_for(state, 0.9, 1.0).name]
    assert not pen.stack
    for x, y in pen.points:
        assert -75 <= x <= 75 and -75 <= y <= 75


def test_effects_off(tmp_path):
    pack = ch.load(make_pack(tmp_path, "plain", toml="effects = false"))
    pen = ImagePen()
    critters.draw(pen, "plain", critters.DONE, 0, 1.0, 0.3, palettes.resolve("lily"), character=pack)
    assert pen.calls == 0 and pen.images == ["idle.png"]          # no sparkles


# ----------------------------------------------------------------- choosing
def test_normalize_and_choose_with_packs(tmp_path):
    make_pack(tmp_path, "mochi", toml='name = "Mochi"')
    make_pack(tmp_path, "cat")                                     # a custom "cat" beats the built-in
    packs, _ = ch.discover(tmp_path)
    assert critters.normalize("my mochi character", packs) == "mochi"
    assert critters.normalize("Mochi", packs) == "mochi"
    assert critters.normalize("cat", packs) == "cat"
    assert critters.normalize("fox", packs) == "fox"
    assert critters.normalize("dragon", packs) is None
    assert critters.choose("auto", "mochi", "bear", packs=packs) == "mochi"
    assert critters.choose("mochi", None, "bear", packs=packs) == "mochi"
    assert critters.choose("auto", "mochi", "bear", packs=()) == "bear"   # pack was deleted


def test_set_mascot_to_a_pack(env):
    eng = env["eng"]
    shown = []
    eng.mascot_hook = shown.append
    make_pack(eng.characters_dir, "mochi", toml='name = "Mochi"')
    ok, msg = eng.set_mascot("Mochi")
    assert ok and msg == "Ta-da, I'm Mochi now." and shown[-1] == "mochi"
    ok, msg = eng.set_mascot("dragon")
    assert not ok and "mochi" in msg
    eng.presets["coffee"]["mascot"] = "mochi"                      # presets can use it too
    ok, msg = eng.apply("coffee")
    assert shown[-1] == "mochi"


def test_config_default_can_be_a_pack(env):
    eng = env["eng"]
    make_pack(eng.characters_dir, "mochi")
    eng.mascot_setting = "Mochi"
    assert eng.current_mascot() == "mochi"


# ----------------------------------------------------------------- voice
class PackMac(RouteMac):
    def __init__(self, presets, packs=("mochi",)):
        super().__init__(presets)
        self.aesthetics.find_character = lambda name: name if name in packs else None

    def set_mascot(self, name):
        self.called = ("set_mascot", name)


@pytest.mark.parametrize("said,expected", [
    ("change your character to mochi", ("set_mascot", "mochi")),
    ("set your mascot to space cat", ("set_mascot", "space cat")),      # unknown → set_mascot explains
    ("be mochi", ("set_mascot", "mochi")),
    ("switch to mochi", ("set_mascot", "mochi")),
    ("use my mochi character", ("set_mascot", "mochi")),
    ("be a fox", ("set_mascot", "fox")),
])
def test_character_routes(said, expected):
    mac = PackMac(dict(PRESETS))
    fn = route(said, mac)
    assert fn is not None, said
    fn()
    assert mac.called == expected


def test_switch_to_an_app_still_works():
    mac = PackMac(dict(PRESETS))
    route("switch to chrome", mac)()
    assert mac.called == ("open_app", "Google Chrome")


@pytest.mark.parametrize("said", [
    "match my aesthetic", "revert my character", "reset your character", "change your mascot back",
    "change your character back to normal", "go back to your normal character", "undo your character",
])
def test_reset_character(said):
    mac = PackMac(dict(PRESETS))
    route(said, mac)()
    assert mac.called == ("set_mascot", "auto")


def test_matcha_still_works():
    mac = PackMac(dict(PRESETS))
    route("matcha mode", mac)()
    assert mac.called == ("apply_aesthetic", "matcha")