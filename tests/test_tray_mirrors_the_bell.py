"""The tray badge mirrors the header bell's count, not a second reading.

A second reader of the proposal store could disagree with the badge on screen;
the window passes the count it already computed. A tray with no badge API, or
none at all, is a no-op - an optional icon must not break the window's tick.
"""

from __future__ import annotations

from shani_chronoa.app import tray


class _Icon:
    def __init__(self):
        self.label = ""
        self.title = ""

    def set_label(self, text):
        self.label = text

    def set_title(self, text):
        self.title = text

    def set_menu(self, _menu):
        pass

    def set_visible(self, _value):
        pass

    def set_icon(self, _icon):
        pass

    def set_status(self, _status):
        pass

    def connect(self, _signal, _handler):
        pass


class _IndicatorClass:
    @staticmethod
    def new(_id):
        return _Icon()


def test_badge_shows_the_count_the_bell_computed():
    t = tray.Tray(None, _IndicatorClass)
    t.set_badge(3, "3 proposals waiting")
    assert t._icon.label == "3"
    assert "3 proposals waiting" in t._icon.title


def test_zero_is_no_badge_not_a_zero_on_screen():
    t = tray.Tray(None, _IndicatorClass)
    t.set_badge(0)
    assert t._icon.label == ""


def test_a_broken_icon_does_not_propagate():
    class Broken:
        def set_label(self, _text):
            raise RuntimeError("no such method in this binding")

    t = tray.Tray(None, _IndicatorClass)
    t._icon = Broken()
    t.set_badge(2, "two waiting")  # must not raise
