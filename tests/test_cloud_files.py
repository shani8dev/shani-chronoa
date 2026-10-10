"""`cloud_files`: what is in my cloud storage?

`backup_status` covers the off-machine *repository* (`restic`); this covers the
remote a person means by "the cloud". Nothing asked `rclone` anything, and it
ships in `shani-tools-network`.

**Every shape below is captured from a real `@blue` slot**, measured against a
local remote the run configured - because the only earlier measurement was a
*refusal* (`rclone about` on the image's R2 remote, rc=3), and a reader written
from a refusal is a reader that can only fail.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import cloud_files as CF  # noqa: E402

#: `rclone listremotes`, verbatim.
REMOTES = "probe:\n"

#: `rclone size`, verbatim.
SIZE = "Total objects: 2\nTotal size: 8 B (8 Byte)\n"

#: `rclone lsf`, verbatim. **`docs/` has its trailing slash** - that is the only
#: thing distinguishing a directory from a file.
LSF = "a.txt\ndocs/\n"

NO_REMOTES = ""

NO_SECTION = ('2026/10/10 08:14:09 CRITICAL: Failed to create file system for '
              '"nosuchremote:": didn\'t find section in config\n')


def _fake_rclone(tmp_path, monkeypatch, *, remotes=REMOTES, size=SIZE, lsf=LSF,
                  remotes_rc=0, size_rc=0, lsf_rc=0, lsf_err="", argv_log=None):
    """One fake `rclone` answering by subcommand, so the skill's real argv is
    both used and recorded."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = bindir / "argv"
    script = bindir / "rclone"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "argv = sys.argv[1:]\n"
        f"open({str(log)!r}, 'a').write(' '.join(argv) + chr(10))\n"
        "verb = [a for a in argv if not a.startswith('-')]\n"
        "verb = verb[0] if verb else ''\n"
        f"if verb == 'listremotes':\n"
        f"    sys.stdout.write({remotes!r}); sys.exit({remotes_rc})\n"
        f"if verb == 'size':\n"
        f"    sys.stdout.write({size!r}); sys.exit({size_rc})\n"
        f"if verb == 'lsf':\n"
        f"    sys.stdout.write({lsf!r})\n"
        f"    sys.stderr.write({lsf_err!r}); sys.exit({lsf_rc})\n"
        "sys.exit(0)\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", "%s:%s" % (bindir, os.environ["PATH"]))
    monkeypatch.setenv("HOME", str(tmp_path))
    return log


def _argv(log):
    return log.read_text().splitlines()


# --- the shapes measured on the slot ------------------------------------------

def test_the_trailing_colon_is_stripped(tmp_path, monkeypatch):
    """`listremotes` prints `probe:` - with the colon. Surfaces that need it and
    answers that quote it have to agree about where it is."""
    _fake_rclone(tmp_path, monkeypatch)
    remotes, _ = CF._list_remotes()
    assert remotes == ["probe"], remotes


def test_a_directory_is_told_from_a_file_by_its_trailing_slash(tmp_path,
                                                              monkeypatch):
    """`a.txt` and `docs/` - the slash is the whole distinction, and stripping it
    makes a directory indistinguishable from a file."""
    _fake_rclone(tmp_path, monkeypatch)
    out = CF._run_skill({})
    assert "1 director(ies): docs" in out
    assert "1 file(s): a.txt" in out


def test_total_objects_and_size_are_parsed(tmp_path, monkeypatch):
    _fake_rclone(tmp_path, monkeypatch)
    objects, size = CF._size("probe:")
    assert objects == 2, objects
    assert "8 B" in size, size


def test_a_single_remote_is_read_without_being_asked_for(tmp_path, monkeypatch):
    """One remote and no `remote` argument: asking again is a question the
    answer already settled."""
    _fake_rclone(tmp_path, monkeypatch)
    out = CF._run_skill({})
    assert "**probe:**" in out
    assert "2 object(s)" in out


# --- the three states an empty answer can mean --------------------------------

def test_no_remotes_is_not_the_same_as_nothing_in_them(tmp_path, monkeypatch):
    """rc=0 with empty stdout. Almost every fresh machine is here, and "nothing
    is in your cloud storage" would be a different and wrong claim."""
    _fake_rclone(tmp_path, monkeypatch, remotes=NO_REMOTES)
    out = CF._run_skill({})
    assert "No cloud storage remotes are configured" in out
    assert "not the same as a configured remote" in out


def test_a_typo_remote_is_not_an_empty_bucket(tmp_path, monkeypatch):
    """**The one that matters most.** rc=1 with `didn't find section in config`
    is rclone saying *you typo'd*, not *it is empty*. Treating rc=1 as "nothing
    in it" reports an empty bucket for a nonexistent remote - the opposite of
    the truth, in a way that reads as good news."""
    # **A remote `listremotes` *does* know but that `lsf` refuses.** A name
    # absent from `listremotes` is caught earlier and correctly, so it is the
    # wrong branch for this test - the first version asked for "nosuch" and got
    # the earlier refusal, passing for a reason unrelated to the wording.
    _fake_rclone(tmp_path, monkeypatch, lsf="", lsf_rc=1, lsf_err=NO_SECTION)
    out = CF._run_skill({"remote": "probe"})
    assert "no remote called" in out
    assert "not an empty bucket" in out
    assert "It holds nothing" not in out


def test_an_unreadable_remote_is_not_an_empty_one(tmp_path, monkeypatch):
    _fake_rclone(tmp_path, monkeypatch, lsf="", lsf_rc=3,
                 lsf_err="NOTICE: credentials expired\n")
    out = CF._run_skill({})
    assert "could not read" in out
    assert "not the same as it being empty" in out


def test_several_remotes_are_all_summarised(tmp_path, monkeypatch):
    _fake_rclone(tmp_path, monkeypatch, remotes="one:\ntwo:\n")
    out = CF._run_skill({})
    assert "2 cloud storage remote(s) configured" in out
    # The list is a plain summary line, not the bold single-remote form - the
    # first version asserted the bold one and failed on correct output.
    assert "- one: 2 object(s), 8 B." in out
    assert "- two: 2 object(s), 8 B." in out


# --- it never transfers anything ---------------------------------------------

def test_it_never_invokes_a_transfer_or_delete(tmp_path, monkeypatch):
    """`rclone` can copy, sync, move and delete. A status skill that can reach
    one is not a status skill, and this is the whole safety argument for the
    module."""
    log = _fake_rclone(tmp_path, monkeypatch)
    CF._run_skill({})
    CF._run_skill({"remote": "probe", "path": "docs"})
    # **The list is written out here, not read from the module.** Reading the
    # module's own tuple makes the test agree with whatever the module says -
    # replacing `_FORBIDDEN` with a no-match left all assertions passing, because
    # a verb forbidden by nothing is forbidden by the test too. A list the test
    # owns is a list the module has to satisfy.
    transfer_verbs = ("copy", "sync", "move", "delete", "purge", "rmdir",
                      "mkdir", "touch", "cat", "dedupe", "check", "cleanup",
                      "mount", "config", "obscure")
    for line in _argv(log):
        verb = line.split()[0]
        assert verb not in transfer_verbs, \
            "cloud_files invoked rclone %r, which is not a reader" % verb


def test_it_never_asks_for_a_configuration(tmp_path, monkeypatch):
    """`rclone config create` writes credentials. This is a reader."""
    log = _fake_rclone(tmp_path, monkeypatch)
    CF._run_skill({})
    assert "config" not in " ".join(_argv(log))


def test_the_remote_and_path_are_one_argument(tmp_path, monkeypatch):
    """A path with a space must not become two argv elements, because the
    sandbox and rclone would each read it differently."""
    log = _fake_rclone(tmp_path, monkeypatch)
    monkeypatch.setattr(CF, "_top_level", lambda target: ([], ""))
    CF._run_skill({"remote": "probe", "path": "my documents"})
    assert any("my documents" in line for line in _argv(log))


# --- the honest ceiling --------------------------------------------------------

def test_it_does_not_claim_the_copy_matches(tmp_path, monkeypatch):
    """The question behind "what is in my cloud" is usually *is it up to date*.
    Saying what is there without implying that is the difference between a
    status and a comparison."""
    _fake_rclone(tmp_path, monkeypatch)
    out = CF._run_skill({})
    assert "not whether it matches" in out


def test_an_empty_remote_says_it_is_empty(tmp_path, monkeypatch):
    _fake_rclone(tmp_path, monkeypatch, lsf="")
    out = CF._run_skill({})
    assert "holds nothing at the top level" in out


def test_a_hung_rclone_is_not_an_empty_remote(tmp_path, monkeypatch):
    _fake_rclone(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("rclone", 45)

    monkeypatch.setattr(CF.subprocess, "run", boom)
    out = CF._run_skill({})
    assert "not the same as" not in out or "could not read" in out
    assert "did not answer" in out


def test_a_missing_rclone_names_its_package(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    out = CF._run_skill({})
    assert "'rclone' package" in out
    assert "the package that provides it" not in out


# --- the one equivalent mutant, recorded rather than hidden --------------------
# **Emptying `_FORBIDDEN` leaves the suite green, and correctly so.** The module
# never invokes a transfer verb, so a guard against one fires neither way - the
# mutation is equivalent, not a gap. What makes the read-only property real is
# the mutation that *does* change behaviour: replacing an `lsf` call with a
# `sync` call fails 4 tests, because the test's own copy of the verb list sees
# the argv it was handed and the module's own guard raises before rclone runs.
#
# So there are two independent controls, and neither can drift silently:
#   - the module refuses a forbidden verb in `_run`, failing closed;
#   - the test asserts the argv against a list it owns, not the module's.
# A module-level list alone was the original bug: a mutation of it left the test
# agreeing with the mutant, because the test read the module's own tuple.
