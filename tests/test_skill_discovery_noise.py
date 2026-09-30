"""A warning that fires on every startup is a warning nobody reads.

`NOT_A_SKILL` is the opt-out that keeps a built-in helper which lives in the
skills package without being a skill from logging

    Skipping 'builtin:X': SKILLS must be a list of Skill entries

on every single start of the app and of the MCP server. The cost is not the
noise itself but what it trains people to ignore: the genuine malformed-skill
warnings next to it are the ones worth seeing.

The marker is honoured for built-ins - and *only* for built-ins, because
honouring it in a user module would silently drop a skill the user explicitly
asked for. That half is exercised below with real user modules written into a
temp skills directory.

No built-in carries the marker today. The one that needed it was a zip-slip
guard superseded by the stricter checks `extract_archive` already applies
inline, so it was deleted rather than left as a second, weaker copy of the same
guard on the same path. The marker stays - a built-in is allowed to say it is
not a skill, and it is the user-module half that must never be able to - and
the built-in half is covered from the other side: no built-in may log a skip
warning at all, which still fails the moment a helper is added beside the skills
without the marker.
"""

import logging

import pytest

from shani_chronoa import skills as skills_pkg
from shani_chronoa.skills import NOT_A_SKILL, discover_skills


def _builtin_skip_warnings(caplog, level=logging.WARNING):
    return [r for r in caplog.records
            if r.levelno >= level and "Skipping 'builtin:" in r.getMessage()]


class TestNoBuiltInIsSkippedWithAWarning:
    def test_no_built_in_logs_a_skip_warning(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="shani_chronoa.skills"):
            discover_skills()
        assert not _builtin_skip_warnings(caplog), (
            f"still warning on every startup: "
            f"{[r.getMessage() for r in _builtin_skip_warnings(caplog)]}")

    def test_it_still_registers_every_real_skill(self):
        tools, handlers = discover_skills()
        assert len(tools) == len(handlers) >= 20
        names = {t["function"]["name"] for t in tools}
        assert "get_datetime" in names
        assert "calculate" in names
        # The marker must not have taken a real skill with it.
        assert len(names) == len(tools)


class TestTheMarkerIsNotABackdoor:
    """Honoured for built-ins, refused for user modules - and both tested."""

    def test_a_user_module_cannot_use_it_to_hide_itself(
            self, tmp_path, monkeypatch, caplog):
        user_dir = tmp_path / "skills"
        user_dir.mkdir()
        (user_dir / "liar.py").write_text(f"{NOT_A_SKILL} = True\n")
        monkeypatch.setattr(skills_pkg, "_USER_SKILLS_DIR", user_dir)
        with caplog.at_level(logging.WARNING, logger="shani_chronoa.skills"):
            discover_skills()
        assert any("liar" in r.getMessage() for r in caplog.records), (
            "a user module declared itself a non-skill and was dropped in "
            "silence; a user who drops a file into the skills directory has "
            "said it is a skill")

    def test_a_genuinely_malformed_user_skill_still_warns(
            self, tmp_path, monkeypatch, caplog):
        user_dir = tmp_path / "skills"
        user_dir.mkdir()
        (user_dir / "broken.py").write_text('SKILLS = "not a list"\n')
        monkeypatch.setattr(skills_pkg, "_USER_SKILLS_DIR", user_dir)
        with caplog.at_level(logging.WARNING, logger="shani_chronoa.skills"):
            discover_skills()
        assert any("broken" in r.getMessage() for r in caplog.records), (
            "the opt-out marker masked a real malformed-skill warning, which "
            "is the failure it was supposed to prevent")
