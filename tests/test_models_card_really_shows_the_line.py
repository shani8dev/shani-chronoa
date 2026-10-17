"""The Models panel's requirement line reaches the **screen**, not just a string.

This is the gap `tests/test_models_panel_shows_requirements.py` states about
itself: four mutations - the label never appended, the label built empty, the
non-LLM filter dropped, the width cap removed - leave that file green, because
it never builds a card. It says so rather than implying coverage it does not
have, and this file is the other half.

**A subprocess, not a fixture.** GDK chooses its display backend when GTK is
initialised and nothing can change it afterwards - `tests/test_now_rail.py`
documents the same thing the hard way - so a card built in the pytest process
either uses the real display or segfaults. The first version of this file tried
in-process and crashed the interpreter. A private Broadway display keeps it off
the desktop as well, which is why `test_browse_in_app.py` has the same fixture.

So the claim is narrow and real: **the line `_needs_label_text` produced is
present in the widget tree GTK actually built.**

**One equivalent mutant, kept rather than hidden.** Deleting the `kind != "llm"`
filter in `_needs_label_text` leaves this file **green**, and it is worth saying
why rather than tuning the test until it fails: `_requirement_text` already
returns `""` for a card with no `requires` row, and only LLM cards carry one, so
the filter is a second statement of a rule that already holds. It is kept
because it names the rule at the place the rule is applied, and removing a
defensive check that is genuinely redundant would make the code worse to read
in exchange for a mutation that proves nothing. The three that *are* load-bearing
are caught: not appending the label, building it empty, and dropping the
shortfall clause.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "usr" / "lib" / "shani-chronoa"

_HARNESS = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import gi
gi.require_version("Gtk", "4.0")
# The typelib on disk is Adw-1.typelib, so the namespace version is "1" - "1.0" raises, and this is the same class as the Adw.Backpoint note in gui/sidebar.py: ask for the version the library actually ships.
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib
Adw.init()

from shani_chronoa.gui.surfaces import models as surface

def labels(widget, out):
    if isinstance(widget, Gtk.Label):
        out.append(widget.get_text())
    child = widget.get_first_child()
    while child is not None:
        labels(child, out)
        child = child.get_next_sibling()

out = {}
try:
    cards = [c for c in surface._cards() if c["kind"] == "llm"]
    if sys.argv[2] == "not-llm":
        # A card of a kind that must never grow the line.
        cards = [c for c in surface._cards() if c["kind"] != "llm"]
    wanted = sys.argv[2]
    card = cards[0] if wanted in ("any", "not-llm") else next(
        c for c in cards if c["key"] == wanted)
    out["key"] = card["key"]
    out["expected"] = surface._needs_label_text(card)
    box = surface._card(None, card, lambda *a: None)
    texts = []
    labels(box, texts)
    out["labels"] = texts
except Exception as exc:
    out["error"] = f"{type(exc).__name__}: {exc}"
print("RESULT" + json.dumps(out))
sys.stdout.flush()
os._exit(0)
"""


@pytest.fixture(scope="module")
def broadway():
    """A private Broadway display, so no window appears on anybody's desktop."""
    daemon = shutil.which("gtk4-broadwayd")
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if daemon is None or not runtime:
        pytest.skip("no gtk4-broadwayd or no XDG_RUNTIME_DIR, so no headless display")
    runtime = pathlib.Path(runtime)
    # A free display is a missing socket *and* a free port: tests/_broadway.py.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _broadway import free_display
    number = free_display(runtime, 40, 2600)
    if number is None:
        pytest.skip("no free Broadway display number")
    proc = subprocess.Popen([daemon, f":{number}"], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    deadline = time.time() + 10
    while time.time() < deadline and not (runtime / f"broadway{number + 1}.socket").exists():
        time.sleep(0.1)
    yield number
    proc.terminate()
    proc.wait(timeout=10)


def _run(broadway, key, tmp_path):
    """Build the card in a subprocess and return what GTK put in it."""
    script = tmp_path / "harness.py"
    script.write_text(_HARNESS)
    env = dict(os.environ)
    env.update(GDK_BACKEND="broadway", BROADWAY_DISPLAY=f":{broadway}",
               PYTHONDONTWRITEBYTECODE="1")
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    done = subprocess.run(
        [sys.executable, str(script), str(PKG), key],
        capture_output=True, text=True, timeout=180, env=env)
    for line in done.stdout.splitlines():
        if line.startswith("RESULT"):
            return json.loads(line[6:])
    pytest.fail(f"the harness produced nothing:\n{done.stdout[-800:]}\n{done.stderr[-800:]}")


class TestTheRequirementLineReachesTheWidget:
    def test_an_llm_card_shows_its_requirements(self, broadway, tmp_path):
        out = _run(broadway, "any", tmp_path)
        assert "error" not in out, out["error"]
        assert out["expected"], "the card under test has no requirement text"
        shown = " | ".join(t for t in out["labels"] if t)
        assert out["expected"] in shown, (
            f"the text was produced but is not in the built card.\n"
            f"expected: {out['expected']!r}\nlabels:   {out['labels']!r}")

    def test_the_whole_sentence_is_one_label_not_truncated(self, broadway, tmp_path):
        """A label built with the first clause and dropped mid-sentence is the
        defect this repository has recorded twice - the subtitle that read as a
        sentence cut in half."""
        out = _run(broadway, "any", tmp_path)
        whole = [t for t in out["labels"] if t.startswith("Needs ")]
        assert whole, f"no label begins with 'Needs': {out['labels']!r}"
        line = whole[0]
        # **A terminator, not a particular word.** The old assertion enumerated
        # `(".", "here.", "machine.")` - the endings the sentence has when the
        # tier is *usable*. `requirements_met`'s reasons are noun phrases in
        # parentheses ("a measured decode rate (no local model is answering)"),
        # so the refused branch ends in `)`. On a machine with no local model
        # answering - which is every tier here - that branch is the one that
        # runs, so the assertion could only ever pass on a box where the first
        # card happened to be the default. A shape that depends on which machine
        # the suite ran on is the `fake_bin PATH` lesson again.
        assert line.endswith((".", ")")), (
            f"the line stops mid-sentence: {line!r}")
        # **And the real truncation: cut at the colon.** A label built from the
        # first clause and dropped leaves "Not the default here: " with nothing
        # after it - which is the sentence-cut-in-half this test names, and which
        # a terminator check alone would accept if the clause happened to end in
        # a period.
        if "Not the default here:" in line:
            after = line.split("Not the default here:", 1)[1].strip()
            assert after, f"the reason was cut at the colon: {line!r}"

    def test_a_refused_tier_names_the_number_it_is_short_by(self, broadway, tmp_path):
        """**The card that justifies the whole change.** A tier the code refuses
        must say *why* on screen, with the figure - not merely omit a tick.

        The first version of this harness always built `cards[0]`, which is the
        0.6B tier and is usable, so dropping the shortfall clause changed nothing
        it looked at and the mutation ran green.
        """
        from shani_chronoa import local_llm
        refused = [k for k in local_llm.REQUIRES
                   if not local_llm.requirements_met(k)[0]]
        assert refused, "no tier is refused on this machine, so nothing to assert"
        out = _run(broadway, refused[0], tmp_path)
        assert "error" not in out, out["error"]
        assert "Not the default here:" in out["expected"], (
            f"{refused[0]} is refused but its card says {out['expected']!r}")
        _met, missing = local_llm.requirements_met(refused[0])
        assert missing[0][:24] in out["expected"], (
            "the card and the code disagree about the shortfall")

    def test_a_non_llm_card_shows_no_requirement_line(self, broadway, tmp_path):
        """**The other direction.** 'Needs 2 CPU cores' under a Piper voice would
        be a claim nobody checked, and a card that grows the line for everything
        is as wrong as one that never does."""
        out = _run(broadway, "not-llm", tmp_path)
        assert "error" not in out, out["error"]
        assert out["expected"] == "", (
            f"{out['key']} should render no line but produced {out['expected']!r}")
        assert not [t for t in out["labels"] if t.startswith("Needs ")], (
            f"{out['key']} shows a requirement line: {out['labels']!r}")

    def test_the_walk_actually_found_the_labels(self, broadway, tmp_path):
        """**The control on the harness.**

        The failure this guards against is the one this repository has recorded
        twice: a harness that produces nothing is read as an empty result, and
        then every assertion passes because the thing it should have found was
        never there. A card always has a title, so an empty `labels` means the
        walk - not the panel - is broken.
        """
        out = _run(broadway, "any", tmp_path)
        assert "error" not in out, out["error"]
        assert out["labels"], "the walk returned no labels at all, so it found nothing"
        title = [t for t in out["labels"] if t.strip()]
        assert len(title) >= 3, (
            f"a card has a title, a size/kind line and a status line; got "
            f"{out['labels']!r} - the walk is not seeing the widget tree")
