"""Setup downloads a language's OCR data into a directory tesseract is never told to read.

`languages.install()` does two things, both correct:

    _link_system_data()          symlinks the system's eng/osd into Chronoa's dir
    install_verified(tess_spec…) puts the new language's traineddata beside them

Its own comment says why: **"tesseract reads one directory, so the user's holds
links to the system's English and ..."**. The whole design assumes tesseract is
pointed at `tessdata_dir()`.

It never was. **Neither tesseract subprocess in `skills/read_document.py` passed
`env=`**, and `languages.tesseract_env()` — the one function that returns
`{"TESSDATA_PREFIX": ...}` — had **no caller in the tree**. So:

- `find_tessdata_dir()` resolves the user's directory correctly, and
  `_installed_languages()` correctly lists `hin`
- `_tesseract_argv()` therefore passes `-l hin`
- the process starts with no prefix, looks in its compiled-in default
  (`/usr/share/tessdata`), and cannot find the language that was just installed

**The listing and the execution disagree**, which is the shape this session has
found five times: one side records the behaviour, nothing connects it.

Measured on this box, which has `tesseract` and no tessdata anywhere:

| invocation | result |
|---|---|
| no prefix | **empty output, no error at all** |
| prefix pointing at an empty dir | `Error opening data file .../eng.traineddata` |

The silent row is the one a user hits.

## The assertions are on the environment that reaches `subprocess.run`

Not on the helper - a helper that computes the right env and is never passed to
the process proves nothing. `subprocess.run` is captured.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import files  # noqa: E402
from shani_chronoa.skills import read_document as R  # noqa: E402


@pytest.fixture
def user_tessdata(tmp_path, monkeypatch):
    """Chronoa's tessdata directory, holding one downloaded language."""
    directory = tmp_path / "shani-chronoa" / "tessdata"
    directory.mkdir(parents=True)
    (directory / "hin.traineddata").write_bytes(b"x")
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    monkeypatch.setattr(files, "data_home", lambda: tmp_path)
    return directory


def test_the_prefix_points_at_the_directory_setup_filled(user_tessdata):
    """**The defect.** Setup put `hin.traineddata` here, and tesseract was never
    told this is where to look."""
    env = R._tesseract_env()

    assert env.get("TESSDATA_PREFIX") == str(user_tessdata), (
        f"the prefix is {env.get('TESSDATA_PREFIX')!r}, but setup installed into "
        f"{user_tessdata}")


def test_the_language_setup_installed_is_now_offered(user_tessdata):
    """The two halves agree. `find_tessdata_dir()` already resolved this
    directory, so this was true before - what was missing was the subprocess."""
    assert "hin" in R._installed_languages()


def test_path_is_preserved(user_tessdata):
    """**The easy way to break this.** `subprocess.run` inherits the parent
    environment only when `env` is omitted; passing `env={"TESSDATA_PREFIX": ...}`
    would take `PATH` away and the tesseract binary itself would stop being
    found - a fix that breaks OCR worse than the silence it replaced."""
    env = R._tesseract_env()

    assert "PATH" in env, "the environment handed to tesseract has no PATH"
    assert env.get("PATH") == os.environ.get("PATH")


def test_no_stale_prefix_when_there_is_no_data_anywhere(tmp_path, monkeypatch):
    """Nothing installed: leave no prefix pointing at a directory with no data in
    it, which is what produced `Error opening data file .../eng.traineddata` for
    a language that was never there."""
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    monkeypatch.setattr(files, "data_home", lambda: tmp_path / "empty")

    env = R._tesseract_env()
    assert "TESSDATA_PREFIX" not in env, env.get("TESSDATA_PREFIX")


def test_the_environment_reaches_the_subprocess(user_tessdata, monkeypatch):
    """**Not the helper - the call.** A function that computes the right
    environment and is never passed to the process proves nothing, which is how
    this defect survived: the resolver was already correct and only the `env=`
    was missing."""
    captured = {}

    class _Done:
        returncode = 0
        stdout = "hello"
        stderr = ""

    def fake_run(argv, **kwargs):
        captured.update(kwargs)
        return _Done()

    monkeypatch.setattr(R.subprocess, "run", fake_run)
    R._ocr_image("/tmp/whatever.png")

    assert "env" in captured, (
        "the tesseract subprocess is still called with no env=, so the prefix "
        "is computed and thrown away")
    assert captured["env"].get("TESSDATA_PREFIX") == str(user_tessdata)


def _real_callers(name, skip):
    """Files that **call** `name`, by AST. Not by substring.

    **The first version of this assertion was a substring match, and it passed
    against code that did not call the function at all.** The docstring added by
    the fix *names* `languages.tesseract_env()` while the implementation
    re-implemented its glob inline - so `"tesseract_env(" in text` found my own
    prose and reported a caller that did not exist.

    An `ast.FunctionDef` stores its name as a string, never as an `ast.Name`, so
    the definition cannot match either. What is left is a real reference.
    """
    import ast

    root = Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"
    found = []
    for path in sorted(root.rglob("*.py")):
        if path.name in skip:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                got = (func.attr if isinstance(func, ast.Attribute)
                       else func.id if isinstance(func, ast.Name) else None)
                if got == name:
                    found.append(path)
                    break
    return found


def test_tesseract_env_now_has_a_caller():
    """The smallest claim that was false, and the reason this file exists:
    `languages.tesseract_env()` had no caller in the tree."""
    callers = _real_callers("tesseract_env", {"languages.py"})

    assert callers, (
        "tesseract_env() is called by nothing. A substring search will NOT find "
        "this: the docstring above names the function while the code re-implements "
        "its glob, so the search finds prose and reports a caller that is not one.")
