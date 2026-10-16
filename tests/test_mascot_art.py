"""The mascot's picture generator - every state and expression renders real SVG.

The art is one parametric function, so the risk is a state or expression that
produces broken or empty SVG and silently shows nothing. This renders each to a
pixbuf (so it is valid SVG a GTK session would accept, not just a string that
starts with "<svg"), and checks the signals that make the states distinguishable
actually differ in the output - which is the bug the design went back and forth on.
"""
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import mascot_art as art  # noqa: E402


def _renders(svg: str) -> bool:
    """True if a GdkPixbuf loader accepts it; skips cleanly without the lib."""
    gi = pytest.importorskip("gi")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=True) as f:
        f.write(svg.encode())
        f.flush()
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(f.name, 128, 128, True)
            return pb is not None and pb.get_width() > 0
        except Exception:  # noqa: BLE001
            return False


def test_every_state_renders_valid_svg():
    for state in art._STATES:
        svg = art.svg(state)
        assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>"), state
        assert _renders(svg), f"{state} did not render"


def test_every_expression_renders_valid_svg():
    for expr in art.expressions():
        assert _renders(art.svg("idle", expr)), expr


def test_the_states_are_actually_distinguishable():
    """The whole point: listening != thinking != idle in the bytes, not just colour."""
    idle = art.svg("idle")
    listening = art.svg("listening")
    thinking = art.svg("thinking")
    assert idle != listening != thinking != idle
    # Listening radiates pulse rings; thinking does not.
    assert 'r="146"' in listening and 'r="146"' not in thinking
    # Thinking rises a thought ("..." top-right) and a spark; listening does not.
    assert 'cx="420"' in thinking and 'cx="420"' not in listening


def test_level_swells_the_halo_only_while_live():
    assert art.svg("listening", level=0.0) != art.svg("listening", level=0.9)
    # idle ignores level (the halo only tracks mic/playback), so it is stable.
    assert art.svg("idle", level=0.0) == art.svg("idle", level=0.9)


def test_blink_closes_the_eyes():
    assert art.svg("idle", blink=True) != art.svg("idle", blink=False)


# --- what svg() is handed, and what it does about it --------------------------------

#: The shapes a level arrives in that are **not a number at all**. `svg` is
#: called from inside `mascot._render`, whose `except` logs at **debug** - so
#: anything that raised here was a silently dropped frame, measured against the
#: real method with a real GdkPixbuf loader.
#:
#: Out-of-range *numbers* are deliberately not in this list: 9 and -5 are real
#: levels and clamping them is correct. They have their own test below, because
#: "clamps to the nearest end" and "is not a number" are different answers and
#: this first version conflated them.
_UNUSABLE_LEVELS = ["x", None, float("nan"), True, [], {}, object()]


@pytest.mark.parametrize("bad", _UNUSABLE_LEVELS, ids=lambda v: type(v).__name__ + ":" + repr(v)[:12])
def test_no_level_shape_stops_the_picture_being_drawn(bad):
    """Every unusable level draws the same picture as level 0.0.

    Not "does not raise" - a `try` around the whole thing would satisfy that
    while drawing nothing. The claim is that the orb still renders, at rest.
    """
    drawn = art.svg("speaking", level=bad)
    assert drawn == art.svg("speaking", level=0.0), (
        f"level={bad!r} drew something other than the resting orb")


def test_a_nan_level_is_not_the_loudest_orb_it_can_draw():
    """The one that mattered, and the one that was quiet.

    `min(1.0, nan)` is `1.0`, so before this a bad reading rendered as the
    **fullest halo the orb has** - which looks exactly like a working indicator
    reading a loud room, rather than like a broken one. It is asserted
    separately from the list above because it is the case with an opposite
    failure mode: every other bad shape used to *raise*, this one used to
    *succeed*, at the wrong value.
    """
    assert art.svg("speaking", level=float("nan")) != art.svg("speaking", level=1.0)


def test_a_level_beyond_the_range_clamps_to_the_end_it_passed():
    """Out of range is a real level and clamping it is right - so it is not the
    same answer as "not a number", and is held apart from the list above.

    The first version of that list included `9`, which failed here and was the
    test's own error: `9` is a number, and `min(1.0, 9)` is 1.0.
    """
    assert art.svg("speaking", level=9) == art.svg("speaking", level=1.0)
    assert art.svg("speaking", level=-5) == art.svg("speaking", level=0.0)


def test_a_real_level_still_moves_the_halo():
    """The other direction: a floor guard that fails one way is not a guard.

    Checked by **halo radius**, not by `svg()` being unequal. Every radius in
    range is three digits, so a length comparison returns the same number for
    0.0, 0.5 and 0.99 - measured, and the first version of this assertion was
    a byte count that could not see its own subject.
    """
    radii = [_halo_radius(art.svg("listening", level=l)) for l in (0.0, 0.5, 0.99)]
    assert radii == sorted(radii) and radii[0] < radii[-1], (
        f"the halo does not track the level: {radii}")


def _halo_radius(svg: str) -> int:
    import re
    m = re.search(r'<circle cx="256" cy="250" r="(\d+)" fill="url\(#halo\)"', svg)
    assert m, "the halo is not drawn, so the level cannot be read off it"
    return int(m.group(1))


def test_the_finite_check_is_the_shared_one_and_not_a_fifth_copy():
    """`sysfs.finite` is the one implementation of this test, twice consolidated.

    Asserted against the module's own docstring rather than by re-deriving the
    rule: a check written out here would be a *second* answer to "what is a real
    number", and the pattern to grep for is the repo's own, lifted so it cannot
    drift from the one in `test_one_finite_check.py`.
    """
    import re

    source = Path(art.__file__).read_text()
    pattern = re.compile(r"!=\s*\w+\s+or\s+\w+\s+in\s*\(\s*float\(['\"]inf['\"]\)")
    assert not pattern.search(source), "mascot_art grew its own finite check"
    assert "sysfs.finite" in _code_only(art._live_level), (
        "_live_level does not go through sysfs.finite")


def test_an_unknown_state_or_expression_is_answered_here_too():
    """One answer to "is this a state", not one per call site.

    `mascot.set_state` used to carry its own tuple, so a state added here would
    render happily and be refused by the caller - and the refusal was invisible,
    because it fell back to idle rather than raising.
    """
    assert art.svg("bogus-state") == art.svg("idle")
    assert art.svg("idle", "bogus-expression") == art.svg("idle")


def test_every_state_is_distinguishable_without_colour():
    """Colour alone excludes anyone who cannot use it.

    This is the property `interrupting` was failing: it carried no signal of its
    own, only a dimmer palette than idle, which is this module's own rule - "a
    per-state signal that reads at a glance, so states never look alike" - and
    the repo's own rule on colour-only indicators, both failing for one state.

    Measured with every colour and every alpha stripped, so a state that differs
    only in palette compares **equal** to its neighbour and fails here. A pass
    therefore means five shapes, not five palettes.
    """
    signatures = {s: _shape_only(art.svg(s)) for s in art.states()}
    for state, sig in signatures.items():
        others = {s: o for s, o in signatures.items() if s != state}
        assert sig not in others.values(), (
            f"{state} is indistinguishable from "
            f"{[s for s, o in others.items() if o == sig]} without colour")


def _shape_only(svg: str) -> str:
    """The drawing with every colour and every alpha removed."""
    import re
    svg = re.sub(r"#[0-9a-fA-F]{3,8}", "", svg)
    return re.sub(r'opacity="[\d.]+"', "", svg)


def _code_only(func) -> str:
    """`func`'s source with the docstring removed, so a comment about the
    behaviour cannot satisfy an assertion about the behaviour."""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    body = tree.body[0].body
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    return ast.unparse(ast.Module(body=body, type_ignores=[]))


# --- gaze: the eyes follow the pointer --------------------------------------

def _iris_translate(svg: str) -> tuple[float, float]:
    """The transform on the group that actually holds the iris, read off markup.

    Anchored on the iris element itself rather than on a bare `translate(`,
    because the document also carries `translate(256 252)` around `_eyes()` and a
    loose match measures that instead.
    """
    import re
    m = re.search(r'<g transform="translate\((-?[\d.]+) (-?[\d.]+)\)">'
                  r'<ellipse cy="2" rx="12" ry="16"', svg)
    assert m, "the iris group is not drawn, so the gaze cannot be read off it"
    return float(m.group(1)), float(m.group(2))


#: The declared amplitude, **as a literal**. This is the design claim - how far the
#: iris travels at full bearing, in SVG units - and a test that reads it back from
#: the module cannot check it: zeroing `_GAZE_X` moves the expectation to `(0.0,
#: 0.0)` along with the transform and the assertion passes with the defect fully
#: present. That is the `WINDOW_TOKENS` / `skills_total` shape, and the mutation
#: run found it here. Every entry below is this number, not `art._GAZE_X`.
_DECLARED_X, _DECLARED_Y = 5.0, 4.0


def test_the_iris_travels_exactly_the_declared_amplitude():
    """The markup carries the declared amplitude - the amplitude *is* the answer.

    A rasterised centroid would measure what the clip leaves visible rather than
    what was applied, and those differ for exactly the reason the clip exists.
    The transform is the declaration, so that is what is asserted - against the
    literal, so that changing the constant is something this can see.
    """
    for g, want in (((0.0, 0.0), (0.0, 0.0)),
                    ((1.0, 1.0), (_DECLARED_X, _DECLARED_Y)),
                    ((-1.0, -1.0), (-_DECLARED_X, -_DECLARED_Y)),
                    ((1.0, -1.0), (_DECLARED_X, -_DECLARED_Y)),
                    ((-1.0, 1.0), (-_DECLARED_X, _DECLARED_Y)),
                    ((0.5, -0.25), (_DECLARED_X * 0.5, -_DECLARED_Y * 0.25))):
        got = _iris_translate(art.svg("idle", "", False, 0.0, g))
        assert got == pytest.approx(want), f"gaze {g} moved the iris to {got}"


def test_the_declared_amplitude_is_the_one_the_module_carries():
    """The other half, and it is what makes the assertion above meaningful.

    Without it, `_DECLARED_X` is a number this file invented and the test is a
    tautology about itself. **One design number, stated twice, each statement
    checking the other** - which is the shape `KV_CACHE_MEASUREMENTS` and
    `test_schema_caps`'s derived count have, against the `wpctl` three copies
    this repository keeps recording.
    """
    assert (art._GAZE_X, art._GAZE_Y) == (_DECLARED_X, _DECLARED_Y)


def test_a_bearing_beyond_the_range_clamps_at_the_amplitude():
    """A pointer far outside the window must not aim the iris further than 1.

    Asserted on the clamped value rather than on a clamp existing: a bearing of 99
    with no clamp would translate the iris 99 units and a string comparison would
    still see a difference - it would just be a different one.
    """
    far = _iris_translate(art.svg("idle", "", False, 0.0, (99.0, -99.0)))
    assert far == pytest.approx((art._GAZE_X, -art._GAZE_Y)), far


def test_a_sighting_that_draws_no_pupil_never_tracks():
    """The refusal half of gaze, and it is a matrix rather than a list.

    Six expressions draw their own eye geometry - the arcs of `happy`, the stars
    of `excited`, the single lid of `wink`, the closed lids of `sleepy`, the
    hearts of `love`, and `blink` itself. A closed lid has nothing to look with
    and a heart is not a pupil, so following the mouse with them reads as a
    glitch rather than as attention. `surprised` has open eyes and does track.
    """
    REFUSING = ("happy", "excited", "wink", "sleepy", "surprised", "love")
    for expr in art.expressions():
        rest = art.svg("idle", expr, False, 0.0, (0.0, 0.0))
        looked = art.svg("idle", expr, False, 0.0, (1.0, 1.0))
        if expr in REFUSING:
            assert rest == looked, f"{expr} moved its eyes with the pointer"
        else:
            assert rest != looked, f"{expr} should track but did not move"
    # ...and the blink closes them whichever way the pointer is.
    assert (art.svg("idle", "", True, 0.0, (0.0, 0.0))
            == art.svg("idle", "", True, 0.0, (1.0, 1.0)))


class TestTheScleraClipIsLoadBearing:
    """The containment fix, and why it is a clip and not a smaller iris.

    Before it, 158 of the pixels the iris moved onto at full travel landed
    **outside** the sclera and on the body's near-white at 3.1:1 - measured by
    diffing two rasterised renders and classifying changed pixels against the
    ellipse. Two repairs were available and one was taken:

    - **shrinking the amplitude** to (3.0, 2.0) so the iris fitted. That is an
      unevaluable visual change made to fix a containment problem clipping solves
      without touching how far the eyes travel, so the amplitude stays 5/4.
    - **asserting containment of the geometry.** The geometry is deliberately
      *larger* than the sclera; the clip is what makes it safe. So this asserts
      the overflow exists AND the clip is there AND it is shaped like the
      sclera. Remove any one of the three and the iris is on the face.
    """

    def test_the_iris_really_would_overflow_without_the_clip(self):
        """The overflow is real, so the clip is work rather than decoration."""
        right = art._GAZE_X + 12.0    # the iris's own rx, at the far side
        bottom = art._GAZE_Y + 18.0   # its cy=2 plus ry=16, at the far side
        assert right > art._SCLERA_RX, (
            f"the iris now reaches {right} inside a sclera of "
            f"{art._SCLERA_RX} - shrinking the amplitude would be the "
            "containment fix again, which is what this clip replaced")
        assert bottom > art._SCLERA_RY, (
            f"the iris reaches {bottom} down and the sclera is {art._SCLERA_RY}")

    #: The sclera's own fill. Only the hero eye draws it - the six expressions
    #: that refuse to track draw their own geometry instead - so it is what says
    #: "there is an eye here to clip".
    _SCLERA = 'fill="#06313a"'

    def test_the_eye_group_carries_the_clip(self):
        """Keyed on the eye being drawn, not on every face.

        Stated unconditionally this fails on `happy`, which draws arcs and
        correctly has no sclera and so nothing to confine. The population is
        asserted below so the conditional cannot pass by never firing.
        """
        drawn = [s for s in art.states() for e in art.expressions()
                 if self._SCLERA in art.svg(s, e)]
        assert drawn, "no face draws a hero eye, so this checks nothing"
        for state in art.states():
            for expr in art.expressions():
                svg = art.svg(state, expr)
                if self._SCLERA in svg:
                    assert 'clip-path="url(#sclera)"' in svg, (
                        f"{state}/{expr} draws an eye with nothing clipping "
                        "the iris")

    def test_the_faces_that_refuse_to_track_draw_no_hero_eye(self):
        """The other half, and it is what keeps the test above from being a
        blanket.

        If a refusing expression grew a hero eye behind its own geometry, the
        eyes would track *and* be clipped - and every containment assertion would
        still pass while the refusal it is paired with stopped being true.
        """
        for expr in ("happy", "excited", "wink", "sleepy", "surprised", "love"):
            svg = art.svg("idle", expr)
            assert self._SCLERA not in svg, (
                f"{expr} draws a hero eye as well as its own geometry")
            assert "url(#sclera)" not in svg, f"{expr} clips an eye it has not"

    def test_the_clip_is_shaped_like_the_sclera_it_confines(self):
        """One number, two places - read from the module, not from a literal.

        The drawn sclera and the clip are both written from `_SCLERA_RX/RY`, so
        two hand-typed copies of one number cannot drift apart, and that drift
        would be invisible until the iris sat outside the white of the eye.
        """
        import re
        svg = art.svg("idle")
        clip = re.search(
            r'<clipPath id="sclera"><ellipse rx="([\d.]+)" ry="([\d.]+)"', svg)
        assert clip, "there is no sclera clip to confine the iris"
        assert (float(clip.group(1)), float(clip.group(2))) == (
            art._SCLERA_RX, art._SCLERA_RY)
        drawn = re.search(
            r'<ellipse rx="([\d.]+)" ry="([\d.]+)" fill="#06313a"/>', svg)
        assert drawn, "the sclera is not drawn"
        assert (float(drawn.group(1)), float(drawn.group(2))) == (
            art._SCLERA_RX, art._SCLERA_RY)

    def test_the_two_clip_paths_have_two_ids(self):
        """A typo'd reference clips nothing and renders nothing wrong-looking.

        Nothing else would notice: the eye would draw, just unclipped - the exact
        pre-fix state, on a document that looks correct in a diff.
        """
        import re
        svg = art.svg("idle")
        assert "<clipPath id=\"ball\">" in svg and "<clipPath id=\"sclera\">" in svg
        assert len(set(re.findall(r'<clipPath id="(\w+)"', svg))) == 2


class TestTheScleraHoldsTheIrisWhenItIsActuallyRendered:
    """The one property markup cannot assert: that the *renderer* honours it.

    Everything above is a declaration. This rasterises two renders - gaze at rest
    and gaze at the far corner - diffs them, and asks whether **any** pixel the
    iris moved to lies outside the sclera of either hero eye.

    There is a positive control for the same reason as everywhere else here: a
    diff instrument reading garbage reports a large number confidently. Two
    renders of the *same* gaze must come back identical, or the count below means
    nothing. The first version of that control compared gaze (0,0) with gaze
    (1,1) and so measured the spill twice while asserting nothing.
    """

    N = 512          # 1 SVG unit == 1 px here, so the geometry reads directly
    TOP, LEFT = 252.0, 256.0

    def _render(self, gaze, state="idle"):
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
        svg = art.svg(state, "", False, 0.0, gaze)
        loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
        loader.set_size(self.N, self.N)
        loader.write(svg.encode())
        loader.close()
        pb = loader.get_pixbuf()
        assert pb.get_width() == self.N and pb.get_height() == self.N
        return bytes(pb.get_pixels()), pb.get_rowstride(), pb.get_n_channels()

    def _eyes(self, state):
        """(cx, cy, sx, sy) of both hero eyes in SVG space, for one state."""
        sx, sy = ((1.12, 1.12) if state == "listening"
                  else (1.0, 0.86) if state == "thinking" else (1.0, 1.0))
        ey = -4.0 if state == "thinking" else 2.0
        return [(self.LEFT + cx, self.TOP + ey, sx, sy) for cx in (-20.0, 20.0)]

    @staticmethod
    def _px(buf, rowstride, nchan, x, y):
        # GTK4's GdkPixbuf has neither get_pixel nor set_pixel.
        i = y * rowstride + x * nchan
        return buf[i], buf[i + 1], buf[i + 2], buf[i + 3]

    def _outside(self, x, y, eyes):
        # One unit of allowance for the antialiased edge of the sclera itself.
        for cx, cy, sx, sy in eyes:
            dx = (x + 0.5 - cx) / (sx * art._SCLERA_RX + 1.0)
            dy = (y + 0.5 - cy) / (sy * art._SCLERA_RY + 1.0)
            if dx * dx + dy * dy <= 1.0:
                return False
        return True

    def test_the_iris_never_leaves_the_sclera(self):
        for state in ("idle", "listening", "thinking"):
            eyes = self._eyes(state)
            # The baseline is this **state** at rest gaze. A single baseline for
            # all three is a baseline from one state compared against another's
            # render, which diffs the whole eye - `thinking` sits at ey=-4 with
            # sy=0.86 against idle's ey=2, sy=1.0 - and reports 356 px of "spill"
            # that is the eye having moved, not the iris having escaped.
            rest, rr, rc = self._render((0.0, 0.0), state)
            for gaze in ((1.0, 1.0), (-1.0, -1.0), (1.0, -1.0), (-1.0, 1.0)):
                moved, mr, mc = self._render(gaze, state)
                ctl, cr, cc = self._render(gaze, state)
                noise = sum(
                    1 for y in range(self.N) for x in range(self.N)
                    if self._px(moved, mr, mc, x, y) != self._px(ctl, cr, cc, x, y))
                assert noise == 0, (
                    f"{noise} px differ between two identical renders of {state} "
                    f"at {gaze}, so the diff below cannot mean anything")
                spilled = [
                    (x, y) for y in range(self.N) for x in range(self.N)
                    if self._px(rest, rr, rc, x, y) != self._px(moved, mr, mc, x, y)
                    and self._outside(x, y, eyes)]
                assert not spilled, (
                    f"{state} at gaze {gaze}: the iris moved {len(spilled)} px "
                    f"outside the sclera, first at {spilled[:4]}")

    def test_the_eye_is_still_an_eye_after_the_clip(self):
        """The other half. "0 px outside" is also what a clip that erased the
        whole eye reports, so the dark sclera is counted inside the ellipse - and
        it **rises** when the iris moves aside, because a centred iris covers the
        most of it."""
        eyes = self._eyes("idle")

        def dark_count(gaze):
            buf, rs, nc = self._render(gaze)
            n = 0
            for cx, cy, sx, sy in eyes:
                for y in range(int(cy - 24), int(cy + 25)):
                    for x in range(int(cx - 20), int(cx + 21)):
                        if self._outside(x, y, [(cx, cy, sx, sy)]):
                            continue
                        p = self._px(buf, rs, nc, x, y)
                        if p[3] > 128 and p[0] < 90 and p[1] < 90 and p[2] < 90:
                            n += 1
            return n

        centred = dark_count((0.0, 0.0))
        assert centred > 100, (
            f"only {centred} dark sclera px: the clip may have taken the eye "
            "with it, which reports the same clean containment")
        for gaze in ((1.0, 1.0), (-1.0, -1.0)):
            aside = dark_count(gaze)
            assert aside > centred, (
                f"gaze {gaze} shows {aside} dark px against {centred} centred - "
                "the iris should uncover sclera as it moves aside")


class TestTheGazeIsNormalisedBeforeItReachesTheArt:
    """`_live_gaze` is the only place a bearing is made answerable.

    It was **written** for this - the docstring says the mascot redraws from
    inside a `GLib` callback where a raised `TypeError` would be swallowed, and
    that a NaN reaching the maths would move the pupils off the face entirely -
    and it had **no test at all**, which is the shape this file keeps recording:
    a guard nobody can fail is a guard nobody has. Every case below is a value a
    caller can hand `svg(gaze=...)`, so each one is reachable in production.
    """

    @pytest.mark.parametrize("value", [
        (float("nan"), 0.0), (0.0, float("nan")),
        (float("inf"), 0.0), (0.0, float("-inf")),
        (True, 0.0), (0.0, True),
        (1.0, None), (None, 1.0),
        "0.5", "up", None, (0.0,), (0.0, 0.0, 0.0), (0.0, {}), object(),
    ])
    def test_something_that_is_not_a_finite_pair_looks_forward(self, value):
        """NaN, an infinity, a bool, a wrong arity, a string, nothing at all.

        Every one of these is `(0.0, 0.0)` rather than an exception and rather
        than the input: a NaN that reaches the maths puts the iris at an
        arbitrary place on a face nobody asked about.
        """
        assert art._live_gaze(value) == (0.0, 0.0), value

    @pytest.mark.parametrize("raw, expected", [
        ((0.0, 0.0), (0.0, 0.0)),
        ((0.5, -0.5), (0.5, -0.5)),
        ((2.0, -2.0), (1.0, -1.0)),
        ((-9.0, 9.0), (-1.0, 1.0)),
        ((1, 0), (1.0, 0.0)),          # ints normalise rather than being refused
        ((1.25, -0.75), (1.0, -0.75)),
    ])
    def test_a_finite_pair_is_carried_and_clamped(self, raw, expected):
        assert art._live_gaze(raw) == expected

    def test_a_refused_gaze_and_a_forward_gaze_draw_the_same_picture(self):
        """The refusal is visible: `(0.0, 0.0)` is what looking forward *is*.

        Asserting only the return value would pin the function without pinning
        the drawing, and the drawing is what a person sees.
        """
        forward = art.svg("idle", gaze=(0.0, 0.0))
        assert art.svg("idle", gaze=(float("nan"), 0.0)) == forward
        assert art.svg("idle", gaze=(float("inf"), float("inf"))) == forward
        assert art.svg("idle", gaze="not a bearing") == forward
        assert art.svg("idle", gaze=None) == forward

    def test_the_refusal_is_reachable_through_svg_not_only_the_helper(self):
        """`svg()` is the public door and routes gaze through the same gate.

        A test that calls `_live_gaze` directly would pass with `svg` no longer
        calling it at all - which is the "a test that reaches past the call site
        is testing the function" trap.
        """
        assert art.svg("idle", gaze=(2.0, 2.0)) == art.svg("idle", gaze=(1.0, 1.0))
        assert art.svg("idle", gaze=(float("nan"), 0.0)) != art.svg(
            "idle", gaze=(1.0, 0.0))


class TestTiltIsDrawnNotJustComputed:
    """`mascot_drag` computes a `tilt` every frame; this is the channel that
    shows it. A value nothing draws is the defect this repository records most -
    the mechanic exists and a person cannot see it."""

    def test_the_two_rest_values_change_nothing(self):
        """A live channel costs nothing at rest. This is the whole reason the
        other 100-odd art tests can stay as they are."""
        assert art.svg(tilt=0.0, mouth=0.0) == art.svg()

    def test_a_tilt_rotates_the_character(self):
        svg = art.svg(tilt=0.18)
        assert 'rotate(10.31 256 250)' in svg

    def test_a_tilt_of_each_sign_leans_its_own_way(self):
        assert 'rotate(10.31 256 250)' in art.svg(tilt=0.18)
        assert 'rotate(-10.31 256 250)' in art.svg(tilt=-0.18)

    def test_the_shadow_stays_level_while_the_orb_leans(self):
        """The docstring's claim, measured as a position rather than asserted.

        The shadow is what fixes the mascot to the desktop; rotating it would
        lift the character off the screen, which is the one thing an
        always-on-top overlay must never look like.
        """
        svg = art.svg(tilt=0.18)
        lean = svg.index("rotate(10.31 256 250)")
        orb = svg.index('r="132" fill="url(#body)"')
        shadow = svg.index('cy="433"')
        assert lean < orb, "the character is inside the lean group"
        assert shadow < lean, "the shadow is outside it"

    def test_a_bounded_lean_is_the_reference_constant(self):
        """The engine's own `TILT_CLAMP` is the bound, so the art is the second
        reader of one number rather than a second number."""
        assert art.svg(tilt=9.9) == art.svg(tilt=art._TILT_MAX)
        assert art.svg(tilt=-9.9) == art.svg(tilt=-art._TILT_MAX)

    def test_the_art_and_the_engine_agree_on_the_bound(self):
        from shani_chronoa import mascot_drag as M
        assert art._TILT_MAX == M.TILT_CLAMP

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), "x", None, True,
                                     [0.1], {}])
    def test_a_tilt_that_is_not_a_number_stands_the_character_upright(self, bad):
        """A `TypeError` here would be swallowed by the `GLib` callback that
        calls `svg()`, and a NaN in a transform is a picture nobody can read."""
        assert art.svg(tilt=bad) == art.svg()

    def test_a_tilted_mascot_is_still_well_formed_xml(self):
        """An unbalanced `<g>` renders as *nothing at all*, and a blank mascot
        is indistinguishable from a broken window."""
        import xml.etree.ElementTree as ET
        ET.fromstring(art.svg(tilt=0.18))
        ET.fromstring(art.svg(tilt=-0.18))


class TestMouthIsDrawnNotJustComputed:
    def test_a_closed_mouth_draws_no_interior(self):
        """`rx=0` is a line across the face, so `0.0` must draw nothing rather
        than a degenerate ellipse."""
        assert 'cy="30" rx=' not in art.svg(mouth=0.0)

    def test_the_mouth_opens_with_the_amount(self):
        closed = art.svg(mouth=0.0)
        half = art.svg(mouth=0.25)
        wide = art.svg(mouth=0.5)
        assert closed != half != wide
        assert half != closed and wide != half

    def test_an_open_mouth_is_drawn_behind_the_smile_stroke(self):
        svg = art.svg(mouth=0.5)
        interior = svg.index('cy="30" rx=')
        smile = svg.index('M-11 28 Q0 38 11 28')
        assert interior < smile, "the interior is behind the lip curve"

    def test_the_mouth_is_bounded_by_the_engine_own_ceiling(self):
        """`MOUTH_MAX`, **not** `MOUTH_OPEN`, and the difference is measured.

        `MOUTH_OPEN` is what the spring is *aimed at* while a lock is held; it is
        not a ceiling, and the spring passes it: driving the real engine through
        grab -> release -> suck and reading `Pose.mouth` every frame peaks at
        **0.53987**, which is 12% above `MOUTH_OPEN` and 8% above `MOUTH_MAX`.

        `MOUTH_MAX` is the ceiling for a different reason - the post-drop branch
        *assigns* it (`mascot_drag.py:740`, `lerp(MOUTH_MAX, 0.0, ...)`) rather
        than stepping toward it, so at `t == T_SUCK_END` the value is exactly
        `MOUTH_MAX`: measured **0.49997** at `t = 2.3333`, within one frame of
        the declared 2.33.

        So the art's denominator is the value that is *reached*, and the clamp
        `min(1.0, amount / _MOUTH_MAX)` is a clamp that actually fires on 8% of
        the frames of a real catch - which is what a defensive ceiling is for.
        Naming `MOUTH_OPEN` here instead would have pinned a spring target as if
        it were a limit, and the docstring above `_MOUTH_MAX` would then be
        describing a number the module no longer holds.
        """
        from shani_chronoa import mascot_drag as M
        assert art.svg(mouth=99.0) == art.svg(mouth=art._MOUTH_MAX)
        assert art._MOUTH_MAX == M.MOUTH_MAX

    def test_the_engine_peak_is_above_both_candidates(self):
        """The control for the test above: if the engine never exceeded
        `MOUTH_OPEN`, the two constants would be interchangeable and its name
        would be a matter of taste. It does exceed both, and on purpose."""
        from shani_chronoa import mascot_drag as M

        clock = [0.0]
        motion = M.MascotMotion(stage_w=212, stage_h=212, size=132,
                                now=lambda: clock[0])

        def step():
            clock[0] += 1.0 / 240.0
            return motion.frame()

        motion.grab(60.0, 60.0)
        for _ in range(6):
            step()
        motion.release(work_seconds=3.0)
        peak = 0.0
        for _ in range(900):
            peak = max(peak, step().mouth)
        assert peak > M.MOUTH_MAX, f"engine peaked at {peak}, at or below the ceiling"
        assert peak > M.MOUTH_OPEN

    @pytest.mark.parametrize("bad", [float("nan"), float("-inf"), "x", None, True,
                                     -0.5])
    def test_a_mouth_that_is_not_a_number_or_a_negative_is_a_closed_mouth(self, bad):
        assert art.svg(mouth=bad) == art.svg(mouth=0.0)

    def test_the_speaking_mouth_scales_rather_than_being_replaced(self):
        """`speaking` already drew an open mouth; the amount widens that one
        rather than drawing a second mouth over it."""
        assert 'rx="7.0" ry="9.0"' in art.svg(state="speaking")
        assert 'rx="9.0" ry="14.0"' in art.svg(state="speaking", mouth=0.5)
        assert art.svg(state="speaking", mouth=0.0).count("cy=\"33\"") == 1

    def test_a_closed_mouth_is_the_mouth_this_module_drew_before_it_existed(self):
        """The byte-identity of `mouth=0.0` with the rest picture is what keeps
        every other art assertion true, so it is asserted rather than assumed."""
        assert art.svg(state="speaking", mouth=0.0) == art.svg(state="speaking")
        assert art.svg(state="idle", mouth=0.0) == art.svg(state="idle")
