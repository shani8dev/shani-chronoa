"""A coordinate that is not on the planet, and a row a keyboard cannot reach.

Two transfers from `harness-study/open-intelligent-ui`, both found by reading
the files the first six passes never opened:

**`map-utils.ts`'s `coordinate()`** checks a pin a *model* supplied:

```ts
return typeof lat === "number" && typeof lng === "number"
    && Number.isFinite(lat) && Number.isFinite(lng)
    && Math.abs(lat) <= 90 && Math.abs(lng) <= 180 ? [lat, lng] : null;
```

Chronoa reads coordinates from *devices* - `senses/location.py` parses gpsd's
JSON and GeoClue's text - and checked nothing at all. Measured on the real
parser before this change, driving `_from_gpsd` with the JSON gpsd emits:

| gpsd sends | before | after |
|---|---|---|
| `"lat": 999.0` | **ACCEPTED** as 999.0 | refused, reason names the value |
| `"lat": NaN` | **ACCEPTED** as `nan` | refused |
| `"lat": "18.5"` (a string) | **ACCEPTED**, coerced | refused |
| `"lat": true` (a bool) | **ACCEPTED** as 1.0 | refused |
| `"lat": null` | **`TypeError` out of the sense** | refused |

The last one is the serious one. gpsd's own man page says *"lat, lon: numeric,
may be null"*, and `locate()` calls `_from_gpsd()` with no guard, so a TPV with
`mode >= 2` and a null lat raised `TypeError: float() argument must be a string
or a real number, not 'NoneType'` out of `get_location`, `weather` and `maps` -
the question about where you are, answered with a traceback.

**`createPinElement`'s two entry points** - `tabIndex = 0`, `role = "button"`,
and *both* `click` and `keydown` (Enter/Space, `preventDefault` on the latter)
calling the same `onSelect`: *"A focusable pin with a label underneath."* The
Diff panel's file list is a `Gtk.Box` holding `Gtk.Box`es with a
`Gtk.GestureClick` and nothing else, so selecting which file you were looking at
was a pointer-only control - measured on the built row, `get_focusable()` was
False and the only controller was the GestureClick, while the Accept and Reject
buttons *inside* the same row were focusable. A keyboard user could act on a
file they could not choose.

Everything here is asserted on the built widget or the real parser, never on a
copy of the logic.
"""

from __future__ import annotations

import json
import sys

sys.path.insert(0, "usr/lib/shani-chronoa")

import pathlib  # noqa: E402

import pytest  # noqa: E402

from shani_chronoa.senses import location as loc  # noqa: E402

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk  # noqa: E402


def rows_label_name(label):
    """The filename out of a row's accessible label.

    `f"{path.name}, {added} added, {removed} removed"` - so the first
    comma-separated field is the name. A path containing a comma (a real thing)
    is truncated, which is the same thing the visible label already does, so
    the two agree rather than one naming more than the other.
    """
    return str(label).split(", ")[0]



def _proc(text, rc=0):
    class _P:
        returncode = rc
        stdout = text
        stderr = ""
    return _P()


class TestACoordinateIsACoordinate:
    """The allowlist itself, so a future reader cannot quietly widen it."""

    @pytest.mark.parametrize("lat,lon", [
        (18.5204, 73.8567), (0.0, 0.0), (-33.87, 151.21), (90.0, -180.0),
        (-90.0, 180.0), (89.9999, -179.9999),
    ])
    def test_a_real_position_is_one(self, lat, lon):
        assert loc._on_the_planet(lat, lon) == (lat, lon)

    @pytest.mark.parametrize("lat,lon", [
        (999.0, 73.8567),      # a latitude nobody is at
        (18.5, 300.0),         # a longitude past the date line
        (-91.0, 0.0),
        (0.0, 181.0),
        ("18.5", 73.8),        # a JSON string, coerced by float()
        (True, 73.8),          # a boolean, coerced to 1.0
        (None, 73.8567),       # gpsd's own "may be null"
        ([18.5], 73.8),
        (float("nan"), 73.8),
        (float("inf"), 73.8),
        (18.5, float("-inf")),
    ])
    def test_a_number_that_is_not_a_place_is_not_one(self, lat, lon):
        """Each of these was accepted before this change, or crashed."""
        assert loc._on_the_planet(lat, lon) is None

    def test_an_int_is_a_number(self):
        """`18` is not a string and not a bool, and `isinstance(x, int)` holds."""
        assert loc._on_the_planet(18, 73) == (18.0, 73.0)


class TestADeviceThatSendsANonPosition:
    """The behaviour that reaches a person: a sentence, never a crash.

    The whole point is `locate()`'s loop - a source that refuses moves on to the
    next one, so a broken gpsd hands the question to GeoClue instead of ending
    it.
    """

    def _gpsd(self, monkeypatch, report):
        monkeypatch.setattr(loc.shutil, "which", lambda b: "/usr/bin/" + b)
        monkeypatch.setattr(loc.subprocess, "run", lambda *a, **k: _proc(report))

    def test_a_fix_outside_the_planet_is_refused_by_name(self, monkeypatch):
        self._gpsd(monkeypatch, json.dumps(
            {"class": "TPV", "mode": 3, "lat": 999.0, "lon": 73.8567}))
        pos, why = loc._from_gpsd()
        assert pos is None
        assert "not a position" in why and "999.0" in why, why
        # The numbers are in the reason, because a reason without them sends
        # somebody to look at a receiver for a value nobody can quote.
        assert "73.8567" in why

    def test_a_null_latitude_no_longer_raises(self, monkeypatch):
        """**This one raised `TypeError` before.** It is gpsd's own allowed
        shape, so "should not happen" is not a guard."""
        self._gpsd(monkeypatch, json.dumps(
            {"class": "TPV", "mode": 3, "lat": None, "lon": 73.8567}))
        pos, why = loc._from_gpsd()
        assert pos is None and "not a position" in why, why

    def test_a_string_latitude_is_refused(self, monkeypatch):
        self._gpsd(monkeypatch, json.dumps(
            {"class": "TPV", "mode": 3, "lat": "18.5204", "lon": 73.8567}))
        pos, why = loc._from_gpsd()
        assert pos is None and "not a position" in why, why

    def test_a_nan_is_refused_rather_than_rendered_as_nan(self, monkeypatch):
        """`float("NaN")` succeeds, so the old code built an Overpass query
        reading `around:5000,nan,nan` - which returns nothing and reads as
        "there is no petrol pump near you"."""
        self._gpsd(monkeypatch, '{"class":"TPV","mode":3,"lat":NaN,"lon":73.8567}')
        pos, why = loc._from_gpsd()
        assert pos is None and "not a position" in why, why

    def test_a_real_fix_still_passes(self, monkeypatch):
        self._gpsd(monkeypatch, json.dumps(
            {"class": "TPV", "mode": 3, "lat": 18.5204, "lon": 73.8567, "eph": 5.0}))
        pos, why = loc._from_gpsd()
        assert pos[:2] == (18.5204, 73.8567) and pos[3] == "GPS receiver (gpsd)"
        assert pos[2] == 5.0

    def test_an_unreadable_accuracy_is_none_not_a_crash(self, monkeypatch):
        """Accuracy is a nicety; `"eph": "about 5"` must not cost the fix."""
        self._gpsd(monkeypatch, json.dumps(
            {"class": "TPV", "mode": 3, "lat": 18.5, "lon": 73.8, "eph": "about 5"}))
        pos, why = loc._from_gpsd()
        assert pos[:2] == (18.5, 73.8) and pos[2] is None

    def test_a_refusing_gpsd_hands_the_question_to_geoclue(self, monkeypatch):
        """`locate()` tries its next source on a refusal. That is the whole
        reason a refusal is a return value and not an exception."""

        def run(argv, **k):
            if argv[0] == "gpspipe":
                return _proc(json.dumps({"class": "TPV", "mode": 3, "lat": 999.0,
                                         "lon": 73.8567}))
            return _proc("Latitude:    19.076000°\nLongitude:   72.877700°\n"
                         "Accuracy:    1500.000000 meters\nDescription: WiFi\n")

        monkeypatch.setattr(loc.shutil, "which", lambda b: "/usr/bin/" + b)
        monkeypatch.setattr(loc.os, "access", lambda p, m: True)
        monkeypatch.setattr(loc.subprocess, "run", run)
        pos, why = loc.locate()
        assert pos is not None and pos[:2] == (19.076, 72.8777), (
            "a gpsd reply that is not a position ended the question instead of "
            "moving on to GeoClue")

    def test_geoclue_coordinates_are_checked_too(self, monkeypatch):
        """The regex captures what the text says, and a source can be wrong in
        a way a regex still matches."""
        monkeypatch.setattr(loc.shutil, "which", lambda b: None)
        monkeypatch.setattr(loc.os, "access", lambda p, m: True)
        monkeypatch.setattr(loc.subprocess, "run", lambda *a, **k: _proc(
            "Latitude:    999.000000°\nLongitude:   73.856700°\n"))
        pos, why = loc._from_geoclue()
        assert pos is None and "not on the planet" in why, why


class TestTheFileRowIsReachableByKeyboard:
    """The Diff panel's file list, which only a pointer could reach."""

    def _row(self, name="a.txt", status="modified", applied=True):
        from gi.repository import Adw

        Adw.init()
        from shani_chronoa.gui.surfaces import diff as diff_mod
        f = diff_mod.DiffFile(path=pathlib.Path(name), status=status, hunks=(),
                              selected_line_ids=set())
        row = diff_mod._DiffFileItem(f, applied=applied)
        seen = []
        row.on_select = lambda f: seen.append(str(f.path))
        return diff_mod, row, seen

    def _key_controller(self, row):
        controllers = [c for c in self._controllers(row)
                       if type(c).__name__ == "EventControllerKey"]
        assert controllers, "no key controller on the row, so Enter does nothing"
        return controllers[0]

    def _controllers(self, row):

        out = []

        def walk(w):
            out.extend(w.observe_controllers() or [])
            c = w.get_first_child()
            while c is not None:
                walk(c)
                c = c.get_next_sibling()
        walk(row)
        return out

    def test_the_row_can_take_focus(self):
        """**`get_focusable()` was False before this change**, and a widget
        that cannot take focus is a widget Tab cannot reach."""
        _mod, row, _seen = self._row()
        assert row.get_focusable() is True, (
            "the diff file list is unreachable without a mouse; only the "
            "Accept/Reject buttons inside each row are focusable")

    def test_the_row_claims_the_button_role(self):

        _mod, row, _seen = self._row()
        # `Gtk.AccessibleProperty.ROLE` does not exist - probed on the
        # installed library - so the role is its own setter, and `role` is
        # what an assistive technology announces.
        assert row.get_accessible_role() == Gtk.AccessibleRole.BUTTON

    def test_the_row_has_a_label_naming_the_file(self, monkeypatch):
        """The label is what a screen reader announces instead of silence.

        **There is no getter.** `Gtk.Widget` exposes `get_accessible_role` and
        nothing for properties - `get_accessible_property` does not exist on
        this PyGObject, and this file records a probe that once read every
        accessible name as `""` for exactly that reason. So the call itself is
        what is asserted: `update_property` is recorded at the class level
        before the row is built, which is the only place it can be caught.

        A label is load-bearing here rather than decorative: the row is a Box
        holding a filename, a stats label and two buttons, so without one a
        screen reader announces three fragments and no idea what they are for.
        """

        from shani_chronoa.gui.surfaces import diff as diff_mod
        recorded = []
        real = Gtk.Widget.update_property

        def recorder(self, properties, values):
            recorded.append((list(properties), list(values)))
            return real(self, properties, values)

        monkeypatch.setattr(Gtk.Widget, "update_property", recorder)
        # The basename, because that is what the visible label says - an
        # accessible name that spells the whole path when the screen says
        # "fstab" is a second naming of the same thing.
        f = diff_mod.DiffFile(path=pathlib.Path("/etc/fstab"), status="modified",
                              hunks=(), selected_line_ids=set())
        diff_mod._DiffFileItem(f, applied=True)
        monkeypatch.setattr(Gtk.Widget, "update_property", real)

        # `update_property(properties, values)` takes two parallel lists, so a
        # value sits beside its property rather than inside it.
        labels = [value for properties, values in recorded
                 for value, prop in zip(values, properties)
                 if prop == Gtk.AccessibleProperty.LABEL]
        assert labels, (
            f"no accessible label was set on the file row; the calls made were "
            f"{recorded!r}")
        assert rows_label_name(labels[0]) == "fstab", labels

    def test_enter_selects_the_same_thing_a_click_does(self):
        from gi.repository import Gdk

        _mod, row, seen = self._row()
        keys = [c for c in self._controllers(row)
                if type(c).__name__ == "EventControllerKey"]
        assert keys, "no key controller on the row, so Enter does nothing"
        keys[0].emit("key-pressed", Gdk.KEY_Return, 0, 0)
        assert seen == ["a.txt"], f"Enter selected {seen!r}"

    def test_the_keypad_enter_selects_too(self):
        from gi.repository import Gdk

        _mod, row, seen = self._row()
        keypad = self._key_controller(row)
        keypad.emit("key-pressed", Gdk.KEY_KP_Enter, 0, 0)
        assert seen == ["a.txt"]

    def test_space_selects_too(self):
        from gi.repository import Gdk

        _mod, row, seen = self._row()
        self._key_controller(row).emit("key-pressed", Gdk.KEY_space, 0, 0)
        assert seen == ["a.txt"]

    def test_an_ordinary_key_is_not_swallowed(self):
        """A controller that returns True for everything would eat every key
        press in the panel - the shortcuts, the arrows and the search box."""
        from gi.repository import Gdk

        _mod, row, seen = self._row()
        controller = self._key_controller(row)
        for keyval in (Gdk.KEY_t, Gdk.KEY_Down, Gdk.KEY_Tab, Gdk.KEY_Escape):
            controller.emit("key-pressed", keyval, 0, 0)
        assert seen == [], f"an unrelated key selected the row: {seen!r}"

    def test_the_mouse_path_still_works(self):

        _mod, row, seen = self._row()
        click = [c for c in self._controllers(row)
                 if type(c).__name__ == "GestureClick"]
        assert click, "the mouse path was removed while adding the keyboard one"
        row._on_click(None, 1, 0.0, 0.0)
        assert seen == ["a.txt"]

    def test_every_file_in_the_list_is_focusable(self):
        """The file list is a `Gtk.Box` of `Gtk.Box`es, not a `Gtk.ListBox`,
        so nothing about the container gives any of them focus for free."""

        from shani_chronoa.gui.surfaces import diff as diff_mod
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for name, status in (("a.txt", "modified"), ("b.txt", "added"), ("c.txt", "deleted")):
            f = diff_mod.DiffFile(path=pathlib.Path(name), status=status, hunks=(),
                                  selected_line_ids=set())
            row = diff_mod._DiffFileItem(f, applied=True)
            box.append(row)
        focusable = []

        def walk(w):
            if w.get_focusable():
                focusable.append(w)
            c = w.get_first_child()
            while c is not None:
                walk(c)
                c = c.get_next_sibling()
        walk(box)
        assert len(focusable) == 3, (
            f"{len(focusable)} of 3 file rows are focusable; before this change "
            "the whole list was pointer-only")
