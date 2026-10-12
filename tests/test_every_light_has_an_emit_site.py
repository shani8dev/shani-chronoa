"""Every light on the organ strip has something behind it.

**This is the check that found two dead lights**, and it is worth more than the
fix. `body.ORGANS` listed eight organs and `ORGAN_ICONS` had an icon for each,
but **only six were ever passed to the body**: `nose` and `brain` had no emit
site anywhere in the shipped code. So the strip showed a brain that never lit
while the model was answering, and showed the machine's own state as permanently
idle while senses were being read.

Two dead controls sitting beside six that work is worse than a smaller strip,
because a row of lights is a claim that something is happening. **A light is a
promise about an event; if the event cannot occur, the light is a lie.**

`organism.py` already declares `indicator`/`gerund`/`source` per organ, which is
a *description* of what should light — not evidence that anything does. So this
parses the shipped source for real call sites instead, the same way the audit
that found the two did.

**The control below is the point of the file.** A scanner that returns an empty
set passes every version of this assertion, so it is run against a synthetic organ
that nothing lights and asked to find it.
"""

import ast
import pathlib
import sys

import pytest

pytest.importorskip("gi")
sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa import body  # noqa: E402

PKG = pathlib.Path("usr/lib/shani-chronoa/shani_chronoa")

#: The methods that put an organ into the body. `lit`/`pulse`/`use` start an
#: activity; `done`/`done_all`/`busy`/`announce` only reference one that is
#: already there and cannot be the thing that lights a light.
STARTERS = {"lit", "pulse", "use"}


def _wrapper_names():
    """Functions that forward a *parameter* to the body - i.e. lighters.

    Without this the scan reports `brain` as dead when it is alive, because
    `assistant.py` lights it through a local `_lit("brain", ...)` that passes its
    own argument straight to `body.body.use`. **That is the same false-positive
    trap as the settings audit, one level down**, and a gate which cries wolf is
    worse than no gate: it would send the next person to add a second, duplicate
    emit site for a light that already works.

    So the two passes are: find the functions that light from a parameter, then
    count calls to *those* by literal name as emit sites too.
    """
    wrappers = set()
    for path in sorted(PKG.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(func):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                fn = node.func
                attr = fn.attr if isinstance(fn, ast.Attribute) else None
                if attr in STARTERS and not isinstance(node.args[0], ast.Constant):
                    wrappers.add(func.name)
    return wrappers


def _lit_by_a_literal():
    """Every organ name that reaches the body, directly or through a lighter."""
    wrappers = _wrapper_names()
    found = set()
    for path in sorted(PKG.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            first = node.args[0]
            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                continue
            fn = node.func
            if isinstance(fn, ast.Attribute):
                lighter = fn.attr in STARTERS
            elif isinstance(fn, ast.Name):
                lighter = fn.id in wrappers
            else:
                lighter = False
            if lighter:
                found.add(first.value)
    return found


def test_every_light_on_the_strip_can_actually_light():
    """No organ is shown that nothing ever lights.

    `nosound` here would be a light that never comes on — the failure measured on
    `nose` and `brain`, which sat in `ORGANS` and in `ORGAN_ICONS` with no emit
    site anywhere in the package.
    """
    lit = _lit_by_a_literal()
    dead = sorted(set(body.ORGANS) - lit)
    assert not dead, (
        f"the strip shows {len(body.ORGANS)} organs, and these are never passed "
        f"to the body by any call site: {dead}. A light with nothing behind it "
        f"is not decoration, it is a claim that does not hold - either give it "
        f"a real emit site or take it off the strip.")


def test_the_scanner_this_file_relies_on_can_find_a_known_site():
    """**The control.** A scanner that found nothing would pass everything above.

    `brain` is lit in `assistant._ask` and `nose` in `sense_reading.reading`;
    both were verified by driving them and reading the body back. If this stops
    finding them, the walk is looking for the wrong shape and every assertion in
    this file is quietly checking nothing.
    """
    lit = _lit_by_a_literal()
    for organ, where in (("brain", "assistant._ask"), ("nose", "sense_reading.reading")):
        assert organ in lit, (
            f"{organ} was lit at {where} and this scan no longer sees it - the "
            f"walk is looking for the wrong shape, so the gate above is passing "
            f"for the wrong reason")


def test_every_light_is_described_as_well_as_lit():
    """A light that cannot say what it was doing is half a control.

    Checked against the same scanner, so an organ added to the strip without a
    gerund fails here rather than shipping as an unlabelled dot.
    """
    undescribed = sorted(o for o in body.ORGANS if not body.ORGAN_NOUNS.get(o))
    assert not undescribed, f"lit but not described: {undescribed}"


def test_a_light_nobody_lights_is_reported(monkeypatch):
    """**The control's control:** the difference is computed, and can come out non-empty.

    An organ nobody lights is put into `ORGANS` for the duration, and the same
    expression that produces an empty list above must produce this one. If it
    does not, the gate cannot fail on the exact case it was written for.
    """
    lit = _lit_by_a_literal()
    monkeypatch.setattr(body, "ORGANS", tuple(body.ORGANS) + ("an_organ_nobody_lights",))
    dead = sorted(set(body.ORGANS) - lit)
    assert dead == ["an_organ_nobody_lights"], (
        f"an organ nothing lights was not reported: {dead} - so the gate above "
        f"would pass on a dead light")
    monkeypatch.undo()
    assert sorted(set(body.ORGANS) - lit) == [], (
        "with the real ORGANS restored, the gate disagrees with itself")