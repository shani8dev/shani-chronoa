"""`gen_eval_cases.py --refresh-coverage`: a repair tool that can repair.

`skills_total` sat at **250** against a registry of **257** for as long as it took
to add seven skills, and `tests/test_task_eval.py` was red about it the whole
time. That is the gate working. The finding is why nobody fixed it.

**The only tool that wrote the block could not fix a stale count.** It rewrote
`coverage` inside `if added:` — so only when a skill was missing a case — and then
set `skills_total = skills_asserted`, because its job is to *fill the gaps* and
leave coverage complete. Using it to correct one stale number would have appended
47 generated cases and flipped `about` to "COVERAGE IS COMPLETE". A repair tool
that cannot repair is a reason the repair does not happen.

`--refresh-coverage` recomputes the three derived numbers from the registry and
the cases, writes **no** cases, and leaves `about` alone.

The three numbers are pure functions of two facts already in this repository — the
registered skill names and the cases in this file — so there is no state in which
the recorded value is right and the derived one wrong. That is the whole argument
for generating them rather than recording them, and it is the same one
`tests/test_task_eval.py`'s own docstring makes about not repeating a number in
prose.

`gated_unguarded` is retired rather than recomputed: it recorded **56**, which
matches neither `len(capabilities.GATED)` (78) nor the gated skills with no case
(9), and nothing in the tree read it or checked it. Its meaning is not recoverable,
so recomputing it would mean inventing one.
"""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "gen_eval_cases", ROOT / "tools" / "gen_eval_cases.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _load()


@pytest.fixture
def shipped():
    return json.loads((ROOT / "tools" / "eval_cases.json").read_text())


class TestTheShippedBlockIsTrue:
    """The gate this exists to satisfy, asserted here too so the reason it is
    green is the block being right rather than the assertion being weakened."""

    def test_the_recorded_total_is_the_registry(self, shipped):
        from shani_chronoa.tools import TOOLS
        names = {t["function"]["name"] for t in TOOLS}
        assert shipped["coverage"]["skills_total"] == len(names), (
            f"run `python3 tools/gen_eval_cases.py --refresh-coverage` - it reads "
            f"the registry and writes nothing else")

    def test_the_note_repeats_the_numbers_it_asserts(self, shipped):
        """The note said *210 of 245* while the block beside it said 250 and the
        registry said 257 - three numbers, none agreeing, and the stale ones were
        only findable by reading them."""
        note = shipped["coverage"]["note"]
        block = shipped["coverage"]
        assert f"{block['skills_asserted']} of {block['skills_total']}" in note, note

    def test_the_retired_number_stays_retired(self, shipped):
        assert "gated_unguarded" not in shipped["coverage"], (
            "it was retired with its reason in the note; a number nothing reads "
            "and nothing derives is an assertion, not a measurement")
        assert "gated_unguarded" in shipped["coverage"]["note"], (
            "retired *with the reason kept* - the rule this repository applies to "
            "knowledge that is not yet actionable")


class TestRefreshRecomputesRatherThanRecords:
    def test_a_doctored_total_is_corrected_from_the_registry(self):
        data = {"cases": [{"expect": [{"tool": "a"}]}],
                "coverage": {"cases": 99, "skills_asserted": 99, "skills_total": 99}}
        got = gen.refresh_coverage(data, ["a", "b", "c"], {"a"})
        assert got["coverage"]["skills_total"] == 3, got["coverage"]
        assert got["coverage"]["skills_asserted"] == 1, got["coverage"]
        assert got["coverage"]["cases"] == 1, got["coverage"]

    def test_a_case_naming_a_tool_that_no_longer_exists_does_not_count(self):
        """`discover_skills` and this file disagree after a rename, and counting
        a name the registry does not have would overstate coverage."""
        data = {"cases": [{"expect": [{"tool": "gone"}]}], "coverage": {}}
        got = gen.refresh_coverage(data, ["here"], {"gone", "here"})
        assert got["coverage"]["skills_asserted"] == 1, got["coverage"]

    def test_it_is_idempotent(self, shipped):
        """**The load-bearing property.** If a refresh changed the block on a
        block that was already true, it would be a generator of diffs rather than
        a repair, and nobody would run it - which is the state this file was in
        before `--refresh-coverage` existed."""
        from shani_chronoa.tools import TOOLS
        names = sorted(entry["function"]["name"] for entry in TOOLS)
        covered = {want["tool"] for case in shipped["cases"]
                   for want in case.get("expect", [])}

        before = json.loads(json.dumps(shipped))
        after = gen.refresh_coverage(json.loads(json.dumps(shipped)), names, covered)

        assert after["coverage"] == before["coverage"], (
            f"{before['coverage']}\n  vs\n{after['coverage']}")


class TestRefreshTouchesNothingElse:
    """It is a refresh, not a completer - that distinction is the whole reason it
    can be run to fix a number."""

    def test_it_adds_no_cases(self, monkeypatch, tmp_path):
        target = tmp_path / "cases.json"
        target.write_text(json.dumps({
            "about": "PROSE THAT MUST SURVIVE",
            "cases": [{"id": "a", "say": "x", "expect": [{"tool": "a"}]}],
            "coverage": {"cases": 0, "skills_asserted": 0, "skills_total": 0}}))
        monkeypatch.setattr(gen, "CASES", target)
        before = len(json.loads(target.read_text())["cases"])

        assert gen.main(["--refresh-coverage"]) == 0

        after = json.loads(target.read_text())
        assert len(after["cases"]) == before, (
            f"{before} -> {len(after['cases'])}: a refresh that adds cases is the "
            "completer, and running it to fix a count would rewrite the corpus")

    def test_it_leaves_the_prose_alone(self, monkeypatch, tmp_path):
        """`about` carries a *claim* about coverage that
        `tests/test_task_eval.py` checks against these numbers. Rewriting it to
        agree would make that check the file agreeing with itself."""
        target = tmp_path / "cases.json"
        target.write_text(json.dumps({
            "about": "PROSE THAT MUST SURVIVE",
            "cases": [{"id": "a", "say": "x", "expect": [{"tool": "a"}]}],
            "coverage": {"cases": 0, "skills_asserted": 0, "skills_total": 0}}))
        monkeypatch.setattr(gen, "CASES", target)

        gen.main(["--refresh-coverage"])

        assert json.loads(target.read_text())["about"] == "PROSE THAT MUST SURVIVE"

    def test_check_still_writes_nothing(self, monkeypatch, tmp_path):
        """`--check` is documented as "report coverage, change nothing", and it is
        what a slot run calls. A refresh flag that leaked into it would write on a
        machine whose file is a read-only checkout."""
        target = tmp_path / "cases.json"
        target.write_text('{"about": "p", "cases": [], "coverage": {"skills_total": 1}}')
        monkeypatch.setattr(gen, "CASES", target)
        # **Bytes, not parsed JSON.** Comparing `json.loads(...)` against the
        # original dict passes for a write that only reformats, so the mutation
        # "make --check write the file back" ran green - the very thing the test
        # is for. A rewrite that changes nothing observable is still a write on a
        # machine whose checkout is read-only.
        before = target.read_bytes()

        assert gen.main(["--check"]) == 0

        assert target.read_bytes() == before, "--check wrote the file"