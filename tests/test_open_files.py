"""`open_files`: which programs have a file, directory or socket open.

Nothing else answers this. `port_owner` reads `/proc/net/tcp`, which gives a
socket and an inode but no command line and no answer for a *file*;
`list_processes` shows names and CPU. So a program holding a file open, or
sitting on a deleted file whose space cannot be reclaimed, was findable by
nothing.

**All four shapes below are measured**, on the dev box and on a real `@blue`
slot: the two header widths, a listening socket, an established connection,
a *named* descriptor, and a deleted file. The empty case is measured too -
`rc=1` with empty stdout and `WARNING: can't stat() ...` lines on **stderr**.

**The one that decides whether this skill is right or confidently wrong** is
the empty case. Measured three ways:

    lsof -nP -iTCP -sTCP:LISTEN   -> rc=0, rows
    lsof -nP (no filter)          -> rc=0, rows, warnings on stderr
    lsof -nP <file nothing has>   -> **rc=1, zero bytes of stdout**, warnings

so rc=1 is the *common* answer for "nothing is open", not an error, and
stdout and stderr must never be concatenated - six `can't stat()` warnings
read exactly like six open files.
"""

from __future__ import annotations

import getpass
import os
import pathlib
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import open_files as OF  # noqa: E402

#: Measured verbatim. Note the header's COMMAND column is 7 wide here and 9 in
#: LISTEN - so a parser cannot use column positions.
LISTEN = """COMMAND    PID             USER  FD   TYPE             DEVICE SIZE/OFF    NODE NAME
bash    587023 shrinivaskumbhar cwd    DIR               0,38     4320     426 /tmp/opencode
bash    587023 shrinivaskumbhar rtd    DIR              252,1     4096       2 /
bash    587023 shrinivaskumbhar txt    REG              252,1  1540520 8923339 /usr/bin/bash
"""

SOCKETS = """COMMAND      PID             USER FD   TYPE  DEVICE SIZE/OFF NODE NAME
llama-ser   4311 shrinivaskumbhar  3u  IPv4   34848      0t0  TCP 127.0.0.1:8765 (LISTEN)
opencode    6476 shrinivaskumbhar 13u  IPv4 129072      0t0  TCP 127.0.0.1:35842->127.0.0.1:49374 (ESTABLISHED)
"""

DELETED = ("deleted.txt 999  someuser   12w   REG   252,1     4096  999999"
           " /tmp/gone.txt (deleted)\n")

WARNINGS = ("lsof: WARNING: can't stat() nsfs file system "
            "/run/docker/netns/default\n"
            "      Output information may be incomplete.\n")


def _fake_lsof(tmp_path, monkeypatch, stdout, rc=0, stderr=""):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    script = bindir / "lsof"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "argv = sys.argv[1:]\n"
        f"open({str(bindir / 'argv')!r}, 'a').write(' '.join(argv) + '\\n')\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.stderr.write({stderr!r})\n"
        f"sys.exit({rc})\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")


def _argv(tmp_path):
    return (tmp_path / "bin" / "argv").read_text().split()


def test_a_truncated_command_name_is_replaced_with_the_real_one(tmp_path,
                                                                monkeypatch):
    """**`COMMAND` is nine characters and `llama-server` is fifteen.**

    Measured: lsof prints `llama-ser`, and `/proc/<pid>/comm` holds the real
    name. Reporting lsof's string names a program that does not exist, which
    is worse than useless - it cannot be typed into a search box.

    **Two things this test got wrong first, both from the fixture.** The pid
    was copied from my own `lsof` output, so `4311` was *the llama-server
    running on the machine running the suite* - and its
    `/proc/4311/cmdline` then contained the very string being asserted, so the
    test passed with the code under mutation **for a reason that had nothing
    to do with the name**, and would have failed on a box where that process
    was not running. It now runs against this process's own pid, and stubs
    `_cmdline` to empty so no argv can stand in for the name.
    """
    mine = os.getpid()
    table = ("COMMAND      PID             USER FD   TYPE  DEVICE SIZE/OFF"
             " NODE NAME\n"
             f"truncat     {mine} root  3u  IPv4   34848      0t0"
             "  TCP 127.0.0.1:9999 (LISTEN)\n")
    real = Path(f"/proc/{mine}/comm").read_text().strip()
    assert real != "truncat", "fixture assumption broken: this pid reads the same"

    _fake_lsof(tmp_path, monkeypatch, table)
    monkeypatch.setattr(OF, "_cmdline", lambda pid: "")
    out = OF._run_skill({})
    assert f"**{real}**" in out, out
    assert "**truncat**" not in out


def test_the_numerical_flags_are_always_used(tmp_path, monkeypatch):
    """`-nP` is not decoration. Without `-n` lsof resolves every address
    through DNS and every port through /etc/services, which is slow enough to
    time out and turns the output into something a parser cannot read."""
    _fake_lsof(tmp_path, monkeypatch, SOCKETS)
    OF._run_skill({})
    argv = _argv(tmp_path)
    # **`-nP` is one element, not two** - the first version of this test
    # asserted "-n" and "-P" separately and failed against correct code.
    assert "-nP" in argv


def test_a_listening_socket_is_not_an_established_one(tmp_path, monkeypatch):
    """`local (LISTEN)` versus `local->peer (ESTABLISHED)`. The arrow is the
    only difference between "accepting connections" and "talking to
    something", and reporting an established session as a listener is the
    sort of wrong that sends someone to fix the wrong thing."""
    _fake_lsof(tmp_path, monkeypatch, SOCKETS)
    out = OF._run_skill({})
    assert "is listening on 127.0.0.1:8765" in out
    assert "established connection 127.0.0.1:35842 → 127.0.0.1:49374" in out


def test_a_named_descriptor_is_described_as_what_it_is(tmp_path, monkeypatch):
    """`cwd`, `rtd`, `txt`, `mem` are descriptor *names*, not numbers. `cwd` in
    particular is how you find the directory a daemon will delete relative
    to - a question /proc/<pid>/cwd answers and list_processes does not."""
    _fake_lsof(tmp_path, monkeypatch, LISTEN)
    rows = OF._parse(LISTEN)
    assert [r["fd"] for r in rows] == ["cwd", "rtd", "txt"]
    assert all(r["named_fd"] for r in rows)
    out = OF._run_skill({})
    assert "its working directory" in out
    assert "the program itself" in out


def test_a_deleted_file_is_called_out_because_its_space_is_still_used(
        tmp_path, monkeypatch):
    """The most useful line lsof prints, and the one most often dropped: a
    file that no longer exists is still held, so its blocks are still in use.
    That is why a disk can be full with nothing visible on it.

    **Asserted on the *summary*, not on the sentence.** The per-row
    description already says a deleted file's space cannot be reclaimed, so an
    assertion on that wording is satisfied by the row alone - and stayed green
    with the whole summary removed. The summary carries a different claim
    (how many of them) and that is what it is for.
    """
    _fake_lsof(tmp_path, monkeypatch, DELETED)
    rows = OF._parse(DELETED)
    assert rows[0]["deleted"] is True
    out = OF._run_skill({})
    assert "1 of these are deleted files" in out
    assert "cannot be reclaimed" in out
    assert "/tmp/gone.txt" in out


def test_nothing_open_is_an_answer_not_an_error(tmp_path, monkeypatch):
    """**Measured: rc=1 with zero bytes of stdout.** rc=1 is the common
    answer for "nothing is open", so treating it as a failure is not a rare
    edge case - it is what happens every time someone asks about a file
    nothing has open."""
    # The path must EXIST: a missing one is refused before lsof runs (there is
    # a separate test for that), so a non-existent fixture would test the
    # pre-check and never reach the rc=1 path this is about.
    probe = tmp_path / "empty.txt"
    probe.write_text("")
    _fake_lsof(tmp_path, monkeypatch, "", rc=1, stderr=WARNINGS)
    out = OF._run_skill({"path": str(probe)})
    assert "Nothing has" in out
    assert "does not exist" not in out
    assert "could not answer" not in out


def test_warnings_on_stderr_are_never_reported_as_open_files(tmp_path,
                                                            monkeypatch):
    """The trap: six `can't stat()` lines read exactly like six open files if
    the two streams are joined. Asserted by content - 'nsfs' is nowhere in a
    list of handles."""
    probe = tmp_path / "empty2.txt"
    probe.write_text("")
    _fake_lsof(tmp_path, monkeypatch, "", rc=1, stderr=WARNINGS)
    out = OF._run_skill({"path": str(probe)})
    assert "nsfs" not in out
    assert "can't stat" not in out


def test_an_incomplete_list_says_it_may_be_incomplete(tmp_path, monkeypatch):
    """The warnings still matter - they mean the list is not the whole truth -
    so they are reported as a caveat on the *answer*, not as content.

    **The warnings go on `stderr`, and that is the whole test.** The first
    version passed them as `stdout`, which asserted the caveat for a reason
    that is not the one the caveat exists for: a reader that joined the two
    streams would have found "WARNING" among the file names and satisfied
    this assertion with three warnings and no caveat.
    """
    _fake_lsof(tmp_path, monkeypatch, LISTEN, stderr=WARNINGS, rc=0)
    out = OF._run_skill({})
    assert "may be incomplete" in out
    # The *rows* survived, asserted on a file name rather than on the user:
    # the owner column is deliberately hidden when it is you (see the next
    # test), and this fixture's user is whoever is running the suite.
    assert "/usr/bin/bash" in out
    assert "can't stat" not in out        # and the warnings are not the content


def test_the_owner_is_shown_only_when_it_is_not_you(tmp_path, monkeypatch):
    """`USER` is a column lsof prints on every single row, so on a list of
    your own processes it repeats your own name and says nothing. It matters
    exactly once - when a handle belongs to someone else.

    **The fixture rows are owned by whoever is running the suite.** The first
    version used `root`, so the "not shown when it is you" half was satisfied
    by the *other* rule and passed with that rule removed - it was asserting
    that root is not the current user, which is true and beside the point.
    """
    me = getpass.getuser()
    mine = ("COMMAND  PID USER  FD   TYPE DEVICE SIZE/OFF NODE NAME\n"
            f"bash 4242 {me} cwd    DIR     0,38     4320     426 /home/{me}\n")
    _fake_lsof(tmp_path, monkeypatch, mine)
    assert f"as {me}" not in OF._run_skill({})

    theirs = ("COMMAND  PID USER  FD   TYPE DEVICE SIZE/OFF NODE NAME\n"
              "dash 900 root cwd    DIR     0,1      4096     2 /root\n")
    _fake_lsof(tmp_path, monkeypatch, theirs)
    assert "as root" in OF._run_skill({})


def test_the_header_is_never_treated_as_a_row(tmp_path, monkeypatch):
    """Two different header widths are measured (COMMAND is 7 and 9). Either
    one parsed as data invents a process."""
    for text in (LISTEN, SOCKETS):
        rows = OF._parse(text)
        assert all(r["command"] != "COMMAND" for r in rows)
        assert all(r["pid"] > 0 for r in rows)


def test_a_pid_that_exited_falls_back_to_lsofs_own_name(tmp_path, monkeypatch):
    """The real name comes from /proc/<pid>/comm, and a process that exited
    between lsof's read and ours has no entry. Its short name is then the only
    name there is, and blanking it is worse than the truncation."""
    rows = OF._parse(DELETED)
    assert OF._real_command(rows[0]["pid"], "deleted.txt") == "deleted.txt"


def test_a_hung_lsof_is_not_an_empty_list(tmp_path, monkeypatch):
    """`lsof -nP` with no filter is the slowest form and can exceed the bound
    on a busy machine. A timeout has proved nothing about what is open."""
    _fake_lsof(tmp_path, monkeypatch, SOCKETS)

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("lsof", 30)

    monkeypatch.setattr(OF.subprocess, "run", boom)
    out = OF._run_skill({})
    assert "did not answer" in out


def test_it_never_closes_anything(tmp_path, monkeypatch):
    """lsof has kill flags (`-t`, and `+c` in some builds). A reader that can
    reach one is a killer, not a reader - asserted on the argv it builds."""
    _fake_lsof(tmp_path, monkeypatch, SOCKETS)
    OF._run_skill({})
    OF._run_skill({"pid": 1})
    for flag in ("-t", "-k", "-K", "--kill", "+c"):
        assert flag not in _argv(tmp_path), f"open_files must not pass {flag}"


def test_arguments_are_bounded(tmp_path, monkeypatch):
    _fake_lsof(tmp_path, monkeypatch, SOCKETS)
    assert "not a process id" in OF._run_skill({"pid": "root"})
    assert "not a count" in OF._run_skill({"limit": "many"})


def test_a_missing_lsof_names_its_package(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    out = OF._run_skill({})
    assert "lsof" in out


def test_a_nonexistent_path_is_reported_before_running_anything(tmp_path,
                                                               monkeypatch):
    """lsof on a path that does not exist still costs a full-system scan.
    Saying so without running it is both faster and clearer."""
    _fake_lsof(tmp_path, monkeypatch, "")
    out = OF._run_skill({"path": str(tmp_path / "definitely-absent")})
    assert "does not exist" in out
    assert not (tmp_path / "bin" / "argv").exists(), "it ran lsof anyway"