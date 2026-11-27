"""Unit tests for the command palette's filter, selection and painting."""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass

import pytest

from shani_chronoa.tui.palette import Palette


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int


class FakeCanvas:
    """Accepts only `text`/`box`, so any other canvas call fails the test."""

    def __init__(self) -> None:
        self.texts: list[tuple[int, int, str]] = []
        self.boxes: list[tuple[Rect, str]] = []

    def text(self, row, col, value):
        self.texts.append((row, col, value))

    def box(self, rect, title):
        self.boxes.append((rect, title))


ENTRIES = [
    ("/models", "list model tiers, or filter them"),
    ("/skills", "list skills and their capabilities"),
    ("/status", "engine and bridge status"),
]


def test_filter_matches_name_and_help_and_ignores_leading_slash():
    palette = Palette(ENTRIES)
    assert palette.filter("mod") == [ENTRIES[0]]
    assert palette.filter("/stat") == [ENTRIES[2]]
    assert palette.filter("bridge") == [ENTRIES[2]]  # help text matches too
    assert palette.filter("") == ENTRIES
    assert palette.filter("no such command") == []
    assert palette.selected() == ""


def test_move_wraps_around_and_selected_returns_exact_entry_name():
    palette = Palette(ENTRIES)
    assert palette.selected() == "/models"
    assert palette.move(-1) == "/status"
    assert palette.move(1) == "/models"
    assert palette.move(1) == "/skills"
    assert palette.move(1) == "/status"
    assert palette.move(1) == "/models"  # wraps forward
    palette.filter("mod")
    assert palette.selected() == "/models"
    assert palette.move(1) == "/models"  # one match cannot move away


def test_render_uses_only_canvas_text_and_box_and_clears_stale_rows():
    canvas = FakeCanvas()
    rect = Rect(0, 0, 20, 6)
    palette = Palette(ENTRIES)
    palette.render(canvas, rect, "mod", 0)
    assert canvas.boxes == [(rect, "COMMANDS")]
    painted = "\n".join(value for _, _, value in canvas.texts)
    assert "> mod" in painted
    assert "/models" in painted
    assert "/skills" not in painted
    canvas.texts.clear()
    palette.render(canvas, rect, "no such command", 0)
    assert "/models" not in "\n".join(value for _, _, value in canvas.texts)


def test_render_recomputes_matches_when_called_with_a_new_query():
    palette = Palette(ENTRIES)
    canvas = FakeCanvas()
    palette.render(canvas, Rect(0, 0, 30, 6), "stat", 0)
    assert palette.selected() == "/status"


def test_render_without_a_canvas_text_or_box_does_not_raise():
    Palette(ENTRIES).render(object(), Rect(0, 0, 20, 6), "", 0)


def test_default_entries_come_from_commands_lazily_and_never_raise(monkeypatch):
    fake = types.ModuleType("shani_chronoa.tui.commands")
    fake.palette_commands = lambda: [("/hello", "say hello")]  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "shani_chronoa.tui.commands", fake)
    # A prior test may already have bound the real module as a package
    # attribute; `from ... import commands` reads that before sys.modules.
    import shani_chronoa.tui as tui

    monkeypatch.delattr(tui, "commands", raising=False)
    assert Palette().entries == [("/hello", "say hello")]

    def broken():
        raise RuntimeError("no command table yet")

    fake.palette_commands = broken  # type: ignore[attr-defined]
    assert Palette().entries == []
