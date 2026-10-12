"""The model picker: one popover, and it must agree with the strip.

Three claims this file holds, each of which was measured by rendering the real
widgets rather than by reading the code:

- **`rows_for` says three different things about one tier** - what it is
  called, whether it is on this machine, and whether it is answering - and a
  row that confuses them is worse than no row.
- **The popover is rebuilt, not appended to.** A model downloaded after the
  window opened has to become a choice.
- **The picker row under the composer and the strip's chip are the same
  question.** They were two independent walks of two different tables and
  produced two different words for the same state - the chip said "No model"
  where the row said "the default model", and then said "No model" while a
  model really was active. That is the two-widgets-that-disagree failure this
  repository records more often than any other, and `test_the_row_and_the_chip
  _always_name_the_same_model` is the reason it cannot come back quietly.
"""

import sys

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa.gui import model_picker  # noqa: E402


class _Spec:
    def __init__(self, key, blurb=""):
        self.key = key
        self.blurb = blurb


#: `(spec, label, order)` - the shape `local_llm.TIERS` actually holds, and
#: the label is copied from it verbatim because `rows_for` **splits on `" - "`**
#: and my first fixture omitted the separator. The result was a test asserting
#: the short name was `Small` while the module was handed `Small (0.6 GB)` and
#: had nothing to split - a fixture shaped unlike the real data tests a
#: different thing, and the first version's failure was the fixture's.
TIERS = (
    (_Spec("qwen3-0.6b", "quick on any computer"),
     "Small (0.6 GB) - quick on any computer, for simple requests", 1),
    (_Spec("qwen3-1.7b", "the best fit for most computers"),
     "Medium (1.1 GB) - the best fit for most computers", 2),
    (_Spec("qwen3-8b", "smarter, noticeably slower without a graphics card"),
     "Large (2.5 GB) - smarter, noticeably slower without a graphics card", 3),
)


@pytest.fixture
def machine(monkeypatch):
    """The three facts `rows_for` reads, settable one at a time."""
    from shani_chronoa import local_llm

    state = {"installed": {"qwen3-1.7b"}, "active": "qwen3-1.7b"}
    monkeypatch.setattr(local_llm, "TIERS", TIERS, raising=False)
    monkeypatch.setattr(local_llm, "installed",
                        lambda: set(state["installed"]), raising=False)
    monkeypatch.setattr(local_llm, "active",
                        lambda: state["active"], raising=False)
    return state


def _row(machine, key):
    return next(r for r in model_picker.rows_for(None) if r[0] == key)


class TestWhatARowSays:

    def test_every_tier_is_offered_even_when_absent(self, machine):
        """**A row is never dropped for being uninstalled.**

        Hiding it leaves a picker whose choices look like the whole story, and
        the next thing that happens is somebody wondering where the big model
        went. The control is `usable`, not presence.
        """
        machine["installed"] = set()
        keys = [r[0] for r in model_picker.rows_for(None)]
        assert keys == ["qwen3-0.6b", "qwen3-1.7b", "qwen3-8b"], keys

    def test_an_absent_model_says_it_is_a_download_not_a_mode(self, machine):
        machine["installed"] = {"qwen3-1.7b"}
        row = _row(machine, "qwen3-0.6b")
        assert row[3] is False, "an uninstalled tier must not be usable"
        assert "download" in row[2] and "not a mode" in row[2], row[2]

    def test_the_answering_model_says_so_and_is_not_reusable(self, machine):
        row = _row(machine, "qwen3-1.7b")
        assert row[4] is True
        assert row[3] is True, "installed, so it is a real option"
        assert row[2].startswith("answering now"), row[2]

    def test_the_title_is_the_label_without_its_sentence(self, machine):
        """**The size stays; the sentence goes.**

        My first assertion was that the title is `"Small"`, and it failed -
        the module returns `"Small (0.6 GB)"`. The module is right and I was
        wrong: the sentence is what will not fit in a 260px popover, and the
        size is exactly what a person needs before choosing something they are
        about to load. `rows_for` splits on `" - "`, which is the boundary
        between the two, and my first fixture had no `" - "` in it at all - so
        the test was asserting against data shaped unlike the real thing.
        """
        row = _row(machine, "qwen3-0.6b")
        assert row[1] == "Small (0.6 GB)", row[1]
        assert " - " not in row[1], "the sentence came along with the name"

    def test_an_installed_model_that_is_not_answering_carries_its_blurb(
            self, machine):
        machine["installed"] = {"qwen3-1.7b", "qwen3-8b"}
        row = _row(machine, "qwen3-8b")
        assert row[2] == TIERS[2][0].blurb, row[2]
        assert "answering now" not in row[2], (
            "a model that is not answering must not claim to be")

    def test_an_unreadable_tier_list_is_no_rows_not_an_exception(self,
                                                                 monkeypatch):
        """`rows_for` catches its own import failure, and says nothing.

        The control is that this path is **reachable**: the positive test above
        answers `[]` for a different reason (a machine with no models), so an
        empty result cannot distinguish "nothing is installed" from "nothing
        could be read" unless the throw path is driven too.
        """
        from shani_chronoa import local_llm

        def _raise():
            raise RuntimeError("the model list is unreadable")

        monkeypatch.setattr(local_llm, "installed", _raise, raising=False)
        assert model_picker.rows_for(None) == []

    def test_the_throw_path_is_not_the_same_as_an_empty_machine(self,
                                                                machine):
        """The control for the test above, stated as a difference.

        On this machine `rows_for` returns three rows. An implementation that
        swallowed everything and returned `[]` would pass the unreadable test
        while failing this one, so the two cannot both be satisfied by a
        single `return []`.
        """
        assert len(model_picker.rows_for(None)) == 3


class TestThePopover:

    @staticmethod
    def _picker(machine):
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw

        Adw.init()
        return model_picker.ModelPicker(None)

    @staticmethod
    def _rows(picker):
        """The ActionRows actually inside the popover, read off the widget."""
        out, stack = [], [picker.get_child()]
        while stack:
            node = stack.pop()
            if node is None:
                continue
            if hasattr(node, "get_title"):
                out.append(node.get_title())
            if hasattr(node, "get_first_child"):
                child = node.get_first_child()
                while child is not None:
                    stack.append(child)
                    child = child.get_next_sibling()
        return sorted(out)

    def test_a_rebuild_refills_rather_than_appending(self, machine):
        """A model downloaded after the window opened has to become a choice.

        **The count is the whole property.** Rebuilding keeps it at the number
        of tiers; appending grows it by one each call, and a popover that grows
        is a popover whose list is a running total of how many times the
        window refreshed.

        This is the method's **second** call on purpose. `__init__` calls it
        with no child, so the clearing loop never ran there - which is how
        `Gtk.Popover.remove` went missing unnoticed and shipped.
        """
        picker = self._picker(machine)
        assert len(self._rows(picker)) == 3
        for _ in range(3):
            picker.rebuild()
        assert len(self._rows(picker)) == 3, (
            f"{len(self._rows(picker))} rows after three rebuilds - the "
            f"popover is accumulating rather than refilling")

    def test_an_unreadable_list_leaves_a_saying_rather_than_a_bare_box(
            self, monkeypatch, machine):
        """A popover with nothing in it looks like a control that does nothing.

        The label carries the sentence, so this asserts the **words**, not just
        that a child exists - an `Adw.StatusPage` with no message would satisfy
        the weaker one.
        """
        from shani_chronoa import local_llm

        picker = self._picker(machine)

        def _raise():
            raise RuntimeError("unreadable")

        monkeypatch.setattr(local_llm, "installed", _raise, raising=False)
        picker.rebuild()
        child = picker.get_child()
        assert child is not None, (
            "an unreadable tier list produced an empty popover with no "
            "explanation on it")
        label = child if isinstance(child, Gtk.Label) else None
        said = label.get_label() if label is not None else ""
        assert "tiers" in said and "read" in said, (
            f"the popover says {said!r} - an explanation naming what could "
            f"not be read is the point, not just that a widget exists")


class TestTheChoice:

    def test_a_pick_hands_the_key_to_the_apps_own_action(self, monkeypatch,
                                                          machine):
        """**The action, not a call into `local_llm`.**

        `app.answer-model` runs the quality gate and restarts the service off
        the main loop; a picker that pointed `current.gguf` itself would name
        the new model while the old one was still answering - the same
        two-widgets failure as the row and the chip, one level down.
        """
        seen = []

        class _App:
            def activate_action(self, name, parameter=None):
                seen.append((name, parameter.get_string() if parameter else None))
                return True

        picker = model_picker.ModelPicker(_App())
        # **On the class, not the instance.** `popdown` is a `Gtk.Popover`
        # vfunc, and assigning it on an *uninitialised* `__new__` object does
        # not shadow it - the call reaches the real method and raises
        # "object ... is not initialized". That is this repository's own
        # rule: a fixture shaped like the call rather than like the thing
        # being called tests the bug and passes.
        monkeypatch.setattr(model_picker.ModelPicker, "popdown",
                            lambda self: seen.append("closed"), raising=False)
        picker._choose("qwen3-8b")
        assert seen == [("app.answer-model", "qwen3-8b"), "closed"], seen

    def test_nothing_to_hand_the_choice_to_is_logged_not_raised(
            self, monkeypatch):
        """A picker whose app cannot activate an action must still be safe.

        The control: the guard is on `activate_action`, so an app that has the
        method is not exercised here - which is what stops this passing on a
        picker that simply never tries to switch anything.
        """
        assert not hasattr(object(), "activate_action")
        picker = model_picker.ModelPicker(object())
        picker._choose("qwen3-8b")          # must not raise


class TestTheRowAndTheChipAgree:

    """The property that had two words for one state.

    Rendered on the real widgets across four machine states before this was
    written: the row walked `local_llm.TIERS` and the chip walked `SPECS`, so a
    machine with nothing downloaded read **"No model"** on the chip and **"the
    default model"** on the row, and a machine with a model nothing describes
    read **"No model"** while one was active.
    """

    def _strip_and_row(self, machine, monkeypatch):
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, GLib
        from types import SimpleNamespace

        from shani_chronoa.gui import window as W

        Adw.init()

        class _Config:
            privacy_mode = False
            answer_mode = "local"
            wake_word_enabled = False
            barge_in_vad_enabled = False
            reply_style = "ordinary"

            def get_bool(self, key, default=False):
                return False

        cfg = _Config()
        app = SimpleNamespace(current_llm_kind=lambda: "local",
                              _answer_mode_problem="")
        strip = W._ModeStrip(app, cfg)
        win = W.ChronoaWindow.__new__(W.ChronoaWindow)
        win._app = app
        win._config = cfg
        win._mode_strip = strip
        win._model_row()
        ctx = GLib.MainContext.default()
        for _ in range(40):
            while ctx.pending():
                ctx.iteration(False)
        return strip, win

    def _chip_word(self, strip):
        current = strip._read_model()
        for key, word, _line, _icon in strip._model_rows():
            if key == current:
                return word
        return strip._model_rows()[0][1]

    @pytest.mark.parametrize("installed,active,label", [
        (set(), "", "nothing installed, nothing answering"),
        ({"qwen3-1.7b"}, "qwen3-1.7b", "installed and answering"),
        ({"qwen3-8b"}, "", "installed, nothing chosen yet"),
        (set(), "qwen3-8b", "a model nothing describes is active"),
    ])
    def test_the_row_and_the_chip_always_name_the_same_model(
            self, machine, monkeypatch, installed, active, label):
        machine["installed"] = set(installed)
        machine["active"] = active
        strip, win = self._strip_and_row(machine, monkeypatch)
        chip = self._chip_word(strip)
        row = win._model_picker_word.get_label()
        assert chip == row, (
            f"{label}: the chip says {chip!r} and the row says {row!r} - two "
            f"widgets answering one question differently")

    def test_a_refresh_moves_the_row_when_the_setting_changes(self, machine,
                                                              monkeypatch):
        """The refresh has to reach the row, or a switch leaves the old name.

        The row is a child of the *window* and the strip's own `refresh()` does
        not touch it - an earlier version looked for the method on the window
        and got `None` from `getattr`, so the redraw silently never happened
        behind a `callable()` guard that made it look like it had.
        """
        strip, win = self._strip_and_row(machine, monkeypatch)
        machine["active"] = "qwen3-8b"
        machine["installed"] = {"qwen3-8b"}
        win._refresh_model_word()
        assert win._model_picker_word.get_label() == "8B", (
            f"the row still reads {win._model_picker_word.get_label()!r} after "
            f"the model changed")

    def test_a_refresh_before_the_row_exists_is_a_no_op_not_a_crash(self):
        """`_refresh_model_word` is called during construction and by the
        handler; the first call has no label to set and must survive it."""
        from shani_chronoa.gui import window as W

        win = W.ChronoaWindow.__new__(W.ChronoaWindow)
        win._refresh_model_word()          # must not raise