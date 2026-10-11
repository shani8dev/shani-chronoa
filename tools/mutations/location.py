"""Mutation set for the coordinate checks: `senses/location.py` and the diff row.

Ported from a `/tmp` harness on 2026-10-11 into the repository, so the commit that
says "14 of 14 confirmed to fail" cites something a fresh checkout can re-run.

**One partial on the first run, and it was an equivalent mutant by construction.**
Dropping the int check leaves every *float* fix passing, so
`test_a_real_fix_still_passes` is not a witness for it; the int case is, and that
is the one asserted. The harness reports the partial as `not caught by` rather
than hiding it.
"""

TEST_FILE = "tests/test_location_coordinates.py"

_LOC = "usr/lib/shani-chronoa/shani_chronoa/senses/location.py"
_DIFF = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/diff.py"

MUTATIONS = [
    # -- the shared predicate's own checks ------------------------------------
    # **Two of these were written against the code before `8e8e96c` moved the
    # finite check into `sysfs.finite`.** A port that copies anchors from a stale
    # harness asserts on the write - the count is zero - and reports an error
    # rather than a survivor, which is what happened here. The finite-check copies
    # now live in `tools/mutations/finite.py` against the right test file.
    {
        "file": _LOC,
        "old": "    return float(value) if sysfs.finite(value) else None\n",
        "new": "    return float(value) if isinstance(value, (int, float)) else None\n",
        "label": "location stops consulting the shared finite check",
        "expect": ["test_a_number_that_is_not_a_place_is_not_one"],
    },
    {
        "file": _LOC,
        "old": "    if abs(flat) > MAX_LATITUDE or abs(flon) > MAX_LONGITUDE:\n        return None\n",
        "new": "",
        "label": "the on-the-planet check removed (999 is accepted again)",
        "expect": ["test_a_number_that_is_not_a_place_is_not_one",
                   "test_a_fix_outside_the_planet_is_refused_by_name",
                   "test_geoclue_coordinates_are_checked_too"],
    },
    {
        "file": _LOC,
        "old": "    return float(value) if sysfs.finite(value) else None\n",
        "new": "    if isinstance(value, bool):\n        return None\n"
               "    return float(value) if isinstance(value, (int, float)) else None\n",
        "label": "only a NaN survives the type check (the bool refusal is local again)",
        # **Not `test_a_real_fix_still_passes`:** with the type check narrowed every
        # float fix still passes, so that mutation is equivalent for it.
        "expect": ["test_a_number_that_is_not_a_place_is_not_one"],
    },
    {
        "file": _LOC,
        "old": "                return None, (f\"gpsd sent a fix that is not a position \"\n"
               "                              f\"(lat={msg.get('lat')!r}, lon={msg.get('lon')!r})\")\n",
        "new": "                return None, \"\"\n",
        "label": "the refusal no longer names what the device sent",
        "expect": ["test_a_fix_outside_the_planet_is_refused_by_name"],
    },
    {
        "file": _LOC,
        "old": "            acc = _coordinate(msg.get(\"eph\")) if msg.get(\"eph\") is not None else None\n"
               "            acc = _coordinate(msg.get(\"epx\")) if acc is None else acc\n",
        "new": "            acc = float(msg.get(\"eph\") or 0) or None\n",
        "label": "accuracy goes back to float() (a string accuracy raises)",
        "expect": ["test_an_unreadable_accuracy_is_none_not_a_crash"],
    },
    {
        "file": _LOC,
        "old": "    where = _on_the_planet(lat, lon)\n"
               "    if where is None:\n"
               "        # GeoClue's `where-am-i` prints whatever its source produced, and a\n"
               "        # source can be wrong in a way the regex still captures happily.\n"
               "        return None, f\"GeoClue gave a position that is not on the planet ({lat}, {lon})\"\n",
        "new": "    where = (lat, lon)\n",
        "label": "GeoClue's coordinates are unchecked",
        "expect": ["test_geoclue_coordinates_are_checked_too"],
    },

    # -- the diff file row ------------------------------------------------------
    {
        "file": _DIFF,
        "old": "        self.set_focusable(True)\n",
        "new": "        self.set_focusable(False)\n",
        "label": "the row is not focusable again",
        "expect": ["test_the_row_can_take_focus",
                   "test_every_file_in_the_list_is_focusable"],
    },
    {
        "file": _DIFF,
        "old": "        self.update_property([Gtk.AccessibleProperty.LABEL],\n"
               "                             [f\"{file.path.name}, {stats}\"])\n",
        "new": "",
        "label": "no accessible label",
        "expect": ["test_the_row_has_a_label_naming_the_file"],
    },
    {
        "file": _DIFF,
        "old": "        self.set_accessible_role(Gtk.AccessibleRole.BUTTON)\n",
        "new": "",
        "label": "no accessible role",
        "expect": ["test_the_row_claims_the_button_role"],
    },
    {
        "file": _DIFF,
        "old": "        keys = Gtk.EventControllerKey()\n"
               "        keys.connect(\"key-pressed\", self._on_key)\n"
               "        self.add_controller(keys)\n",
        "new": "",
        "label": "the key controller removed (Enter and Space do nothing)",
        "expect": ["test_enter_selects_the_same_thing_a_click_does",
                   "test_the_keypad_enter_selects_too",
                   "test_space_selects_too"],
    },
    {
        "file": _DIFF,
        "old": "        if keyval not in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):\n"
               "            return False\n",
        "new": "        if False:\n            return False\n",
        "label": "every key selects the row (the shortcut path is swallowed)",
        "expect": ["test_an_ordinary_key_is_not_swallowed"],
    },
    {
        "file": _DIFF,
        "old": "        if self.on_select:\n            self.on_select(self._file)\n        return True\n",
        "new": "        return True\n",
        "label": "the key handler claims the press but does nothing",
        "expect": ["test_enter_selects_the_same_thing_a_click_does",
                   "test_the_keypad_enter_selects_too",
                   "test_space_selects_too"],
    },
    {
        "file": _DIFF,
        "old": "        click = Gtk.GestureClick()\n"
               "        click.set_button(1)\n"
               "        click.connect(\"pressed\", self._on_click)\n"
               "        self.add_controller(click)\n",
        "new": "",
        "label": "the mouse path removed while adding the keyboard one",
        "expect": ["test_the_mouse_path_still_works"],
    },
]
