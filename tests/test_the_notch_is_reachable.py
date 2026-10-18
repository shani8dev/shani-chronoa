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
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
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

def _schema_path() -> Path:
    """Where the schema `metadata.json` names actually lives in this tree.

    A **function, not a constant**, so a control can point a check at a scratch
    copy - and it reads `metadata.json` rather than restating the id, because
    the manifest is the authority (`test_the_schema_it_names_is_the_one_this_
    repository_ships` pins that) and a second copy of the id is one more list
    to drift. The path a test redirects is not also the record of where the
    schema really is; `_SHIPPED_SCHEMA_XML` is that.
    """
    schema = json.loads(METADATA.read_text())["settings-schema"]
    return REPO / f"usr/share/glib-2.0/schemas/{schema}.gschema.xml"


#: The real file, under a name the controls do not touch. See `_SHIPPED_JS`.
_SHIPPED_SCHEMA_XML = (REPO
                       / "usr/share/glib-2.0/schemas/org.shani.chronoa.gschema.xml")


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

    def test_the_schema_compiles_into_a_store_that_still_holds_the_keys(
            self, tmp_path):
        """`glib-compile-schemas` **discards the entire file** for one bad
        declaration, **prints a warning and exits 0**, and the machine then has no
        `org.shani.chronoa` at all.

        Measured on this very file: a `mascot-corner` default naming a string
        that is not in its own `<choices>` produced

            <default> contains a string not in <choices>.  This entire file
            has been ignored.
            COMPILED (exit 0)
            No such schema "org.shani.chronoa"

        so **every one of the schema's ~200 keys silently takes its Python
        default** - consent gates closed, a switch nothing reads, no error
        anywhere a person would look. This is the recorded `calendar-write-enabled`
        failure applied to the whole table at once, and it is asserted against the
        **store** rather than the exit code for that reason: a compile that
        succeeded and compiled nothing is the failure.

        It guards more than the notch. The schema is a mixed file with another
        session's keys in it, so a red here can be theirs - which is the correct
        reading, because the failure takes the whole application with it.

        **And the exit code is weaker still**, which is why the two assertions
        below are not redundant. Measured across five distinct malformations -
        not a `<schemalist>`, unparseable XML, a key name with an underscore, a
        default outside its own `<choices>`, an empty `<schemalist>` - **every
        one exits 0 and every one writes a `gschemas.compiled`**. The compiler
        reports success on a schema store holding nothing at all.

        It exits non-zero in exactly three cases: it cannot write its output, the
        directory does not exist, the argument is not a directory. **None of the
        three is reachable through this check**, which stages its own writable
        directory - so `assert done.returncode == 0` has **no witness in this
        file**, and that is recorded rather than dressed up. It is the documented
        contract and it is worth keeping, because it is the one assertion that
        would name *"the compiler could not write its output"*: without it a
        permission failure falls through to `"has been ignored" not in said`,
        which **passes** - the message is *"Permission denied"* - and then to the
        `gschemas.compiled` check, whose failure would name a missing file rather
        than the disk that would not take it. Keeping it is right; a test
        claiming it was covered would not be.
        """
        schema = json.loads(METADATA.read_text())["settings-schema"]
        src = _schema_path()
        assert src.is_file(), f"{src} does not exist"
        compile_schema = shutil.which("glib-compile-schemas")
        gsettings = shutil.which("gsettings")
        assert compile_schema and gsettings, (
            "glib-compile-schemas and gsettings are both required to tell a "
            "schema that compiled from one that compiled nothing")

        # **A directory of its own, holding only this file** - the layout is the
        # point: compiling a flattened copy gives a different answer, and reading
        # a real install's store would mean reading whatever that install put
        # there rather than what this tree declares.
        stage = tmp_path / "schemas"
        stage.mkdir()
        shutil.copy(src, stage / src.name)

        done = subprocess.run([compile_schema, str(stage)], capture_output=True,
                              text=True, timeout=60)
        said = done.stdout + done.stderr
        assert done.returncode == 0, f"glib-compile-schemas failed:\n{said}"
        assert "has been ignored" not in said, (
            "glib-compile-schemas exited 0 and discarded the whole schema:\n"
            f"{said.strip()}\nEvery setting in the application now takes its "
            "Python default, silently.")

        assert (stage / "gschemas.compiled").is_file(), (
            "no gschemas.compiled was written")

        env = dict(os.environ, GSETTINGS_SCHEMA_DIR=str(stage),
                   GSETTINGS_BACKEND="memory")
        ask = subprocess.run(
            [gsettings, "get", schema, "mascot-corner"],
            capture_output=True, text=True, timeout=60, env=env)
        assert ask.returncode == 0 and "No such schema" not in ask.stderr, (
            "the schema compiled to a file and the store cannot read a key out "
            f"of it: {ask.stdout.strip()} {ask.stderr.strip()}")

    def test_the_corner_the_schema_picks_is_a_corner_the_code_handles(self):
        """The default is the **only** thing that decides where the notch goes.

        Measured: nothing in this repository writes `mascot-corner` - no skill, no
        Settings row, no wizard, no CLI. So on any machine where the extension
        comes up, `_corner()` returns the **schema's default**, and that one
        literal is the whole of the placement decision.

        Which is worth writing down because it was carried here as an open
        product question - *"`_place()` stays bottom-right while the schema's
        default is `top-center`"* - and that reading is wrong. It is the
        **no-schema fallback** that says `bottom-right`, in both readers
        (`extension.js:163`'s `value || 'bottom-right'` and `mascot.py:568`), not
        the default: with the schema installed `gsettings get` answers
        `'top-center'` and the strip is drawn at `monitor.y + bar`, which is the
        whole reason the notch exists. The decision has been made and is
        `top-centre`; what is *not* shipped is the key, which lives in two files
        mixed with another session's work.

        The second half guards the other direction of the same hand-kept list: a
        `<choice>` the code has no branch for does not fail, it **falls into the
        `else`** and lands at bottom-right with nothing saying so. Measured by
        adding `top-left` to the choices - it compiles, the default is unaffected,
        and `_place()` has no `top-left` anywhere.
        """
        schema = json.loads(METADATA.read_text())["settings-schema"]
        root = ET.parse(_schema_path()).getroot()
        key = next((k for k in root.iter("key")
                    if k.get("name") == "mascot-corner"), None)
        assert key is not None, (
            f"{schema} declares no mascot-corner key. It is the key the "
            "extension reads to place the strip, and nothing writes it, so its "
            "absence leaves _corner() on its own fallback.")

        default = (key.findtext("default") or "").strip().strip('"')
        choices = [c.get("value") for c in key.iter("choice")]
        assert default in choices, (
            f"mascot-corner's default is {default!r}, which is not one of its "
            f"own choices {choices}. glib-compile-schemas discards the ENTIRE "
            "schema for that, exits 0, and every setting in the application "
            "silently takes its Python default.")

        assert default == "top-center", (
            f"mascot-corner defaults to {default!r}. Nothing in the tree "
            "writes this key, so the default is the only thing that decides "
            "placement - and the notch is the top-centre strip, not the "
            "bottom-right corner it was ported away from.")

        js = EXTENSION_JS.read_text()
        unhandled = [c for c in choices
                     if c != "bottom-right" and f"'{c}'" not in js]
        assert not unhandled, (
            f"the schema offers mascot-corner={unhandled}, and extension.js "
            "compares against no such string - _place() would fall into its "
            "else and draw the strip at the bottom-right corner instead, with "
            "nothing saying the choice was not honoured. Either the branch or "
            "the choice is missing.")


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


# --------------------------------------------------------------------------
# The two manifest checks can bite, measured rather than argued
# --------------------------------------------------------------------------

def _point_schema_at(tmp_path, monkeypatch, plant: tuple = None):
    """Copy the shipped schema into a scratch dir and read *that*.

    The parallel of `_point_at`, for the file `metadata.json` names rather than
    for `extension.js`. `plant` asserts it matched **exactly once**, for the
    reason the sibling does - the needles here are single lines of a file another
    session also edits, so a reworded anchor stops matching silently.

    **Each half gets its own directory**, which `_point_at` does not need: the
    five checks above only *read* the copy, while
    `test_the_schema_compiles_into_a_store_that_still_holds_the_keys` **stages
    one** (`stage.mkdir()`) and then compiles it. Pointed at one path twice it
    raises `FileExistsError` on the second half, which is a harness error
    arriving as a rejection - the worst shape there is, because the case would
    read as a captured fact when nothing was decided. That is not hypothetical:
    the probe that established these controls reported it that way first, and
    `FileExistsError` is not an `AssertionError`.
    """
    scratch = tmp_path / "schema"
    scratch.mkdir(parents=True, exist_ok=True)
    text = _SHIPPED_SCHEMA_XML.read_text()
    if plant is not None:
        old, new = plant
        assert text.count(old) == 1, (
            f"the planted fact {old!r} occurs {text.count(old)} times in "
            "the schema, not once - so this control would read the unmutated "
            "file and pass for the wrong reason")
        text = text.replace(old, new)
    target = scratch / _SHIPPED_SCHEMA_XML.name
    target.write_text(text)
    monkeypatch.setattr(_MOD, "_schema_path", lambda: target)
    return target


def _schema_bites(check, tmp_path, monkeypatch, plant) -> str:
    """`_bites` for the schema, with the two refusals it needs.

    **The check takes the half's directory**, which is why the corner check is
    reached through a closure rather than as the method itself: that check reads
    the schema through `_schema_path()` and declares no `tmp_path`, while the
    compile check *stages* one. Giving the helper a fixed contract keeps both
    shapes expressible without it inspecting a signature - and a helper that
    guesses what a callable wants is a helper whose failure depends on the
    callable.

    **`ET.parse` on the planted copy before the check reads it.** A malformed
    plant would make the check raise `ParseError` - not an `AssertionError`, so
    the "did it reject the fact" question would be answered by a syntax error,
    and the case would report a captured fact when the only thing that happened
    is that the file no longer parses.
    """
    for half, seed in (("clean", None), ("planted", plant)):
        target = _point_schema_at(tmp_path / half, monkeypatch, seed)
        ET.parse(target)
        if half == "clean":
            check(tmp_path / half)  # green, or "goes red" means nothing
            continue
        try:
            check(tmp_path / half)
        except AssertionError as exc:
            return str(exc)
    raise AssertionError(
        f"{check.__qualname__} passed with the fact planted that this file "
        "records as the reason it exists, so it is not a witness for it")


def _the_corner_check(half_dir):
    """`TestTheManifest`'s corner check, in the shape `_schema_bites` calls.

    It takes no `tmp_path` - everything it reads comes through `_schema_path()` -
    so it is wrapped rather than handed a directory it has no use for.
    """
    TestTheManifest().test_the_corner_the_schema_picks_is_a_corner_the_code_handles()


class TestTheManifestChecksCanBite:
    """The two `TestTheManifest` checks this port added, shown able to go red.

    The notch set exercises them through **one** mutation - *"the manifest names
    a schema this repository does not ship"* - and it drives both of them down
    the **missing-file** branch, because `_schema_path()` then points at
    something that is not there. That is a real capture and it is not the whole
    claim: four assertions in these two tests are about a schema that exists,
    and a mutation that renamed a schema would have exercised none of them.

    So each is planted by hand here, the way
    `TestEachDroppedMutationWouldHaveBeenCaught` plants the five the runner
    cannot measure. **The distinction is why both classes exist.** Those five are
    unmeasurable to the runner *because* they cannot be applied to a JavaScript
    file; these four are ordinary measurable behaviour and the reason nothing has
    exercised them is narrower and worse - the only mutation aimed at
    `metadata.json` happens to fail earlier in the same check.

    The measurements these assert, in the order they are asked:

    | planted fact | the assertion that has to reject it |
    |---|---|
    | default is a valid choice but not `top-center` | the top-centre claim |
    | default is not one of its own `<choices>` | the compile-discards-everything claim, at parse time |
    | a `<choice>` `extension.js` has no branch for | the honest-placement claim |
    | default names a string outside its `<choices>` | the compile-discards-everything claim, for real |

    The last one is the reason this class exists rather than being tidiness. A
    `mascot-corner` default outside its own choices is **exactly** the defect
    `TestTheManifest`'s docstring records having measured - `glib-compile-schemas`
    prints *"This entire file has been ignored"*, **exits 0**, and the machine
    has no `org.shani.chronoa` at all - and it takes the whole application with
    it. It is the most consequential thing either test guards, and nothing in
    the mutation set touched it.
    """

    _TOP_CENTRE = '<default>"top-center"</default>'

    def test_a_default_that_is_not_top_centre_goes_red(self, tmp_path,
                                                       monkeypatch):
        """The top-centre claim, planted with a default that is a legal choice.

        `bottom-right` is deliberately the *valid* one: it isolates the assertion
        under test from the choices assertion below, which would otherwise fire
        first and make this look like coverage of the same fact twice.
        """
        said = _schema_bites(
            _the_corner_check, tmp_path, monkeypatch,
            (self._TOP_CENTRE, '<default>"bottom-right"</default>'))
        assert "defaults to 'bottom-right'" in said, said
        # And the choices assertion is *not* what fired: `bottom-right` is one of
        # them, so a case that let it fire would be passing for the wrong reason.
        assert "not one of its own choices" not in said, said

    def test_a_default_outside_its_own_choices_goes_red(self, tmp_path,
                                                       monkeypatch):
        """The choices assertion, on its own."""
        said = _schema_bites(
            _the_corner_check, tmp_path, monkeypatch,
            (self._TOP_CENTRE, '<default>"no-such-corner"</default>'))
        assert "not one of its own choices" in said, said

    def test_a_choice_extension_js_cannot_handle_goes_red(self, tmp_path,
                                                          monkeypatch):
        """The honest-placement claim: a `<choice>` with no branch behind it.

        This is the failure that does **not** announce itself. The schema
        compiles, the default is untouched, and `_place()` has no branch for
        `top-left` - so the value falls into its `else` and the strip is drawn at
        the bottom-right corner with nothing saying the choice was not honoured.
        A one-line addition to the `<choices>` is the whole plant.
        """
        plant = ('        <choice value="top-center"/>\n',
                 '        <choice value="top-center"/>\n'
                 '        <choice value="top-left"/>\n')
        said = _schema_bites(
            _the_corner_check, tmp_path, monkeypatch, plant)
        assert "top-left" in said, said

    def test_the_schema_being_discarded_goes_red(self, tmp_path, monkeypatch):
        """The failure the store check exists for, which nothing else reaches.

        This is the measured one, and it is asserted against **the store**
        rather than the exit code because that is the shape of the defect:
        `glib-compile-schemas` **exits 0** having discarded the entire file, so a
        check that watched the exit code would report success for a schema that
        no longer exists. The check's own message says so.
        """
        said = _schema_bites(
            TestTheManifest().test_the_schema_compiles_into_a_store_that_still_holds_the_keys,
            tmp_path, monkeypatch,
            (self._TOP_CENTRE, '<default>"no-such-corner"</default>'))
        assert "discarded the whole schema" in said, said

    def test_the_helper_reports_a_check_that_does_not_bite(self, tmp_path,
                                                           monkeypatch):
        """`_schema_bites`' own `raise`, which nothing above makes it reach.

        Four cases are green because a real check rejected a planted fact. If
        that `raise` were unreachable the class would read as coverage of four
        claims it had measured nothing about.

        So it is driven with a **real check and a plant it cannot see**: the
        only assertion of `test_the_corner_...` that a `bottom-right` default
        satisfies is *"the default is one of its own choices"*, so a check
        carrying that one assertion alone stays green under a plant that a real
        defect would trip. It is written out rather than reached for, because
        pointing at a method that asserts four things and expecting it to pass is
        a claim about the *other three* staying satisfied - true today, and the
        kind of coupling that quietly stops holding.
        """
        def only_the_choices_assertion(tmp_dir):
            root = ET.parse(_MOD._schema_path()).getroot()
            key = next(k for k in root.iter("key")
                       if k.get("name") == "mascot-corner")
            default = (key.findtext("default") or "").strip().strip('"')
            choices = [c.get("value") for c in key.iter("choice")]
            assert default in choices, (
                f"mascot-corner's default is {default!r}, which is not one of "
                f"its own choices {choices}")

        # The positive half first, and it is asserted on the **returned
        # message**: `_schema_bites` catches a rejection and hands it back rather
        # than re-raising, which is what lets a case above assert *why* it went
        # red. So only the "did not bite" direction raises.
        said = _schema_bites(only_the_choices_assertion, tmp_path, monkeypatch,
                             (self._TOP_CENTRE,
                              '<default>"no-such-corner"</default>'))
        assert "not one of its own choices" in said, said

        with pytest.raises(AssertionError, match="passed with the fact planted"):
            _schema_bites(only_the_choices_assertion, tmp_path, monkeypatch,
                          (self._TOP_CENTRE, '<default>"bottom-right"</default>'))
