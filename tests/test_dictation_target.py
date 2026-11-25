"""Dictation into the focused app: one guarded type path, one stored target.

The target is captured before the microphone takes focus, and `send()` uses
`ui_driver.type_text` - the function whose secure-field and comms guards decide
whether a keystroke may leave the process. A test that called a lower-level
input function would pass with the guards bypassed, which is the defect this
module exists to prevent.
"""

from __future__ import annotations

import pytest

from shani_chronoa.gui import dictation_target as target


class _Store:
    pass


def test_target_is_a_value_not_a_guessed_name():
    t = target.Target("writer", "notes — draft")
    assert t.as_tuple() == ("writer", "notes — draft")
    assert t.app == "writer"


def test_send_without_text_types_nothing(monkeypatch):
    called = []
    monkeypatch.setattr(
        "shani_chronoa.ui_driver.type_text", lambda *a: called.append(a) or "typed"
    )
    store = _Store()
    setattr(store, "_dictation_target", target.Target("writer", "notes"))
    assert "no words" in target.send("   ", store).lower()
    assert called == []


def test_send_without_a_remembered_target_types_nothing(monkeypatch):
    called = []
    monkeypatch.setattr(
        "shani_chronoa.ui_driver.type_text", lambda *a: called.append(a) or "typed"
    )
    said = target.send("some dictated words", _Store())
    assert "no focused app" in said.lower()
    assert called == []


def test_send_uses_the_guarded_driver_with_the_remembered_target(monkeypatch):
    called = []

    def fake(app, title, text):
        called.append((app, title, text))
        return f"Typed into {app}"

    monkeypatch.setattr("shani_chronoa.ui_driver.type_text", fake)
    store = _Store()
    setattr(store, "_dictation_target", target.Target("writer", "notes"))
    said = target.send("  the dictated line  ", store)
    assert called == [("writer", "notes", "the dictated line")]
    assert "writer" in said


def test_a_guard_refusal_is_a_sentence_not_an_exception(monkeypatch):
    def refuse(app, title, text):
        raise RuntimeError("refusing to type into a password field")

    monkeypatch.setattr("shani_chronoa.ui_driver.type_text", refuse)
    store = _Store()
    setattr(store, "_dictation_target", target.Target("login", "Sign in"))
    said = target.send("secret", store)
    assert "password field" in said
    assert "not typed" in said


def test_clear_drops_the_target():
    store = _Store()
    setattr(store, "_dictation_target", target.Target("writer", "notes"))
    target.clear(store)
    assert target.remembered_target(store) is None
