"""The notch has to be *reachable*: the Shell must offer what the app calls,
and the app must be able to get a companion without knowing which it got.

`mascot.available()` is `False` on every GNOME machine, because Mutter has
never implemented `zwlr_layer_shell_v1`. So the GNOME Shell extension
(`chronoa-mascot@shani.dev`) is not a nicety - it is the only surface this
desktop can show. That makes three ordinary-looking facts load-bearing, and
each is a thing that can rot silently:

- **the interface has the methods the client calls.** `mascot_notch` reaches the
  Shell over `org.shani.Chronoa.Mascot`. A method that is in the Python client
  and not in the Shell's `IFACE` is not a feature that degrades; it is a
  `call_sync` that returns `None` forever, and `_push_art()` is the one place
  that would notice.
- **the strip is tall enough to hold the sprite.** `NOTCH_H` is *derived*
  (`SIZE + 2 * NOTCH_PAD`), which is the shape that cannot drift - but only if
  the derivation is still there, and a typed `NOTCH_H = 154` would be a number
  nobody re-measures.
- **`metadata.json` names a settings schema.** This is what lets
  `extension.getSettings()` read the app's own `mascot-corner` key rather than
  carrying a second copy, and it is what lets the recording harness compile the
  schema at all. `metadata.json` with no `settings-schema` is not a smaller
  manifest; it is a `getSettings()` that throws on `enable()`.

And the seam itself: `mascot.create(app)` is the **only** construction site,
and both classes answer the same surface. The reason this needs a test is the
exact bug the seam was written to remove - `application.py` used to ask
"is there a `_win`?", which is a question about the window rather than about
the companion, and is unanswerable for a surface that has no window.

**Five of these checks are witnesses the mutation runner cannot measure**, and
`TestEachDroppedMutationWouldHaveBeenCaught` at the foot of this file is what
carries them: `tools/mutate.py`'s `_apply` runs `ast.parse` over the whole
spliced file, so a mutation aimed at `extension.js` can never pass its guard -
line 1 is a `//` comment and the apostrophe in *Chronoa's* opens a Python string
that never closes. **A dropped mutation is a witness nobody has measured**, which
is the same failure as one that survives, so each of the five facts is planted
into a scratch copy here and the real check is required to reject it. What that
establishes is narrower than a verdict and is stated as such: the runner still
cannot measure these five.
"""

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import mascot, mascot_notch  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
EXT = REPO / "usr/share/gnome-shell/extensions/chronoa-mascot@shani.dev"
EXTENSION_JS = EXT / "extension.js"
METADATA = EXT / "metadata.json"
APPLICATION = REPO / "usr/lib/shani-chronoa/shani_chronoa/app/application.py"

#: What `mascot_notch` actually calls over the bus. Kept as literals rather
#: than derived from the client, because the whole claim is that the two agree -
#: a set built from the client's own constants would be satisfied by any rename
#: on both sides at once.
CALLED_METHODS = ("Status", "Notch", "PanelHeight", "Art", "Rect", "Pointer", "Show", "Scale")


def _js_constants() -> dict:
    """The numeric constants in `extension.js`, read out of the file.

    Parsed with a pattern rather than imported, because this is GJS and there is
    no GJS here - and **read out rather than retyped**, which is the only reason
    the numbers below mean anything about the shipped file. A trailing `//`
    comment is allowed because several of these carry one, and a reader that
    refused to look past it would report the constant as missing rather than as
    declared.
    """
    text = EXTENSION_JS.read_text()
    out = {}
    for name in ("SIZE", "NOTCH_W", "NOTCH_PAD", "NOTCH_H", "MARGIN"):
        found = re.search(rf"^const {name} = ([^;]+);", text, re.M)
        assert found, f"extension.js no longer declares {name} in the shape this reads"
        out[name] = found.group(1).strip()
    return out


def _iface_methods() -> list:
    """The method names in the Shell's D-Bus interface XML."""
    text = EXTENSION_JS.read_text()
    start = text.index("const IFACE = `")
    end = text.index("`", start + len("const IFACE = `"))
    return re.findall(r'<method name="([^"]+)"', text[start:end])


# --------------------------------------------------------------------------
# The Shell offers what the app calls
# --------------------------------------------------------------------------

class TestTheShellOffersWhatTheAppCalls:
    def test_every_method_the_client_calls_is_in_the_shell_interface(self):
        offered = set(_iface_methods())
        missing = [m for m in CALLED_METHODS if m not in offered]
        assert not missing, (
            "mascot_notch calls methods the Shell's IFACE does not declare: "
            f"{missing}. GDBus refuses an undeclared method, so each of these "
            "is a call_sync that returns None forever.")

    def test_the_three_that_carry_the_notch_are_declared_with_the_right_arity(self):
        """`Status` writes, `Notch` and `PanelHeight` read - and each shape matters.

        `Status` is the only reason the strip shows *what Chronoa is doing*
        rather than the mascot alone, `Notch` is where the strip goes, and
        `PanelHeight` is what puts the strip below the top bar instead of under
        it. Arity is checked rather than presence because a method declared with
        the wrong signature is a `TypeError` inside the Shell, which the client
        sees as the same `None` as a missing one.
        """
        text = EXTENSION_JS.read_text()
        start = text.index("const IFACE = `")
        xml = text[start:text.index("`", start + len("const IFACE = `"))]

        def _shape(method: str) -> tuple:
            block = re.search(
                rf'<method name="{method}">(.*?)</method>', xml, re.S)
            assert block, f"{method} is not declared as a block in IFACE"
            args = re.findall(r'<arg type="(\w)" name="(\w+)" direction="(\w+)"',
                              block.group(1))
            return tuple((t, n, d) for t, n, d in args)

        assert _shape("Status") == (("s", "title", "in"),
                                    ("s", "detail", "in"))
        for reader in ("Notch", "PanelHeight"):
            shape = _shape(reader)
            assert shape, f"{reader} declares no arguments"
            assert all(d == "out" for _, _, d in shape), (
                f"{reader} must be a reader; a direction the client does not "
                f"expect makes its reply unreadable: {shape}")

    def test_the_scanner_can_report_an_absence_at_all(self):
        """The control for the scan above, and the reason it is not a formality.

        A needle that finds nothing reports the same clean result as one that
        works, so "no missing methods" is only worth reading if this scanner is
        known to be able to say a name is *not* there. The fabricated name is
        built at runtime, so it is not in this file's own source when the scan
        over `extension.js` runs - the `price`/`collapse` shape, where the
        scanner's needle is in its input and it therefore finds everything.
        """
        fabricated = "Panel" + "HeightV2"
        offered = _iface_methods()
        assert fabricated not in offered, (
            "a fabricated method name came back as declared, so the scan "
            "cannot tell present from absent and the test above means nothing")
        assert "PanelHeight" in offered, (
            "the control passed but the real method is not there either, so "
            "the scan is reading nothing at all")
        assert offered, "the interface declares no methods; the file moved"


# --------------------------------------------------------------------------
# The strip is tall enough, and is derived rather than typed
# --------------------------------------------------------------------------

class TestTheStripFitsTheMascot:
    def test_the_strip_is_at_least_as_tall_as_the_sprite(self):
        constants = _js_constants()
        assert constants["SIZE"] == str(mascot.SIZE), (
            f"extension.js draws {constants['SIZE']}px; mascot.SIZE is "
            f"{mascot.SIZE}. The recorder checks this too, but a mismatch here "
            "means the two files have drifted and neither test can say which "
            "is right.")
        numbers = {name: int(text) for name, text in constants.items()
                   if text.lstrip("-").isdigit()}
        # The arithmetic is the extension's own expression over the extension's
        # own numbers - re-derived here it would be a number about this file.
        height = eval(constants["NOTCH_H"], {"__builtins__": {}}, numbers)  # noqa: S307
        assert height >= numbers["SIZE"], (
            f"the strip is {height}px tall and the sprite is "
            f"{numbers['SIZE']}px, so the mascot is cropped by the surface "
            "meant to hold it")

    def test_the_height_is_the_sprite_plus_padding_on_both_edges(self):
        """The property that makes the number above survive a redraw.

        `NOTCH_H = SIZE + 2 * NOTCH_PAD` cannot go stale, because a change to
        either term moves it. A typed literal can, and would pass the `>=`
        assertion above while describing a strip that no longer fits.

        **Both halves are needed, and the first one alone was a survivor.** A
        first version asked only whether the expression *names* `SIZE` and
        `NOTCH_PAD`, and `SIZE + 2 * NOTCH_PAD + 12` - which mentions both, and
        is still tall enough - passed it. So the arithmetic is checked too:
        the padding is on **both** edges, which is why the height must be the
        sprite plus *twice* the padding and not any expression that happens to
        mention the right two names. That is a specification of the strip, not a
        copy of the line, so it does not rot when the line is rewritten.
        """
        constants = _js_constants()
        numbers = {name: int(text) for name, text in constants.items()
                   if text.lstrip("-").isdigit()}
        expression = constants["NOTCH_H"]
        assert "SIZE" in expression and "NOTCH_PAD" in expression, (
            f"NOTCH_H is {expression!r}, which no longer mentions the sprite "
            "and its padding. The strip is still tall enough today, but the "
            "size is now a number somebody has to remember updating.")
        height = eval(expression, {"__builtins__": {}}, numbers)  # noqa: S307
        expected = numbers["SIZE"] + 2 * numbers["NOTCH_PAD"]
        assert height == expected, (
            f"NOTCH_H evaluates to {height} but the sprite is {numbers['SIZE']} "
            f"and the padding is {numbers['NOTCH_PAD']} on each edge, which is "
            f"{expected}. The strip is a different height from the one its own "
            "parts describe.")

    def test_the_padding_is_positive(self):
        """The other half of the derivation, and the one that can go wrong alone.

        A negative `NOTCH_PAD` would satisfy `NOTCH_H >= SIZE` by making the
        strip shorter, and the sprite would be cropped rather than contained -
        so the invariant above is not sufficient on its own.
        """
        constants = _js_constants()
        pad = eval(constants["NOTCH_PAD"], {"__builtins__": {}}, constants)  # noqa: S307
        assert pad > 0, (
            f"NOTCH_PAD is {pad}; a strip padded by a negative amount is "
            "shorter than its sprite while still satisfying NOTCH_H >= SIZE")


# --------------------------------------------------------------------------
# The manifest
# --------------------------------------------------------------------------

class TestTheManifest:
    def test_it_names_a_settings_schema(self):
        """Without this, `extension.getSettings()` throws on `enable()`.

        That is the whole reason the key exists: `_corner()` reads the app's own
        `mascot-corner` rather than carrying a second copy of the corner list,
        and a missing `settings-schema` is not "no settings" but an exception
        during enable - so the extension never comes up at all.
        """
        data = json.loads(METADATA.read_text())
        assert data.get("settings-schema") == "org.shani.chronoa", (
            f"metadata.json declares {data.get('settings-schema')!r}; the "
            "extension reads the app's own schema, and the recording harness "
            "compiles the same one. A different or absent id means "
            "getSettings() cannot resolve org.shani.chronoa.")

    def test_the_schema_it_names_is_the_one_this_repository_ships(self):
        schema = json.loads(METADATA.read_text()).get("settings-schema")
        assert schema == "org.shani.chronoa"
        xml = (REPO / f"usr/share/glib-2.0/schemas/{schema}.gschema.xml")
        assert xml.is_file(), (
            f"metadata.json names {schema} and the repository ships no "
            f"{xml.name}. The extension would resolve the app's real settings "
            "on a user's machine and resolve nothing in this tree.")

    def test_it_declares_the_shell_versions_it_was_measured_on(self):
        versions = json.loads(METADATA.read_text())["shell-version"]
        assert versions == ["48", "49", "50"], (
            f"shell-version is {versions}. The measurements in "
            "HARVEST-COUCOU.md PART 9/10 were taken on the versions listed "
            "here; widening it without measuring claims a surface nobody saw.")


# --------------------------------------------------------------------------
# create() - one call, either surface
# --------------------------------------------------------------------------

class TestCreatePicksASurface:
    def test_the_window_wins_where_a_layer_shell_exists(self, monkeypatch):
        """Not a fallback order - a product decision.

        The window can be dragged, clicked and moved. The notch is a strip in the
        Shell's own composite with no input region of its own, so it is strictly
        less capable, and it wins nowhere the window can run.
        """
        monkeypatch.setattr(mascot, "available", lambda: (True, "a layer surface"))
        monkeypatch.setattr(mascot_notch, "available", lambda: (True, "owned"))
        assert isinstance(mascot.create(None), mascot.Mascot)

    def test_the_notch_is_used_where_the_compositor_refuses(self, monkeypatch):
        monkeypatch.setattr(mascot, "available", lambda: (False, "no layer surface"))
        monkeypatch.setattr(mascot_notch, "available", lambda: (True, "owned"))
        assert isinstance(mascot.create(None), mascot_notch.Notch)

    def test_neither_available_still_returns_an_object(self, monkeypatch):
        """Both reasons are kept, and the caller is never handed `None`.

        `None` would make "this machine has no companion surface" and "nobody
        built one" indistinguishable at every reader, and the first is a fact
        about the machine while the second is a bug. `start()` refusing is the
        next test; this one is only about the return value.
        """
        monkeypatch.setattr(mascot, "available", lambda: (False, "no layer surface"))
        monkeypatch.setattr(mascot_notch, "available", lambda: (False, "not enabled"))
        assert mascot.create(None) is not None

    def test_both_reasons_are_kept_rather_than_one(self, monkeypatch, caplog):
        """Two different facts about the machine, so the log names both.

        A caller that reads only the window's reason ("Mutter has no layer
        shell") would send somebody to install a compositor plugin on a machine
        where the extension is also switched off - and the reverse is equally
        unhelpful, because a person cannot enable a Shell extension from here.
        """
        monkeypatch.setattr(mascot, "available", lambda: (False, "no layer surface"))
        monkeypatch.setattr(mascot_notch, "available", lambda: (False, "not enabled"))
        with caplog.at_level("INFO", logger="shani_chronoa.mascot"):
            mascot.create(None)
        said = caplog.text
        assert "no layer surface" in said, said
        assert "not enabled" in said, (
            f"the notch's reason is missing from the log: {said!r}. One reason "
            "for two unavailable surfaces tells the reader half of what is wrong.")

    def test_start_is_the_gate_rather_than_creation(self, monkeypatch):
        """`create()` picks a class; `start()` decides whether it runs.

        Both availability answers depend on a main loop, and
        `GLib.main_depth()` reads 0 inside a real `do_startup` (measured), so a
        `create()` that refused would refuse at exactly the one place the app
        asks. Constructing is a property of the machine; running is not.
        """
        monkeypatch.setattr(mascot, "available", lambda: (False, "no layer surface"))
        monkeypatch.setattr(mascot_notch, "available", lambda: (False, "not enabled"))
        built = mascot.create(None)
        assert built.running is False
        assert built.start() is False, (
            "start() answered True with neither surface available, so a "
            "companion would claim to be up when nothing is showing")

    def test_both_surfaces_answer_the_same_methods(self):
        """The property that lets one call site hold either object.

        Asked of the two classes rather than of one object, because a fixture
        that built only a `Mascot` would be satisfied by any method list at all.
        """
        surface = ("running", "start", "stop", "set_state", "set_level",
                   "express", "say", "notice")
        for name in surface:
            for cls in (mascot.Mascot, mascot_notch.Notch):
                assert hasattr(cls, name), (
                    f"{cls.__name__} has no {name}; application.py calls it on "
                    "whichever object create() returned")


# --------------------------------------------------------------------------
# The app reaches it the same way whichever surface it got
# --------------------------------------------------------------------------

class TestTheAppAsksTheCompanionRatherThanTheWindow:
    def test_it_builds_through_create(self):
        source = APPLICATION.read_text()
        assert "mascot.create(self)" in source, (
            "application.py constructs the companion directly. That is the bug "
            "the seam was written for: on Mutter the window never opens, so "
            "the one surface that works is the one that would be skipped.")
        assert "mascot.Mascot(self)" not in source, (
            "application.py still constructs the window directly somewhere.")

    def test_it_asks_running_rather_than_reading_the_window(self):
        """The reader that was replaced, named without spelling it in this file.

        `_win` is a private attribute of `Mascot`. A question about the
        companion that is really a question about one of its two
        implementations is what made the notch unreachable in the first place:
        the notch has no window, so the old gate answered "not running" forever
        and the extension was never started.
        """
        private = "_" + "win"
        hits = [n for n, line in enumerate(APPLICATION.read_text().splitlines(), 1)
                if re.search(rf"\b{private}\b", line) and not line.lstrip().startswith("#")]
        assert not hits, (
            f"application.py reads {private} at line(s) {hits} - outside a "
            "comment. That is the window's private state, so the gate is a "
            "question about the window rather than about the companion.")

    def test_running_agrees_with_the_state_it_wraps(self):
        """Both properties answer their own class's state, not a shared one.

        Asked of the class rather than of an instance, because an instance
        built through `__new__` would carry whatever attributes a test gave it.
        """
        assert isinstance(mascot.Mascot.running, property)
        assert isinstance(mascot_notch.Notch.running, property)

    def test_the_reader_sites_are_the_two_that_are(self):
        """A count of the sites, read from the module.

        Not a hand-kept number - it is derived, so adding a reader is visible
        and removing one cannot pass by leaving the count behind. Two here and
        one more in `app/voice.py`: `do_shutdown` must stop whatever is up, and
        `do_activate` must start it, so neither can assume it exists.
        """
        source = APPLICATION.read_text()
        found = source.count('getattr(self, "mascot", None)')
        assert found == 2, (
            "application.py reads the companion through "
            f'`getattr(self, "mascot", None)` in {found} place(s), not 2. A '
            "site that reached for the object directly would raise on a "
            "partially-started app instead of skipping - which is the reason "
            "every reader here goes through getattr.")


# --------------------------------------------------------------------------
# The five mutations that cannot be measured, planted by hand instead
# --------------------------------------------------------------------------

#: This module, as the object the monkeypatch has to write into. The checks
#: above read `EXTENSION_JS` as a module global, so pointing that global at a
#: scratch copy is what makes a planted fact reach them.
_MOD = sys.modules[__name__]

#: The shipped path, under a name the monkeypatch does not touch.
#:
#: **`EXTENSION_JS` cannot be read after a patch.** The helper below points that
#: global at a scratch file, so a second read of it returns the *scratch* copy -
#: and the first version of this helper did exactly that, which made every case
#: in this class compare a file with itself. The control at the foot of the class
#: is what found it, and it is why this binding exists: a path that a test
#: redirects cannot also be the record of where the code really is. It is the
#: `GSETTINGS_BACKEND` / `fake_bin` lesson about a fixture that does not control
#: its environment, in the shape where the fixture is a module global.
_SHIPPED_JS = EXTENSION_JS


def _point_at(tmp_path, monkeypatch, plant: tuple = None):
    """Copy `extension.js` into a scratch extension dir and read *that*.

    `plant` is `(old, new)`; the replace asserts it matched **exactly once**,
    because a silent no-op here would produce a green case that had measured
    nothing - the `str.replace` trap this repository keeps meeting, and the one
    place it is easiest to hit, since the needles are real lines of the shipped
    file and a reworded anchor stops matching without ever saying so.
    """
    scratch = tmp_path / "chronoa-mascot@shani.dev"
    scratch.mkdir(parents=True, exist_ok=True)
    target = scratch / "extension.js"
    text = _SHIPPED_JS.read_text()
    if plant is not None:
        old, new = plant
        assert text.count(old) == 1, (
            f"the planted fact {old!r} occurs {text.count(old)} times in "
            "extension.js, not once - so this control would read the "
            "unmutated file and pass for the wrong reason")
        text = text.replace(old, new)
    target.write_text(text)
    monkeypatch.setattr(_MOD, "EXTENSION_JS", target)
    return target


def _bites(check, tmp_path, monkeypatch, plant) -> str:
    """Drive one real check against an unmutated copy and against a planted one.

    Returns the message from the planted run, so a case can assert *why* it went
    red rather than only that it did.

    Both halves run against the same scratch file and the same monkeypatch, so the
    only difference between them is the planted fact. A control that only
    asserted "goes red" would be satisfied by a check that always fails, and one
    that only asserted "passes" by a check that cannot read the file at all -
    which is exactly what happens when a scan's needle is no longer in its input.
    """
    _point_at(tmp_path, monkeypatch)
    check()  # the unmutated copy must be green, or "goes red" means nothing

    _point_at(tmp_path, monkeypatch, plant)
    try:
        check()
    except AssertionError as exc:
        return str(exc)
    raise AssertionError(
        f"{check.__qualname__} passed with the fact planted that this file "
        "records as the reason it exists, so it is not a witness for it")


class TestEachDroppedMutationWouldHaveBeenCaught:
    """Five mutations in `tools/mutations/notch.py` are UNMEASURABLE, and these
    are what carries them instead.

    `mutate.py`'s `_apply` calls `ast.parse` on the whole spliced file before
    writing it, so a mutation aimed at `extension.js` cannot pass the guard at
    all: line 1 is `// Chronoa's notch: ...`, and `//` is a comment in
    JavaScript and a division in Python, so the apostrophe in *Chronoa's* opens
    a string that never closes. Measured - `ast.parse` refuses the **unmutated**
    file with `unterminated string literal (detected at line 1)`, and `_apply`
    driven directly raises `MalformedMutation` for all five.

    They were dropped rather than patched, because relaxing that guard is a
    change to `tools/mutate.py` inside a notch commit, and the guard is itself
    mutation-verified by `tools/mutations/harness.py`.

    **A dropped mutation is a witness nobody has measured, and that is the same
    failure as one that survives.** So each fact is planted here by hand and the
    real check is required to reject it. Nothing here is a *substitute* for a
    verdict: the runner still cannot measure these five, and the honest report
    remains that they are unmeasurable. What is established here is narrower and
    is what was actually at risk - that the five witnesses above are witnesses,
    rather than five test names that pass either way.
    """

    def test_a_renamed_interface_method_is_caught(self, tmp_path, monkeypatch):
        """`PanelHeight` renamed to `PanelHeightV2`.

        GDBus refuses an undeclared method, so `_call_int` answers `None` and
        the strip falls back to y=0 - *almost* right on a machine whose top bar
        is 0px and visibly wrong on every other one.
        """
        plant = ('    <method name="PanelHeight">',
                 '    <method name="PanelHeightV2">')

        said = _bites(
            TestTheShellOffersWhatTheAppCalls()
            .test_every_method_the_client_calls_is_in_the_shell_interface,
            tmp_path, monkeypatch, plant)
        assert "PanelHeight" in said, said

        # The arity check goes red too, and separately: it looks the method up by
        # name and finds no block. Two readers of one file, catching one rename.
        _bites(TestTheShellOffersWhatTheAppCalls()
               .test_the_three_that_carry_the_notch_are_declared_with_the_right_arity,
               tmp_path, monkeypatch, plant)

        # **And the control for the scan goes red as well**, because the
        # fabricated name it builds is `PanelHeightV2` - the one now present.
        # That is the scan proving it can report an absence: it could not do it
        # while the real method was renamed into the shape of the fake one. A
        # control that can fail on the defect is worth more than one that only
        # ever passes.
        said = _bites(TestTheShellOffersWhatTheAppCalls()
                      .test_the_scanner_can_report_an_absence_at_all,
                      tmp_path, monkeypatch, plant)
        assert "fabricated" in said, said

    def test_a_method_declared_as_a_reader_is_caught(self, tmp_path, monkeypatch):
        """`Status`'s two arguments flipped to `direction="out"`.

        Presence alone is not the claim: `Status` is the one method that makes
        the strip carry *what Chronoa is doing*, and a wrong direction is a
        `TypeError` inside the Shell, which the client cannot distinguish from a
        missing method.
        """
        plant = ('      <arg type="s" name="title" direction="in"/>\n'
                 '      <arg type="s" name="detail" direction="in"/>',
                 '      <arg type="s" name="title" direction="out"/>\n'
                 '      <arg type="s" name="detail" direction="out"/>')
        said = _bites(TestTheShellOffersWhatTheAppCalls()
                      .test_the_three_that_carry_the_notch_are_declared_with_the_right_arity,
                      tmp_path, monkeypatch, plant)
        # **This particular assertion carries no message of its own**, so the
        # evidence is pytest's own diff rather than prose from the check - and
        # the diff names the flipped direction, which is the planted fact. The
        # other four cases assert on a message the check wrote and can be quoted.
        assert "'out'" in said and "'title'" in said, said

    def test_a_height_literal_too_small_is_caught(self, tmp_path, monkeypatch):
        """`NOTCH_H = 100`, replacing the derivation.

        100 < 132, so the mascot is cropped by the surface meant to hold it -
        and nothing else in the file notices. The size check is the one that
        sees it; the arithmetic check stops because the expression no longer
        names the sprite at all.
        """
        plant = ("const NOTCH_H = SIZE + 2 * NOTCH_PAD;",
                 "const NOTCH_H = 100;")
        said = _bites(TestTheStripFitsTheMascot()
                      .test_the_strip_is_at_least_as_tall_as_the_sprite,
                      tmp_path, monkeypatch, plant)
        assert "cropped" in said, said

        _bites(TestTheStripFitsTheMascot()
               .test_the_height_is_the_sprite_plus_padding_on_both_edges,
               tmp_path, monkeypatch, plant)

    def test_a_taller_strip_is_caught_only_by_the_arithmetic(self, tmp_path,
                                                             monkeypatch):
        """`NOTCH_H = SIZE + 2 * NOTCH_PAD + 12` - and this is the case the file
        already paid for once.

        This mutation was reported as a **SURVIVOR** by the first run of the
        set, and it was a check of mine: the "is it still derived" test asked
        only whether the expression *names* `SIZE` and `NOTCH_PAD`, and this one
        mentions both while still being tall enough. So both assertions passed.

        Which makes this the most useful control in the file, and the assertion
        is deliberately two-sided. **`>=` does not go red and must not**: 176 is
        above 132, the strip fits, and a control that claimed otherwise would be
        inventing a defect. The arithmetic check is the only witness that bites,
        and saying so here is what stops the `>=` check being counted as
        coverage of this fact later.
        """
        plant = ("const NOTCH_H = SIZE + 2 * NOTCH_PAD;",
                 "const NOTCH_H = SIZE + 2 * NOTCH_PAD + 12;")

        _point_at(tmp_path, monkeypatch, plant)
        # Green, and that is the finding: the size check cannot see this.
        TestTheStripFitsTheMascot().test_the_strip_is_at_least_as_tall_as_the_sprite()

        said = _bites(TestTheStripFitsTheMascot()
                      .test_the_height_is_the_sprite_plus_padding_on_both_edges,
                      tmp_path, monkeypatch, plant)
        assert "different height" in said, said

    def test_a_negative_padding_is_caught(self, tmp_path, monkeypatch):
        """`NOTCH_PAD = -11` - the direction `NOTCH_H >= SIZE` cannot see alone.

        A negative padding satisfies the invariant *by making the strip shorter*
        than its sprite: 132 + 2 x (-11) = 110, so the mascot is cropped rather
        than contained. Two checks go red, and the second is why the padding is
        asserted separately rather than left to the size comparison.
        """
        plant = ("const NOTCH_PAD = 11;", "const NOTCH_PAD = -11;")
        said = _bites(TestTheStripFitsTheMascot().test_the_padding_is_positive,
                      tmp_path, monkeypatch, plant)
        assert "negative" in said, said

        _bites(TestTheStripFitsTheMascot()
               .test_the_strip_is_at_least_as_tall_as_the_sprite,
               tmp_path, monkeypatch, plant)

    def test_the_scratch_copy_is_a_faithful_one(self, tmp_path, monkeypatch):
        """The control for every case above, and it is the one that matters most.

        `_point_at` copies **only** `extension.js`. If the five checks read
        anything else - a schema, a constant imported from the package, a second
        copy of the numbers - then a planted fact would never reach them and each
        case would be green for a reason that has nothing to do with its
        witness. So: the copy must differ from the shipped file in exactly the
        planted way, and the shipped file must be untouched by the run.
        """
        shipped = _SHIPPED_JS.read_text()
        target = _point_at(tmp_path, monkeypatch)
        assert target.read_text() == shipped, (
            "an unplanted scratch copy differs from extension.js, so a case "
            "that went red would be red for a reason of the copy's making")
        assert _SHIPPED_JS.read_text() == shipped, (
            "a scratch run modified the shipped extension.js")

        _point_at(tmp_path, monkeypatch, ("const NOTCH_PAD = 11;",
                                          "const NOTCH_PAD = -11;"))
        assert target.read_text() != shipped, "the planted copy is not different"
        assert _SHIPPED_JS.read_text() == shipped, (
            "planting a fact into the scratch copy wrote through to "
            "extension.js")

    def test_the_helper_reports_a_witness_that_does_not_bite(self, tmp_path,
                                                            monkeypatch):
        """`_bites` raising is the branch every case above depends on, and nothing
        had ever made it raise.

        Five cases are green because the real check rejected a planted fact. If
        `_bites`' own `raise` were unreachable - a typo in the handler, a `return`
        where a `raise` belongs, a check that had been gutted - then all five
        would be green for the reason this class exists to prevent, and the class
        would read as coverage of five facts it had measured nothing about.

        So: a plant that is a **real change to the file and invisible to the
        check**. `PanelHeight` -> `PanelHeightV2` rewrites the `IFACE` XML and
        touches no geometry, so `test_the_padding_is_positive` still passes - and
        `_bites` must say so rather than hand back a case that looks like it bit.
        """
        plant = ('    <method name="PanelHeight">',
                 '    <method name="PanelHeightV2">')
        with pytest.raises(AssertionError, match="passed with the fact planted"):
            _bites(TestTheStripFitsTheMascot().test_the_padding_is_positive,
                   tmp_path, monkeypatch, plant)

    def test_the_helper_does_not_swallow_a_check_that_is_itself_broken(
            self, tmp_path, monkeypatch):
        """The other direction, and it is the one that would hide a gutted check.

        `_bites` runs the check against the **unmutated** copy first, so a check
        that no longer agrees with the shipped file fails before any plant is
        applied. That failure must propagate. If it were absorbed - by the
        handler's `except AssertionError` catching its own `raise`, which it
        cannot currently do, or by a future `except Exception` - then a broken
        check would reach the planted half, pass there too, and the case would
        report a clean capture of a fact nothing is looking at.

        The message is the discriminator: a check that fails against the real file
        says so, and `_bites`'s "passed with the fact planted" does not appear.
        """
        def broken():
            raise AssertionError("the check is broken against the real file")

        plant = ("const NOTCH_PAD = 11;", "const NOTCH_PAD = -11;")
        with pytest.raises(AssertionError) as caught:
            _bites(broken, tmp_path, monkeypatch, plant)
        said = str(caught.value)
        assert said == "the check is broken against the real file", said
        assert "passed with the fact planted" not in said, said
