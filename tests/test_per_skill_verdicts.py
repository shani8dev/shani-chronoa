"""What the assistant has been learning from, which turned out to be two fixtures.

Two measurements, both from this machine's own log, and both found by building
the per-skill summary rather than by auditing anything:

- **`liar` failed every one of its 313 calls and never verified once.** It claims
  "Created the file." 343 times while its post-condition records *"the file was
  never created"*. There is no `skills/liar.py` in this tree. It is a fixture
  that did exactly what it was built to do.
- **`liar` and `unver` account for 692 of 12,856 logged calls and 313 of the 378
  failures.** So this machine's entire failure signal comes from tools that are
  no longer installed, and a model fitted on that log reports a number about a
  machine that does not exist any more - unless it says so.

The panel says "not installed any more, so this is history, not a fault", and the
model's provenance records the count rather than filtering it out, because
filtering would change what the model says about the past while looking helpful.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import learning  # noqa: E402
from shani_chronoa.gui.surfaces import activity  # noqa: E402


def _log(tmp_path, rows):
    path = tmp_path / "tool_calls.log"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return path


def _call(tool, verdict, result="ok", evidence=""):
    return {"timestamp": "2026-10-05T05:00:00+00:00", "tool_name": tool,
            "args": {}, "result": result, "duration_ms": 1.0,
            "origin": "user", "verdict": verdict, "evidence": evidence}


# ---------------------------------------------------------------------------
# the summary
# ---------------------------------------------------------------------------


def test_it_counts_per_skill_and_worst_first():
    records = [_call("delete_file", "verified")] * 8 + \
              [_call("delete_file", "failed")] * 2 + \
              [_call("write_text_file", "failed")] * 5
    rows = activity.per_skill(records, known={"delete_file", "write_text_file"})
    assert [r[0] for r in rows] == ["write_text_file", "delete_file"], (
        "a skill that fails every time must outrank one that fails 10%")
    assert [r[1:4] for r in rows] == [(5, 0, 5), (10, 8, 2)]
    assert all(r[4] is True for r in rows), "both names were passed as known"


def test_unverified_calls_are_left_out_rather_than_averaged_in():
    """Most calls have no verdict, and counting them as successes would turn
    every rate into a statement about the post-condition coverage instead of the
    skill."""
    records = [_call("delete_file", "unverified")] * 100 + \
              [_call("delete_file", "failed")] * 2
    rows = activity.per_skill(records, known={"delete_file"})
    assert rows[0][1] == 2, "the 100 unverified calls were counted"


def test_a_skill_that_fails_every_time_is_called_broken():
    rows = [_call("liar", "failed") for _ in range(30)]
    sentence = activity._rate_sentence("liar", 30, 0, 30, installed=True)
    assert "broken" in sentence and "every one" in sentence


def test_a_removed_skill_says_so_rather_than_looking_broken():
    """The two readings are completely different: broken, or a fixture that failed
    on purpose. Only one of them is a defect, and the count alone cannot say
    which."""
    sentence = activity._rate_sentence(
        "liar", 313, 0, 313, installed=False)
    assert "not installed any more" in sentence
    assert "history, not a fault" in sentence


def test_too_few_calls_is_said_rather_than_shown_as_a_rate():
    """A rate on five calls is not a rate, and printing "91% failed" for it would
    be the panel inventing a finding."""
    sentence = activity._rate_sentence("get_clipboard", 5, 0, 5, installed=True)
    assert "too few" in sentence
    assert "91%" not in sentence


def test_it_knows_which_skills_are_installed():
    from shani_chronoa.tools import TOOLS
    known = activity.installed_skills()
    assert known == {t["function"]["name"] for t in TOOLS}
    assert "delete_file" in known
    assert "liar" not in known, "liar is a fixture from a previous tree"


def test_a_log_with_nothing_scored_produces_nothing():
    assert activity.per_skill([_call("x", "unverified")], known={"x"}) == []


# ---------------------------------------------------------------------------
# the provenance
# ---------------------------------------------------------------------------


def test_it_counts_the_examples_about_skills_that_are_gone(tmp_path):
    path = _log(tmp_path, [_call("liar", "failed")] * 3 +
                [_call("delete_file", "verified")] * 2)
    count, names = learning.unknown_tool_examples(learning._entries(path))
    assert count == 3
    assert names == ["liar"]


def test_it_reads_records_not_examples(tmp_path):
    """An `Example` is a hashed vector and a label; the tool name is gone by then.

    So this cannot be answered from the training set, and a first attempt that
    asked an `Example` for `.tool` raised `AttributeError` - at fit time, on
    every save.
    """
    path = _log(tmp_path, [_call("delete_file", "verified")] * 2)
    examples = learning.load(path)
    assert examples and not hasattr(examples[0], "tool"), (
        "if Example grows a tool field this test should stop asserting its absence")


def test_a_clean_log_reports_nothing_unknown(tmp_path):
    path = _log(tmp_path, [_call("delete_file", "verified")] * 4)
    assert learning.unknown_tool_examples(learning._entries(path)) == (0, [])


def test_the_provenance_says_how_much_of_the_training_data_is_about_the_past(tmp_path):
    """Recorded, not filtered.

    Filtering would be the more helpful behaviour and the wrong one: it changes
    what the model says about the past without saying it did, so the number is
    for a person to decide with.
    """
    # Enough distinct feature vectors per verdict for the split to hold anything
    # out. Two things were wrong in the first attempt and both looked like the
    # product refusing: `args` values do **not** make distinct vectors (they
    # collapse to a shape, so 9 arg values were 1 vector and the whole log was
    # 4), and `train_and_save` then correctly answered "not enough data to hold
    # anything out" because the holdout stride never landed on 4 groups.
    # Names that are genuinely not installed. The first attempt used
    # `get_clipboard`, `recording` and `scan_network` on the strength of a
    # different test's list, and all three *are* real skills - so the count came
    # back 24 instead of 96 and the names list had one entry. A fixture that
    # asserts "not installed" should ask the registry, not a human memory.
    removed = ["liar", "unver", "not_a_skill", "fixture_x"]
    real = ["delete_file", "write_text_file", "get_datetime", "set_volume",
            "list_processes", "list_wifi_networks", "get_battery_status",
            "system_info"]
    rows = [_call(tool, "failed") for tool in removed for _ in range(24)]
    rows += [_call(tool, "verified") for tool in real for _ in range(24)]
    # The third class, because the gate after the split is right to refuse a fit
    # that has never seen `unverified` - which is most real logs, and which two
    # of my earlier attempts here tripped over in turn.
    rows += [_call(tool, "unverified") for tool in real for _ in range(24)]
    path = _log(tmp_path, rows)
    out = learning.train_and_save(path=path, name="outcome-test", dry_run=True)
    provenance = out.get("provenance") or {}
    assert provenance, f"the fit never got as far as a provenance: {out.get('reason')}"
    assert provenance.get("examples_for_unknown_tools") == len(removed) * 24, (
        "a model fitted on records about fixtures must say how many in its own "
        f"provenance, not merely report an accuracy: {provenance}")
    assert "liar" in (provenance.get("unknown_tools") or [])
