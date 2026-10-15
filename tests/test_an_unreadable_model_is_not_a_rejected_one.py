"""**The third list was always empty.** `scan()`'s docstring says its three
returns are separate "on purpose":

> `unreadable` ... is separate on purpose: **a file that could not be opened is
> a permissions problem, and reporting it as a format problem is a confident
> wrong answer.**

**Measured 2026-10-15, before this test existed, against a real mode-000 file
beside a real model:**

    models=1  excluded=1  unreadable=0
    scan()["unreadable"] -> []

Every unreadable entry was appended to `excluded` with `kind="unreadable"`, so
the third list was `[]` on every input and a caller reading it took that as
**"nothing was unreadable"** - the absence-shaped wrong answer, in the one
module whose docstring argues against exactly that reading.

This is the shape `AGENTS.md` records for `wpctl` (three copies), the finite
check (four) and `_SYNONYMS`: **a documented separation the code does not
have.** Two consequences are asserted here rather than left implied:

- an unreadable file is in `unreadable` and **not** in `excluded`, so a caller
  that counts "files this build refuses, and why" does not inherit a
  permissions problem as a format verdict; and
- a file that reads fine and is genuinely **not** a model (an adapter, say) is
  still in `excluded` and **not** in `unreadable`, because that answer *was*
  reached. The over-fix - "anything not offered is unreadable" - is the shape
  this repository records for `get_children` and `_secure_field`, so it gets its
  own witness rather than being left to the count.

Both directions are needed: the first alone is satisfied by an `unreadable`
list that swallows everything, which is what an unexamined "fix" looks like.
"""

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "usr/lib/shani-chronoa"))

from shani_chronoa import model_registry            # noqa: E402
from tests.test_a_lora_is_not_a_language_model import _synth   # noqa: E402


def _dir(tmp_path: Path) -> Path:
    d = tmp_path / "llm"
    d.mkdir()
    return d


@pytest.mark.skipif(os.geteuid() == 0,
                    reason="root opens a mode-000 file; the premise is absent")
def test_a_file_that_cannot_be_opened_is_unreadable_and_not_excluded(tmp_path):
    """The shipped defect, driven through the real reader.

    ``_synth`` writes a real GGUF header, so the model beside it is a genuine
    positive control: if the unreadable case were satisfied by something that
    refuses *everything*, that control would still be offered.
    """
    d = _dir(tmp_path)
    _synth(d / "q-model.gguf", **{"general.type": "model",
                                 "general.basename": "Qwen3"})
    locked = d / "locked.gguf"
    locked.write_bytes(b"")
    locked.chmod(0o000)
    try:
        # The premise itself: opening it really does fail for this user.
        with pytest.raises(PermissionError):
            locked.open("rb").read(4)

        scan = model_registry.scan(model_dir=d, specs={})

        unread = [u["entry"]["filename"] for u in scan["unreadable"]]
        excluded = [e["entry"]["filename"] for e in scan["excluded"]]

        assert "locked.gguf" in unread, (
            f"a file this user cannot open is not reported as unreadable: "
            f"{scan}")
        assert "locked.gguf" not in excluded, (
            "an unreadable file is in `excluded`, so a caller that reads "
            "`excluded` as 'files this build refuses, and why' inherits a "
            f"permissions problem as a format verdict: {excluded}")

        # The control: the readable model beside it was still classified.
        assert "q-model.gguf" in [m["filename"] for m in scan["models"]], (
            f"the real model was caught by the same rule: {scan}")
    finally:
        locked.chmod(0o644)


def test_a_file_that_reads_and_is_not_a_model_stays_excluded(tmp_path):
    """**The over-fix.** An adapter's header *was* read; the answer is known.

    Putting this in `unreadable` would make "unreadable" mean "not offered",
    which is the two answers this split exists to keep apart.
    """
    d = _dir(tmp_path)
    _synth(d / "q-adapter.gguf", **{"general.type": "adapter",
                                    "adapter.type": "lora",
                                    "general.basename": "Qwen3 1.7B"})

    scan = model_registry.scan(model_dir=d, specs={})

    assert scan["unreadable"] == [], (
        "a header that read cleanly and declared an adapter is a known answer, "
        f"not an unreadable one: {scan['unreadable']}")
    assert [e["kind"] for e in scan["excluded"]] == ["adapter"], (
        f"and it is still reported with its reason: {scan['excluded']}")


def test_a_clean_directory_reports_nothing_unreadable(tmp_path):
    """**The negative control.** Empty is `[]` for a reason, not for want of a case.

    Without this, a `scan()` that reported everything as unreadable would pass
    the two tests above on a directory holding one good model - so "unreadable"
    being non-empty would prove nothing.
    """
    d = _dir(tmp_path)
    _synth(d / "q-model.gguf", **{"general.type": "model",
                                 "general.basename": "Qwen3"})
    scan = model_registry.scan(model_dir=d, specs={})

    assert len(scan["models"]) == 1, f"the control model was not read: {scan}"
    assert scan["unreadable"] == [] and scan["excluded"] == [], (
        f"a directory with one good model reports {scan}")


def test_the_two_lists_stay_disjoint_on_a_mixed_directory(tmp_path):
    """**The property**, stated over both populations at once.

    The two earlier tests each hold for one directory. This one asks the
    question the docstring actually argues for - *can a caller tell the two
    answers apart on the directory it was given?* - and it would catch a
    change that put a readable non-model into `unreadable` while a single-file
    fixture happened not to notice.
    """
    d = _dir(tmp_path)
    _synth(d / "q-model.gguf", **{"general.type": "model",
                                 "general.basename": "Qwen3"})
    _synth(d / "q-adapter.gguf", **{"general.type": "adapter",
                                    "adapter.type": "lora",
                                    "general.basename": "Qwen3 1.7B"})
    _synth(d / "q-vocab.gguf", **{"general.type": "vocab"})
    locked = d / "locked.gguf"
    locked.write_bytes(b"")
    locked.chmod(0o000)
    try:
        scan = model_registry.scan(model_dir=d, specs={})
        unread = {u["entry"]["filename"] for u in scan["unreadable"]}
        excluded = {e["entry"]["filename"] for e in scan["excluded"]}
        models = {m["filename"] for m in scan["models"]}

        assert not (unread & excluded), (
            f"the same file is both unreadable and refused: {unread & excluded}")
        assert not (unread & models) and not (excluded & models), (
            f"a file is both offered and not: {scan}")
        if os.geteuid() != 0:
            assert "locked.gguf" in unread
        assert {"q-adapter.gguf", "q-vocab.gguf"} <= excluded
        assert "q-model.gguf" in models
    finally:
        locked.chmod(0o644)


def test_a_header_that_says_vocab_is_not_offered_as_a_model(tmp_path):
    """**The second answer to one question.**

    `modelfmt.kind_of` answers "tokenizer data, not weights" for a file
    *named* `-vocab`, and `classify()` consulted `general.type` for exactly one
    value - `adapter`. So the same file, named so the rule cannot see it and
    carrying the answer in its own header, was offered as a loadable model:

        general.type=vocab    filename-kind=model    classify=vocab   (after)

    Derived from `_TYPE_SUFFIXES` rather than a new tuple, because a second
    hand-written vocabulary is the `wpctl`-three-copies shape: the two copies
    agree the day they are written and drift the week after.
    """
    d = _dir(tmp_path)
    _synth(d / "thing.gguf", **{"general.type": "vocab",
                                "general.basename": "Qwen3"})

    entry = model_registry.describe(d / "thing.gguf")
    kind, reason = model_registry.classify(entry)

    assert kind == "vocab", (
        f"a file whose own header says it is tokenizer data is {kind!r}: "
        f"{reason}")
    assert "tokenizer data" in reason, (
        f"and the reason does not say what it is: {reason}")


def test_the_header_does_not_outrank_a_declared_model(tmp_path):
    """**The over-fix.** A header that says `model` is a model.

    Reading `general.type` at all is new, so the direction that matters is
    checked too: adding a type must not start refusing files that declare
    themselves loadable.
    """
    d = _dir(tmp_path)
    _synth(d / "thing.gguf", **{"general.type": "model",
                                "general.basename": "Qwen3"})
    scan = model_registry.scan(model_dir=d, specs={})
    assert [m["filename"] for m in scan["models"]] == ["thing.gguf"], (
        f"a header declaring a model is no longer offered: {scan}")