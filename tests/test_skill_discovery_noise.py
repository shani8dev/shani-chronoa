"""A warning that fires on every startup is a warning nobody reads.

`scan_archive` lives in the skills package but is not a skill: it is a zip-slip
guard for a skill-download feature this project does not have, so it has no
`SKILLS` list. `discover_skills()` therefore logged

    Skipping 'builtin:scan_archive': SKILLS must be a list of Skill entries

on every single start of the app and of the MCP server, for the life of the
project. The cost is not the noise itself but what it trains people to ignore:
the genuine malformed-skill warnings next to it are the ones worth seeing.

The fix is an explicit opt-out marker for built-ins - and *only* for built-ins,
because honouring it in a user module would silently drop a skill the user
explicitly asked for.
"""

import logging

import pytest

from shani_chronoa import skills as skills_pkg
from shani_chronoa.skills import NOT_A_SKILL, discover_skills


def _records(caplog, level=logging.WARNING):
    return [r for r in caplog.records
            if r.levelno >= level and "scan_archive" in r.getMessage()]


class TestTheBuiltInNonSkillIsSkippedQuietly:
    def test_it_does_not_warn(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="shani_chronoa.skills"):
            discover_skills()
        assert not _records(caplog), (
            f"still warning on every startup: "
            f"{[r.getMessage() for r in _records(caplog)]}")

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
