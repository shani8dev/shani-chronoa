"""The terminal renderer: scrollback, wrapping, status and approvals.

Pure text over a StringIO: no D-Bus, no GTK, no engine. A wrong status clear
would leave "thinking" in scrollback; a stray reset code would break pipes.
"""

import io

import pytest

from shani_chronoa.tui.render import Renderer


class TTY(io.StringIO):
    def isatty(self):
        return True


def test_defaults_follow_the_stream_and_no_color(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    tty = Renderer(TTY())
    assert tty.color is True
    assert tty.interactive is True
    monkeypatch.setenv("NO_COLOR", "1")
    assert Renderer(TTY()).color is False
    plain = Renderer(io.StringIO())
    assert plain.color is False
    assert plain.interactive is False


def test_color_and_interactive_are_read_only():
    renderer = Renderer(io.StringIO(), color=False, interactive=False)
    with pytest.raises(AttributeError):
        renderer.color = True
    with pytest.raises(AttributeError):
        renderer.interactive = True


def test_no_ansi_at_all_when_color_is_false():
    out = io.StringIO()
    renderer = Renderer(out, width=80, color=False, interactive=True)
    renderer.render_event({"event": "state", "state": "thinking"})
    renderer.render_event({"event": "tool", "text": "read_file · path=/tmp/a.txt"})
    renderer.render_event({"event": "tool_done", "ok": True, "text": ""})
    renderer.render_event({"event": "reply", "text": "# Hi\n- item\n"})
    renderer.render_event(
        {
            "event": "approval",
            "text": "Run it?",
            "detail": "d",
            "options": ["Yes", "No"],
        }
    )
    renderer.finish()
    assert "\x1b" not in out.getvalue()


def test_wrap_at_width_twenty():
    out = io.StringIO()
    renderer = Renderer(out, width=20, color=False, interactive=False)
    renderer.render_text("aaaa bbbb cccc dddd eeee\n")
    renderer.finish()
    assert out.getvalue() == "aaaa bbbb cccc dddd\neeee\n"
    assert all(len(line) <= 20 for line in out.getvalue().splitlines())


def test_reply_chunks_buffer_the_partial_line():
    out = io.StringIO()
    renderer = Renderer(out, width=20, color=False, interactive=False)
    renderer.render_text("hello w")
    assert out.getvalue() == ""
    renderer.render_text("orld\n")
    assert out.getvalue() == "hello world\n"
    renderer.render_text("tail")
    assert out.getvalue() == "hello world\n"
    renderer.finish()
    assert out.getvalue() == "hello world\ntail\n"


def test_chunk_boundary_inside_a_word_adds_no_space():
    out = io.StringIO()
    renderer = Renderer(out, width=20, color=False, interactive=False)
    renderer.render_text("hello w")
    renderer.render_text("orld\n")
    renderer.finish()
    assert out.getvalue() == "hello world\n"


def test_heading_gets_a_blank_line_before_it():
    out = io.StringIO()
    renderer = Renderer(out, width=40, color=False, interactive=False)
    renderer.render_text("intro\n# Title\nbody\n")
    renderer.finish()
    assert out.getvalue() == "intro\n\n# Title\nbody\n"


def test_list_indentation_survives_wrapping():
    out = io.StringIO()
    renderer = Renderer(out, width=20, color=False, interactive=False)
    renderer.render_text("- alpha beta gamma delta epsilon\n")
    renderer.finish()
    assert out.getvalue() == "- alpha beta gamma\n  delta epsilon\n"


def test_fence_marker_replaces_the_backticks():
    out = io.StringIO()
    renderer = Renderer(out, width=40, color=False, interactive=False)
    renderer.render_text("```python\ncode\n```\n")
    renderer.finish()
    assert out.getvalue() == "-- code --\ncode\n-- code --\n"
    assert "```" not in out.getvalue()


def test_tool_and_tool_done_lines_keep_engine_text_verbatim():
    out = io.StringIO()
    renderer = Renderer(out, width=80, color=False, interactive=False)
    renderer.render_tool({"tool": "read_file", "text": "read_file · path=/tmp/a.txt"})
    renderer.render_tool_done(
        {"tool": "read_file", "ok": True, "text": "ignored on success"}
    )
    renderer.render_tool_done({"tool": "write_file", "ok": False, "text": "no room"})
    renderer.finish()
    assert out.getvalue() == (
        "  > read_file · path=/tmp/a.txt\n  = ok\n  = failed: no room\n"
    )


def test_approval_prints_question_detail_and_numbered_options():
    out = io.StringIO()
    renderer = Renderer(out, width=80, color=False, interactive=False)
    renderer.render_approval(
        {
            "id": "a1",
            "text": "Run it?",
            "detail": "first line\nsecond line",
            "options": ["Allow this once", "Deny"],
            "kind": "confirm",
        }
    )
    renderer.finish()
    assert out.getvalue() == (
        "Run it?\n  first line\n  second line\n  1) Allow this once\n  2) Deny\n"
    )


def test_state_is_suppressed_when_not_interactive():
    out = io.StringIO()
    renderer = Renderer(out, width=80, color=False, interactive=False)
    renderer.render_state({"state": "thinking"})
    renderer.finish()
    assert out.getvalue() == ""


def test_state_stays_in_place_and_is_cleared_before_scrollback():
    out = TTY()
    renderer = Renderer(out, width=40, color=False, interactive=True)
    renderer.render_event({"event": "state", "state": "thinking"})
    assert out.getvalue() == "\r  thinking..."
    renderer.render_text("done\n")
    renderer.finish()
    value = out.getvalue()
    assert "\r" + " " * len("  thinking...") + "\r" in value
    assert "thinking\n" not in value
    assert value.endswith("done\n")


def test_finish_erases_a_lingering_status():
    out = TTY()
    renderer = Renderer(out, width=40, color=False, interactive=True)
    renderer.render_state({"state": "thinking"})
    renderer.render_event({"event": "done"})
    assert out.getvalue().endswith("\r" + " " * len("  thinking...") + "\r")


def test_color_uses_only_simple_bold_and_dim():
    out = TTY()
    renderer = Renderer(out, width=40)
    renderer.render_text("# Hi\n")
    renderer.render_tool({"text": "read_file"})
    renderer.finish()
    value = out.getvalue()
    assert "\x1b[1m# Hi\x1b[0m" in value
    assert "\x1b[2m  > read_file\x1b[0m" in value
    assert "\x1b[" not in value.replace("\x1b[1m", "").replace("\x1b[2m", "").replace(
        "\x1b[0m", ""
    )


def test_unknown_event_is_ignored():
    out = io.StringIO()
    renderer = Renderer(out, width=80, color=False, interactive=False)
    renderer.render_event({"event": "mystery", "text": "NOISE"})
    renderer.render_event({})
    renderer.finish()
    assert out.getvalue() == ""
