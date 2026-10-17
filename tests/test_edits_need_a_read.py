"""`edit_file` edits the version the model read, and refuses any other.

The OpenDots shape (`expectedRevision`, refuse a stale write), applied to
files through `read_ledger`. Measured 2026-10-15: Qwen3-1.7B edited pricing.py
with text it never read, from the test's description, and drifted off the
task. What must hold:

- never read: refused, the file shown, and the next call goes through;
- changed after it was read: refused with the current text;
- read with read_text_file: the edit goes through first time;
- its own edits count as read, so a run of edits needs no read between.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import edit_file, read_text_file  # noqa: E402

pytestmark = pytest.mark.read_guard


class _On:
    def get_bool(self, key, default=False):
        return True


@pytest.fixture
def shop(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "shop").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(edit_file, "ChronoaConfig", lambda: _On())
    path = home / "shop" / "pricing.py"
    path.write_text("def total(p, d=0):\n    return sum(p) - sum(p) * d\n")
    return path


def _edit(path, old, new):
    return edit_file._run({"path": str(path), "old_string": old, "new_string": new})


def test_an_unread_file_is_shown_and_the_retry_goes_through(shop):
    out = _edit(shop, "sum(p) * d", "sum(p) * d / 100")
    assert out.startswith("Nothing was edited: you have not read it yet"), out
    assert "return sum(p) - sum(p) * d" in out, "the refusal did not show the file"
    assert "/ 100" not in shop.read_text()
    assert _edit(shop, "sum(p) * d", "sum(p) * d / 100").startswith("Replaced")


def test_a_file_changed_after_the_read_is_refused(shop):
    read_text_file._run({"path": str(shop)})
    shop.write_text(shop.read_text() + "# a person added this\n")
    out = _edit(shop, "sum(p) * d", "sum(p) * d / 100")
    assert "it changed after you read it" in out and "# a person added this" in out
    assert "/ 100" not in shop.read_text()


def test_a_read_then_two_edits_need_no_second_read(shop):
    read_text_file._run({"path": str(shop)})
    assert _edit(shop, "sum(p) * d", "sum(p) * d / 100").startswith("Replaced")
    assert _edit(shop, "d=0", "d=0.0").startswith("Replaced"), "its own edit made the file stale"


def test_a_preview_neither_checks_nor_counts_as_a_read(shop):
    out = edit_file._run({"path": str(shop), "old_string": "sum(p) * d",
                          "new_string": "x", "preview": True})
    assert out.startswith("Preview only")
    assert _edit(shop, "sum(p) * d", "x").startswith("Nothing was edited: you have not read")
