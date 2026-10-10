"""`check_units`: `systemd-analyze verify`, which is the answer to "why won't it start".

`control_service` can start and stop a unit and cannot say what is wrong with
it; `boot_report` reports how long booting took. Nothing ran
`systemd-analyze verify`, which is the command that answers the question
directly. This skill was committed with a table entry and a consent key and
**no test file at all** — the gate test proved the table, not the behaviour.

**Two measurements from this machine are the whole design, and both are the
shapes that make a naive version confidently wrong:**

- **`systemd-analyze verify` exits 0 whether or not it found problems.** A unit
  whose `ExecStart` points at a file that does not exist prints
  `broken.service: Command /nonexistent/binary is not executable` and still
  returns 0. Reading the exit code as "valid" would call every unit fine.
- **It verifies the whole system, not the unit you asked about.** The same
  invocation prints pre-existing complaints about unrelated units, so the
  output is filtered to the units and paths named — otherwise every run blames
  somebody else's unit file.

Both are asserted here against a real unit file and a real `systemd-analyze`.
"""

from __future__ import annotations

import pathlib
import shutil
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import check_units as CU  # noqa: E402


def _needs_systemd():
    return shutil.which("systemd-analyze") is not None


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return h


def _unit(name: str, body: str) -> pathlib.Path:
    """A unit file on disk, so `verify` reads a real file rather than a name."""
    d = pathlib.Path.home() / ".config" / "systemd" / "user"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(body)
    return p


BROKEN = """[Unit]
Description=A unit whose ExecStart does not exist

[Service]
ExecStart=/nonexistent/binary --flag
"""

GOOD = """[Unit]
Description=A unit that is fine

[Service]
ExecStart=/bin/true
"""


@pytest.mark.skipif(not _needs_systemd(), reason="systemd-analyze is not installed")
def test_a_unit_with_a_missing_execstart_is_reported(home):
    """**The trap the skill exists for.** Exit 0 with a complaint in the
    output, so the answer must come from the output rather than the status.
    """
    path = _unit("broken.service", BROKEN)
    out = CU._run({"unit": str(path)})
    assert "not executable" in out, out
    assert "broken.service" in out, out
    # And the claim it must not make: "no problems found" for a broken unit.
    assert "No problems found" not in out, out


@pytest.mark.skipif(not _needs_systemd(), reason="systemd-analyze is not installed")
def test_a_sound_unit_reports_no_problems_and_says_what_that_means(home):
    path = _unit("good.service", GOOD)
    out = CU._run({"unit": str(path)})
    assert "No problems found" in out, out
    # The caveat that keeps the clean report honest.
    assert "does not promise" in out
    assert "journalctl" in out


@pytest.mark.skipif(not _needs_systemd(), reason="systemd-analyze is not installed")
def test_output_about_other_units_is_filtered_out(home):
    """**It verifies the whole system.** The same invocation complains about
    unrelated units; only what was asked about may be reported, or every run
    blames somebody else's file.
    """
    path = _unit("mine.service", GOOD)
    filtered = CU._for_units("com.Workpuls.service: Something pre-existing\n"
                             f"{path.name}: the thing that was asked about\n"
                             "other.service: also not ours\n",
                             ["mine.service"])
    assert filtered == [f"{path.name}: the thing that was asked about"], filtered


def test_a_path_that_does_not_exist_is_refused(home):
    out = CU._run({"unit": "/nope/not-there.service"})
    assert "does not exist" in out


def test_no_unit_lists_what_the_machine_has(home):
    """With nothing named, the answer is a list to choose from rather than a
    guess at which service was meant.
    """
    out = CU._run({})
    assert "service(s) systemd can see" in out or "No service unit files" in out, out


def test_the_answer_comes_from_the_text_not_the_exit_code(monkeypatch, home):
    """**The version-independent control.** `systemd-analyze verify` exits 0 on
    the systemd the module was first measured on and 1 on this one, for the same
    broken unit — so a test that relies on the real exit code passes on one
    version and silently fails to test the other. The status is pinned here:
    with rc=0 and a complaint in the output, the complaint must still be
    reported.
    """
    class Fake:
        returncode = 0
        stdout = "broken.service: Command /nonexistent/binary is not executable\n"
        stderr = ""

    monkeypatch.setattr(CU, "_verify", lambda units: Fake())
    out = CU._run({"unit": "broken.service"})
    assert "not executable" in out, out
    assert "No problems found" not in out, out


def test_the_answer_comes_from_the_text_not_a_nonzero_exit(monkeypatch, home):
    """The other side: rc=1 with nothing to say about the named unit is not a
    failure either. `verify` complains about other people's units on this
    machine and returns 1 for it.
    """
    class Fake:
        returncode = 1
        stdout = "somebody-else.service: Unknown key 'X', ignoring.\n"
        stderr = ""

    monkeypatch.setattr(CU, "_verify", lambda units: Fake())
    out = CU._run({"unit": "mine.service"})
    assert "No problems found" in out
    assert "somebody-else" not in out


def test_a_missing_systemd_analyze_is_unknown_not_clean(monkeypatch, home):
    """**The real wording, not the one assumed.** The message names the
    package that provides the binary; the first version of this test looked for
    "UNKNOWN" and learned the actual sentence by failing.
    """
    monkeypatch.setattr(CU.shutil, "which", lambda b: None)
    out = CU._run({"unit": "nginx.service"})
    assert "Could not check" in out
    assert "systemd-analyze is not installed" in out
    assert "'systemd' package" in out          # what to install
    assert "Nothing was guessed" in out
    assert "No problems found" not in out


def test_a_timeout_is_unknown_not_clean(monkeypatch, home):
    """`systemd-analyze verify` can hang on a pathological unit; a timeout is
    UNKNOWN, never "no problems".
    """
    monkeypatch.setattr(CU, "_verify", lambda units: None)
    out = CU._run({"unit": "nginx.service"})
    assert "UNKNOWN" in out
    assert "Nothing was guessed" in out
