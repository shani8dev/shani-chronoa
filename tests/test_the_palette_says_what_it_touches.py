"""The palette says what each skill **touches**, not what it is.

**The palette's skill rows carried the tool's whole description** - wordy,
model-facing prose, cut off mid-word in a search result ("your crontab,
th..."). A person reading them could not tell a clipboard read from a file
delete, and the one thing a search result must answer is *does this touch
my machine*.

So the subtitle is the **effect**, read from the sets that already exist -
the consent key, the destructive list, the read-only list - because a second
hand-kept list of "which tools are dangerous" is a list that rots the moment
a skill lands.

And because the effect is in the subtitle, **the palette's search finds it**:
typing "delete" reaches the destructive skills, which is the question the
palette is now able to answer.
"""

from __future__ import annotations

import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "usr", "lib", "shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
Gtk = pytest.importorskip("gi.repository.Gtk", reason="GTK 4 is not installed")

from shani_chronoa import capabilities  # noqa: E402
from shani_chronoa.config import ChronoaConfig  # noqa: E402
from shani_chronoa.gui.palette import filter_entries  # noqa: E402
from shani_chronoa.gui.window import ChronoaWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    application = Gtk.Application(application_id="test.chronoa.palette.effect")
    yield application
    application.quit()


@pytest.fixture
def window(app):
    return ChronoaWindow(app, config=ChronoaConfig())


def _skill_entries(window):
    return [e for e in window.palette_entries() if e.group == "Skills"]


class TestWhatEachSkillTouches:
    def test_every_skill_row_says_what_it_touches(self, window):
        """**Not what it is** - the title already says that - but what it
        does to the machine, in one of the four plain phrases."""
        entries = _skill_entries(window)
        assert entries, "the palette offers no skills to describe"
        allowed = {
            capabilities.EFFECT_READS,
            capabilities.EFFECT_ACTS,
            capabilities.EFFECT_PERMISSION,
            capabilities.EFFECT_DESTRUCTIVE,
        }
        said = {e.subtitle for e in entries}
        unknown = said - allowed
        assert not unknown, (
            f"the palette says something that is not an effect: {unknown}")

    def test_a_skill_that_can_delete_says_so(self, window):
        """**The difference between "it asked" and "it took"**, and it is the
        one a person needs to see before pressing Enter."""
        entries = {e.title: e.subtitle for e in _skill_entries(window)}
        destructive = [t for t, s in entries.items()
                       if s == capabilities.EFFECT_DESTRUCTIVE]
        assert destructive, (
            "nothing in the palette says it can delete - so the phrase is "
            "either unreachable or wrong")

    def test_a_skill_that_only_reads_says_so(self, window):
        entries = {e.title: e.subtitle for e in _skill_entries(window)}
        readers = [t for t, s in entries.items()
                   if s == capabilities.EFFECT_READS]
        assert readers, (
            "nothing in the palette says it only reads - so the phrase is "
            "either unreachable or wrong")

    def test_the_effect_is_searchable(self, window):
        """**The point of putting it in the subtitle**: the palette's search
        matches it, so "delete" reaches the destructive skills."""
        entries = _skill_entries(window)
        hits = filter_entries(entries, "delete")
        assert hits, "searching for 'delete' finds nothing"
        # **The destructive ones are in it** - and a skill that only reads is
        # not, whether its name mentions deleting or not.
        assert [h for h in hits
                if h.subtitle == capabilities.EFFECT_DESTRUCTIVE], (
            f"searching for 'delete' found nothing destructive: "
            f"{[h.title for h in hits]}")
        assert not [h for h in hits
                    if h.subtitle == capabilities.EFFECT_READS], (
            f"a read-only skill came up for 'delete': "
            f"{[h.title for h in hits]}")

    def test_the_effect_comes_from_the_sets_and_not_a_second_list(self):
        """**A second hand-kept list of which tools are dangerous rots** the
        moment a skill lands, and nothing would notice.

        The check is that the function reads the sets the rest of the tree
        reads - so a tool that becomes destructive is described as destructive
        because the *set* says so.
        """
        import inspect
        source = inspect.getsource(capabilities.effect_of)
        for name in ("gated_by", "DESTRUCTIVE_CONSENT_KEYS",
                      "READ_ONLY_TOOLS"):
            assert name in source, (
                f"effect_of does not read {name} - so it is a second list")
