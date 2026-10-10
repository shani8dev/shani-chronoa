"""`sync_folder`: incremental folder sync that shows the plan before it acts.

`move_or_copy_file` copies one path in one shot. The other half of that
question - "I edit files on my laptop and copy them to the server every day, can
it send only what changed" - is an incremental sync, and `rsync` is the tool.

**The two defects that matter were both "a confident no-op that copies
nothing",** and both were found by running the real binary:

- **`rsync src/ dst/` transfers nothing without a recursion flag.** Measured on
  rsync 3.4.1: it prints `skipping directory .` and **exits 0**. So a sync
  without `-a`/`-r` reported "already up to date" on folders that differed -
  the worst possible failure for a tool whose whole claim is that it knows what
  changed. The exit status distinguishes nothing here either.
- **The trailing slash is load-bearing.** `rsync src dst` copies the folder
  *into* the destination as `dst/src/...`, nesting one level deeper on every
  run. "Sync A to B" means A's contents land in B.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import sync_folder as SF  # noqa: E402

#: Real `rsync --stats --itemize-changes --dry-run` output, captured from this
#: machine's rsync 3.4.1.
STATS_DIFF = """sending incremental file list
>f.st...... a.txt
>f+++++++++ sub/b.txt

Number of files: 2
Number of created files: 1
Number of deleted files: 0
Number of regular files transferred: 2
Total file size: 11 bytes
Total transferred file size: 11 bytes
Literal data: 11 bytes
Matched data: 0 bytes
"""

STATS_SAME = """sending incremental file list

Number of files: 2
Number of created files: 0
Number of deleted files: 0
Number of regular files transferred: 0
Total file size: 11 bytes
Total transferred file size: 0 bytes
"""


def _fake_rsync(tmp_path, monkeypatch, stdout=STATS_DIFF, code=0, stderr=""):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    data = tmp_path / "out.txt"
    data.write_text(stdout)
    log = tmp_path / "argv.txt"
    script = bindir / "rsync"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"open({str(log)!r}, 'a').write('\\x00'.join(sys.argv[1:]) + '\\n')\n"
        f"sys.stdout.write(open({str(data)!r}).read())\n"
        f"sys.stderr.write({stderr!r})\n"
        f"sys.exit({code})\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    return log


def _two_folders(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "a.txt").write_text("hello\n")
    (src / "sub").mkdir()
    (src / "sub" / "b.txt").write_text("deep\n")
    return src, dst


def test_it_recurses(tmp_path, monkeypatch):
    """**Measured: without a recursion flag rsync prints "skipping directory"
    and exits 0.** The skill would then say "already up to date" on folders
    that differ - a confident no-op that copies nothing.
    """
    log = _fake_rsync(tmp_path, monkeypatch)
    src, dst = _two_folders(tmp_path)
    SF._run({"source": str(src), "destination": str(dst)})
    argv = log.read_text().split("\n")
    dry = argv[0].split("\x00")
    assert "--archive" in dry or "-a" in dry, dry


def test_the_source_gets_a_trailing_slash(tmp_path, monkeypatch):
    """`rsync src dst` nests as `dst/src/...`; `src/` copies contents to `dst`.
    Measured here: without the slash the destination came out as `dst/src`.
    """
    log = _fake_rsync(tmp_path, monkeypatch)
    src, dst = _two_folders(tmp_path)
    SF._run({"source": str(src), "destination": str(dst)})
    args = log.read_text().split("\x00")
    assert args[-2].endswith("/"), args


def test_the_plan_is_shown_and_nothing_is_copied(tmp_path, monkeypatch):
    """The dry run is the feature, not a preview: a copy tool that only
    reports after the fact asks you to trust it with your files first.
    """
    src, dst = _two_folders(tmp_path)
    _fake_rsync(tmp_path, monkeypatch)
    out = SF._run({"source": str(src), "destination": str(dst)})
    assert "dry run" in out
    assert "nothing has been copied" in out
    assert "a.txt" in out and "sub/b.txt" in out


def test_a_matching_folder_pair_says_so(tmp_path, monkeypatch):
    src, dst = _two_folders(tmp_path)
    _fake_rsync(tmp_path, monkeypatch, stdout=STATS_SAME)
    out = SF._run({"source": str(src), "destination": str(dst)})
    assert "up to date" in out


def test_a_missing_source_is_named_not_a_failure(tmp_path, monkeypatch):
    _fake_rsync(tmp_path, monkeypatch)
    out = SF._run({"source": str(tmp_path / "nothing"),
                   "destination": str(tmp_path / "dst")})
    assert "nothing at" in out
    assert "Nothing was changed" in out or "to sync from" in out


def test_a_file_source_points_at_the_other_skill(tmp_path, monkeypatch):
    """A folder sync given a file should say so plainly rather than produce a
    confusing rsync error - `move_or_copy_file` is the right tool.
    """
    target = tmp_path / "one.txt"
    target.write_text("x")
    _fake_rsync(tmp_path, monkeypatch)
    out = SF._run({"source": str(target),
                   "destination": str(tmp_path / "dst")})
    assert "move_or_copy_file" in out
    assert "not a folder" in out


def test_no_arguments_asks_for_both_paths():
    out = SF._run({})
    assert "source" in out and "destination" in out


def test_a_failing_rsync_reports_rc_23_honestly(tmp_path, monkeypatch):
    """**Measured: a missing source exits 23, not 1.** So the status alone
    cannot say why, and the reason must come from rsync's own words.
    """
    src, dst = _two_folders(tmp_path)
    _fake_rsync(tmp_path, monkeypatch, code=23,
                stderr="rsync: [sender] change_dir \"nope\" failed: "
                       "No such file or directory (2)\n")
    out = SF._run({"source": str(src), "destination": str(dst)})
    assert "No such file or directory" in out
    assert "Nothing was changed" in out


def test_a_delete_request_is_refused_when_asked_to_apply(tmp_path, monkeypatch):
    """Deleting at the destination is how a mistyped path erases work, so the
    refusal is unconditional and says why - not "are you sure?".

    The plan (dry run) is still allowed to run, so the assertion is that no
    invocation at all carried `--delete`, not that nothing ran.
    """
    src, dst = _two_folders(tmp_path)
    log = _fake_rsync(tmp_path, monkeypatch)
    out = SF._run({"source": str(src), "destination": str(dst),
                   "apply": True, "delete": True})
    assert "do not run a sync that deletes" in out
    assert "--delete" not in log.read_text()


def test_apply_actually_copies(tmp_path, monkeypatch):
    """The log holds **both** invocations - the plan and the real run - so the
    assertion is that the last one is not a dry run, not that none is."""
    src, dst = _two_folders(tmp_path)
    log = _fake_rsync(tmp_path, monkeypatch)
    out = SF._run({"source": str(src), "destination": str(dst), "apply": True})
    assert "Copied" in out
    invocations = [line for line in log.read_text().split("\n") if line]
    assert len(invocations) == 2, invocations
    assert "--dry-run" in invocations[0]
    assert "--dry-run" not in invocations[1]


def test_a_missing_rsync_names_the_package(tmp_path, monkeypatch):
    src, dst = _two_folders(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    out = SF._run({"source": str(src), "destination": str(dst)})
    assert "rsync" in out


def test_as_dir_is_idempotent():
    assert SF._as_dir("/a/b") == "/a/b/"
    assert SF._as_dir("/a/b/") == "/a/b/"


@pytest.mark.skipif(not pathlib.Path("/usr/bin/rsync").exists(),
                    reason="rsync is not installed here")
def test_the_real_rsync_syncs_a_real_tree(tmp_path):
    """Not a stub: the real `rsync`, moving a real tree, twice.

    The second run is the assertion that matters - a sync that reports "up to
    date" after copying nothing would pass the first half of this and fail
    here, which is exactly the defect.
    """
    src, dst = _two_folders(tmp_path)
    plan = SF._run({"source": str(src), "destination": str(dst)})
    assert "dry run" in plan
    assert not list(dst.iterdir()), "the dry run copied something"

    applied = SF._run({"source": str(src), "destination": str(dst),
                       "apply": True})
    assert "Copied" in applied
    assert (dst / "a.txt").read_text() == "hello\n"
    assert (dst / "sub" / "b.txt").read_text() == "deep\n"

    again = SF._run({"source": str(src), "destination": str(dst)})
    assert "up to date" in again, again


@pytest.mark.skipif(not pathlib.Path("/usr/bin/rsync").exists(),
                    reason="rsync is not installed here")
def test_the_real_rsync_without_recursion_would_have_been_a_no_op(tmp_path):
    """**The defect, demonstrated.** Plain `rsync src/ dst/` on this machine
    transfers nothing and exits 0 - so the guard is not theoretical.
    """
    src, dst = _two_folders(tmp_path)
    bare = subprocess.run(["rsync", "--stats", "--dry-run", f"{src}/", f"{dst}/"],
                          capture_output=True, text=True)
    assert bare.returncode == 0
    assert "Number of regular files transferred: 0" in bare.stdout
    # And with recursion it does transfer.
    recursed = subprocess.run(
        ["rsync", "--archive", "--stats", "--dry-run", f"{src}/", f"{dst}/"],
        capture_output=True, text=True)
    assert "Number of regular files transferred: 2" in recursed.stdout