"""The row that says what is answering, for a machine that answers in the cloud.

**The user this is written for asked for cloud and got it.** They configured a
provider, the provider is holding every turn, and the most important line on the
main window reads:

    Answers with  No model

`local_llm.active()` is empty on such a machine - nothing is downloaded - so
`_model_rows()` falls through to its placeholder row and `_model_word()` returns
that row's word. The row's whole job is *"what is answering me"*, and it answers
it from the **local registry only**, which is the one question that cannot tell
this user anything true.

It reads as an app that cannot answer, at exactly the moment somebody is deciding
whether to bother asking. The cloud is not a special case of "no model"; it is
the engine.

**Why the obvious fix is not obvious.** `local_llm.installed()` is the right
question for the *picker* - the popover chooses a local tier - and it is the
wrong question for the *label*, which is an observation rather than a menu. The
app already has the right reader, `ChronoaApplication.current_llm_kind()`, which
returns `local`/`cloud`/`none` **from the object holding the turn** rather than
from a setting, because the engine is selected and re-decided at startup and by
the fallback. So the label asks that, and falls back to the registry when there
is no engine to ask about.
"""

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))


def _window():
    """A stand-in carrying the two attributes `_model_word` reads.

    **The mode strip is real, not absent.** `_model_rows` lives on the strip, so
    a stand-in with `_mode_strip = None` reaches a branch the app never does -
    and the first version of this fixture did exactly that, which is this
    repository's own "two mixins that are one object in production are two
    objects in a test" trap.

    **`_model_rows(None)`, and the `None` is the honest argument.** The method
    declares `self` and never reads it, so passing `None` calls the code the app
    runs rather than a re-derivation of it. Calling it on a real `_ModeStrip`
    would drag in GTK construction this file does not need.

    **The application is a real stand-in for `current_llm_kind`**, which is the
    method the fix reads. `tests/test_a_second_message_waits_its_turn.py`
    records the other half of this: two mixins that are one object in
    production have to be driven against the conversation's own object.
    """
    from shani_chronoa.gui import window as window_mod

    strip = types.SimpleNamespace(
        _model_rows=lambda: window_mod._ModeStrip._model_rows(None))
    window = types.SimpleNamespace(_mode_strip=strip, _app=_App())
    # **The stand-in carries the methods it calls, from the real class.**
    # `_model_word` calls `self._answering_kind()`, and on a bare
    # `SimpleNamespace` that attribute does not exist - so the call raised, and
    # `_model_word`'s blanket `except` returned `"No model"`. That made the
    # local-model control fail too, which reads exactly like the defect still
    # being present. It is this file's own recorded trap twice over: the
    # `Gtk.Popover.__new__` shape, and "a stand-in that does not carry what the
    # code calls is describing a different machine".
    window._answering_kind = (
        lambda: window_mod.ChronoaWindow._answering_kind(window))
    return window


class _App:
    """`ChronoaApplication` as far as one method reads it - `current_llm_kind`."""

    kind = "none"

    def current_llm_kind(self):
        return self.kind


def _say(window_mod, window):
    return window_mod.ChronoaWindow._model_word(window)


@pytest.fixture()
def machine(monkeypatch):
    """Set both halves of the machine, so a test cannot set up half a story.

    Returns a callable: `machine(installed=..., active=..., kind=...)`.
    """
    from shani_chronoa import local_llm

    def build(installed=(), active="", kind="none"):
        monkeypatch.setattr(local_llm, "installed", lambda: list(installed))
        monkeypatch.setattr(local_llm, "active", lambda: active)
        window = _window()
        window._app.kind = kind
        return window

    return build


class TestTheRowThatSaysWhatIsAnswering:
    def test_a_cloud_engine_is_not_reported_as_no_model(self, machine):
        """**The defect.** A provider is answering every turn."""
        from shani_chronoa.gui import window as window_mod

        word = _say(window_mod, machine(kind="cloud"))

        assert word != "No model", (
            f"a provider is answering every turn and the row says {word!r} - "
            f"this is the machine the user described: configured a provider and "
            f"got a window that says it has no model")

    def test_the_word_says_the_answer_leaves_this_machine(self, machine):
        """**Cloud, not "a model".** A user choosing a provider is making a
        privacy decision, and a label that said only "a model" would hide the
        one fact they chose it for.
        """
        from shani_chronoa.gui import window as window_mod

        word = _say(window_mod, machine(kind="cloud")).lower()
        assert "cloud" in word or "provider" in word or "remote" in word, (
            f"the row must say where the answer is coming from, and says "
            f"{word!r}")

    def test_nothing_answering_is_still_no_model(self, machine):
        """The absence-shaped answer must survive the fix.

        This is the case the placeholder row was written for: no engine at all,
        where "No model" is the truth. **A fix that cannot be over-applied is not
        a fix yet** - and this panel has three in this file already.
        """
        from shani_chronoa.gui import window as window_mod

        assert _say(window_mod, machine(kind="none")) == "No model"

    def test_a_local_model_still_names_the_tier(self, machine):
        """The over-fix, from the other side.

        If the cloud branch were reached without asking the engine, a machine
        with a perfectly good local model would claim its answers come from a
        provider - which is the more alarming of the two wrong answers, because
        it is the privacy one.
        """
        from shani_chronoa.gui import window as window_mod

        word = _say(window_mod, machine(
            installed=["qwen3-1.7b"], active="qwen3-1.7b", kind="local"))

        assert "1.7B" in word.upper(), (
            f"a local tier is installed and answering, and the row says {word!r}")
        assert "cloud" not in word.lower() and "provider" not in word.lower(), (
            f"a local model was reported as remote: {word!r}")

    def test_no_engine_to_ask_falls_back_to_the_registry(self, monkeypatch):
        """An application that cannot say what is answering still names a model.

        `_note_engine_disagreement` already takes this shape - *"a strip that
        cannot check is a strip that says nothing, which is the status quo and
        not worse"* - and this row has to agree. A window built without a real
        application must keep working, because the renderer builds one and a
        dozen fixtures build another.
        """
        from shani_chronoa.gui import window as window_mod

        window = _window()
        window._app = object()          # no `current_llm_kind` at all

        word = _say(window_mod, window)
        assert word == "No model", (
            "with no engine to ask and nothing installed, the row falls back "
            f"to the registry's own answer rather than raising: {word!r}")

    def test_a_reader_that_is_absent_degrades_rather_than_raises(self):
        """**Asked of `_answering_kind` directly, and that is the whole point.**

        This started life as an assertion about the *row*, and it was a witness
        that could not fail: with the `if reader is None` guard removed, the
        missing reader raises `TypeError` inside `_answering_kind`, and
        `_model_word`'s blanket `except` catches it and returns `"No model"` -
        which is what the test asserted. The mutation run reported the
        survivor and was right.

        **A test that reaches its subject through a caller that swallows
        exceptions cannot fail**, because the caller answers the same way
        whether the subject works or not. The property belongs to the reader, so
        it is asked of the reader.
        """
        from shani_chronoa.gui import window as window_mod

        window = _window()
        window._app = object()          # no `current_llm_kind` at all

        assert window_mod.ChronoaWindow._answering_kind(window) == "", (
            "an application with no engine reader must answer '' - nobody could "
            "say - not raise")

    def test_a_reader_that_raises_degrades_rather_than_raises(self):
        """The same property for the other fault: a reader that raises.

        An application whose `current_llm_kind` blows up is the same "nobody
        could say" case wearing a different fault, and it is the one my own
        first fixture hit - the stand-in had no `current_llm_kind` at all and
        the row answered "No model" for a reason that had nothing to do with
        the engine.
        """
        from shani_chronoa.gui import window as window_mod

        class _Exploding:
            def current_llm_kind(self):
                raise RuntimeError("no engine is loaded")

        window = _window()
        window._app = _Exploding()

        assert window_mod.ChronoaWindow._answering_kind(window) == ""

    def test_the_three_engines_get_three_words(self, machine):
        """The control that makes the assertions above mean something.

        Without it, "cloud is not 'No model'" holds on code that answers one
        thing three times, and a blanket "always say a provider" would satisfy
        it while deleting both other answers.
        """
        from shani_chronoa.gui import window as window_mod

        seen = {
            _say(window_mod, machine(kind="cloud")),
            _say(window_mod, machine(kind="none")),
            _say(window_mod, machine(
                installed=["qwen3-1.7b"], active="qwen3-1.7b", kind="local")),
        }
        assert len(seen) == 3, (
            f"three different engines must get three different words, and got "
            f"{seen}")