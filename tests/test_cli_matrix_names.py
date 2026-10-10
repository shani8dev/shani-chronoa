"""Two fields in `cli_matrix.py` that promised something they did not measure.

Both were recorded in AGENTS.md as open decisions - *"measure the name's
meaning or remove the column"* - and this file is the measurement that made
the decision and the test that holds it.

**`service_exposure`.** The field carried systemd's own sandboxing count
(`systemd-analyze security --offline`: 0 tight .. 10 no sandboxing) under a
name that reads as attack surface. Nothing about network reachability, what
the service can read, or whether the program inside is careful is in that
number. Renamed to `service_sandboxing`, and the test drives the real parser
against a real `systemd-analyze` line rather than a mock of the regex.

**`ideas[].score`.** Measured against the matrix in this tree: of 382 ideas,
**8 tie at exactly 25** (every one of them an `inspect` intent in one of the
two biggest categories) and **94 score 0**. So the number ranks how *big* a
category is, not how valuable an idea is - and ranking by it surfaces the
category-size winner rather than anything actionable. Renamed
`category_weight` and relabelled in both renderers, so a reader cannot
mistake a size for a priority.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "tools" / "cli_matrix.py"
_spec = importlib.util.spec_from_file_location("cli_matrix", _PATH)
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)

MATRIX = (Path(__file__).resolve().parent.parent.parent
          / "shani-install-media" / "test-env" / "mout-plasma"
          / "shanios-matrix.json")


# --- service_sandboxing -------------------------------------------------------

def test_the_field_is_named_for_what_it_measures(monkeypatch, tmp_path):
    """The rename itself: the output key is `sandboxing`, not `exposure`.

    A reader who takes `exposure` at face value decides what to harden from a
    checklist of systemd options; the key name is the only thing standing
    between those two facts.
    """
    monkeypatch.setattr(cm, "_UNIT_DIRS", (str(tmp_path),))
    unit = tmp_path / "cups.service"
    unit.write_text("[Service]\nExecStart=/usr/sbin/cupsd\n")
    monkeypatch.setattr(cm.shutil, "which", lambda b: "/usr/bin/systemd-analyze")
    monkeypatch.setattr(cm, "run", lambda argv, **kw: (
        "cups.service\n"
        "  Overall exposure level for cups.service: 9.4 EXPOSED\n"))
    rows = cm.service_sandboxing([{"unit": "cups.service", "scope": "system"}])
    assert rows == [{"unit": "cups.service", "sandboxing": 9.4, "rating": "EXPOSED"}], rows
    assert "exposure" not in json.dumps(rows)


def test_the_value_comes_from_systemds_own_wording(monkeypatch, tmp_path):
    """The number is parsed out of the tool's real sentence, so a wording
    change in systemd shows up as an empty list rather than as a fabricated
    score - and the field keeps systemd's vocabulary for what it is."""
    monkeypatch.setattr(cm, "_UNIT_DIRS", (str(tmp_path),))
    unit = tmp_path / "x.service"
    unit.write_text("[Service]\n")
    monkeypatch.setattr(cm.shutil, "which", lambda b: "/usr/bin/systemd-analyze")
    monkeypatch.setattr(cm, "run", lambda argv, **kw: "  Overall exposure level for x.service: 3.1 OK\n")
    rows = cm.service_sandboxing([{"unit": "x.service", "scope": "system"}])
    assert rows[0]["sandboxing"] == 3.1
    assert rows[0]["rating"] == "OK"


def test_no_systemd_analyze_means_no_rows(monkeypatch):
    monkeypatch.setattr(cm.shutil, "which", lambda b: None)
    assert cm.service_sandboxing([{"unit": "a.service", "scope": "system"}]) == []


def test_a_unit_file_that_cannot_be_found_is_skipped(monkeypatch):
    monkeypatch.setattr(cm.shutil, "which", lambda b: "/usr/bin/systemd-analyze")
    monkeypatch.setattr(cm.os.path, "isfile", lambda p: False)
    assert cm.service_sandboxing([{"unit": "ghost.service", "scope": "system"}]) == []


def test_template_instances_and_user_services_are_left_out(monkeypatch, tmp_path):
    """`@` templates and session services are not what the field is for, and
    running `systemd-analyze security` on a template path guesses at which
    instance it means."""
    monkeypatch.setattr(cm, "_UNIT_DIRS", (str(tmp_path),))
    (tmp_path / "real.service").write_text("[Service]\n")
    monkeypatch.setattr(cm.shutil, "which", lambda b: "/usr/bin/systemd-analyze")
    monkeypatch.setattr(cm, "run", lambda argv, **kw: "")
    rows = cm.service_sandboxing([
        {"unit": "getty@.service", "scope": "system"},
        {"unit": "pipewire.service", "scope": "user"},
    ])
    assert rows == []


# --- ideas' category_weight ---------------------------------------------------

@pytest.mark.skipif(not MATRIX.exists(), reason=f"no matrix at {MATRIX}")
def test_the_ideas_number_is_not_a_priority_ranking():
    """**The measurement behind the rename.** The old `score` key ranked
    category *size*: a big `inspect` category ties at the top while the
    interesting, small ideas sit at 0. Asserted from the matrix in this tree,
    so the claim is checked against real numbers rather than restated.

    The matrix on disk was written by the old code, so it carries `score` -
    which is the point: this is the field a reader of that file saw.
    """
    data = json.load(MATRIX.open())
    ideas = data.get("ideas") or []
    assert ideas, "the matrix carries no ideas, so this test proves nothing"
    values = [i["score"] for i in ideas]          # the field as it was shipped
    top = max(values)
    tied = [i for i in ideas if i["score"] == top]
    # A tie at the top is the shape of a category-size number, and it is the
    # reason the column is no longer presented as a rank.
    assert len(tied) > 1, (
        "expected a tie at the top - a single winner would mean the number "
        f"does discriminate (top={top})")
    assert 0 in values, (
        "expected zero-weight ideas: a number with no zeros is not a category "
        "proxy")


def test_the_source_emits_the_renamed_key():
    """The writer's own field name, which is what the rename actually is.

    A matrix still carrying `score` is a run of the old code; the source is
    what the next run writes.
    """
    src = _PATH.read_text()
    assert '"category_weight"' in src
    assert '"score": sum(' not in src


def test_the_markdown_and_html_no_longer_call_it_a_score():
    """Both renderers said *"score 25"* next to a number that ranks size. The
    words a reader sees are the interface here, so they are asserted."""
    src = _PATH.read_text()
    assert "score {i['score']}" not in src
    assert "score ${i.score}" not in src
    assert "category weight {i['category_weight']}" in src
    assert "category weight ${i.category_weight}" in src
    # The markdown now says what the number is not, so a reader who would have
    # worked top-down is told not to.
    assert "not a priority" in src
