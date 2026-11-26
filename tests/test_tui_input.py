"""Terminal input: continuation, EOF, completion dispatch and history I/O.

Pure tests over a fake reader and a fake readline module. No live terminal,
no D-Bus, no engine: a wrong continuation would change what the engine is
sent, and a wrong completion would send the wrong command or path.
"""

import builtins

import pytest

import shani_chronoa.tui.input as input_module
from shani_chronoa.tui.input import Input, completer, read_multiline


class FakeReadline:
    def __init__(self, buffer="", end=0, delims="/@ \t"):
        self.buffer = buffer
        self.end = end
        self.delims = delims
        self.bindings = []
        self.read_files = []
        self.written = []
        self.completer = None

    def get_line_buffer(self):
        return self.buffer

    def get_endpos(self):
        return self.end

    def set_completer(self, fn):
        self.completer = fn

    def parse_and_bind(self, binding):
        self.bindings.append(binding)

    def get_completer_delims(self):
        return self.delims

    def set_completer_delims(self, delims):
        self.delims = delims

    def read_history_file(self, path):
        self.read_files.append(path)

    def write_history_file(self, path):
        self.written.append(path)


def reader_from(lines):
    lines = iter(lines)

    def read(prompt=""):
        try:
            return next(lines)
        except StopIteration:
            raise EOFError from None

    return read


def test_read_multiline_uses_builtins_input_and_default_prompt(monkeypatch):
    seen = []

    def fake_input(prompt=""):
        seen.append(prompt)
        return "hello"

    monkeypatch.setattr(builtins, "input", fake_input)
    assert read_multiline() == "hello"
    assert seen == ["chronoa> "]


def test_read_multiline_returns_a_single_line_verbatim():
    assert read_multiline(reader=reader_from(["a normal line"])) == "a normal line"


def test_read_multiline_joins_continuations_with_newlines():
    reader = reader_from(["first\\", "second\\", "third"])
    assert read_multiline(reader=reader) == "first\nsecond\nthird"


def test_read_multiline_passes_prompt_to_every_physical_line():
    seen = []

    def read(prompt=""):
        seen.append(prompt)
        return "left\\" if len(seen) == 1 else "right"

    assert read_multiline("q> ", read) == "left\nright"
    assert seen == ["q> ", "q> "]


def test_read_multiline_accepts_an_empty_line():
    assert read_multiline(reader=reader_from([""])) == ""


def test_read_multiline_accepts_an_empty_continuation():
    assert read_multiline(reader=reader_from(["before\\", ""])) == "before\n"


def test_read_multiline_propagates_eof_on_the_first_line():
    with pytest.raises(EOFError):
        read_multiline(reader=reader_from([]))


def test_read_multiline_propagates_eof_mid_continuation():
    with pytest.raises(EOFError):
        read_multiline(reader=reader_from(["first\\"]))


def test_completer_offers_slash_commands(monkeypatch):
    fake = FakeReadline("/mo", 3)
    monkeypatch.setattr(input_module, "readline", fake)
    assert completer("/mo", 0) == "/models"
    assert completer("/mo", 1) is None


def test_completer_completes_the_token_the_cursor_is_in(monkeypatch):
    # Text after the cursor must not change which token is completed.
    fake = FakeReadline("/he trailing", 3)
    monkeypatch.setattr(input_module, "readline", fake)
    assert completer("ignored", 0) == "/help"


def test_completer_returns_none_for_plain_text(monkeypatch):
    fake = FakeReadline("just words", 10)
    monkeypatch.setattr(input_module, "readline", fake)
    assert completer("ignored", 0) is None


def test_completer_dispatches_at_paths(monkeypatch, tmp_path):
    (tmp_path / "notes.txt").write_text("x")
    token = "@" + str(tmp_path / "no")
    fake = FakeReadline("see " + token, len("see " + token))
    monkeypatch.setattr(input_module, "readline", fake)
    assert completer("ignored", 0) == str(tmp_path / "notes.txt")


def test_completer_uses_the_real_readline_hooks_when_available(monkeypatch):
    readline = pytest.importorskip("readline")
    monkeypatch.setattr(readline, "get_line_buffer", lambda: "/st")
    monkeypatch.setattr(readline, "get_endidx", lambda: 3)
    assert completer("/st", 0) == "/status"


def test_install_binds_tab_alt_enter_and_history_completion(monkeypatch, tmp_path):
    history = tmp_path / "history"
    history.write_text("/help\n")
    fake = FakeReadline()
    monkeypatch.setattr(input_module, "readline", fake)

    Input(str(history)).install()

    assert fake.completer is completer
    assert any("complete" in binding for binding in fake.bindings)
    assert any(r"\e\C-m" in b and r"\C-e\\" in b for b in fake.bindings)
    assert "/" not in fake.delims and "@" not in fake.delims
    assert fake.read_files == [str(history)]


def test_install_skips_a_history_file_that_does_not_exist(monkeypatch, tmp_path):
    fake = FakeReadline()
    monkeypatch.setattr(input_module, "readline", fake)

    Input(str(tmp_path / "absent")).install()

    assert fake.read_files == []


def test_close_writes_history_and_creates_the_parent(tmp_path, monkeypatch):
    history = tmp_path / "new" / "history"
    fake = FakeReadline()
    monkeypatch.setattr(input_module, "readline", fake)

    Input(str(history)).close()

    assert history.parent.is_dir()
    assert fake.written == [str(history)]


def test_read_calls_read_multiline_with_the_prompt(monkeypatch):
    seen = []
    monkeypatch.setattr(
        input_module, "read_multiline", lambda prompt: seen.append(prompt) or "line"
    )
    assert Input().read() == "line"
    assert seen == ["chronoa> "]
    assert Input().read("other> ") == "line"
    assert seen == ["chronoa> ", "other> "]


def test_no_readline_is_a_silent_no_op(monkeypatch, tmp_path):
    monkeypatch.setattr(input_module, "readline", None)
    history = tmp_path / "history"

    assert completer("/he", 0) is None
    control = Input(str(history))
    control.install()
    control.close()

    assert not history.exists()
