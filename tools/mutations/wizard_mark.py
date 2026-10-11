"""Mutation set for the wizard's void mark and the processes panel's rename.

**"if void it should have some icon no?"** Measured against the 620px default
window, four of the sixteen wizard pages have far less content than it has room
for - `ears` 270px, `brain` 84px, `mode` 316px, `model-picker` 470px - so
between the last row and the pinned action there was 150 to 536px of nothing.

Four things this set pins, each of which was wrong in the first version and found
by running it:

1. **The mark is embedded, not looked up.** `Gtk.Image.new_from_icon_name` is a
   *theme lookup* and did not resolve from this checkout - the render came back
   as GTK's missing-image placeholder, **a gear**, which is what "some page had a
   gear icon" was reporting. Living in `usr/share/icons/hicolor/` is right for an
   installed system and useless for the tree the wizard is developed in.
2. **The unallocated-scroller case.** `upper <= page_size` is true when both are
   0, which is what a never-laid-out scroller reports - so the first version
   showed the mark on all sixteen pages, `review`'s 1029px of content in a 620px
   window included.
3. **The mark is not a control.** It is a picture over the scroller; if it can
   take a click, the space above a button becomes a dead target.
4. **The panel is `processes`, not `background`.** The first name collided with
   `shani_chronoa.background` - the module that owns the inventory it draws - so
   `from shani_chronoa.gui.surfaces import background` resolved to the surface
   itself and `background.inventory` did not exist.
"""

TEST_FILE = "tests/test_ui_layout_contract.py"

_W = "usr/lib/shani-chronoa/shani_chronoa/setup_wizard.py"
_P = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/processes.py"
_R = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/__init__.py"

MUTATIONS = [
    # -- 1. embedded, not looked up ------------------------------------------
    {
        "file": _W,
        "old": "    svg = Gtk.Svg.new_from_bytes(GLib.Bytes.new(_ORB_MARK_SVG))\n",
        "new": "    svg = None\n",
        "label": "**the gear, restored**: the mark is not built from the "
                 "embedded bytes",
        "expect": ["test_the_mark_is_rendered_from_embedded_bytes"],
    },
    {
        "file": _W,
        "old": "    b'<circle cx=\"56\" cy=\"56\" r=\"48\" fill=\"#3584e4\" "
               "fill-opacity=\"0.85\"/>'\n",
        "new": "    b'<circle cx=\"56\" cy=\"56\" r=\"48\" "
               "fill=\"#808080\"/>'\n",
        "label": "the mark is not Chronoa's blue, so it is not the app's own orb",
        "expect": ["test_the_mark_is_rendered_from_embedded_bytes"],
    },

    # -- 2. the unallocated scroller ----------------------------------------
    {
        "file": _W,
        "old": "    if adjustment.get_page_size() <= 1:\n        return False\n",
        "new": "",
        "label": "**the all-sixteen-pages bug, restored**: an unallocated "
                 "scroller reports room for the mark",
        # **Measured**: `upper = page_size = 0` before layout, so `0 <= 0 + 1`
        # is true and the mark appeared over `review`'s overflowing content.
        "expect": ["test_the_mark_is_shown_exactly_when_the_content_cannot_scroll"],
    },
    {
        "file": _W,
        "old": "    return adjustment.get_upper() <= adjustment.get_page_size() + 1\n",
        "new": "    return True\n",
        "label": "the mark is always shown, so it is drawn over a row",
        "expect": ["test_the_mark_is_shown_exactly_when_the_content_cannot_scroll",
                   "test_the_rule_is_what_hides_it_on_a_long_page"],
    },
    {
        "file": _W,
        "old": "    return adjustment.get_upper() <= adjustment.get_page_size() + 1\n",
        "new": "    return False\n",
        "label": "the mark is never shown, so the void is back",
        "expect": ["test_the_mark_is_shown_exactly_when_the_content_cannot_scroll"],
    },

    # -- 3. it is a picture, not a control ----------------------------------
    {
        "file": _W,
        "old": "    picture.set_can_target(False)\n",
        "new": "",
        "label": "the mark can take a click, so the space above a button is a "
                 "dead target",
        "expect": ["test_a_short_page_has_a_mark_and_it_is_not_a_control"],
    },
    {
        "file": _W,
        "old": "    picture.set_focusable(False)\n",
        "new": "",
        "label": "the mark is focusable, so a keyboard user stops on a picture",
        # **Equivalent, and measured rather than assumed.** A `Gtk.Picture` is not
        # focusable by default, so removing the line changes no observable - the
        # first run of this set reported it as a survivor, and the truth is that
        # no input distinguishes the two. Kept with the reason, because the line
        # is defence against the default changing.
        "expect": ["test_a_short_page_has_a_mark_and_it_is_not_a_control"],
        "equivalent": True,
        "why": "Gtk.Picture.get_focusable() is already False, so removing the "
               "setter changes nothing observable",
    },

    # -- 4. the panel's name does not collide --------------------------------
    {
        "file": _R,
        "old": '    "processes",\n',
        "new": '    "background",\n',
        "label": "**the name collision, restored**: the surface shadows the "
                 "module that owns the inventory it draws",
        # `from shani_chronoa.gui.surfaces import background` inside it resolved
        # to itself, so `background.inventory` did not exist and the whole
        # layout suite came back as errors.
        #
        # **The witness is the surfaces, not the wizard pages.** The first version
        # named `test_no_wizard_page_needs_a_sideways_scrollbar`, which passes -
        # it only ever builds the wizard. What breaks is every test that walks the
        # surfaces: `test_every_surface_can_scroll` is parametrized, so the prefix
        # matches `[processes]` as it errors.
        "expect": ["test_every_surface_can_scroll",
                   "test_every_surface_icon_exists_on_this_machine"],
    },
    {
        "file": _P,
        "old": "        scroller.set_child(self.root)\n        self._set_content(scroller)\n",
        "new": "        self._set_content(self.root)\n",
        "label": "the panel has no scroller, so its last Stop button is "
                 "unreachable on a short window",
        # The `inventory` panel bug this repo records, in a panel that starts
        # short and grows as more components are added.
        "expect": ["test_every_surface_can_scroll"],
    },
]
