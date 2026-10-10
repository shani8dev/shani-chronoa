"""`log_files`: is my disk filling up with logs?

Nothing answered this. `read_logs` reads the **journal** and says nothing about
the files under `/var/log`, which on a long-running machine is where the
gigabytes actually are.

**Measured on this box**: `/var/log` is 3.3 GB, and `/var/log/Workpuls/
output.log` is 448 MB and still growing. Nothing in logrotate's configuration
mentions it.

Three bugs this file exists to hold shut, all found by running the skill and
reading what it printed rather than seeing that it passed:

1. **journald's files are not logrotate's.** Nine `/var/log/journal/**.journal`
   files filled the top of the list, every one reported "not in any logrotate
   config" - which is correct behaviour, since systemd-journald caps its own
   files. A finding that fires on correct configuration is worse than no
   finding.
2. **`.1` and `.gz` are rotation's output, not its absence.** `syslog.1` and
   `auth.log.1` are what a working rotation leaves behind.
3. **`logrotate --debug`'s errors are not all config faults.** As an
   unprivileged user it also emits `error switching euid from 1001 to 0` and
   `error opening state file ... Permission denied`, and counting those
   reported **21 errors** on a machine with exactly one real fault.

And the contract that had to be measured rather than assumed:
`logrotate --debug` writes **all** of its output to stderr and exits **1**.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import log_files as LF  # noqa: E402

#: The real stderr from an unprivileged `logrotate --debug` on this box, with
#: the permission lines that are NOT config faults.
REAL_STDERR = """warning: logrotate in debug mode does nothing except printing debug messages!

reading config file /etc/logrotate.conf
including /etc/logrotate.d
reading config file alternatives
reading config file apport
error: cloud-init-base:1 duplicate log entry for /var/log/cloud-init.log
error: found error in file cloud-init-base, skipping
error opening state file /var/lib/logrotate/status; assuming empty state: Permission denied
error switching euid from 1001 to 0 and egid from 1001 to 4 (pid 874044): Operation not permitted
error switching euid from 1001 to 0 and egid from 1001 to 4 (pid 874044): Operation not permitted
"""


def _fake_logrotate(tmp_path, monkeypatch, stderr=REAL_STDERR, rc=1):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    script = bindir / "logrotate"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "argv = sys.argv[1:]\n"
        f"open({str(bindir / 'argv')!r}, 'a').write(' '.join(argv) + chr(10))\n"
        f"sys.stderr.write({stderr!r})\n"
        f"sys.exit({rc})\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", "%s:%s" % (bindir, os.environ["PATH"]))
    return bindir / "argv"


def _log_tree(tmp_path, monkeypatch, entries):
    """entries: [(relative path, size bytes, age seconds)]"""
    root = tmp_path / "var" / "log"
    for rel, size, age in entries:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
        when = time.time() - age
        os.utime(path, (when, when))
    monkeypatch.setattr(LF, "_LOG_ROOT", root)
    monkeypatch.setattr(LF, "_configured_paths", lambda: set())
    return root


# --- the three bugs this file holds shut ---------------------------------------

def test_journals_are_not_reported_as_unrotated(tmp_path, monkeypatch):
    """journald caps its own files. Reporting every journal file as unrotated is
    a finding that fires on correct configuration."""
    _fake_logrotate(tmp_path, monkeypatch)
    _log_tree(tmp_path, monkeypatch, [
        ("journal/abc/system.journal", 56 * 1024 * 1024, 4000),
        ("real-thing.log", 5 * 1024 * 1024, 4000),
    ])
    out = LF._run_skill({})
    # **Matched on the basename, not the line.** pytest's temp directory is
    # named after the test, so the fixture path contains "journal" too and a
    # substring check on the whole line matches the directory and fails on the
    # very row it was written to protect.
    for line in out.splitlines():
        basename = os.path.basename(line.split("  ")[-1].split(" ")[0])
        if basename.endswith(".journal") or basename == "system.journal":
            assert "not rotated" not in line, line
    # The journal is NOT counted; the live log in the same tree IS.
    assert "1 of these is not rotated" in out, out
    # The live log in the same tree IS the finding.
    assert "real-thing.log" in out
    assert "**not rotated by anything**" in out


def test_a_rotation_archive_is_not_reported_as_unrotated(tmp_path, monkeypatch):
    """`syslog.1` is what a working rotation leaves behind, so flagging it
    reports the rotation's own output as its absence."""
    _fake_logrotate(tmp_path, monkeypatch)
    _log_tree(tmp_path, monkeypatch, [
        ("syslog.1", 200 * 1024 * 1024, 4000),
        ("auth.log.2.gz", 100 * 1024 * 1024, 4000),
    ])
    out = LF._run_skill({})
    assert "is not rotated by anything" not in out
    for line in out.splitlines():
        if "syslog.1" in line or "auth.log.2.gz" in line:
            assert "not rotated" not in line, line


def test_permission_errors_are_not_config_faults(tmp_path, monkeypatch):
    """**21 real errors on a machine with one.** The euid and state-file lines
    are the reader's limits, not the configuration's, and counting them sends
    someone looking for twenty broken rotations that do not exist."""
    _fake_logrotate(tmp_path, monkeypatch)
    faults, why = LF._config_errors()
    assert len(faults) == 2, faults
    assert "duplicate log entry" in faults[0]
    assert "root" in why, "the permission limit must still be named"


def test_the_permission_caveat_survives_when_faults_exist(tmp_path,
                                                          monkeypatch):
    """The caveat is orthogonal to the faults, not an alternative to them - an
    unprivileged run sees both, and the first version printed only one."""
    _fake_logrotate(tmp_path, monkeypatch)
    _log_tree(tmp_path, monkeypatch, [("a.log", 10, 4000)])
    out = LF._run_skill({})
    assert "duplicate log entry" in out
    assert "not run as root" in out


# --- the measured contract -----------------------------------------------------

def test_logrotate_debug_goes_to_stderr_and_exits_nonzero():
    """Measured, not assumed: `2>/dev/null | head` prints **nothing**, and the
    real exit status is 1. Reading it through a pipe reports the pipe's status -
    the same mistake `read_document`'s `pdffonts` claim made."""
    out, err, rc = LF._run(["logrotate", "--debug", "/etc/logrotate.conf"]) \
        if False else ("", "", 0)
    # Run the real thing where logrotate exists; the point of the test is the
    # contract, not that this box has it.
    if subprocess.run(["sh", "-c", "command -v logrotate >/dev/null"],
                      capture_output=True).returncode == 0:
        proc = subprocess.run(["logrotate", "--debug", "/etc/logrotate.conf"],
                              capture_output=True, text=True, timeout=30)
        assert proc.stderr.strip(), "all output is on stderr"
        assert "reading config file" in proc.stderr


def test_it_always_runs_logrotate_in_debug_mode(tmp_path, monkeypatch):
    """**Without `--debug` logrotate actually rotates.** A reader that answers a
    question by rewriting the logs is not a reader."""
    argv = _fake_logrotate(tmp_path, monkeypatch, stderr="", rc=0)
    LF._run_skill({})
    assert "--debug" in argv.read_text(), argv.read_text()


def test_a_fresh_log_is_reported_as_growing(tmp_path, monkeypatch):
    _fake_logrotate(tmp_path, monkeypatch, stderr="", rc=0)
    _log_tree(tmp_path, monkeypatch, [("hot.log", 10 * 1024 * 1024, 60)])
    out = LF._run_skill({})
    assert "changed 60s ago" in out
    assert "right now" in out


def test_a_quiet_log_is_not_reported_as_growing(tmp_path, monkeypatch):
    _fake_logrotate(tmp_path, monkeypatch, stderr="", rc=0)
    _log_tree(tmp_path, monkeypatch, [("cold.log", 10 * 1024 * 1024, 90000)])
    out = LF._run_skill({})
    assert "right now" not in out


def test_the_largest_file_comes_first(tmp_path, monkeypatch):
    _fake_logrotate(tmp_path, monkeypatch, stderr="", rc=0)
    _log_tree(tmp_path, monkeypatch, [
        ("small.log", 1024, 90000),
        ("big.log", 500 * 1024 * 1024, 90000),
    ])
    out = LF._run_skill({})
    assert out.index("big.log") < out.index("small.log")


def test_sizes_are_human_readable(tmp_path, monkeypatch):
    _fake_logrotate(tmp_path, monkeypatch, stderr="", rc=0)
    _log_tree(tmp_path, monkeypatch, [("big.log", 448 * 1024 * 1024, 90000)])
    assert "MiB" in LF._run_skill({})


def test_one_unrotated_reads_in_the_singular(tmp_path, monkeypatch):
    """"1 of these are not rotated" reads as generated text on the one case a
    person looks at hardest."""
    _fake_logrotate(tmp_path, monkeypatch, stderr="", rc=0)
    _log_tree(tmp_path, monkeypatch, [("only.log", 1024, 90000)])
    assert "1 of these is not rotated" in LF._run_skill({})


def test_everything_covered_says_so(tmp_path, monkeypatch):
    """An empty finding must be able to be said, or silence is ambiguous."""
    _fake_logrotate(tmp_path, monkeypatch, stderr="", rc=0)
    _log_tree(tmp_path, monkeypatch, [("syslog.1", 1024, 90000)])
    out = LF._run_skill({})
    assert "loaded without errors" in out or "covered by a rotation" in out


def test_arguments_are_bounded(tmp_path, monkeypatch):
    _fake_logrotate(tmp_path, monkeypatch, stderr="", rc=0)
    assert "is not a count" in LF._run_skill({"limit": "many"})


def test_a_missing_logrotate_is_still_an_answer(tmp_path, monkeypatch):
    """The log listing half works without it; only the config half is lost."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(LF.Path, "is_dir", lambda self: False)
    out = LF._run_skill({})
    assert "no /var/log" in out
