"""`_make_argfile_dir()`'s docstring said "A private 0700 directory".

It made an **umask-default** one. Measured on this machine with the usual
`umask 002`: `~/.local/share/shani-chronoa/argfiles/` was **0775**, sitting next
to `egress/`, `logs/`, `sessions/`, `models/` and `percepts/`, all 0700.

Not a disclosure hole — the per-call directories are `mkdtemp`, so always 0700,
and the envelopes inside are written 0600. An **integrity** one: a 0775 directory
lets another local account unlink and replace an entry, and the function's own
docstring claimed a mode it was not delivering.

**Why `mkdir` then `chmod`, and not `mkdir(mode=0o700)` alone.** With
`parents=True`, the mode argument applies to the **leaf only** - intermediate
directories are created at the umask default - and `exist_ok=True` leaves an
already-created directory exactly as it was. So a root that predates the mode
argument, or that was made as an intermediate, stays 0775 forever. The `chmod`
is what makes the docstring true on every machine, including this one. It is the
same reasoning `egress.py` already carries in a comment.
"""

import os
import stat


class TestTheRootIsPrivate:
    def test_it_is_0700_under_a_permissive_umask(self, tmp_path, monkeypatch):
        from shani_chronoa import argfile

        monkeypatch.setattr(argfile, "_ARGFILE_ROOT", tmp_path / "argfiles")
        old = os.umask(0o002)
        try:
            argfile._make_argfile_dir()
        finally:
            os.umask(old)
        mode = stat.S_IMODE((tmp_path / "argfiles").stat().st_mode)
        assert mode == 0o700, (
            f"the argfiles root is {oct(mode)} - another local account can "
            "unlink and replace an entry in it")

    def test_it_tightens_a_directory_that_already_existed(self, tmp_path, monkeypatch):
        """**The case `mkdir(mode=...)` cannot fix.**"""
        from shani_chronoa import argfile

        root = tmp_path / "argfiles"
        root.mkdir()
        os.chmod(root, 0o775)
        monkeypatch.setattr(argfile, "_ARGFILE_ROOT", root)
        argfile._make_argfile_dir()
        assert stat.S_IMODE(root.stat().st_mode) == 0o700, (
            "an existing world-readable root was left alone; the docstring "
            "claimed 0700 on every machine")

    def test_the_per_call_directory_is_private_too(self, tmp_path, monkeypatch):
        from shani_chronoa import argfile

        monkeypatch.setattr(argfile, "_ARGFILE_ROOT", tmp_path / "argfiles")
        made = argfile._make_argfile_dir()
        assert stat.S_IMODE(os.stat(made).st_mode) == 0o700, made

    def test_a_read_only_home_still_falls_back(self, tmp_path, monkeypatch):
        """The fallback must survive the fix: it is how a read-only HOME avoids
        making tool calls fail outright."""
        from shani_chronoa import argfile

        blocked = tmp_path / "blocked"
        blocked.mkdir()
        os.chmod(blocked, 0o500)
        monkeypatch.setattr(argfile, "_ARGFILE_ROOT", blocked / "argfiles")
        try:
            made = argfile._make_argfile_dir()
        finally:
            os.chmod(blocked, 0o700)
        assert stat.S_IMODE(os.stat(made).st_mode) == 0o700, made
