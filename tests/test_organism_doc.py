"""`ARCHITECTURE-TARGET.md` Part 3 is generated, and nothing ran the generator.

`tools/gen_organism_doc.py` prints Part 3 from `organism.py`'s inventory, and its
own docstring says *"a test asserts the panel and the inventory still match,
which is the part that can be checked automatically."* **No test ran it.** So the
committed section had drifted, in two independent ways, and both were invisible:

- **The generator truncated the Code column to `organ.code[:2]`** while **23 of
  26 organs declare more than two files**. The column is headed "Code" and
  nothing anywhere says it is a sample, so `Nose` - which declares six files -
  was published as two. On the page whose stated job is to map every box to
  today's code, that is a confident wrong answer about its own contents.
- **One row was hand-edited after generation.** `Synaptic plasticity` carried
  `distill.py` - a file `organism.py` has never declared - plus a measured note
  (*"the outcome model is a flag, not a predictor: 83% top-1 against a 94%
  constant"*). Regenerating with the truncation still in place reproduced
  neither, so the row read as generated while being the only hand-written one.

This module is the wiring, and it is the same shape as
`tests/test_capabilities_doc.py` for `CAPABILITIES.md` - which is also generated,
also gated, and the reason a generated document here is expected to be checked
rather than trusted. **A gate that is not run is not a gate.**

The hand-edit's finding is not lost by the regeneration: `AGENTS.md` carries it
in fuller form, with the whole measurement table it came from.

Run by hand:

    python3 tools/gen_organism_doc.py
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
DOC = REPO / "ARCHITECTURE-TARGET.md"
GENERATOR = REPO / "tools" / "gen_organism_doc.py"
SECTION = "## Part 3: the anatomy, and which of it you can see"


def _committed_part_3(doc_path: pathlib.Path = DOC) -> str:
    """The committed Part 3, bounded and normalised the way the generator ends.

    Bounded at the next `## ` heading, because a section that ran to EOF when
    this was written should not silently swallow one appended after it. The
    generator prints a leading blank line for `>> append`; that is stripped so
    the two texts are comparable.
    """
    lines = doc_path.read_text(encoding="utf-8").split("\n")
    start = next(i for i, line in enumerate(lines) if line.startswith(SECTION))
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start:end]).rstrip() + "\n"


def _generated_part_3() -> str:
    """The generator's own output, run for real as a subprocess."""
    out = subprocess.run(
        [sys.executable, str(GENERATOR)],
        capture_output=True, text=True, cwd=str(REPO), timeout=300)
    assert out.returncode == 0, (
        "gen_organism_doc.py failed, which is a regression in the generator "
        "rather than a clean result:\n" + out.stdout + out.stderr)
    return out.stdout.lstrip("\n").rstrip() + "\n"


def test_part_3_is_what_the_generator_produces():
    """The committed section equals the generator's output, byte for byte."""
    assert _committed_part_3() == _generated_part_3(), (
        "ARCHITECTURE-TARGET.md's Part 3 has drifted from organism.py. "
        "Regenerate it:\n"
        "    python3 tools/gen_organism_doc.py\n"
        "and paste it over the committed section. If the difference is a "
        "measured note the generator cannot carry, put the fact in "
        "organism.py's declaration rather than hand-editing the document.")


def test_the_gate_actually_fails_when_the_document_drifts():
    """**The negative control, because a green run proves nothing on its own.**

    The test above compares two strings; if either helper silently returned the
    same value regardless of the file, it would pass forever. So the *document*
    is doctored in a temp copy and the comparison is re-run: a row that loses a
    file from its Code column - the exact truncation this replaced - must be
    detected, and the mismatch must be attributable to the edit rather than to
    some unrelated difference.
    """
    import tempfile

    generated = _generated_part_3()
    work = pathlib.Path(tempfile.mkdtemp(prefix="organism-doc-", dir="/tmp/opencode"))
    try:
        doctored = work / "ARCHITECTURE-TARGET.md"
        text = DOC.read_text(encoding="utf-8")
        # Drop one file from a Code cell. The generator's own output carries
        # every declared file now, so this is the shape of the old defect.
        victim = "| Senses | Nose | smell:"
        assert victim in text, "the row this control edits is no longer there"
        doctored_text = text.replace(
            "`senses/power.py`, `senses/thermalgrid.py`, `senses/hwmon.py`",
            "`senses/power.py`, `senses/thermalgrid.py`", 1)
        assert doctored_text != text, (
            "the control's edit matched nothing, so it could not fail")
        doctored.write_text(doctored_text, encoding="utf-8")
        committed = _committed_part_3(doctored)
        assert committed != generated, (
            "a doctored Part 3 compared equal to the generator's output, so "
            "the test above would pass on a drifted document")
        # The mismatch must be the edit, not an unrelated difference: the
        # generator's own text is the reference, and the only lines that
        # disagree are the one droppped file.
        lost = [line for line in generated.split("\n")
                if "senses/hwmon.py" in line and line not in committed]
        assert lost, (
            "the doctored row was reported as different without the dropped "
            "file being the reason:\n" + committed)
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
