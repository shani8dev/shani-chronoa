"""A render target is not proved by a file appearing.

`tools/render_ui_batch.sh` decided PASS with `[[ -s "$out" ]]`, and **a blank
PNG is 20,000-odd bytes** - so every check passed on a capture of nothing. Four
instances are recorded in AGENTS.md, the last of which cost this session the
rest of its budget:

    render_ui.py at HEAD (byte-identical, `git diff` empty), 1280x800:
      1 rendered, 0 not.        <- what the sweep said
      opaque 0.2%              <- what the picture was

**The measure took three forms and only the third was right**, which is why the
tests assert against each one rather than against a number:

1. `alpha == 255` - refused every genuine render. The real `main` window is
   alpha 25 across 80% of its image; not one pixel is at 255.
2. `alpha > 0` - then passed `state:composer-empty`, which is a **pure white
   rectangle**: alpha 255 everywhere and no picture in it at all.
3. **contrast against the background** - the most common colour in the sample is
   the background by definition, and the question is how much is something else.
   A transparent page and a white page both score 0; a picture scores well above
   the floor.

`tests/test_mutation_harness_can_fail.py` exists for the instinct that the
change made five minutes earlier was the cause of a failure the change was meant
to reveal. The render harness had no equivalent. This is it: the measure is read
out of the driver and run against real images, so it cannot pass by being broken.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BATCH = REPO / "tools" / "render_ui_batch.sh"


def _script() -> str:
    return BATCH.read_text(encoding="utf-8")


def _measure_body() -> str:
    """The measurement the gate uses, read out of the script.

    **Extracted, not re-typed.** A copy of the threshold in this file would be a
    second number that can drift from the one in the driver, and this repository
    records `skills_total`/`42/87/70`/`105 characters` as three separate
    incidents of exactly that.
    """
    body = _script()
    chunk = body[body.index("_content_percent() {"):]
    # **To the heredoc's terminator, not its opener.** `index("PYOPACITY")` finds
    # `<<'PYOPACITY'` first and truncates the body to nothing, so every
    # measurement came back `None` and all three assertions below failed - which
    # reads exactly like the gate being broken, and is the same "a probe that
    # measures nothing returns the same thing as one that works" shape.
    end = chunk.index("\nPYOPACITY\n")
    # **The chunk already opens the function, and the terminator has to go
    # back.** Slicing from `_content_percent() {` means the text starts with that
    # line, so wrapping it again gave `_content_percent() {_content...` and a
    # syntax error.
    return chunk[:end] + "\nPYOPACITY"


def _floor() -> int:
    """The threshold the gate compares against, read out of the script."""
    m = re.search(r'MIN_CONTENT_PCT="\$\{MIN_CONTENT_PCT:-(\d+)\}"', _script())
    assert m, "the batch driver declares no MIN_CONTENT_PCT floor"
    return int(m.group(1))


def _measure(path: Path) -> int | None:
    """Run the script's own measurement against `path`.

    **By subprocess, because the body is a bash heredoc.** Extracting the python
    and running it in-process would test the extraction as much as the check.
    """
    out = subprocess.run(
        ["bash", "-c",
         f'{_measure_body()}\n}}\n_content_percent "$1"',
         "_", str(path)],
        capture_output=True, text=True,
    )
    text = out.stdout.strip()
    return int(text) if text else None


def _png(path: Path, painter, size=(64, 64)) -> Path:
    """A real PNG whose content is whatever `painter` says it is."""
    from PIL import Image
    im = Image.new("RGBA", size, (255, 255, 255, 255))
    painter(im)
    im.save(path)
    return path


def _blank_white(im) -> None:
    """`state:composer-empty`, measured: 26,589 bytes and a pure white rectangle.

    **This is the case alpha got wrong.** Every pixel is at 255, so `alpha > 0`
    scores it 100% and calls a page with nothing on it a successful render.
    """
    im.paste((255, 255, 255, 255), (0, 0) + im.size)


def _blank_transparent(im) -> None:
    """What an exhausted pool produced: transparent everywhere."""
    im.putalpha(0)


def _real_window(im) -> None:
    """A window with something in it: a background, an accent band, and text."""
    px = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            if 4 <= y <= 10:                       # an accent-coloured header
                px[x, y] = (237, 128, 89, 255)
            elif 2 <= x <= 5 and y > h // 3:       # a sidebar
                px[x, y] = (240, 240, 240, 255)
            elif h // 2 <= y < h // 2 + 6 and 20 <= x < w - 20:   # a line of text
                px[x, y] = (20, 20, 20, 255)


def _sparse_speckle(im) -> None:
    """The recorded `cloud-keys` signature, reproduced rather than quoted.

    **7.2% of the image differing from the background, and the largest file in
    its sweep** - which is the whole point: a byte count ranked it above a
    genuine page, so the size check and the content check point in *opposite*
    directions.
    """
    px = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            px[x, y] = (200, 40, 40, 255 if (x + y) % 14 == 0 else 0)


class TestTheRenderGateIsNotAFileAppearing:
    def test_a_transparent_capture_is_below_the_floor(self, tmp_path):
        """**The defect.** A blank PNG is a file, so the old gate passed it."""
        got = _measure(_png(tmp_path / "blank.png", _blank_transparent))
        assert got is not None, "the measurement returned nothing at all"
        assert got < _floor(), (
            f"a blank capture measured {got}% against a floor of {_floor()}% - "
            f"the gate would call a photograph of nothing a render")

    def test_a_white_page_is_below_the_floor(self, tmp_path):
        """**The case alpha got wrong, and why alpha is not the measure.**

        `state:composer-empty` is 26,589 bytes of pure white. `alpha > 0` scores
        it 100% - the gate's second version passed it, which is a *worse* false
        positive than the false negative it fixed, because it looks like a
        working sweep.
        """
        got = _measure(_png(tmp_path / "white.png", _blank_white))
        assert got is not None and got < _floor(), (
            f"a page of solid white measured {got}%, above the {_floor()}% floor "
            f"- the gate would pass a page with nothing on it")

    def test_a_real_capture_is_above_the_floor(self, tmp_path):
        """The positive control, and **the one that makes the negatives mean
        something.** A check that cannot fail passes on a blank page too.
        """
        got = _measure(_png(tmp_path / "real.png", _real_window))
        assert got is not None, (
            "the measurement returned nothing for a real window, so the blank "
            "assertions above would be passing for the wrong reason")
        assert got >= _floor(), (
            f"a window with a header, a sidebar and text measured {got}% against "
            f"a floor of {_floor()}% - the gate would reject a real render")

    def test_the_documented_false_pass_is_below_the_floor(self, tmp_path):
        """The `cloud-keys` shape, reproduced rather than quoted."""
        got = _measure(_png(tmp_path / "cloud-keys.png", _sparse_speckle))
        assert got is not None and got < _floor(), (
            f"the recorded false-pass shape measured {got}%, above the "
            f"{_floor()}% floor - the gate would pass the page it missed")

    def test_the_floor_sits_below_the_poorest_real_thing(self):
        """**And the floor is low enough to be useful.**

        A floor that only the best pictures clear turns every hard target into a
        failure, and a check that fails on everything stops being read - the
        `-rs` shape this repository already records for a check that stopped
        looking and still printed a tally. The poorest thing measured that is
        really a picture is the orange-framed `main` at about 20%.
        """
        assert 0 < _floor() <= 20, (
            f"a floor of {_floor()}% is above the poorest genuine render measured "
            f"here (~20%), so the gate would refuse real captures")

    def test_a_file_that_is_not_a_png_measures_nothing(self, tmp_path):
        """**And "could not measure" is not a pass.**

        The gate this replaces called anything present a render. If Pillow is
        missing or the file is truncated, the measurement prints nothing - and
        the gate must fail rather than wave it through, which is
        `verification.py`'s exit-code contract and this file's own rule that a
        check which could not run is not a check that found nothing.
        """
        junk = tmp_path / "not-a.png"
        junk.write_bytes(b"this is not a PNG")
        assert _measure(junk) is None, (
            "an unreadable file measured as something, so a broken measurement "
            "could be read as a render")

    def test_the_driver_actually_calls_the_gate(self):
        """**The wiring, because a correct function nothing calls is a dead
        control** - the shape this repository records for `Registry.register`,
        `Gateway.may_execute` and `refused_tools`.
        """
        body = _script()
        assert '_content_percent "$out"' in body, (
            "render_once does not consult the measurement, so the function "
            "above is correct and changes nothing")
        assert "(( content >= MIN_CONTENT_PCT ))" in body, (
            "render_once measures and does not compare against the floor")

    def test_a_missing_measurement_fails_rather_than_passing(self):
        """The second half of the wiring, and the one that is easy to drop."""
        body = _script()
        assert "[[ -n $content ]] || return 1" in body, (
            "an empty measurement must fail; without this the gate returns the "
            "arithmetic's own exit status and a missing Pillow reads as a pass")