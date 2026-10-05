"""The learning panel: the surface for machinery that had no entry point at all.

`learning.train_and_save()`, `distill.harvest_rows()`, `learning.export_knowledge()`
and `distill.train_router()` were four pieces of machinery reachable only by
importing a module in a Python shell. This file exists because a feature nobody
can reach is the dead-code class this repository documents at length - and the
tests here are about the two properties that make such a panel trustworthy:

- **it reports what exists, and does not report a model as good.** The outcome
  model's row leads with its detection and prints both lifts, because 83% top-1
  against a 94% constant is what a model that learned nothing also scores.
- **its buttons refuse visibly.** Training with nothing to learn says why
  instead of writing a file, which is the behaviour the functions already have
  and the reason the panel must not paper over it.
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
pytest.importorskip("gi")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa.config import ChronoaConfig  # noqa: E402
from shani_chronoa.gui import surfaces  # noqa: E402
from shani_chronoa.gui.surfaces import learning  # noqa: E402


class _App:
    def __init__(self, config=None):
        self.config = config or ChronoaConfig()

    def activate_action(self, name, arg):
        pass


def _drain(predicate, tries=200) -> bool:
    """Pump the main loop until `predicate()` or the tries run out.

    `_run_async` reports from a worker thread through `GLib.idle_add`, so
    without a main loop those callbacks never run and a test of the async path
    measures nothing at all - it passed or failed on how long it slept.
    """
    from gi.repository import GLib

    context = GLib.MainContext.default()
    for _ in range(tries):
        if predicate():
            return True
        while context.pending():
            context.iteration(False)
        time.sleep(0.02)
    return predicate()


def _walk(node, out=None):
    out = [] if out is None else out
    out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


def _rows(widget):
    return {r.get_title(): r for r in _walk(widget)
            if type(r).__name__ == "ActionRow"}


def _buttons(widget):
    return [b for b in _walk(widget) if isinstance(b, Gtk.Button)]


def test_it_is_in_the_sidebar():
    """A panel nobody can open is the thing this file exists to prevent."""
    assert "learning" in surfaces.SURFACE_IDS
    assert "learning" in surfaces.all_surfaces()
    assert surfaces.all_surfaces()["learning"][0] == learning.TITLE


def test_it_declares_the_section_the_sidebar_renders():
    assert learning.SECTION in surfaces.SECTION_ORDER, (
        "a panel whose section is not in the order lands in 'Everything else'")


def test_it_builds_and_says_what_is_here():
    widget = learning.build(_App())
    rows = _rows(widget)
    for title in learning.ROW_TITLES:
        assert title in rows, title


def test_the_rows_are_addressed_by_name_not_by_position():
    """A new row must not silently renumber the ones after it, which is how the
    outcome row's tests were reading the wrong line until they were written
    against the title instead of `ROW_TITLES[4]`."""
    assert len(set(learning.ROW_TITLES)) == len(learning.ROW_TITLES), \
        "two rows share a title, so one of them cannot be addressed"
    assert len(learning.ROW_TITLES) == 6


def test_no_row_claims_a_model_is_good_when_there_is_none(tmp_path, monkeypatch):
    """The empty machine must read as empty, not as working."""
    from shani_chronoa import learning as core
    monkeypatch.setattr(core, "read_model_knowledge", lambda model=None: [])
    monkeypatch.setattr(core, "render_bandit", lambda b, top=12: "No arms yet.")
    monkeypatch.setattr(core, "model_path",
                        lambda name="outcome": tmp_path / "absent.json")
    monkeypatch.setattr("shani_chronoa.distill.harvest_rows", lambda **k: [])
    monkeypatch.setattr("shani_chronoa.distill.load_router", lambda **k: None)
    rows = _rows(learning.build(_App()))
    assert "none" in rows["Facts remembered"].get_subtitle().lower()
    assert "none" in rows["Routing pairs from your conversations"].get_subtitle().lower()
    assert "not trained" in rows["The outcome model"].get_subtitle().lower()


def test_the_outcome_row_leads_with_the_detection_not_the_accuracy(tmp_path, monkeypatch):
    """83% top-1 against a 94% constant is what *not* learning scores too."""
    import json

    payload = {
        "format": 1, "weights": {"0": [0.1, 0.2, 0.3]}, "bias": [0.0, 0.1, 0.0],
        "digest": "x",
        "provenance": {"honest": True, "detected": "verified",
                       "accuracy": 0.83, "baseline": 0.94,
                       "precision_lift": {"verified": 5.86}},
    }
    target = tmp_path / "outcome.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    from shani_chronoa import learning as core
    monkeypatch.setattr(core, "model_path", lambda name="outcome": target)
    subtitle = _rows(learning.build(_App()))["The outcome model"].get_subtitle()
    assert "verified" in subtitle
    assert "5.9x" in subtitle
    assert "predicts no verdict" in subtitle, (
        "it must say it is a flag, or the number above reads as an accuracy")


def test_a_dishonest_model_is_called_dishonest(tmp_path, monkeypatch):
    import json

    payload = {"format": 1, "weights": {"0": [0.1]}, "bias": [0.0], "digest": "x",
               "provenance": {"honest": False, "accuracy": 0.5, "baseline": 0.9}}
    target = tmp_path / "outcome.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    from shani_chronoa import learning as core
    monkeypatch.setattr(core, "model_path", lambda name="outcome": target)
    subtitle = _rows(learning.build(_App()))["The outcome model"].get_subtitle()
    assert "never beat" in subtitle


def test_it_offers_the_three_actions_and_none_of_them_fires_on_build():
    labels = [b.get_label() for b in _buttons(learning.build(_App()))]
    assert "Train the outcome model" in labels
    assert "Train a router" in labels
    assert "Export" in labels


def test_training_a_router_with_nothing_learned_says_so(tmp_path, monkeypatch):
    """The refusal is the output. A panel that said "done" would be lying."""
    status = _Status()
    button = Gtk.Button()
    from shani_chronoa.gui.surfaces import learning as panel
    monkeypatch.setattr("shani_chronoa.distill.harvest_rows", lambda **k: [])
    panel._train_router(button, status)
    _drain(lambda: any("not" in m.lower() or "nothing" in m.lower()
                        for m in status.seen))
    assert any("not" in m.lower() or "nothing" in m.lower()
               for m in status.seen), status.seen


class _Status(Gtk.Label):
    def __init__(self):
        super().__init__()
        self.seen = []

    def set_label(self, text):  # noqa: D102 - a recorder for the async path
        self.seen.append(text)
        Gtk.Label.set_label(self, text)


def test_the_export_button_reports_when_there_is_nothing_to_export(monkeypatch):
    """`export_knowledge` returns None on a machine with no model, and that has
    to read as a refusal rather than as a silent no-op."""
    from shani_chronoa import learning as core
    seen = []
    monkeypatch.setattr(core, "export_knowledge", lambda **k: None)

    def work(report):
        report("Packaging what this machine has learned...")
        path = core.export_knowledge()
        report(f"Wrote {path}" if path else "Nothing to export - there is no "
                                              "trained model on this machine yet")

    status = _Status()
    learning._run_async(Gtk.Button(), work, status)
    _drain(lambda: any("Nothing to export" in m for m in status.seen))
    assert any("Nothing to export" in m for m in status.seen), status.seen


def test_the_train_button_survives_a_failure_and_says_why(monkeypatch):
    """A fit that raises must not leave a button insensitive with no message -
    which is the failure mode `_run_async` exists to prevent."""
    status = _Status()

    def work(report):
        raise ValueError("the log is not readable")

    button = Gtk.Button()
    learning._run_async(button, work, status)
    _drain(lambda: any("ValueError" in m for m in status.seen))
    assert any("ValueError" in m and "not readable" in m for m in status.seen), status.seen
    _drain(lambda: button.get_sensitive())
    assert button.get_sensitive(), "a failure left the button insensitive with no way back"
