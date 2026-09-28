"""The main window must take its chrome from the desktop theme.

`_apply_css` hardcoded a dark window (`background-color: #14141f`) plus seven
hand-picked foregrounds, so a user on a light GTK theme got a dark dialog whose
white borders and halo were invisible against it. The theme is the user's, and
GTK4 exposes it as named palette entries - which is also the only version that
looks right in high-contrast.

The orb's state palette is the deliberate exception: green/amber/blue/violet/
red are not decoration, they are how the assistant says what it is doing, and a
theme cannot supply "thinking". Colour there is reinforcement, not the only
channel - the state icon and the state text carry it too.

This reads the CSS out of the module rather than reconstructing it, because a
test asserting against its own copy of the stylesheet would keep passing after
the stylesheet changed.
"""

import ast
import pathlib
import re

import pytest

GUI = pathlib.Path("usr/lib/shani-chronoa/shani_chronoa/gui.py")

# The orb's states. Listed explicitly so that ADDING one is a deliberate act
# that has to update this list, rather than a colour quietly slipping through.
STATE_PALETTE = {"#22c55e", "#f59e0b", "#3b82f6", "#a855f7", "#ef4444"}


def _css() -> str:
    source = GUI.read_text()
    start = source.index('css_data = b"""') + len('css_data = b"""')
    end = source.index('"""', start)
    return source[start:end]


def test_the_module_is_readable():
    assert GUI.exists()


class TestChromeFollowsTheTheme:
    def test_no_fixed_window_background(self):
        """.cajita-window{background-color:#14141f} is the single line that
        made a light theme unusable. The window has to inherit."""
        assert ".cajita-window" not in _css()

    @pytest.mark.parametrize("colour", [
        "#14141f", "#e6e6ef", "#9aa0b4", "#1e1e30",
        "#23233a", "#f2f2f7", "#6b7280", "#34344d",
    ])
    def test_no_hardcoded_chrome_colour_survives(self, colour):
        assert colour not in _css(), (
            f"{colour} is a fixed theme colour; the desktop theme owns this"
        )

    def test_no_white_with_alpha_left(self):
        """rgba(255,255,255,...) was the orb's hover glow and the idle halo -
        invisible on a light background, which is exactly when the user most
        needs to see that something is listening."""
        assert "rgba(255,255,255" not in _css()

    def test_chrome_uses_named_theme_palette_entries(self):
        css = _css()
        for name in ("@theme_fg_color", "@theme_base_color"):
            assert name in css, f"{name} is how a widget asks the theme for a colour"

    def test_tints_are_expressed_as_alpha_of_the_foreground(self):
        """One rule that works on light and dark beats a second rule that
        picks the other background."""
        assert len(re.findall(r"alpha\(", _css())) >= 5


class TestStatePaletteIsDeliberate:
    def test_only_the_orb_states_are_hardcoded(self):
        stray = set(re.findall(r"#[0-9a-fA-F]{6}", _css())) - STATE_PALETTE
        assert not stray, f"unexpected hardcoded colours: {sorted(stray)}"

    def test_every_active_state_has_an_orb_rule(self):
        """Each active state needs a class the widget actually sets, or it
        shows no colour at all.

        IDLE is excluded on purpose: it is the *base* `.chronoa-orb`
        appearance, and the stylesheet says so - idle being the one state with
        no glow is what makes every active state read as active even when the
        transition is a single frame. Adding a `.state-idle {}` rule to satisfy
        a stricter-looking test would be dead CSS that asserts nothing.
        """
        import sys

        sys.path.insert(0, "usr/lib/shani-chronoa")
        from shani_chronoa.gui import AssistantState

        css = _css()
        active = [s for s in AssistantState if s is not AssistantState.IDLE]
        assert active, "the enum lost every active state"
        for state in active:
            slug = state.name.lower()
            # Anchored on the selector, not a substring: "state-thinking" is
            # found inside "state-thinkingXX", so a plain `in` check passes
            # against a rule that no longer matches anything.
            assert re.search(rf"\.chronoa-orb\.state-{re.escape(slug)}\s*\{{", css), (
                f"AssistantState.{state.name} has no .state-{slug} rule, so the "
                f"orb shows no colour for it"
            )

    def test_idle_relies_on_the_base_appearance(self):
        """Pins the asymmetry the stylesheet documents, so the next person does
        not 'fix' idle into an explicit rule and lose the flat-by-default
        reading of it."""
        css = _css()
        assert ".state-idle" not in css
        assert ".chronoa-orb {" in css, "the base orb rule idle falls back to"

    def test_every_halo_variant_tracks_a_state(self):
        """The halo rings the orb; if its border colour lagged the orb's, the
        two would disagree about what is happening."""
        css = _css()
        for state in re.findall(r"\.chronoa-orb\.state-([a-z]+)", css):
            assert f".chronoa-halo.halo-{state}" in css, (
                f"state-{state} has no matching halo-{state} ring"
            )


class TestTheStylesheetIsValid:
    def test_gtk_accepts_every_declaration(self, capfd):
        """GTK4's CSS parser silently drops an invalid declaration, so a typo
        in alpha() loses the rule with no error and no test failure - the same
        failure mode as glib-compile-schemas discarding a whole schema file.

        GTK does report it ("Theme parser error: ... Expected a valid color"),
        but on stderr, and a GLib.log_set_handler for the Gtk domain does NOT
        intercept it - an earlier version of this test used one, collected
        nothing, and passed against a stylesheet GTK was rejecting. So this
        captures the real stream rather than trusting a handler that was
        silently not firing.
        """
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        Gtk.CssProvider().load_from_data(_css().encode())
        err = capfd.readouterr().err
        problems = [
            line for line in err.splitlines()
            if "Theme parser error" in line or "CSS" in line and "error" in line.lower()
        ]
        assert not problems, "GTK rejected declarations:\n" + "\n".join(problems[:5])

    def test_the_module_defines_no_stray_unused_css_function(self):
        """Guards the rewrite itself: the helper that reads the stylesheet out
        of the module has to keep finding the real one."""
        tree = ast.parse(GUI.read_text())
        assert any(
            isinstance(n, ast.FunctionDef) and n.name == "_apply_css"
            for n in ast.walk(tree)
        )
