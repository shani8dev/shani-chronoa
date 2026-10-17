"""The launch verbs a shortcut or the Circle to Search overlay sends.

`--dictate` and `--stop` are what a desktop shortcut binds (Handy's
--toggle-transcription and --cancel); `--rules=build` with `--ask=` is the
overlay's "Fix this error", which must be asked in Build mode - so the rules
change before the question goes, not after.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

gi = pytest.importorskip("gi")
from gi.repository import GLib  # noqa: E402

from shani_chronoa.app.application import ChronoaApplication as App  # noqa: E402


class _Line:
    def __init__(self, *args):
        self.args = ["shani-chronoa", *args]

    def get_arguments(self):
        return self.args

    def get_cwd(self):
        return "/"


class _Rules:
    def __init__(self, log):
        self.log = log

    def change_state(self, value):
        self.log.append(("rules", value.unpack()))


class _Window:
    def __init__(self, log):
        self.log = log

    def submit_text(self, text):
        self.log.append(("ask", text))

    def add_attachments(self, paths):
        self.log.append(("attach", paths))


class _Stub:
    do_command_line = App.do_command_line
    _model_override = None
    llm = None

    def __init__(self):
        self.log = []
        self.window = _Window(self.log)
        self.config = type("C", (), {"debug_mode": False})()

    def _parse_args(self, _args):
        pass

    def activate(self):
        pass

    def lookup_action(self, name):
        return _Rules(self.log) if name == "answer-rules" else None

    def activate_action(self, name, _value):
        self.log.append(("action", name))


def _run(*args):
    app = _Stub()
    assert app.do_command_line(_Line(*args)) == 0
    ctx = GLib.MainContext.default()
    while ctx.pending():
        ctx.iteration(False)
    return app.log


def test_fix_this_error_asks_in_build_mode():
    log = _run("--rules=build", "--ask=Fix it")
    assert log == [("rules", "build"), ("ask", "Fix it")], log


def test_dictate_and_stop_reach_their_actions():
    assert _run("--dictate") == [("action", "dictate")]
    assert _run("--stop") == [("action", "stop-speaking")]
