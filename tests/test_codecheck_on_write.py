"""Write-time syntax validation for code-like files (Maze-AI codecheck.py's rule).

Chronoa has a file-write skill that verifies a write happened (size readback).
This adds the other half: a successful write that leaves broken Python/JSON/TOML
on disk is not success, it is a mistake moved, so the tool refuses before the
write and names the parse error. Append mode is exempt on purpose - a fragment
appended to a larger document is not a standalone document.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import edit_file, write_text_file  # noqa: E402


@pytest.fixture(autouse=True)
def _grant_file_edit_consent(tmp_path, monkeypatch):
    ChronoaConfig().set("file-edit-enabled", "true")
    # edit_file confines its target to the home directory; make the home
    # directory the test's own tmp dir so targets there are in-scope.
    monkeypatch.setenv("HOME", str(tmp_path))
    yield
    ChronoaConfig().set("file-edit-enabled", "false")


class TestParseProblem:
    def test_valid_python_is_accepted(self):
        assert files.parse_problem("def f():\n    return 1\n", "x.py") == ""

    def test_broken_python_names_the_line_and_refuses(self):
        out = files.parse_problem("def f(:\n    return 1\n", "x.py")
        assert out.startswith("x.py: not valid Python (")
        assert "line 1" in out

    def test_json_shape_is_checked(self):
        assert files.parse_problem('{"a": 1}', "x.json") == ""
        assert "not valid JSON" in files.parse_problem('{"a":}', "x.json")

    def test_toml_shape_is_checked(self):
        assert files.parse_problem('a = 1\n', "x.toml") == ""
        assert "not valid TOML" in files.parse_problem('a = \n', "x.toml")

    def test_unknown_and_free_text_are_untouched(self):
        assert files.parse_problem('not (python', "x.txt") == ""
        assert files.parse_problem('', "x.csv") == ""


class TestWriteTextFileValidates:
    def test_broken_python_write_is_refused_and_nothing_is_written(self, tmp_path):
        target = tmp_path / "broken.py"
        out = write_text_file._run({"path": str(target), "content": "def f(:\n"})
        assert "not valid Python" in out
        assert not target.exists()

    def test_valid_json_write_succeeds(self, tmp_path):
        target = tmp_path / "ok.json"
        out = write_text_file._run({"path": str(target), "content": '{"a": 1}\n'})
        assert "Created" in out or "Wrote" in out
        assert target.read_text() == '{"a": 1}\n'

    def test_append_is_exempt_from_parse_check(self, tmp_path):
        target = tmp_path / "code.py"
        target.write_text("def f():\n")
        out = write_text_file._run({
            "path": str(target), "content": "    return 1\n", "append": True})
        assert "not valid Python" not in out
        assert target.read_text() == "def f():\n    return 1\n"


class TestEditFileValidatesTheResult:
    def test_an_edit_that_breaks_the_parser_is_refused(self, tmp_path):
        target = tmp_path / "config.json"
        target.write_text('{"a": 1}\n')
        out = edit_file._run({"path": str(target), "old_string": '{"a"', "new_string": '{"b'})
        assert "not valid JSON" in out
        assert target.read_text() == '{"a": 1}\n', "nothing must have been written"

    def test_an_edit_that_repairs_is_accepted(self, tmp_path):
        target = tmp_path / "config.json"
        target.write_text('{"a": 1,\n')
        out = edit_file._run({"path": str(target), "old_string": '{"a": 1,\n', "new_string": '{"a": 1}\n'})
        assert "not valid JSON" not in out
        assert json.loads(target.read_text()) == {"a": 1}

    def test_non_code_files_are_not_gated(self, tmp_path):
        target = tmp_path / "notes.txt"
        target.write_text("hello\n")
        out = edit_file._run({"path": str(target), "old_string": "hello", "new_string": "world("})
        assert "not valid" not in out
        assert target.read_text() == "world(\n"
