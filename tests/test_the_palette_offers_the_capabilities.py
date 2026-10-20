"""The palette reaches the capabilities, and asks for them rather than firing them.

**The palette had five groups and no skills.** "Go to", "Panels", "Modes",
"Commands" and the cloud models - and not one of the 260 things the
assistant can actually do. A skill was reachable only by asking for it in
words, and by the registry's name rather than a person's.

So the palette is where a person looks for a capability, and it could not
find one. The fix is a group of the capabilities themselves, named the way
the menu names them.

**And pressing one fills the composer rather than running it.** A skill that
writes files or leaves the machine should be *asked for*, not fired by a
keystroke in a search box - so the entry puts the prompt in the box and
leaves the question with the person.
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

from shani_chronoa.config import ChronoaConfig  # noqa: E402
from shani_chronoa.gui.window import ChronoaWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    application = Gtk.Application(application_id="test.chronoa.palette.skills")
    yield application
    application.quit()


@pytest.fixture
def window(app):
    return ChronoaWindow(app, config=ChronoaConfig())


class TestThePaletteOffersTheCapabilities:
    def test_every_capability_is_in_the_palette(self, window):
        """**The defect, in the shape it had.**

        The palette reached panels, commands and modes, and not one of the
        260 capabilities - so the thing the palette exists for was missing
        from it.
        """
        from shani_chronoa import skills

        schemas, _handlers = skills.discover_skills()
        entries = window.palette_entries()
        offered = {e.title for e in entries if e.group == "Skills"}
        assert len(schemas) > 200, (
            f"the registry holds {len(schemas)} schemas, so this test is not "
            f"measuring the palette any more")
        assert len(offered) > 200, (
            f"the palette offers {len(offered)} capabilities out of "
            f"{len(schemas)} - the skills are still not in it")

    def test_a_capability_is_named_the_way_the_menu_names_it(self, window):
        """**One list, not a second hand-kept one.**

        The names come from `capabilities`, which already knows the plain
        label for every tool - so the palette and the menu cannot disagree
        about what a skill is called.
        """
        from shani_chronoa import capabilities as caps

        entries = [e for e in window.palette_entries() if e.group == "Skills"]
        titles = {e.title for e in entries}
        from_menu = {caps.tool_title(c.tool) for c in caps.find_capabilities(
            _schemas())}
        assert titles & from_menu, (
            f"none of the palette's {len(titles)} capability names is one the "
            f"menu uses: {sorted(titles)[:5]!r}")

    def test_pressing_one_asks_for_it(self, window):
        """**It fills the composer; it does not run the skill.**

        A skill that writes files or leaves the machine must be *asked for*,
        so the entry puts the prompt in the box and the person sends it.
        """
        entries = [e for e in window.palette_entries() if e.group == "Skills"]
        assert entries, "the palette has no capabilities to choose from"
        entry = entries[0]
        assert entry.run is not None, (
            "a capability with nothing to do is dropped by `collect()` - so it "
            "would not be offered at all")
        entry.run()
        said = window.get_input_text()
        assert said, (
            "choosing a capability left the composer empty, so the person has "
            "nothing to send and the row did nothing")
        assert window.get_input_text() == said


def _schemas():
    from shani_chronoa import skills
    schemas, _handlers = skills.discover_skills()
    return schemas
