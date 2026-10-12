"""A dream pass that could not be recorded was silent, and so was its failure.

`dream.write_dream()` runs the offline pass and keeps the result. It catches
`OSError` and returns `None` - and the daemon's caller was

    written = dream_module.write_dream()
    if written:
        logger.info("dreamed; wrote %s", written.name)

**Two outcomes where there are three.** "It wrote a file" and "it could not write
one" both came out as `written` being falsy, and the second raised nothing - so
it logged **nothing at all**. Measured with the destination unwritable: the pass
read this machine's whole 18.9 MB log, produced its full 40-line report, and the
daemon's log held no line about it. A pass that could not be recorded is
indistinguishable from a pass that found nothing to say, which is the same shape
this repository records as *a check that could not run is not a check that found
nothing* - here, a check that could not *persist*.

The log line inside `write_dream` was at `debug`, so even it was invisible at the
daemon's INFO level.

**And the first fix for this was wrong in the recorded way.** The `except`
handler referred to `destination`, which the first version assigned *inside* the
`try` - so when `ensure_private_dir`, the block's first statement, raised, the
handler died with `UnboundLocalError` instead of reporting the OSError it was
written for. A failure that corrupts the run rather than reporting itself. Found
by running it, not by reading it.

`daemon._dream_tick()` is a named function rather than a block inside the
`while`, because a block inside a loop is unreachable from a test and this is the
one piece of the daemon whose failure has to be observable.
"""

from __future__ import annotations

import logging
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]
                       / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import daemon, dream  # noqa: E402

REAL_LOG = (pathlib.Path.home() / ".local" / "share" / "shani-chronoa"
            / "logs" / "tool_calls.log")


@pytest.fixture
def scratch():
    """Somewhere to keep test dreams, outside the real home."""
    root = pathlib.Path("/tmp/opencode/dream-test")
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_a_dream_that_can_be_written_returns_its_path(scratch):
    """The happy path, so the two below are measured against something."""
    out = scratch / "written"
    out.mkdir(parents=True, exist_ok=True)
    if not REAL_LOG.exists():
        pytest.skip("no tool-call log on this machine to dream about")
    written = dream.write_dream(REAL_LOG, out_dir=out)
    assert written is not None and written.exists(), (
        "a writable destination produced no file")
    assert written.name == "dream-latest.txt"


def test_a_dream_that_cannot_be_written_says_so_at_warning(scratch, caplog):
    """**The failure must be visible, and it has to say where.**

    `out_dir` is a *file*, so `ensure_private_dir`'s `mkdir(exist_ok=True)`
    raises `FileExistsError` and no `chmod` can rescue it - unlike a directory
    whose mode is merely wrong, which `ensure_private_dir` re-chmods to 0700 and
    then writes into successfully. That distinction is why this fixture is a file
    and not a read-only directory: measured, the directory version writes fine.
    """
    if not REAL_LOG.exists():
        pytest.skip("no tool-call log on this machine to dream about")
    blocked = scratch / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="shani_chronoa.dream"):
        written = dream.write_dream(REAL_LOG, out_dir=blocked)
    assert written is None, "a destination that cannot be written to reported success"
    assert [r for r in caplog.records if r.levelno >= logging.WARNING], (
        "the write failed and nothing was logged at WARNING - the failure is "
        "invisible again")
    assert "could not be written" in caplog.text, (
        "the warning did not say what went wrong:\n" + caplog.text)
    assert "blocked" in caplog.text, (
        "the warning did not name the destination it failed at:\n" + caplog.text)


def test_the_daemon_distinguishes_could_not_write_from_wrote(monkeypatch, caplog):
    """**The control that makes the daemon's two log lines different.**

    With `write_dream` returning `None`, the daemon must neither say "wrote" nor
    stay silent. Both halves are asserted, because a caller that handled this
    case by claiming success would pass a silence check while lying.
    """
    monkeypatch.setattr(dream, "write_dream", lambda *a, **k: None)
    with caplog.at_level(logging.INFO, logger="shani_chronoa.daemon"):
        daemon._dream_tick()
    assert "wrote dream-latest.txt" not in caplog.text, (
        "the daemon reported writing a file it did not write:\n" + caplog.text)
    assert "could not be written" in caplog.text, (
        "the daemon said nothing about a pass it could not record:\n" + caplog.text)


def test_the_daemon_says_wrote_when_it_did(monkeypatch, caplog, tmp_path):
    """The other direction: a real path must not read as a failure."""
    written = tmp_path / "dream-latest.txt"
    written.write_text("a dream", encoding="utf-8")
    monkeypatch.setattr(dream, "write_dream", lambda *a, **k: written)
    with caplog.at_level(logging.INFO, logger="shani_chronoa.daemon"):
        daemon._dream_tick()
    assert "dreamed; wrote dream-latest.txt" in caplog.text, (
        "a written dream was not reported:\n" + caplog.text)
    assert "could not be written" not in caplog.text, (
        "a written dream was reported as a failure:\n" + caplog.text)


def test_a_dream_that_raises_does_not_escape_the_daemon(monkeypatch, caplog):
    """The daemon's own guarantee: a dream that cannot run costs nothing else.

    It is why the `try` is inside `_dream_tick` rather than around it in the
    loop - and the reason the block had to become a named function to be testable.
    """
    def boom(*_a, **_k):
        raise RuntimeError("the log vanished")

    monkeypatch.setattr(dream, "write_dream", boom)
    with caplog.at_level(logging.WARNING, logger="shani_chronoa.daemon"):
        daemon._dream_tick()          # must not raise
    assert "dream pass failed" in caplog.text, (
        "the raising pass was contained but not reported:\n" + caplog.text)
