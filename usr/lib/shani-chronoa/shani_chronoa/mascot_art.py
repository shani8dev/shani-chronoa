"""The mascot's picture, drawn as one parametric SVG - Chronoa as a ShaniOS AI.

An orbit sprite: a glossy orb (its glowing core) with the gold **Shani/Saturn**
ring and sense-nodes orbiting it, a refined neural **mind** inside - a small,
deliberate constellation of glowing nodes, the digital-AI tell - and a soft
holographic base, a presence projected onto the desktop. Small, simple eyes
(not anime) so it stays an emblem, not a cartoon. Original character; the
Saturn-orb is Chronoa's own identity (see the app icon).

`svg(state, expression, blink, level)` returns the whole image as a string - one
source of truth, no folder of near-duplicate files. The companion renders it to
a texture; everything here is pure and tested (`tests/test_mascot_art.py`).

- `state` recolours the core/halo and lights the mind: idle/speaking teal,
  listening green, thinking amber (mind pulses, a spark rises), interrupting a
  dimmed teal behind a **cut ring** - the five `AssistantState`s the window orb
  uses, so they agree. Every state carries a signal that is *shape* and not only
  colour, so none of them looks alike without colour.
- `expression` briefly overrides the face: happy, excited (star eyes), wink,
  concerned - a good result, a greeting, a failure.
- `blink` closes the eyes a frame; `level` (0..1) swells the halo and the mind's
  glow to the live mic/playback level.

**`svg()` never raises**, because it is drawn inside `mascot._render()`, whose
`except` logs at debug - so a value it could not use would silently stop the
picture updating, and a level that reached the maths as NaN would pin the halo
to full volume and read as "loud" rather than as "broken". Every input is
answered here instead, which is why `_live_level` exists and why the inputs are
checked in one place rather than at each of the five call sites.
"""
from __future__ import annotations

import math
from typing import Dict, List, Tuple

from shani_chronoa import sysfs

_PALETTE: Dict[str, Dict[str, str]] = {
    "idle":         {"c0": "#e6fbff", "c1": "#34c6bd", "c2": "#0a6670", "halo": "#63ecdf", "net": "#bffff4"},
    "listening":    {"c0": "#e9ffe8", "c1": "#40cc77", "c2": "#1b8a4e", "halo": "#5ff09a", "net": "#d6ffe6"},
    "thinking":     {"c0": "#fff3cf", "c1": "#f5b740", "c2": "#d9821a", "halo": "#ffd166", "net": "#fff0c6"},
    "speaking":     {"c0": "#e6fbff", "c1": "#33c3ba", "c2": "#0d7f8c", "halo": "#5fe8dd", "net": "#bffff4"},
    "interrupting": {"c0": "#eef3f4", "c1": "#8fb6b8", "c2": "#4e6f72", "halo": "#9fd0cf", "net": "#d6e6e6"},
}
_STATES = tuple(_PALETTE)
_EXPRESSIONS = ("happy", "excited", "wink", "concerned", "sleepy", "surprised", "love")


def states() -> Tuple[str, ...]:
    """The states this module can draw, as the one list of them.

    `mascot.set_state` validates against this rather than carrying its own
    tuple: it used to, so a state added to `_PALETTE` would render happily here
    and be refused by the caller - two answers to "is this a state", and the one
    that is wrong is the one nobody tests.
    """
    return _STATES


def expressions() -> Tuple[str, ...]:
    """The expressions that override the face. The caller's counterpart to `states()`."""
    return _EXPRESSIONS

#: The mind: a deliberate little neural web (not scattered), centred in the orb.
#: Nodes in orb-space (centre 256,250), and the edges that connect them.
#: Kept clear of the face box (~x 226-286, y 230-282): a top arc, the sides,
#: and a lower arc, so the web reads around the eyes rather than through them.
_NODES: List[Tuple[int, int, float]] = [
    (210, 182, 2.8), (256, 168, 3.4), (302, 182, 2.8),      # top arc
    (178, 236, 2.4), (334, 236, 2.4),                        # sides
    (206, 308, 2.6), (256, 324, 3.0), (306, 308, 2.6),       # lower arc
]
_EDGES = [(0, 1), (1, 2), (0, 3), (2, 4), (3, 5), (4, 7), (5, 6), (6, 7), (1, 6)]


def _mind(net: str, op: float) -> str:
    lines = "".join(
        f'<line x1="{_NODES[a][0]}" y1="{_NODES[a][1]}" x2="{_NODES[b][0]}" y2="{_NODES[b][1]}"/>'
        for a, b in _EDGES)
    glow = "".join(f'<circle cx="{x}" cy="{y}" r="{r*2.6:.1f}"/>' for x, y, r in _NODES)
    dots = "".join(f'<circle cx="{x}" cy="{y}" r="{r:.1f}"/>' for x, y, r in _NODES)
    return (f'<g clip-path="url(#ball)" opacity="{op:.2f}">'
            f'<g stroke="{net}" stroke-width="1.1" opacity="0.55">{lines}</g>'
            f'<g fill="{net}" opacity="0.18">{glow}</g>'
            f'<g fill="#ffffff">{dots}</g></g>')


def _live_gaze(value: object) -> Tuple[float, float]:
    """The pointer's bearing as a gaze vector, each axis clamped to -1..1.

    Answered here rather than at the caller, for `_live_level`'s reason: the
    mascot redraws from inside a `GLib` callback where a raised `TypeError`
    would be swallowed, and a NaN that reached the maths would move the pupils
    off the face entirely rather than looking anywhere in particular.

    `sysfs.finite` is asked about each **raw** element, not about a float made
    from it, for the reason `_live_level` documents: `float(True)` is 1.0, so
    coercing first would hand back the `bool` refusal the check exists to
    provide. After it returns True the element is an `int`/`float` that is
    neither a bool, a NaN nor an infinity, so the coercion and the clamp below
    cannot raise - and this cannot become a second copy of the finite check the
    repo consolidated into `sysfs`.
    """
    try:
        dx, dy = value          # type: ignore[misc]
    except (TypeError, ValueError):
        return (0.0, 0.0)
    if not (sysfs.finite(dx) and sysfs.finite(dy)):
        return (0.0, 0.0)
    return (max(-1.0, min(1.0, float(dx))), max(-1.0, min(1.0, float(dy))))


#: A tilt of at most this many radians, in either direction. `mascot_drag`'s own
#: `TILT_CLAMP` is the same number: the engine has already bounded what it will
#: ask for, so this is the second reader's bound rather than a new one, and the
#: art must not be the thing that decides a drag looks wrong.
_TILT_MAX = 0.18


def _live_tilt(value: object) -> float:
    """A lean in radians, clamped to `-_TILT_MAX.._TILT_MAX`.

    Answered here for `_live_gaze`'s reason: the mascot redraws from inside a
    `GLib` callback where a raised `TypeError` is swallowed, and a NaN that
    reached `rotate()` would produce a transform the renderer refuses - which
    looks like a broken window rather than a bad number.
    """
    if not sysfs.finite(value):
        return 0.0
    try:
        degrees = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(-_TILT_MAX, min(_TILT_MAX, degrees))


#: The mouth opens as a fraction of its widest, so `1.0` is `MOUTH_MAX` in
#: `mascot_drag` - the engine's own ceiling, named once here rather than
#: re-derived into a second number that could disagree with it.
#:
#: **It is `MOUTH_MAX` and not `MOUTH_OPEN`, and that is measured rather than
#: picked.** `MOUTH_OPEN` is the spring's *target* while a lock is held, so the
#: spring passes it; `MOUTH_MAX` is *assigned* outright by the post-drop branch
#: (`mascot_drag.py:740`), so the engine reaches it exactly - measured 0.49997
#: at `t = T_SUCK_END`. A real grab -> release -> suck peaks at **0.53987**,
#: which is above both, so the clamp below is a clamp that fires on 8% of a
#: catch's frames rather than a number nothing ever reaches. `MOUTH_OPEN` would
#: have been a spring target wearing a ceiling's name.
_MOUTH_MAX = 0.5


def _live_mouth(value: object) -> float:
    """How far the mouth is open, `0.0` closed and `_MOUTH_MAX` widest."""
    if not sysfs.finite(value):
        return 0.0
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(_MOUTH_MAX, amount))


#: The sclera, in the eye group's own units. Written once and used by both the
#: ellipse that is drawn and the `clipPath` that confines the iris to it: two
#: hand-typed copies of one number drift apart, and the drift is invisible until
#: the iris is outside the white of the eye.
_SCLERA_RX, _SCLERA_RY = 15.0, 20.0

#: How far the iris may travel, in SVG units. This is a *design* number - how
#: much of the gaze a person reads - and deliberately not a containment one.
#: The iris is `rx=12 ry=16` about `cy=2`, so at 5/4 its painted extent reaches
#: 17 right and 22 down, past a sclera of 15 and 20: measured, 158 of those
#: pixels land on the body at 512px with a worst contrast of 3.1:1. The
#: `clipPath` is what keeps the iris inside; the amplitude is what it is trimmed
#: to, so shrinking the amplitude instead would be an unevaluable visual change
#: made to fix a containment problem that clipping solves without touching it.
_GAZE_X, _GAZE_Y = 5.0, 4.0


def _open_mouth(amount: float) -> str:
    """The mouth's interior, growing with how far it is open.

    Drawn *behind* the smile stroke, which is why the stroke is left where it
    was: an opening mouth keeps the same lower-lip curve and gains a dark
    interior behind it, so `0.0` is byte-identical to the mouth this module drew
    before `mouth` existed and a half-open one is the same mouth rather than a
    different one.

    **The empty string at `0.0` is the whole point**, and it is why this is a
    separate function rather than an `rx`/`ry` on a fixed ellipse: a mouth that
    is closed must draw nothing at all, because a zero-height ellipse is a line
    across the face and `rx=0` is a rendering warning in some renderers.
    """
    if amount <= 0.0:
        return ""
    open_frac = min(1.0, amount / _MOUTH_MAX)
    return (f'<ellipse cx="0" cy="30" rx="{6.0 + 3.0 * open_frac:.1f}" '
            f'ry="{2.0 + 8.0 * open_frac:.1f}" fill="#07343a"/>')


def _eyes(expression: str, state: str, blink: bool,
          gaze: Tuple[float, float] = (0.0, 0.0),
          mouth: float = 0.0) -> str:
    """Small, simple eyes + mouth. Expression wins; else the state decides.

    `gaze` moves the iris inside the hero eye and nothing else. The faces that
    draw their own eye geometry - the arcs of `happy`, the stars of `excited`,
    the single lid of `wink`, the closed lids of `sleepy` and `blink`, the
    hearts of `love` - deliberately do **not** track the pointer: a closed lid
    has nothing to look with, and a heart is not a pupil. Following the mouse
    with those would read as a glitch rather than as attention.
    """
    cheeks = ('<ellipse cx="-40" cy="24" rx="12" ry="7" fill="#ff9ea0" opacity="0.5"/>'
              '<ellipse cx="40" cy="24" rx="12" ry="7" fill="#ff9ea0" opacity="0.5"/>')
    smile = (cheeks + _open_mouth(mouth) +
             '<path d="M-11 28 Q0 38 11 28" fill="none" stroke="#07343a" stroke-width="3.4" '
             'stroke-linecap="round"/>')
    if blink:
        return ('<path d="M-22 -2 Q-14 4 -6 -2" stroke="#07343a" stroke-width="3" fill="none" '
                'stroke-linecap="round"/><path d="M6 -2 Q14 4 22 -2" stroke="#07343a" '
                'stroke-width="3" fill="none" stroke-linecap="round"/>' + smile)
    if expression == "happy":
        return ('<path d="M-22 0 Q-14 -11 -6 0" stroke="#07343a" stroke-width="3.2" fill="none" '
                'stroke-linecap="round"/><path d="M6 0 Q14 -11 22 0" stroke="#07343a" '
                'stroke-width="3.2" fill="none" stroke-linecap="round"/>' + smile)
    if expression == "excited":
        star = ('<path transform="translate({x} -2)" d="M0 -11 l3 6.5 7 1 -5 5 1.2 7 -6.2 -3.5 '
                '-6.2 3.5 1.2 -7 -5 -5 7 -1 z" fill="#ffd34d" stroke="#e5a81f" stroke-width="0.8"/>')
        return star.format(x=-15) + star.format(x=15) + smile
    if expression == "wink":
        return ('<ellipse cx="-17" cy="-2" rx="9.5" ry="13" fill="#07343a"/>'
                '<circle cx="-14" cy="-7" r="3.4" fill="#fff"/>'
                '<path d="M8 -2 Q17 -11 26 -2" stroke="#07343a" stroke-width="3.6" fill="none" '
                'stroke-linecap="round"/>' + smile)
    if expression == "sleepy":
        return ('<path d="M-26 0 Q-17 7 -8 0" stroke="#07343a" stroke-width="3.2" fill="none" '
                'stroke-linecap="round"/><path d="M8 0 Q17 7 26 0" stroke="#07343a" '
                'stroke-width="3.2" fill="none" stroke-linecap="round"/>'
                '<text x="30" y="-14" font-family="sans-serif" font-size="20" fill="#07343a" '
                'opacity="0.8">z</text><text x="46" y="-28" font-family="sans-serif" '
                'font-size="14" fill="#07343a" opacity="0.6">z</text>'
                '<path d="M-7 22 Q0 26 7 22" fill="none" stroke="#07343a" stroke-width="2.6" '
                'stroke-linecap="round"/>')
    if expression == "surprised":
        return ('<ellipse cx="-17" cy="-3" rx="11" ry="15" fill="#07343a"/>'
                '<ellipse cx="17" cy="-3" rx="11" ry="15" fill="#07343a"/>'
                '<circle cx="-13" cy="-9" r="4" fill="#fff"/><circle cx="21" cy="-9" r="4" fill="#fff"/>'
                '<ellipse cx="0" cy="26" rx="7" ry="9" fill="#07343a"/>')
    if expression == "love":
        heart = ('<path transform="translate({x} -2) scale(0.9)" d="M0 7 C-9 -2 -16 -6 -16 -13 '
                 'C-16 -18 -11 -20 -7 -20 C-3 -20 -1 -17 0 -15 C1 -17 3 -20 7 -20 C11 -20 16 -18 '
                 '16 -13 C16 -6 9 -2 0 7 Z" fill="#ff5a7a"/>')
        return (heart.format(x=-16) + heart.format(x=16) +
                '<path d="M-10 22 Q0 30 10 22" fill="none" stroke="#07343a" stroke-width="3" '
                'stroke-linecap="round"/>')
    concerned = expression == "concerned"
    ey = -4 if state == "thinking" else 2         # glance up while thinking
    # The polished hero eyes: a lash line, a big glossy eye, a gradient iris and
    # two catchlights. Listening opens them wider; thinking narrows them a touch.
    # The gaze shifts the iris and its catchlights inside the fixed dark sclera,
    # which is what makes the mascot read as watching something rather than
    # having its pupils painted on: the highlight travels with the iris, so the
    # two never disagree about where it is looking.
    sx, sy = (1.12, 1.12) if state == "listening" else (1.0, 0.86) if state == "thinking" else (1.0, 1.0)
    gx, gy = gaze[0] * _GAZE_X, gaze[1] * _GAZE_Y
    lash = ('<path d="M-36 -16 Q-22 -30 -6 -18" stroke="#06313a" stroke-width="4.5" fill="none" '
            'stroke-linecap="round"/><path d="M6 -18 Q22 -30 36 -16" stroke="#06313a" '
            'stroke-width="4.5" fill="none" stroke-linecap="round"/>')
    eye = (f'<g transform="translate({{cx}} {ey}) scale({sx:.2f} {sy:.2f})" '
           f'clip-path="url(#sclera)">'
           f'<ellipse rx="{_SCLERA_RX:g}" ry="{_SCLERA_RY:g}" fill="#06313a"/>'
           f'<g transform="translate({gx:.2f} {gy:.2f})">'
           '<ellipse cy="2" rx="12" ry="16" fill="url(#iris)"/>'
           '<circle cx="-5" cy="-7" r="5.5" fill="#fff"/>'
           '<circle cx="4" cy="8" r="3" fill="#bdeff0"/></g></g>')
    eyes = lash + eye.format(cx=-20) + eye.format(cx=20)
    if state == "thinking" and not concerned and expression == "":
        return eyes + '<path d="M-8 26 L9 24" fill="none" stroke="#07343a" stroke-width="3.2" stroke-linecap="round"/>'
    if concerned:
        eyes += ('<path d="M-24 -14 Q-15 -19 -7 -15" stroke="#07343a" stroke-width="2.6" '
                 'fill="none" stroke-linecap="round"/><path d="M7 -15 Q15 -19 24 -14" '
                 'stroke="#07343a" stroke-width="2.6" fill="none" stroke-linecap="round"/>')
        mouth = ('<path d="M-11 30 Q0 22 11 30" fill="none" stroke="#07343a" stroke-width="3.2" '
                 'stroke-linecap="round"/>')
    elif state == "speaking":
        # The speaking mouth is already open, so it takes the amount as a
        # *scale* rather than as the interior `smile` draws: growing its own
        # ellipse is what makes a dragged sprite look like it is talking, and
        # at `0.0` it is the 7x9 it has always been.
        open_frac = 0.0 if mouth <= 0.0 else min(1.0, mouth / _MOUTH_MAX)
        mouth = (f'<ellipse cx="0" cy="30" rx="{7.0 + 2.0 * open_frac:.1f}" '
                 f'ry="{9.0 + 5.0 * open_frac:.1f}" fill="#07343a"/>'
                 f'<ellipse cx="0" cy="33" rx="{3.5 + 1.5 * open_frac:.1f}" '
                 f'ry="{3.5 + 2.0 * open_frac:.1f}" fill="#e86a6a"/>')
    else:
        mouth = smile
    return eyes + mouth


def _live_level(value: object) -> float:
    """A live mic/playback level as 0.0-1.0, and 0.0 for anything else.

    **`sysfs.finite` is asked about the raw value**, not about a float made from
    it: `float(True)` is 1.0, so coercing first would turn the `bool` refusal
    that helper exists to provide back into the bug it was written for. Measured
    on the three shapes that used to behave differently here -

    | fed in | before | after |
    |---|---|---|
    | `"x"`, `None` | **`svg()` raised**, and `_render` swallowed it | 0.0, draws |
    | `float("nan")` | **1.0** - halo pinned to full volume | 0.0, draws |
    | `True` | 1.0 | 0.0 |

    NaN is the one that mattered and it is the quiet one. `min(1.0, nan)` is
    `1.0`, so a bad reading rendered as the loudest the orb can draw and looked
    exactly like a working indicator reading a loud room.

    After `finite` returns True the value is an `int`/`float` that is neither a
    bool, a NaN nor an infinity, so the coercion and the clamp below cannot
    raise - which is why there is no `try` here, and why this cannot become a
    second copy of the finite check the repo consolidated into `sysfs`.
    """
    if not sysfs.finite(value):
        return 0.0
    return max(0.0, min(1.0, float(value)))


#: The finest difference in level the drawn character can actually show.
#:
#: **A level finer than this is a redraw that looks identical**, and the art
#: module's own docstring already says why it cannot simply write the number:
#: `mind_op` is interpolated at full float precision, so `svg()` returns a
#: *different string* for nearly every level - 159 distinct strings for
#: `listening` over a 100,000-sample sweep, and a "smallest visible change" of
#: 0.0000. A string that differs by 1e-6 is not a picture that differs by 1e-6:
#: 8-bit alpha cannot show it.
#:
#: Measured by hashing the **rasterised pixbuf** rather than the string, over a
#: 1,000-sample sweep of `listening` at `SIZE * 2` (levels `i/1000` for
#: `i` in 0..999):
#:
#: | step | distinct pictures |
#: |---|---|
#: | none | **156** |
#: | 0.02 | **51** |
#: | 0.05 | 21 |
#:
#: The 51 and the 21 are grid-independent; the 156 is this grid's count and
#: 157 with `1.0` included, which is why the row above names the grid.
#:
#: **The load-bearing property, and the one 0.02 was chosen for.** Over the 51
#: quantised levels the halo radius `int(44 * level)` takes **45 distinct
#: values - all 45 the unquantised range spans**, so the step is at or below the
#: drawing's own granularity and costs the picture nothing. (Measured: the
#: radius moves by 1 on 44 of the 50 transitions and by 0 on 6. An earlier
#: version of this comment read that as `int(44 * 0.02) = 0` "on most steps",
#: which is the *level* increment and not the *radius* move - the two differ
#: because the radius accumulates.)
#:
#: **This is load bearing rather than cosmetic**: a Shell actor that displays
#: this art takes a file route (`St.TextureCache.load_file_sync`, measured in
#: `harness-study/HARVEST-COUCOU.md` PART 9), and that costs **12.4 ms per
#: changed frame** against a 16.6 ms budget - so 156 re-encodes per turn is a
#: frame budget spent, and 51 is not.
LEVEL_STEP = 0.02


def quantise_level(level: float) -> float:
    """A live level snapped to the finest step the drawn character can show.

    Snapping is `round`, not `floor` or a string format, so it is symmetric: a
    level that arrives as 0.019 or 0.021 draws the same picture and neither is
    the one that owns it. **Rounding is what makes the boundary stable** - a
    floor would put 0.019 and 0.021 in different buckets either side of a value
    that looks the same, so a level hovering at a step edge would re-encode
    forever.
    """
    if level <= 0.0:
        return 0.0
    if level >= 1.0:
        return 1.0
    return min(1.0, round(level / LEVEL_STEP) * LEVEL_STEP)


def _decor(state: str, level: float, net: str) -> str:
    """A per-state signal that reads at a glance, so states never look alike.

    Drawn behind the orb (the pulse rings radiate out from under it).
    """
    if state == "listening":
        # concentric pulse rings radiating from the orb - unmistakably "hearing"
        o = 0.5 + 0.4 * level
        return (f'<g fill="none" stroke="{net}" stroke-linecap="round">'
                f'<circle cx="256" cy="250" r="146" stroke-width="5" opacity="{o:.2f}"/>'
                f'<circle cx="256" cy="250" r="170" stroke-width="4" opacity="{o*0.6:.2f}"/>'
                f'<circle cx="256" cy="250" r="196" stroke-width="3" opacity="{o*0.32:.2f}"/></g>')
    if state == "interrupting":
        # A ring with a gap cut out of it: the signal was cut short. This state
        # was the one with **no** signal of its own - only a dimmer palette than
        # idle, which is the "states never look alike" rule above this function
        # failing for exactly one state, and a colour alone excludes anyone who
        # cannot use colour. Shape, because shape is the part that survives.
        return ('<g fill="none" stroke="#e86a6a" stroke-width="5" stroke-linecap="butt">'
                '<path d="M413.2 166.4 A 178 178 0 0 1 78.1 243.8"/>'
                '<path d="M98.8 333.6 A 178 178 0 0 1 345.0 404.2"/></g>')
    return ""


def _overlay(state: str) -> str:
    """A per-state signal drawn in front (thinking's rising thought)."""
    if state == "thinking":
        return ('<g fill="#ffe08a" stroke="#e8a62a" stroke-width="1">'
                '<circle cx="352" cy="190" r="6"/><circle cx="384" cy="160" r="8.5"/>'
                '<circle cx="420" cy="128" r="11" opacity="0.95"/></g>'
                '<path d="M300 150 l5 11 11 4 -11 4 -5 11 -5 -11 -11 -4 11 -4 z" fill="#ffe08a"/>')
    return ""


def svg(state: str = "idle", expression: str = "", blink: bool = False,
        level: float = 0.0, gaze: object = (0.0, 0.0),
        tilt: object = 0.0, mouth: object = 0.0) -> str:
    """The mascot as an SVG string for a given state/expression/blink/level/gaze.

    **Every argument is answered here**, so no caller has to. `state` and
    `expression` are checked against the tables below rather than against a
    caller's own validation: `mascot.set_state` has its own tuple and this module
    has this one, and two answers to "is this a state" is how they drift. A
    state this module does not know falls back to idle, which is what the palette
    lookup already did for the colours - it now also does it for the eyes, the
    rings and the overlay, which used to branch on the unvalidated string.

    `gaze`, `tilt` and `mouth` are the three live channels from
    `mascot_drag`, so each is normalised rather than merely accepted, for the
    reason `_live_gaze` gives: `_render` calls this from inside a `GLib`
    callback where a raised `TypeError` is swallowed, and a NaN in a transform is
    a picture nobody can read rather than a loud failure. `_render` calls this
    only when the bearing has actually changed, because this returns a fresh
    string that the caller then rasterises.

    **`tilt` rotates the character and not the shadow.** The shadow is what
    fixes the mascot to the screen - it is drawn under the orb, in perspective,
    and it stays level while the orb leans into a drag. Rotating it would lift
    the mascot off the desktop, which is the one thing an always-on-top overlay
    must never look like.
    """
    state = state if state in _PALETTE else "idle"
    expression = expression if expression in _EXPRESSIONS else ""
    p = _PALETTE[state]
    # The halo tracks the live mic/playback level, so only listening and speaking
    # move with it; any other state is steady regardless of a stray level.
    level = _live_level(level) if state in ("listening", "speaking") else 0.0
    level = quantise_level(level)
    halo_r = 150 + int(44 * level)
    halo_op = 0.30 + 0.30 * level
    mind_op = (0.7 if state == "thinking" else 0.26) + 0.15 * level
    rings = _decor(state, level, p['halo'])
    overlay = _overlay(state)
    gaze = _live_gaze(gaze)
    tilt = _live_tilt(tilt)
    mouth = _live_mouth(mouth)
    # Degrees, because SVG's `rotate()` takes them and `mascot_drag` works in
    # radians; a conversion that happened here is a conversion that cannot be
    # forgotten at the call site.
    lean = f'<g transform="rotate({math.degrees(tilt):.2f} 256 250)">'
    unlean = "</g>"
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">
<defs>
<radialGradient id="body" cx="42%" cy="34%" r="70%">
<stop offset="0%" stop-color="#fff"/><stop offset="22%" stop-color="{p['c0']}"/>
<stop offset="55%" stop-color="{p['c1']}"/><stop offset="100%" stop-color="{p['c2']}"/></radialGradient>
<radialGradient id="halo" cx="50%" cy="50%" r="50%">
<stop offset="0%" stop-color="{p['halo']}" stop-opacity="{halo_op:.2f}"/>
<stop offset="100%" stop-color="{p['halo']}" stop-opacity="0"/></radialGradient>
<radialGradient id="core" cx="50%" cy="56%" r="55%">
<stop offset="0%" stop-color="#eafffe" stop-opacity="0.85"/><stop offset="100%" stop-color="#eafffe" stop-opacity="0"/></radialGradient>
<linearGradient id="ring" x1="0" y1="0" x2="1" y2="0">
<stop offset="0%" stop-color="#e5a62f"/><stop offset="50%" stop-color="#ffe28a"/><stop offset="100%" stop-color="#e5a62f"/></linearGradient>
<linearGradient id="iris" x1="0" y1="0" x2="0" y2="1">
<stop offset="0%" stop-color="#042e36"/><stop offset="48%" stop-color="#0c5f69"/>
<stop offset="82%" stop-color="#21b7b2"/><stop offset="100%" stop-color="#58e6d8"/></linearGradient>
<linearGradient id="rim" x1="0" y1="1" x2="0" y2="0">
<stop offset="0%" stop-color="{p['halo']}" stop-opacity="0.9"/><stop offset="100%" stop-color="{p['halo']}" stop-opacity="0"/></linearGradient>
<clipPath id="ball"><circle cx="256" cy="250" r="132"/></clipPath>
<clipPath id="sclera"><ellipse rx="{_SCLERA_RX:g}" ry="{_SCLERA_RY:g}"/></clipPath>
</defs>
<g opacity="0.8">
<ellipse cx="256" cy="430" rx="120" ry="22" fill="none" stroke="{p['halo']}" stroke-width="2.5" opacity="0.5"/>
<ellipse cx="256" cy="433" rx="86" ry="15" fill="none" stroke="{p['halo']}" stroke-width="2" opacity="0.32"/></g>
{lean}
<circle cx="256" cy="250" r="{halo_r}" fill="url(#halo)"/>
{rings}
<g transform="translate(256 258) rotate(-20)"><path d="M -168 0 A 168 52 0 0 1 168 0" fill="none" stroke="url(#ring)" stroke-width="15" opacity="0.92"/></g>
<circle cx="256" cy="250" r="132" fill="url(#body)"/>
<path d="M 256 382 A 132 132 0 0 1 132 198" fill="none" stroke="url(#rim)" stroke-width="8" opacity="0.8"/>
<circle cx="256" cy="262" r="92" fill="url(#core)"/>
<ellipse cx="212" cy="198" rx="44" ry="28" fill="#fff" opacity="0.5" transform="rotate(-18 212 198)"/>
{_mind(p['net'], mind_op)}
<g transform="translate(256 258) rotate(-20)">
<path d="M 168 0 A 168 52 0 0 1 -168 0" fill="none" stroke="url(#ring)" stroke-width="15"/>
<path d="M 168 0 A 168 52 0 0 1 -168 0" fill="none" stroke="#fff6d8" stroke-width="3" opacity="0.7"/>
<circle cx="168" cy="0" r="9" fill="#fff3c6"/><circle cx="-160" cy="16" r="6" fill="#ffe29a"/><circle cx="30" cy="50" r="4.5" fill="#fff3c6"/></g>
{overlay}
<g transform="translate(256 252)">{_eyes(expression, state, blink, gaze, mouth)}</g>
<path d="M412 150 l4 10 10 4 -10 4 -4 10 -4 -10 -10 -4 10 -4 z" fill="#ffe28a" opacity="0.85"/>
<path d="M108 160 l3 7 7 3 -7 3 -3 7 -3 -7 -7 -3 7 -3 z" fill="{p['halo']}" opacity="0.85"/>
{unlean}
</svg>'''
