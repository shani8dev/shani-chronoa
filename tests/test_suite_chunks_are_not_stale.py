"""The chunk runner must not believe a log written against a different tree.

`tools/run_suite_in_chunks.sh` exists because a single full-suite run loses its
output three documented ways. It also had a defect that produced **a green tally
for a suite that had not run**, which is the failure this repository records as
worse than a missing label - and the label here was attached to a *measurement*.

    skip chunk_aa (already done)   ...   x8

Every one of those logs was from **01:00-01:09**, against a tree whose last
commit was **02:54**. The condition was `[ -s "$log" ] && grep -qE
'passed|failed|error' "$log"` - existence, and nothing else.

**Both directions are asserted, because a staleness check that always fires is
as useless as one that never does.** A log newer than the source is skipped; the
same log back-dated is re-run. And the chunk count is checked, because
`split` overwrites `chunk_aa` and leaves a previous run's higher-numbered
manifests behind - so `CHUNKS=2` after `CHUNKS=8` iterated **four** manifests.

**`DRY_RUN=1` launches nothing.** A test that starts eight parallel pytest
shards manufactures exactly the D-Bus contention this repository spends whole
entries diagnosing, and four times this session a concurrent run was mistaken
for a defect.
"""

import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _run(logs: Path, chunks: str = "2") -> str:
    env = {**os.environ, "LOGS": str(logs), "CHUNKS": chunks, "DRY_RUN": "1"}
    proc = subprocess.run(
        ["bash", "tools/run_suite_in_chunks.sh"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-400:]
    return proc.stdout


def _fabricated_logs(directory: Path, when: float) -> None:
    """A log that reads like a finished run, stamped `when`."""
    directory.mkdir(parents=True, exist_ok=True)
    for path in directory.glob("chunk_*.log"):
        path.unlink()
    for name in ("chunk_aa", "chunk_ab"):
        log = directory / f"{name}.log"
        log.write_text("12 passed in 1.00s\n", encoding="utf-8")
        os.utime(log, (when, when))


class TestALogIsOnlyDoneIfItIsNewerThanTheCode:

    def test_a_log_newer_than_the_source_is_skipped(self, tmp_path):
        """The ordinary case: nothing has changed since the last run."""
        _fabricated_logs(tmp_path, time.time())
        out = _run(tmp_path)
        assert out.count("skip chunk_") == 2, out
        assert "STALE" not in out, (
            "a log written after the newest source file was called stale - "
            "the check cannot tell a fresh result from an old one")

    def test_the_same_log_back_dated_is_re_run(self, tmp_path):
        """**The control, and the whole point.**

        The identical log, stamped 2000. Reading it as "already done" is what
        produced a green tally for a suite that had not run since the strip
        redesign and the plan-mode change.
        """
        _fabricated_logs(tmp_path, time.time())
        assert "skip chunk_aa" in _run(tmp_path), "precondition failed"
        _fabricated_logs(tmp_path, 946684800.0)      # 2000-01-01
        out = _run(tmp_path)
        assert "skip chunk_" not in out, (
            "a log older than the source was accepted as a finished run:\n"
            + out)
        assert out.count("STALE") == 2, (
            f"both stale chunks should say so:\n{out}")

    def test_the_stale_line_names_a_file_that_is_actually_newer(self, tmp_path):
        """**A warning that names nothing is a warning nobody acts on.**

        The check reports *which* file made the log stale, so a reader can tell
        a harness artefact from a real regression without opening the tree.

        **Compared against the log, and this was wrong the first time.** The
        assertion read `tmp_path.stat()` - the mtime of the *directory*, which is
        when the test created the fabricated logs inside it, i.e. now. So it
        demanded that a source file be newer than the current moment, which is
        false for every file in the tree, and it failed while the runner named
        files correctly.

        It is the same mistake the runner itself made and this pass fixed there:
        **the log directory is not the log.** What has to hold is that the named
        file is newer than *the log that was rejected*, and the log is stamped
        2000 by this test, so that is checkable.
        """
        _fabricated_logs(tmp_path, 946684800.0)
        named = re.findall(r"STALE \S+ - re-running: (\S+)", _run(tmp_path))
        assert named, "the stale line names no file"
        stale_at = tmp_path / "chunk_aa.log"
        assert stale_at.stat().st_mtime == 946684800.0, "precondition: the log is old"
        for path in named:
            assert (REPO / path).exists(), (
                f"it named {path}, which is not in the tree")
            assert (REPO / path).stat().st_mtime > stale_at.stat().st_mtime, (
                f"it named {path}, which is not newer than the log it rejected")

    def test_an_empty_log_is_not_done_either_way(self, tmp_path):
        """A log with no summary in it is a run that was killed.

        The old condition's `grep -qE 'passed|failed|error'` is kept for this,
        and this is the test that says why.
        """
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "chunk_aa.log").write_text("", encoding="utf-8")
        out = _run(tmp_path)
        assert "skip chunk_aa" not in out, (
            "an empty log was accepted as a finished run")


class TestTheChunkCountIsTheChunkCount:

    def test_chunks_two_means_two_manifests(self, tmp_path):
        """`split` overwrites `chunk_aa` and leaves the rest behind.

        Measured: `CHUNKS=8` then `CHUNKS=2` iterated **four** manifests, two
        of them the older and larger split - so a chunking parameter did not
        change the chunks, which is a parameter that lies.
        """
        _fabricated_logs(tmp_path, time.time())
        _run(tmp_path, chunks="8")                   # leaves 8 manifests
        out = _run(tmp_path, chunks="2")
        assert out.count("dry-run chunk_") == 2, (
            f"CHUNKS=2 iterated {out.count('dry-run chunk_')} manifests:\n{out}")

    def test_and_no_manifest_is_invented(self, tmp_path):
        _fabricated_logs(tmp_path, time.time())
        out = _run(tmp_path, chunks="4")
        assert out.count("dry-run chunk_") == 4, out


def test_the_script_is_syntactically_valid():
    """`bash -n`, which is the only check that catches a quoting error here."""
    proc = subprocess.run(["bash", "-n", "tools/run_suite_in_chunks.sh"],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert sys.platform  # keep the import used and the intent obvious