"""Animal mascots: drawing (with a fake pen), picking the animal, presets and voice commands."""
import pytest

from heymac import critters, palettes
from heymac.commands import route
from tests.test_aesthetics import PRESETS, RouteMac, env  # noqa: F401  (pytest fixture)


class RecordingPen:
    """Remembers every point drawn so we can check nothing leaves the 150×150 window."""

    def __init__(self):
        self.calls = 0
        self.stack = []
        self.xf = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)       # a b c d tx ty
        self.points = []

    def _pt(self, x, y):
        a, b, c, d, tx, ty = self.xf
        self.points.append((a * x + c * y + tx, b * x + d * y + ty))

    def _mul(self, a2, b2, c2, d2, tx2, ty2):
        a, b, c, d, tx, ty = self.xf
        self.xf = (a * a2 + c * b2, b * a2 + d * b2, a * c2 + c * d2, b * c2 + d * d2,
                   a * tx2 + c * ty2 + tx, b * tx2 + d * ty2 + ty)

    def save(self):
        self.stack.append(self.xf)

    def restore(self):
        self.xf = self.stack.pop()

    def translate(self, x, y):
        self._mul(1, 0, 0, 1, x, y)

    def rotate(self, deg):
        import math
        r = math.radians(deg)
        self._mul(math.cos(r), math.sin(r), -math.sin(r), math.cos(r), 0, 0)

    def scale(self, sx, sy):
        self._mul(sx, 0, 0, sy, 0, 0)

    def ellipse(self, x, y, rx, ry, **kw):
        self.calls += 1
        for px, py in ((x - rx, y), (x + rx, y), (x, y - ry), (x, y + ry)):
            self._pt(px, py)

    def shape(self, cmds, **kw):
        self.calls += 1
        for c in cmds:
            for i in range(1, len(c), 2):
                self._pt(c[i], c[i + 1])

    def glow(self, x, y, radius, color, strength):
        assert radius <= 75
        assert len(color) == 3


@pytest.mark.parametrize("animal", critters.ANIMALS)
@pytest.mark.parametrize("state", range(5))
def test_every_animal_draws_in_every_state(animal, state):
    for phase in (0.0, 0.5, 1.7, 3.45):
        pen = RecordingPen()
        critters.draw(pen, animal, state, 0.8, 1.0, phase, palettes.resolve("coffee"))
        assert pen.calls > 10
        assert not pen.stack                            # every save() was restored
        for x, y in pen.points:
            assert -75 <= x <= 75 and -75 <= y <= 75, (animal, state, x, y)


def test_closed_draws_nothing():
    pen = RecordingPen()
    critters.draw(pen, "cat", critters.IDLE, 0, 0.0, 0, palettes.resolve("lily"))
    assert pen.calls == 0


def test_unknown_animal_falls_back_to_default():
    pen = RecordingPen()
    critters.draw(pen, "dragon", critters.IDLE, 0, 1.0, 0, palettes.resolve("lily"))
    assert pen.calls > 10


def test_every_palette_has_an_animal():
    assert set(critters.MASCOT_FOR_PALETTE) == set(palettes.PALETTES)
    assert set(critters.MASCOT_FOR_PALETTE.values()) <= set(critters.ANIMALS)


@pytest.mark.parametrize("said,expected", [
    ("fox", "fox"), ("a little bunny", "bunny"), ("kitty", "cat"), ("rabbit", "bunny"), ("owls", "owl"),
    ("the flower", "lily"), ("Lily", "lily"), ("dragon", None)])
def test_normalize(said, expected):
    assert critters.normalize(said) == expected


def test_choose():
    assert critters.choose("auto", None, "bear") == "bear"            # follows the preset
    assert critters.choose("auto", "fox", "bear") == "fox"            # you picked one
    assert critters.choose("owl", None, "bear") == "owl"              # config default beats auto
    assert critters.choose("owl", "frog", "bear") == "frog"
    assert critters.choose("lily", "fox", "bear") == "lily"           # config says flower, always
    assert critters.choose(None, None, None) == critters.DEFAULT_ANIMAL


# ----------------------------------------------------------------- with presets
def _with_mascot_hook(env):
    eng = env["eng"]
    shown = []
    eng.mascot_hook = shown.append
    return eng, shown


def test_preset_colors_pick_the_animal(env):
    eng, shown = _with_mascot_hook(env)
    eng.apply("coffee")
    assert shown[-1] == "bear"
    assert eng.current_mascot() == "bear"


def test_preset_can_name_an_animal(env):
    eng, shown = _with_mascot_hook(env)
    eng.presets["coffee"]["mascot"] = "owl"
    ok, msg = eng.apply("coffee")
    assert ok, msg
    assert shown[-1] == "owl"


def test_custom_colors_without_mascot_use_default(env):
    eng, shown = _with_mascot_hook(env)
    eng.apply("rainy_day")
    assert shown[-1] == critters.DEFAULT_ANIMAL


def test_set_mascot_then_preset_then_undo(env):
    eng, shown = _with_mascot_hook(env)
    ok, msg = eng.set_mascot("fox")
    assert ok and "fox" in msg and shown[-1] == "fox"
    eng.apply("coffee")                                  # presets decide while they're on
    assert shown[-1] == "bear"
    eng.undo()                                           # …and undo gives your fox back
    assert shown[-1] == "fox"
    assert eng.state()["lily"] == palettes.DEFAULT


def test_set_mascot_auto_and_bad_names(env):
    eng, shown = _with_mascot_hook(env)
    eng.set_mascot("frog")
    ok, _ = eng.set_mascot("auto")
    assert ok and shown[-1] == "cat"                     # default palette → cat
    ok, msg = eng.set_mascot("dragon")
    assert not ok and "bunny" in msg


def test_config_lily_wins(env):
    eng, shown = _with_mascot_hook(env)
    eng.mascot_setting = "lily"
    ok, msg = eng.set_mascot("bunny")
    assert ok and "auto" in msg
    assert shown[-1] == "lily"


def test_save_keeps_explicit_mascot(env):
    eng, _ = _with_mascot_hook(env)
    eng.set_mascot("otter")
    ok, msg = eng.save("beach day")
    assert ok, msg
    assert eng.presets["beach_day"]["mascot"] == "otter"
    assert 'mascot = "otter"' in env["config"].read_text()


def test_mascot_is_a_known_preset_key(env):
    eng, _ = _with_mascot_hook(env)
    eng.presets["coffee"]["mascot"] = "bear"
    ok, msg = eng.apply("coffee")
    assert "unknown setting" not in msg


# ----------------------------------------------------------------- voice
class MascotMac(RouteMac):
    def set_mascot(self, animal):
        self.called = ("set_mascot", animal)


@pytest.mark.parametrize("said,animal", [
    ("be a fox", "fox"),
    ("hey jarvis turn into a bunny", "bunny"),
    ("make jarvis an owl", "owl"),
    ("change your mascot to a frog", "frog"),
    ("switch my animal to the cat", "cat"),
    ("go back to the lily", "lily"),
    ("can you be a little otter", "otter"),
])
def test_mascot_routes(said, animal):
    mac = MascotMac(dict(PRESETS))
    fn = route(said, mac)
    assert fn is not None, said
    fn()
    assert mac.called == ("set_mascot", animal)


def test_coffee_mode_still_means_the_preset():
    mac = RouteMac(dict(PRESETS))
    route("coffee mode", mac)()
    assert mac.called == ("apply_aesthetic", "coffee")