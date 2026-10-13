"""A render target is not proved by a file appearing.

`tools/render_ui_batch.sh` decided PASS with `[[ -s "$out" ]]`, and **a blank
PNG is 20,000-odd bytes** - so every check passed on a capture of nothing. Four
instances are recorded in AGENTS.md, and the last one cost this session the rest
of its budget:

    render_ui.py at HEAD (byte-identical, `git diff` empty), 1280x800:
      1 rendered, 0 not.        <- what the sweep said
      opaque 0.2%              <- what the picture was

The same command on the same commit gave 100% opaque earlier in the day. The
difference was an exhausted broadway pool - the `acquire_display` cascade - and
the instinct was to suspect the change made five minutes earlier. Reverting the
one file that had been touched, from HEAD, and rendering again was the whole
diagnosis: **the instrument was already broken.**

`tests/test_mutation_harness_can_fail.py` exists for exactly that instinct, and
the render harness had no equivalent. This is it: the gate is read out of the
script and run against real images, so it cannot pass by being broken.
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


def _opaque_percent_body() -> str:
    """The measurement the gate uses, read out of the script.

    **Extracted, not re-typed.** A copy of the threshold in this file would be a
    second number that can drift from the one in the driver, and this repository
    records `skills_total`/`42/87/70`/`105 characters` as three separate
    incidents of exactly that.
    """
    body = _script()
    chunk = body[body.index("_opaque_percent() {"):]
    # **To the heredoc's terminator, not its opener.** `index("PYOPACITY")`
    # finds `<<'PYOPACITY'` first and truncates the body to nothing, so all
    # three measurements came back `None` and every assertion below failed for
    # that reason - which reads exactly like the gate being broken, and is the
    # same "a probe that measures nothing reports the same result as one that
    # works" shape this file keeps meeting.
    end = chunk.index("\nPYOPACITY\n")
    # **The chunk already opens the function, and the terminator has to be put
    # back.** Slicing from `_opaque_percent() {` means the extracted text starts
    # with that line - so wrapping it again gave `_opaque_percent() {_opaque...`
    # and a syntax error - and stopping before the terminator left the heredoc
    # unclosed, which bash reports as the same kind of thing. Both were failures
    # of the *extraction*, presenting as failures of the gate, which is the
    # trap this file exists in front of.
    return chunk[:end] + "\nPYOPACITY"


def _floor() -> int:
    """The threshold the gate compares against, read out of the script."""
    m = re.search(r'MIN_OPAQUE_PCT="\$\{MIN_OPAQUE_PCT:-(\d+)\}"', _script())
    assert m, "the batch driver declares no MIN_OPAQUE_PCT floor"
    return int(m.group(1))


def _measure(path: Path) -> int | None:
    """Run the script's own measurement against `path`.

    **By subprocess, because the body is a bash heredoc.** Extracting the python
    and running it in-process would test the extraction as much as the check.
    """
    out = subprocess.run(
        ["bash", "-c",
         f'{_opaque_percent_body()}\n}}\n_opaque_percent "$1"',
         "_", str(path)],
        capture_output=True, text=True,
    )
    text = out.stdout.strip()
    return int(text) if text else None


def _png(path: Path, painter, size=(64, 64)) -> Path:
    """A real PNG whose alpha is whatever `painter` says it is."""
    from PIL import Image
    im = Image.new("RGBA", size, (20, 20, 20, 255))
    painter(im)
    im.save(path)
    return path


def _blank(im) -> None:
    """Transparent everywhere - what an exhausted pool produces."""
    im.putalpha(0)


def _opaque(im) -> None:
    im.putalpha(255)


def _mostly_transparent(im) -> None:
    """The recorded `cloud-keys` signature: 7.2% opaque, and the largest file
    in its sweep."""
    px = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            px[x, y] = (200, 40, 40, 255 if (x + y) % 14 == 0 else 0)


class TestTheRenderGateIsNotAFileAppearing:
    def test_a_blank_capture_is_below_the_floor(self, tmp_path):
        """**The defect.** A blank PNG is a file, so the old gate passed it."""
        got = _measure(_png(tmp_path / "blank.png", _blank))
        assert got is not None, "the measurement returned nothing at all"
        assert got < _floor(), (
            f"a blank capture measured {got}% opaque against a floor of "
            f"{_floor()}% - the gate would call a photograph of nothing a render")

    def test_a_real_capture_is_above_the_floor(self, tmp_path):
        """The positive control, and **the one that makes the negative mean
        something.** A check that cannot fail passes on a blank page too.
        """
        got = _measure(_png(tmp_path / "real.png", _opaque))
        assert got is not None
        assert got >= _floor(), (
            f"a fully painted capture measured {got}% against a floor of "
            f"{_floor()}% - the gate would reject a real render")

    def test_the_documented_false_pass_is_below_the_floor(self, tmp_path):
        """The `cloud-keys` shape, reproduced rather than quoted.

        **7.2% opaque and the largest file in its sweep** - which is the whole
        point: a byte count ranked it above a genuine 25,932-byte page, so the
        size check and the quality check point in *opposite* directions.
        """
        got = _measure(_png(tmp_path / "cloud-keys.png", _mostly_transparent))
        assert got is not None and got < _floor(), (
            f"the recorded false-pass shape measured {got}%, above the "
            f"{_floor()}% floor - the gate would pass the page it missed")

    def test_the_floor_sits_between_the_two_published_numbers(self):
        """**Nothing is near the line**, which is why the floor is not a guess.

        Every genuine render in this repository's recorded sweeps is 100% opaque
        and every recorded false pass is 7.2% or worse. A threshold placed
        between two populations that far apart does not need tuning per target;
        one placed in a gap would.
        """
        floor = _floor()
        assert 0 < floor <= 50, (
            f"a floor of {floor}% is inside the population rather than in the "
            f"gap between it")

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
        `may_execute`, and `refused_tools`."""
        body = _script()
        assert "_opaque_percent \"$out\"" in body, (
            "render_once does not consult the measurement, so the function "
            "above is correct and changes nothing")
        assert "(( opaque >= MIN_OPAQUE_PCT ))" in body, (
            "render_once measures and does not compare against the floor")

    def test_a_missing_measurement_fails_rather_than_passing(self):
        """The second half of the wiring, and the one that is easy to drop."""
        body = _script()
        assert "[[ -n $opaque ]] || return 1" in body, (
            "an empty measurement must fail; without this the gate returns the "
            "arithmetic's own exit status and a missing Pillow reads as a pass")