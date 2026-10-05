"""`common.banner()` puts its notice on screen, not just in the widget tree.

`Adw.Banner` has a `revealed` property, and it starts `False`. A caller that
builds a banner, appends it to a page and stops has written a notice that every
text assertion can find and nobody can see - the assertion passes against a
panel that renders an empty strip where the warning should be.

This is tested on the shared helper rather than on one panel, because that is
where the fix is and that is where the mistake has to be prevented: a
per-panel test covers the panel it names and silently leaves every other caller
free to repeat it. Before the fix, walking every panel that calls `banner()`
found `diagnostics` and `memory` building one and rendering nothing, while
`machine` - the only caller with its own local `_revealed()` - was correct.

The negative control is the point. `Adw.Banner` is constructed twice here: once
left as the library builds it, and once through the helper. If the control
cannot fail, the assertion it supports proves nothing - and a test that cannot
fail is exactly how the original bug stayed invisible.
"""

from __future__ import annotations

from typing import List

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from shani_chronoa.gui.surfaces import common  # noqa: E402


def _tree(widget: Gtk.Widget) -> List[Gtk.Widget]:
    """Every widget in the built tree, in order."""
    found: List[Gtk.Widget] = [widget]
    child = widget.get_first_child()
    while child is not None:
        found.extend(_tree(child))
        child = child.get_next_sibling()
    return found


def _banners(widget: Gtk.Widget) -> List[Adw.Banner]:
    return [node for node in _tree(widget) if isinstance(node, Adw.Banner)]


class TestBannerIsRevealed:
    def test_a_bare_adw_banner_starts_hidden(self):
        """The negative control: the thing this file guards against is real.

        If a future libadwaita ever made `Adw.Banner` visible by default, every
        other test in this file would start passing for a reason that has
        nothing to do with the fix, and the regression they exist to catch could
        come back unnoticed. Asserting the failure mode here is what keeps the
        rest of the file honest.
        """
        assert Adw.init() is None or True  # libadwaita initialises idempotently
        assert common.adw_ready(), "the Adw path is not reachable, so nothing below is proven"
        control = Adw.Banner(title="control")
        assert control.get_revealed() is False, (
            "Adw.Banner now starts revealed; the regression this file tests for "
            "is no longer possible and the assertions below prove nothing"
        )

    def test_the_helper_returns_a_revealed_banner(self):
        assert common.adw_ready()
        widget = common.banner("Something is true and worth saying.")
        banners = _banners(widget)
        assert len(banners) == 1, f"expected one banner, got {len(banners)}"
        assert banners[0].get_revealed() is True, (
            "a banner that is in the tree but not revealed renders nothing"
        )

    def test_the_text_survives_alongside_the_fix(self):
        """The reveal must not cost the notice its wording.

        A banner that renders an empty strip has fixed the reveal and lost the
        message, which is the same defect in the other direction.
        """
        assert common.adw_ready()
        widget = common.banner("3 of 16 subsystems working.")
        assert _banners(widget)[0].get_title() == "3 of 16 subsystems working."

    def test_a_requested_button_is_built_and_actually_clickable(self):
        """`Adw.Banner` cannot hold a button, so this path must not be one.

        `Adw.Banner.add_button` does not exist on libadwaita 1.5 and the class
        carries no signals, so the only way to honour a button request is to
        build something that has one. The old code called `add_button`
        unconditionally whenever a label and a callback were passed, which
        raised `AttributeError` and took the panel's whole build with it.

        Asserted by clicking it rather than by checking the widget exists: a
        button that is present but not wired is the same defect this file is
        about, one layer down.
        """
        assert common.adw_ready()
        clicked: List[bool] = []
        widget = common.banner("Turn it on.", "Turn it on", lambda: clicked.append(True))
        buttons = [
            node for node in _tree(widget)
            if isinstance(node, Gtk.Button) and node.get_label() == "Turn it on"
        ]
        assert len(buttons) == 1, f"expected one 'Turn it on' button, got {len(buttons)}"
        assert buttons[0].get_sensitive(), "the button is there and cannot be pressed"
        buttons[0].emit("clicked")
        assert clicked == [True], "the button was clicked and the callback never ran"

    def test_a_buttonless_request_still_takes_the_adw_path(self):
        """The Adw path is kept for the common case, not abandoned.

        Losing it would mean every panel silently fell back to plain GTK, so
        this pins the branch that is actually taken when no button is asked for.
        """
        assert common.adw_ready()
        widget = common.banner("No action needed.")
        assert _banners(widget), "the Adw path was not taken for a buttonless banner"


class TestEveryBannerCallerReveals:
    """The panels, not the panels' names.

    Each of these builds a real banner on this machine today. The assertion is
    the same one for all of them, because the bug was never specific to a
    panel: it was the helper, and every caller inherited it.
    """

    def test_the_diagnostics_headline_is_visible(self):
        """The sentence someone opens that panel for.

        Built here with `app=None` on purpose - the panel accepts the argument
        and never uses it, so this exercises the real widget tree without any
        stub to keep honest.
        """
        from shani_chronoa.gui.surfaces import diagnostics

        assert common.adw_ready()
        banners = _banners(diagnostics.build(None))
        assert banners, "the diagnostics panel built no banner at all"
        for banner in banners:
            assert banner.get_revealed() is True, (
                "the health summary was in the tree and not on screen"
            )

    def test_a_plain_panel_reaches_the_helper_at_all(self):
        """A cheap guard that the module under test is the one being exercised.

        Without this, an import that silently resolved to a different `common`
        would leave the tests above passing against the wrong code.
        """
        from shani_chronoa.gui.surfaces import diagnostics

        assert diagnostics.common is common