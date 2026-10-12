"""`answer-mode` actually moves the answering engine.

**This test file existed once and was destroyed** - not by a concurrent session
but by my own `git show HEAD:<file> > <file>` against a file I was editing. It is
rebuilt here from what the setting has to do, not from what the old one checked.

The half that was missing was `_apply_answer_mode`. Two callers and a warning
existed for it, and the warning was **false**: it told the reader the choice was
"not being applied - the model will answer as it did before that setting
existed", while `brain.py` was reading `privacy-mode` and
`cloud-fallback-enabled` on every turn. What the setting did not do was move the
engine off the one already loaded. That is `BrainMixin.select_llm` - written for
the Models panel, documented as "switch the answering engine **now**", with no
product caller at all.

The cases below are the strip's own four rows, which reduce to three stored
modes, plus the two refusals.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa.app import application as application_module  # noqa: E402


class _Config:
    def __init__(self, **values):
        self._values = values

    def get(self, key, default=""):
        return self._values.get(key, default)


def _app(answer_mode="", kind="local", refusal=""):
    """An application carrying only what `_apply_answer_mode` touches.

    `select_llm` is replaced with a recorder, so every assertion is about the
    *call* - which engine was asked for - and not about a chain this test then
    had to construct.
    """
    app = type("_App", (), {})()
    app.config = _Config(**({"answer-mode": answer_mode} if answer_mode else {}))
    app.asked = []
    app.refusal = refusal

    def select_llm(what):
        app.asked.append(what)
        return app.refusal

    def current_llm_kind():
        return app._kind

    app.select_llm = select_llm
    app._kind = kind
    app.current_llm_kind = current_llm_kind
    # **The real method, bound to the stub.** Without this, `getattr(self,
    # "_apply_answer_mode")` finds nothing and the guard reports the method
    # missing on an application that has it - which is how
    # `test_the_missing_method_warning_no_longer_fires` first failed, blaming
    # the guard for the stub's incompleteness.
    app._apply_answer_mode = (
        application_module.ChronoaApplication._apply_answer_mode.__get__(app))
    return app


def _apply(app):
    application_module.ChronoaApplication._apply_answer_mode(app)


# -- the mapping ------------------------------------------------------------


def test_cloud_mode_asks_for_the_cloud():
    app = _app(answer_mode="cloud", kind="local")
    _apply(app)
    assert app.asked == ["cloud"], app.asked


def test_local_mode_asks_for_local():
    app = _app(answer_mode="local", kind="cloud")
    _apply(app)
    assert app.asked == ["local"], app.asked


def test_local_then_cloud_starts_local():
    """"Answer here when you can, reach a provider when you cannot" is *start*
    local. Falling back is `_maybe_enable_cloud_fallback`'s job, so this maps to
    local rather than to cloud - and mapping it to cloud would silently answer
    every question remotely while the chip said "This machine, then cloud"."""
    app = _app(answer_mode="local-cloud", kind="cloud")
    _apply(app)
    assert app.asked == ["local"], app.asked


def test_an_unset_mode_leaves_the_engine_alone():
    """Guessing "local" would override a cloud chain the startup path
    legitimately built."""
    app = _app(answer_mode="", kind="cloud")
    _apply(app)
    assert app.asked == [], app.asked


def test_an_unknown_mode_is_treated_as_local_not_ignored():
    """A value nobody wrote must not leave a cloud engine answering while the
    setting says otherwise. Local is the safe direction."""
    app = _app(answer_mode="something-new", kind="cloud")
    _apply(app)
    assert app.asked == ["local"], app.asked


# -- not switching for nothing ----------------------------------------------


def test_an_engine_already_on_the_right_one_is_left_alone():
    """Rebuilding a cloud chain on every startup to arrive at the engine already
    running would spend a provider's rate limit to learn nothing."""
    app = _app(answer_mode="cloud", kind="cloud")
    _apply(app)
    assert app.asked == [], app.asked


def test_the_same_holds_for_local():
    app = _app(answer_mode="local", kind="local")
    _apply(app)
    assert app.asked == [], app.asked


# -- refusals ---------------------------------------------------------------


def test_a_refusal_is_reported_and_leaves_the_engine_in_place(caplog):
    """`select_llm` returns a sentence naming the gate that shut. The previous
    engine keeps answering - the safe direction - but it must not be reported
    as a success."""
    app = _app(answer_mode="cloud", kind="local",
               refusal="no cloud provider is reachable")
    with caplog.at_level(logging.WARNING, logger="shani_chronoa.app.application"):
        _apply(app)
    assert app.asked == ["cloud"], app.asked
    assert "no cloud provider is reachable" in caplog.text, caplog.text
    assert app.current_llm_kind() == "local", "a refusal moved the engine"


def test_a_success_logs_nothing(caplog):
    app = _app(answer_mode="cloud", kind="local", refusal="")
    with caplog.at_level(logging.WARNING, logger="shani_chronoa.app.application"):
        _apply(app)
    assert "answer-mode" not in caplog.text, caplog.text


def test_an_unreadable_engine_still_switches(caplog):
    """`current_llm_kind()` imports `CloudLLMChain`, which can fail. Reporting
    it and skipping the switch would leave the setting unapplied."""
    app = _app(answer_mode="cloud", kind="local")

    def broken():
        raise RuntimeError("no cloud module")

    app.current_llm_kind = broken
    _apply(app)
    assert app.asked == ["cloud"], app.asked


# -- the guard --------------------------------------------------------------


def test_the_missing_method_warning_no_longer_fires(caplog):
    """**The guard was the false claim.** It exists because `do_startup` once
    called a method that was defined nowhere, and the application could not
    start. While the method was missing the warning was also *wrong* - it said
    the setting governed nothing, when `brain.py` read the egress settings every
    turn. Now the method exists, so the guard must be silent; if this test ever
    fails, either the method was removed again or the guard is misreporting."""
    app = _app(answer_mode="cloud", kind="local")
    with caplog.at_level(logging.WARNING, logger="shani_chronoa.app.application"):
        application_module.ChronoaApplication._apply_answer_mode_if_present(app)
    assert "not implemented" not in caplog.text, caplog.text
    assert app.asked == ["cloud"], (
        "the guard must call the applier now it exists, not merely stay quiet")


def test_the_guard_still_warns_when_the_method_is_really_gone(caplog):
    """Insurance is only insurance if it still works. A test that only checks
    the present case cannot tell a working guard from a dead one."""
    app = _app(answer_mode="cloud", kind="local")
    app._apply_answer_mode = None            # what the guard is looking for
    with caplog.at_level(logging.WARNING, logger="shani_chronoa.app.application"):
        application_module.ChronoaApplication._apply_answer_mode_if_present(app)
    assert "not implemented" in caplog.text, caplog.text
    assert "cloud" in caplog.text, (
        "the warning must name the mode it is complaining about")


def test_the_guard_does_not_call_a_method_that_is_set_to_none():
    """`app._apply_answer_mode = None` makes `getattr` return None, which is
    the guard's "missing" case - and calling it would be `TypeError`."""
    app = _app(answer_mode="cloud", kind="local")
    app._apply_answer_mode = None
    application_module.ChronoaApplication._apply_answer_mode_if_present(app)  \
        # must not raise
    assert app.asked == [], app.asked


# -- the real method exists -------------------------------------------------


def test_the_method_is_on_the_class_not_just_on_a_stub():
    """Every test above binds the method to a stand-in. Without this, they
    would all pass against a method that had been deleted from the class."""
    assert hasattr(application_module.ChronoaApplication, "_apply_answer_mode")