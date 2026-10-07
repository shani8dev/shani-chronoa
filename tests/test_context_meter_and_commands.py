"""What a harness UI has that ours did not, and is now.

Six gaps came out of comparing against opencode, cline, Roo-Code, OpenHands and
sayri. These tests pin the four that were new machinery:

- **context meter** (`context_meter.py`) - opencode's `session-context-usage`
  / `session-context-breakdown`: a headline plus a per-segment breakdown, and
  `None` rather than a made-up percentage when the window size is unknown;
- **compaction notice** - cline's `CompactionRow`, OpenHands' `CondensationEvent`:
  compression says what it cut instead of doing it silently;
- **cloud-turn notice** - a turn answered off the machine says so;
- **slash commands** (`gui/commands.py`) - kimi-cli's registry and cline's
  `SlashCommandMenu`, with the rule that a command must run something real.

The other two (task card, expandable tool output) are exercised here too,
because both are "wired or it is decoration" claims.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))


class TestTheContextMeter:
    def test_it_splits_the_prompt_the_way_a_person_would(self):
        from shani_chronoa import context_meter as meter
        messages = [
            {"role": "system", "content": "You are Chronoa."},
            {"role": "system", "content": "percepts: battery at 82%"},
            {"role": "user", "content": "how much disk space is left?"},
            {"role": "assistant", "content": "Root has 38G free."},
            {"role": "tool", "content": "x" * 4000, "tool_call_id": "c1"},
        ]
        report = meter.measure(messages, tools=[{"function": {"name": "web_search"}}],
                               limit=8192, reply_reserve=1024)
        keys = [segment.key for segment in report.segments]
        assert keys == ["system", "percepts", "user", "assistant", "tool", "tools"]
        # The tool *schemas* are a slice of their own: "the tool list is the
        # biggest thing in your context" and "a tool returned a lot" are
        # different conversations, and lumping them together hides the first.
        assert report.schema_tokens > 0
        assert report.total_tokens > 0
        assert "of 8,192 tokens" in report.headline()

    def test_it_refuses_to_invent_a_percentage(self):
        from shani_chronoa import context_meter as meter
        report = meter.measure([{"role": "user", "content": "hi"}])
        assert report.percent is None
        assert report.usable_percent is None
        assert "window size unknown" in report.headline()

    def test_the_divisor_is_the_one_the_shipper_uses(self):
        """If these drift, the meter and `fit_to_context` disagree about what
        "too big" means - and the meter would then be describing a window the
        assistant is not actually using. My own first guess for the constant
        was wrong, which is the drift in microcosm."""
        from shani_chronoa import local_llm
        from shani_chronoa.context_meter import _chars_per_token
        assert _chars_per_token() == local_llm.CHARS_PER_TOKEN

    def test_usable_percent_reserves_the_reply_room(self):
        from shani_chronoa import context_meter as meter
        report = meter.measure([{"role": "user", "content": "x" * 4000}],
                               limit=1000, reply_reserve=400)
        assert report.percent < report.usable_percent


class TestCompressionSaysWhatItCut:
    def test_an_elision_is_reported_with_a_size(self):
        from shani_chronoa import compression
        history = ([{"role": "system", "content": "sys"}]
                   + [{"role": "tool", "content": "x" * 50000,
                       "tool_call_id": str(i)} for i in range(4)]
                   + [{"role": "user", "content": f"u{i}"} for i in range(8)])
        compression.compress(history)
        said = compression.last_elision()
        assert said.messages == 4
        assert said.chars > 0
        assert "shortened" in said.sentence()
        assert "context window" in said.sentence()

    def test_a_history_that_fits_reports_nothing(self):
        from shani_chronoa import compression
        compression.compress([{"role": "user", "content": "hello"}])
        assert compression.last_elision().sentence() == ""
        assert compression.last_elision().anything is False

    def test_an_unchanged_answer_is_still_byte_identical(self):
        """The recording must not change what compression produces."""
        from shani_chronoa import compression
        history = ([{"role": "system", "content": "sys"}]
                   + [{"role": "tool", "content": "x" * 50000,
                       "tool_call_id": str(i)} for i in range(4)]
                   + [{"role": "user", "content": f"u{i}"} for i in range(8)])
        first = compression.compress([dict(m) for m in history])
        second = compression.compress([dict(m) for m in history])
        assert first == second


class TestTheCommandsAllRunSomething:
    def test_every_registered_command_is_wired(self):
        """A command that runs nothing is the dead-control class this repo keeps
        meeting: a name in a menu, an action behind it, silence instead."""
        from shani_chronoa.gui import commands
        assert commands.names(), "no slash commands are registered"
        for name, command in commands.commands().items():
            assert callable(command.run), f"/{name} has no action"
            assert command.summary.strip(), f"/{name} says nothing about itself"

    def test_only_a_leading_slash_is_a_command(self):
        from shani_chronoa.gui import commands
        found, argument = commands.lookup("/undo")
        assert found is not None and found.name == "undo"
        # A sentence that begins with a fraction, and a path, are prose.
        assert commands.lookup("what about /etc/passwd")[0] is None
        assert commands.lookup("/etc/passwd is unreadable")[0] is None
        assert commands.lookup("how are you")[0] is None
        assert commands.lookup("")[0] is None

    def test_an_unknown_command_is_prose_not_an_error(self):
        from shani_chronoa.gui import commands
        assert commands.lookup("/nonsense")[0] is None

    def test_undo_goes_through_the_skill_not_around_it(self, monkeypatch):
        """`/undo` must not be a shortcut past the permission layers."""
        from shani_chronoa.gui import commands
        seen = {}

        def fake_outcome(name, arguments):
            seen["called"] = (name, arguments)
            return type("R", (), {"text": "put it back"})()

        import shani_chronoa.tools as tools_mod
        monkeypatch.setattr(tools_mod, "execute_tool_outcome", fake_outcome)
        command, _ = commands.lookup("/undo")
        said = command.run(object(), "")
        assert seen["called"][0] == "undo_last_change"
        assert said == "put it back"


def _texts_of(widget):
    """Every label's text under `widget`, so one row can be asserted on."""
    out = []
    if hasattr(widget, "get_label") and widget.get_label():
        out.append(widget.get_label())
    child = widget.get_first_child()
    while child is not None:
        out.extend(_texts_of(child))
        child = child.get_next_sibling()
    return out


def _icon_names_of(widget):
    """Every `Gtk.Image`'s icon name under `widget`."""
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    out = []
    if isinstance(widget, Gtk.Image):
        out.append(widget.get_icon_name() or "")
    child = widget.get_first_child()
    while child is not None:
        out.extend(_icon_names_of(child))
        child = child.get_next_sibling()
    return out


class TestTheTaskCard:
    def test_it_shows_three_states_and_names_what_blocks(self, tmp_path,
                                                        monkeypatch):
        """Three states, and the blocked one says *what* is blocking it.

        The detail used to live only in a tooltip, which needs a hover (or a
        keyboard focus), never appears on touch, and is in no screenshot - and a
        card that says "blocked check the backup" while the reason was one hover
        away answers the question nobody asked. Rendering the card is what
        found it: the row looked complete and said nothing about the disk.
        """
        import gi
        gi.require_version("Gtk", "4.0")
        import json
        from shani_chronoa import config as config_mod
        from shani_chronoa.gui import task_card

        store = tmp_path / "state" / "shani-chronoa" / "todos.json"
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(json.dumps([
            {"id": 1, "content": "renew the domain", "status": "in_progress"},
            {"id": 2, "content": "water the plants", "status": "pending"},
            {"id": 3, "content": "check the backup", "status": "blocked",
             "blocked_by": ["disk full"]},
            {"id": 4, "content": "already done", "status": "completed"},
        ]), encoding="utf-8")
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        real = config_mod.ChronoaConfig.get_bool
        monkeypatch.setattr(config_mod.ChronoaConfig, "get_bool",
                            lambda self, key, default=False:
                            True if key == "todo-list-enabled" else real(self, key, default))

        card = task_card.TaskCard()
        assert card.get_visible() is True
        texts = []

        def walk(widget):
            if hasattr(widget, "get_label") and widget.get_label():
                texts.append(widget.get_label())
            child = widget.get_first_child()
            while child is not None:
                walk(child)
                child = child.get_next_sibling()

        walk(card)
        joined = "\n".join(texts)
        assert "renew the domain" in joined
        assert "water the plants" in joined
        assert "check the backup" in joined
        assert "already done" not in joined, "a finished task is not outstanding"
        assert "doing" in joined
        assert "disk full" in joined, (
            "the blocked task does not say what blocks it; the reason is "
            "reachable only by hovering, which is not the same as shown")
        # And the marker that distinguishes "blocked" without colour is still
        # on the row: an earlier attempt returned a fresh box holding only the
        # two labels and dropped the icon, which the next render showed at once.
        blocked_row = next(row for row in card._rows() if "check the backup" in
                           " ".join(_texts_of(row)))
        assert "dialog-warning-symbolic" in _icon_names_of(blocked_row), (
            "the blocked row lost its warning icon, so the only thing marking "
            "it as blocked is the word 'blocked'")

    def test_it_is_absent_when_there_is_nothing_outstanding(self, tmp_path,
                                                            monkeypatch):
        import gi
        gi.require_version("Gtk", "4.0")
        from shani_chronoa import config as config_mod
        from shani_chronoa.gui import task_card
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        real = config_mod.ChronoaConfig.get_bool
        monkeypatch.setattr(config_mod.ChronoaConfig, "get_bool",
                            lambda self, key, default=False:
                            True if key == "todo-list-enabled" else real(self, key, default))
        card = task_card.TaskCard()
        assert card.get_visible() is False, (
            "an empty card is a permanent tax on attention for no information")

    def test_it_is_absent_when_the_list_is_switched_off(self, tmp_path,
                                                       monkeypatch):
        import gi
        gi.require_version("Gtk", "4.0")
        import json
        from shani_chronoa.gui import task_card
        store = tmp_path / "state" / "shani-chronoa" / "todos.json"
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(json.dumps([{"id": 1, "content": "secret plan",
                                      "status": "pending"}]), encoding="utf-8")
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        from shani_chronoa import config as config_mod
        real = config_mod.ChronoaConfig.get_bool
        monkeypatch.setattr(config_mod.ChronoaConfig, "get_bool",
                            lambda self, key, default=False:
                            False if key == "todo-list-enabled" else real(self, key, default))
        card = task_card.TaskCard()
        assert card.get_visible() is False

    def test_an_unreadable_store_is_not_shown_as_no_tasks(self, tmp_path,
                                                           monkeypatch):
        import gi
        gi.require_version("Gtk", "4.0")
        from shani_chronoa import config as config_mod
        from shani_chronoa.gui import task_card
        store = tmp_path / "state" / "shani-chronoa" / "todos.json"
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text("{not json", encoding="utf-8")
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        real = config_mod.ChronoaConfig.get_bool
        monkeypatch.setattr(config_mod.ChronoaConfig, "get_bool",
                            lambda self, key, default=False:
                            True if key == "todo-list-enabled" else real(self, key, default))
        card = task_card.TaskCard()
        assert card.get_visible() is False
        assert card.refresh() is False


def test_tool_output_is_already_collapsible():
    """Gap #5 from the comparison turned out to be already there - the tool card
    carries a toggle and a revealer. Asserted so nobody "fixes" it twice, and so
    a future change that removes it fails loudly rather than quietly making
    every transcript taller."""
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    from shani_chronoa.gui.blocks import ToolCallCard
    card = ToolCallCard("read_file", {"path": "/tmp/x"}, "contents", True)
    toggles, revealers = [], []

    def walk(widget):
        if isinstance(widget, Gtk.ToggleButton):
            toggles.append(widget)
        if isinstance(widget, Gtk.Revealer):
            revealers.append(widget)
        child = widget.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(card)
    assert toggles and revealers, "a tool card with no expander is a wall of text"
