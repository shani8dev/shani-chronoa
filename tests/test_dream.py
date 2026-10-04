"""Dream: an offline pass that reads the day's experience and says what it saw.

The property that matters here is not what the pass *finds* - it is that it
**cannot change anything**. It runs unattended, over a record of everything the
user did, and produces a file. An offline pass with the ability to alter the
system's permissions is a backdoor with a cron entry, so that is asserted
structurally rather than described in prose.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import dream  # noqa: E402


def _log(tmp_path: Path, rows, name="tool_calls.log") -> Path:
    path = tmp_path / name
    path.write_text("\n".join(
        row if isinstance(row, str) else json.dumps(row) for row in rows))
    return path


def _call(tool, verdict="verified", origin="user", ms=10, evidence=""):
    return {"tool_name": tool, "verdict": verdict, "origin": origin,
            "duration_ms": ms, "evidence": evidence}


# ------------------------------------------------------- it changes nothing

def test_the_module_can_change_nothing():
    """Checked against the syntax tree, because a prose promise about a
    permission boundary is not a test.

    No writing, no deleting, no config, no settings, no gate, no consent key.
    `write_dream` writes exactly one file, to a directory the caller names or
    the log's own directory, and `chmod`s it - which is why `Path.write_text`
    appears below and nothing else does.
    """
    import ast
    tree = ast.parse(Path(dream.__file__).read_text(encoding="utf-8"))

    banned_attrs = {"remove", "unlink", "rmtree", "rename", "replace",
                    "symlink_to", "mkdir", "makedirs", "touch"}
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            used.add(node.func.attr)
    offending = used & banned_attrs
    assert not offending, (
        f"dream.py calls {offending}; a consolidation pass must not be able to "
        f"delete, move or create anything"
    )

    # `open` is allowed, but only for reading. A write mode would be the whole
    # point of the test failing, so the mode argument is checked rather than
    # the name - a first version banned `open` outright and flagged the
    # legitimate `path.open("rb")` used to tail the log.
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "open"):
            modes = [a.value for a in node.args[1:2] if isinstance(a, ast.Constant)]
            mode = str(modes[0]) if modes else "r"
            assert not set(mode) & set("wax+"), (
                f"dream.py opens a file for writing ({mode!r})"
            )

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(f"{node.module or ''}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    for forbidden in ("config", "permissions", "capabilities", "tools",
                      "sandbox", "argfile", "settings"):
        assert not any(forbidden in name for name in imported), (
            f"dream.py imports {forbidden!r}; it must not reach any module that "
            f"can change what the assistant is allowed to do"
        )


def test_analysing_changes_nothing(tmp_path):
    """The pure function really is pure - same input, same output, nothing
    written."""
    path = _log(tmp_path, [_call("a", "failed") for _ in range(4)])
    before = sorted(p.name for p in tmp_path.iterdir())
    first = dream.analyse(dream._entries(path))
    second = dream.analyse(dream._entries(path))
    assert first == second
    assert sorted(p.name for p in tmp_path.iterdir()) == before


# ------------------------------------------------------- honesty about gaps

def test_an_empty_log_says_nothing_rather_than_all_clear(tmp_path):
    text = dream.dream(_log(tmp_path, []))
    assert "Nothing to read" in text


def test_a_missing_log_says_nothing_rather_than_all_clear(tmp_path):
    text = dream.dream(tmp_path / "does-not-exist.log")
    assert "Nothing to read" in text


def test_unparseable_lines_are_counted_not_hidden(tmp_path):
    path = _log(tmp_path, [_call("a"), "garbage", "{not json"])
    reading = dream.read_log(path)
    assert reading.records == 1
    assert reading.unreadable == 2
    assert "2 line(s) could not be parsed" in dream.dream(path)


def test_a_truncated_log_says_so(tmp_path):
    path = _log(tmp_path, [_call("a") for _ in range(200)])
    reading = dream.read_log(path, limit_bytes=200)
    assert reading.truncated is True
    assert "oldest records were skipped" in dream.render(
        reading, dream.analyse(dream._entries(path)))


def test_a_malformed_record_does_not_crash_the_pass(tmp_path):
    path = _log(tmp_path, [_call("a"), {"no_tool_name": True}, 42, None])
    assert dream.dream(path)


# ------------------------------------------------------- it finds real things

def test_a_tool_that_always_fails_is_found(tmp_path):
    path = _log(tmp_path, [_call("dissect_traffic", "failed",
                                 evidence="Error: could not open") for _ in range(5)])
    findings = dream.analyse(dream._entries(path))
    assert any("failed every time" in f.heading for f in findings)
    assert findings[0].severity == dream.SEV_HIGH


def test_a_tool_that_can_never_verify_is_found(tmp_path):
    """The one that matters most and is easiest to miss."""
    path = _log(tmp_path, [_call("interface_counters", "unverified") for _ in range(6)])
    headings = " ".join(f.heading for f in dream.analyse(dream._entries(path)))
    assert "never verifies" in headings


def test_unattended_activity_is_reported(tmp_path):
    rows = [_call("create_directory", "verified", origin="unattended")
            for _ in range(5)]
    rows += [_call("get_datetime") for _ in range(5)]
    path = _log(tmp_path, rows)
    headings = " ".join(f.heading for f in dream.analyse(dream._entries(path)))
    assert "nobody watching" in headings


def test_one_observation_makes_no_claim(tmp_path):
    """A rate needs more than one sample. A single failed call is an anecdote,
    and saying 'always fails' about it would be the confident-wrong-answer
    failure this repo keeps recording."""
    path = _log(tmp_path, [_call("x", "failed")])
    assert dream.analyse(dream._entries(path)) == []


def test_argument_values_never_appear_in_a_finding(tmp_path):
    """Structural privacy: the aggregation keys are name, verdict, origin,
    duration and error - so a finding cannot reassemble what the user did."""
    secret = "sk-live-SECRET-VALUE-1234567890"
    rows = [_call("connect_api", "failed", evidence=f"401 for {secret}")
            for _ in range(5)]
    path = _log(tmp_path, rows)
    text = dream.dream(path)
    assert secret not in text, (
        "the error text is quoted verbatim, so a credential that ended up in an "
        "error string is reproduced in the findings file"
    )


# ------------------------------------------------------- controls

def test_control_finding_nothing_about_a_healthy_log(tmp_path):
    """Proves the findings above are being read rather than passing because
    the assertions are weak."""
    path = _log(tmp_path, [_call(f"tool{i}", "verified") for i in range(20)])
    assert dream.analyse(dream._entries(path)) == [], (
        "twenty distinct verified tools produced findings, so the analyser is "
        "reporting something it should not"
    )


def test_control_the_sample_floor_is_load_bearing():
    assert dream._MIN_SAMPLE >= 2, (
        "a sample floor below 2 would let one observation support a rate"
    )