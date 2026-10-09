"""`fingerprint_status`: whether fingerprint login is set up.

Four states that a boolean collapses into two, and the third is the one that
matters: a reader attached with **nothing enrolled** makes `fprintd-list`
succeed and print nothing, so anything reading its exit code or its output
length answers "no fingerprints enrolled" for a machine whose reader is not even
attached. That is the confident-wrong-answer shape this package records for a
sense that finds no camera and calls it absent.

The states below are the four **measured** on this machine, which is the fourth
(no reader attached). Three are reproduced against fixture output, because the
other three need hardware that is not on this machine and a test that asserts
them without ever exercising them is a comment.
"""

from __future__ import annotations

import getpass
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import tools  # noqa: E402
from shani_chronoa.skills import fingerprint_status as fp  # noqa: E402


@pytest.fixture
def fprintd(monkeypatch):
    """`fprintd-list` answering any of the three shapes, or being absent.

    `present=False` is the control for every refusal below: without it a
    "cannot check" answer could also come from a parser that reads nothing.
    """
    state = {"present": True, "code": 0, "out": "", "err": "",
             "fingers": ["left index finger: /var/lib/fprint/1/2/3"]}

    def _which(name):
        if name == "fprintd-list":
            return "/usr/bin/fprintd-list" if state["present"] else None
        if name == "systemctl":
            return "/usr/bin/systemctl"
        return None

    def _run(cmd):
        if cmd[0].endswith("fprintd-list"):
            return (state["code"], state["out"], state["err"])
        if cmd[0] == "systemctl":
            return (0, "inactive\n", "")
        return (-1, "", "no")

    monkeypatch.setattr(fp.shutil, "which", _which)
    monkeypatch.setattr(fp, "_run", _run)
    return state


def test_no_reader_is_a_hardware_answer_not_a_setting(fprintd):
    """Measured on this machine: `No devices available`, exit 1."""
    fprintd.update(code=1, out="No devices available\n", err="")
    text = fp._run_skill({})
    assert "no fingerprint reader attached" in text, text
    assert "hardware answer" in text, text
    # It must not read as "you have none enrolled".
    assert "no fingerprints enrolled" not in text


def test_a_reader_with_nothing_enrolled_says_so_plainly(fprintd):
    """Exit 0 and no output - the state a boolean gets wrong."""
    fprintd.update(code=0, out="", err="")
    text = fp._run_skill({})
    assert "no fingerprints" in text
    assert "reader is present" in text
    assert "will not work yet" in text


def test_enrolled_fingers_are_reported_as_the_command_printed_them(fprintd):
    """One line per finger, passed through verbatim.

    `fprintd-list(1)` has no EXAMPLES section and no exit-status table, so there
    is no documented format to parse. A parser for one would be a guess, and a
    guess that renders "left index finger" wrongly is a confident wrong answer
    about somebody's own hand.
    """
    fprintd.update(code=0, out="left index finger: a\nright thumb: b\n")
    text = fp._run_skill({})
    assert "2 fingerprint(s) enrolled" in text, text
    assert "left index finger: a" in text, text
    assert "right thumb: b" in text, text


def test_an_unreadable_database_is_not_an_empty_one(fprintd):
    """Exit non-zero with no "no devices" wording = could not read.

    This is the distinction the whole skill exists for, and collapsing it into
    "you have no fingerprints enrolled" is a statement about a machine the
    command never managed to ask.
    """
    fprintd.update(code=1, out="", err="GDBus.Error:net.reactivated.Fprint.Error.Failed\n")
    text = fp._run_skill({})
    assert "Could not read" in text, text
    assert "not the same as having none" in text, text
    assert "no fingerprints enrolled" not in text


def test_a_missing_package_is_named_with_the_package_that_provides_it(fprintd):
    """Not "the package that provides it", which helps nobody install anything."""
    fprintd["present"] = False
    text = fp._run_skill({})
    assert "not installed" in text, text
    assert "fprintd" in text, text
    assert "the package that provides it" not in text


def test_a_different_user_can_be_asked_about(fprintd, monkeypatch):
    """`fprintd-list` takes the account, and defaulting to you is a choice.

    **monkeypatch, not a hand-written try/finally.** The first version restored
    `_run` around the call by hand, so an assertion failing inside would leave
    the module globally patched for every later test in the file - a failure
    that reads as the next test's bug.
    """
    seen = {}

    def _run(cmd):
        if cmd[0].endswith("fprintd-list"):
            seen["user"] = cmd[-1]
            return (1, "No devices available\n", "")
        return (0, "active\n", "")

    monkeypatch.setattr(fp, "_run", _run)
    fp._run_skill({"user": "someone-else"})
    assert seen["user"] == "someone-else"


def test_the_account_defaults_to_you(fprintd, monkeypatch):
    """The default is the asking user, not a hard-coded name."""
    seen = {}

    def _run(cmd):
        if cmd[0].endswith("fprintd-list"):
            seen["user"] = cmd[-1]
            return (1, "No devices available\n", "")
        return (0, "active\n", "")

    monkeypatch.setattr(fp, "_run", _run)
    fp._run_skill({})
    assert seen["user"] == getpass.getuser()


# ── reachability, and what it must never do ─────────────────────────────────

def test_the_skill_is_registered():
    assert "fingerprint_status" in {t["function"]["name"] for t in tools.TOOLS}


def test_it_is_in_help_and_is_a_read():
    from shani_chronoa import capabilities
    group, label = capabilities._GROUPS["fingerprint_status"]
    assert group != "Other", group
    assert label
    assert "fingerprint_status" not in capabilities.GATED
    assert "fingerprint_status" in capabilities.READ_ONLY_TOOLS


def test_it_can_never_enrol_or_delete_a_fingerprint():
    """`fprintd-enroll` and `fprintd-delete` are in the same package.

    "Which fingers are enrolled" is a question; deleting the database is not one
    anybody asked, and a skill named `fingerprint_status` that can wipe a login
    method is a control the wrong shape. Checked on the source, because a test
    that calls the handler cannot see what it chose not to call.
    """
    source = pathlib.Path(fp.__file__).read_text()
    for forbidden in ("fprintd-enroll", "fprintd-delete"):
        # Present in the docstring as the reason it is *not* used, so the
        # assertion is on the command list, not on the text mentioning it.
        assert f'"{forbidden}"' not in source and f"[{forbidden!r}," not in source, forbidden
    assert forbidden in source, "the docstring should say why it is not reachable"


def test_the_router_offers_it_for_the_plainest_phrasings():
    from shani_chronoa import tool_select
    for request_ in ("is fingerprint login set up", "which fingerprints are enrolled",
                     "do i have a fingerprint reader", "is my fingerprint working"):
        names = [t["function"]["name"]
                 for t in tool_select.select_tools(request_, tools.TOOLS)]
        assert "fingerprint_status" in names, f"{request_!r} -> {names[:5]}"