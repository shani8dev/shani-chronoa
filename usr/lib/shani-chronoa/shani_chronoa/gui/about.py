"""The two windows an assistant is expected to have and this one did not.

`SHORTCUT_SECTIONS` and the about text are data, and the windows are built from
them, because both are things that go stale silently. A shortcut window that
lists a shortcut the app does not have is worse than no shortcut window at all:
it is a confident wrong answer to the only question the window asks. So every
row here names a GAction that `app/application.py` really registers, and every
accelerator is one it really binds with `set_accels_for_action` -
`tests/test_about.py` reads both back out of that file with `ast` and fails if
a row names something that is not there. Adding a row therefore means adding
the action first.

Three traps this module is shaped by, all measured on this machine rather than
taken from a tutorial:

- **`Adw.init()` runs at import, before anything else here.** It has to happen
  before any Adw widget is constructed *and* before the `Gtk.Application`
  exists; a window built before it renders nothing, silently. Doing it here
  rather than in the constructor means the first caller to build one of these
  cannot be the one that breaks - which is usually a test.
- **The native shortcuts API has moved twice, and this module tracks both
  spellings.** The installed libadwaita is 1.9 (measured: `Adw.get_micro_version()`
  is 1), which has `Adw.ShortcutsDialog` - the whole dialog, search field and
  window controls included - and `_ADW_SHORTCUTS_API` detects it. The window used
  to be written against 1.5, where none of it existed, and then against 1.8's
  `Adw.ShortcutsShortcut`, which 1.9 **removed**: the accelerator is a property on
  `Adw.ShortcutsItem` now, and `AdwShortcutsSection` / `AdwShortcutsItem` are
  models rather than widgets. So the guard that named the 1.8 class answered
  `False` on 1.9 and the native path was unreachable - the window rendered the
  hand-rolled fallback and nothing complained, because the fallback exists
  precisely so that nothing has to. `_detect_adw_shortcuts()` is the fix: probe
  for what the library actually has, and branch on the spelling rather than
  assuming the newest. Anything older than 1.8 still gets the
  `Adw.PreferencesGroup` / `Adw.ActionRow` / `Gtk.ShortcutLabel` rows, which is
  what the dialog is one level down. Every row on every path carries a real
  accelerator widget, so what a row claims is a thing GTK can render rather than
  a string in a table.
- **`Adw.AboutWindow` cannot be used as this window's base, and cannot be
  put inside another window either.** Three separate measurements, because each
  way of doing it looks fine until you run it:
  - `class AboutWindow(Adw.AboutWindow)` fails at *import*: `could not create
    new GType ... (subclass of AdwAboutWindow)` - `AdwAboutWindow` and
    `AdwAboutDialog` are `G_DECLARE_FINAL`, and PyGObject refuses to derive
    from a final type.
  - `Gtk.Window.set_child(Adw.AboutWindow(...))` is accepted without a
    complaint, because an about window *is* a `Gtk.Window`, and then it never
    appears: the wrapper mapped `True` while the about window inside it stayed
    `get_mapped() == False` at 0x0, silently.
  - Stealing the page out of it (`about.get_content().unparent()`, then put it
    in our own window) maps and looks right at 520x560 - and the close button
    in that stolen page then closes the *unpresented* dialog: clicked, the
    about window went invisible while our window stayed open and mapped.

  `Adw.Window.add_dialog()` would be the supported way to host it, and the
  installed libadwaita 1.9 still does not have it (`hasattr(Adw.Window,
  "add_dialog")` is `False`, measured) - so this is still the answer on the
  version that ships, not only on the old one. The base is GTK's own
  `Gtk.AboutDialog`, which the module docstring's own rule allows, is derivable,
  and keeps `get_license_type()` machine-readable. It is a `Gtk.Window`, so
  callers do not care. If a future libadwaita exposes the about page as a
  separate widget, this is the one line that changes.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # type: ignore

from shani_chronoa import __version__

logger = logging.getLogger(__name__)

# Module scope, idempotent, and before the first widget below exists. See the
# trap in this module's docstring: called inside a constructor it is too late.
Adw.init()


class Shortcut(NamedTuple):
    """One row: what the app calls it, what it really runs, and its key.

    `accelerator` is empty for a real action that has no key bound, which is a
    different fact from a key that does not work - the row says so rather than
    showing a shortcut the app never registered.
    """

    title: str
    action: str
    accelerator: str = ""
    subtitle: str = ""


class ShortcutSection(NamedTuple):
    title: str
    shortcuts: tuple


SHORTCUT_SECTIONS = (
    ShortcutSection("General", (
        Shortcut(
            "Settings", "open-settings", "<Ctrl>comma",
            "Every setting, including what Chronoa is allowed to change.",
        ),
        Shortcut(
            "Quick Ask", "quick-ask", "<Ctrl><Shift>a",
            "One question, one answer, and nothing kept unless you ask.",
        ),
        Shortcut(
            "In-app browser", "open-browser", "<Ctrl><Shift>u",
            "Needs WebKitGTK. Without it the key does nothing and the browser "
            "says which package is missing.",
        ),
        Shortcut(
            "Show or hide the panels", "toggle-sidebar", "F9",
            "The panel list: this machine, what Chronoa knows, what it did.",
        ),
        Shortcut(
            "Find in this conversation", "find-in-conversation", "<Ctrl>f",
            "Search the transcript. Escape closes it again.",
        ),
        Shortcut(
            "The body register", "show-body", "<Ctrl><Shift>B",
            "Every organ, what it is for, and which lights show it. The strip "
            "above the composer is the same thing, live.",
        ),
        Shortcut(
            "Hands-free listening", "toggle-wake-word", "<Ctrl><Shift>W",
            "Turn the wake phrase on or off. Needs a speech recognition model "
            "installed; push to talk works without one.",
        ),
        Shortcut(
            "Set Chronoa up", "setup", "<Ctrl><Shift>s",
            "The model, the ears and the voice. Also the button in the header, "
            "and “Set up Chronoa again” under Settings.",
        ),
        Shortcut(
            "Dictate", "dictate", "<Ctrl><Shift>d",
            "Type with the keyboard, but talk instead of pressing keys.",
        ),
        Shortcut("Quit", "quit", "<Ctrl>Q", "Close Chronoa."),
    )),
    ShortcutSection("Conversation", (
        Shortcut(
            "Start or stop listening", "toggle-listening", "",
            "The orb. Press it to start; press it again to end the turn early.",
        ),
        Shortcut(
            "Open a saved conversation", "switch-conversation", "",
            "The conversations list in the header.",
        ),
        Shortcut(
            "Delete a conversation", "delete-conversation", "",
            "The bin beside each conversation in that list.",
        ),
    )),
    ShortcutSection("This window", (
        Shortcut("New conversation", "reset-conversation", "<Ctrl>N",
                 "Start again with an empty transcript."),
        Shortcut("Stop speaking", "stop-speaking", "Escape",
                 "Cut off the reply being read out."),
    )),
)

#: Whether this libadwaita has the native shortcuts widgets, and *which spelling*.
#:
#: This checked for `Adw.ShortcutsShortcut`, which is what libadwaita 1.8 had. That
#: class is **gone** on the installed 1.9 - measured: `hasattr(Adw,
#: "ShortcutsShortcut")` is `False`, and so are `ShortcutsSection(section_name=..)`
#: and `section.add_shortcut(item)`, both of which raise. The accelerator moved
#: onto the item itself (`Adw.ShortcutsItem.set_accelerator`), and sections are
#: handed to the dialog with `add()` rather than collected in the section.
#:
#: So the guard was `False` on the version that actually ships, and every
#: shortcuts window silently rendered the hand-rolled `PreferencesGroup` fallback
#: instead - no error, because the fallback exists precisely so there is no error.
#: A capability check is only worth anything if it is re-measured when the library
#: moves; this one was written against 1.5, kept its 1.8 spelling, and silently
#: answered for the wrong version.
#:
#: Both spellings are accepted here rather than only the new one, because the
#: fallback is still the right answer on anything older than 1.8 and dropping it
#: would cost a working window on those systems.
def _detect_adw_shortcuts() -> str:
    """Return "1.8", "1.9", or "" for the native shortcuts API this Adw has."""
    if not all(hasattr(Adw, name)
               for name in ("ShortcutsDialog", "ShortcutsSection", "ShortcutsItem")):
        return ""
    # 1.8: a ShortcutsShortcut widget per item, added to the section.
    if hasattr(Adw, "ShortcutsShortcut"):
        return "1.8"
    # 1.9: the accelerator is a property on the item itself.
    return "1.9" if hasattr(Adw.ShortcutsItem, "set_accelerator") else ""


_ADW_SHORTCUTS_API = _detect_adw_shortcuts()
#: True when the native widgets can be built at all. Kept as a name because
#: `tests/test_about.py` asserts on it.
_HAVE_ADW_SHORTCUTS = bool(_ADW_SHORTCUTS_API)

APP_NAME = "Shani Chronoa"
#: The icon the package and the desktop entry actually install.
APP_ICON = "shani-chronoa"
#: The project's own repository - the one a person wants when they click
#: "website" in an About window, and the one README names.
#:
#: This used to point at `shani-pkgbuilds/tree/main/shani-chronoa`, copied
#: from the manifest's `url=`. That is the right value *for a package* (it is
#: where the PKGBUILD lives) and the wrong one for an application: the About
#: window is asking a user where this program's source is, and sending them to
#: a packaging directory answers a question nobody asked.
PROJECT_URL = "https://github.com/shani8dev/shani-chronoa"
#: PKGBUILD's maintainer, and the copyright holder named in LICENSE.
DEVELOPERS = ("Shrinivas Vishnu Kumbhar", "the Shanios authors")
COPYRIGHT = "© 2026 the Shanios authors"
#: PKGBUILD declares `license=('GPL-3.0-only')` and LICENSE is GPL-3.
LICENCE = Gtk.License.GPL_3_0

WHAT_THIS_IS = """\
Chronoa is the assistant that ships with Shanios, and it runs on your machine.

Talk to it or type at it. It listens with whisper.cpp, thinks with a local
model (llama.cpp by default, Ollama if you would rather), and answers out loud
with espeak-ng, Piper or Kokoro. The words you say and the things it looks up
are processed here, not somewhere else.

What it can do is a list, and the list is short on purpose: open an app, set
the volume, tell the time, keep a timer, read the battery, search the web, and
run the skills you drop into ~/.config/shani-chronoa/skills. There is no
free-form shell. A model that can run any command it likes is not something to
hand a microphone.

Anything that reaches past this machine - a cloud model when the local one is
not there, a web search, a screenshot, moving the pointer - is off until you
switch it on, and the settings name the switch that governs it.

The same skills are served over MCP, so Claude Desktop, Claude Code and Cursor
can call them too.\
"""


def _section_19(section: ShortcutSection) -> "Adw.ShortcutsSection":
    """One section in the libadwaita 1.9 shape.

    1.9 moved the accelerator onto the item - there is no `Adw.ShortcutsShortcut`
    any more - and both the section and the item are plain models, not widgets, so
    this returns the section for the dialog to `add()` rather than something to
    append to a box. Measured: `ShortcutsSection(section_name=..)` raises
    `TypeError` and `section.add_shortcut(item)` raises `AttributeError` on this
    library, so the 1.8 spelling cannot be what runs here.
    """
    widget = Adw.ShortcutsSection(title=section.title)
    for shortcut in section.shortcuts:
        item = Adw.ShortcutsItem(
            title=shortcut.title, subtitle=shortcut.subtitle or None
        )
        item.set_accelerator(shortcut.accelerator)
        widget.add(item)
    return widget


def ShortcutsWindow(application=None, parent=None) -> Gtk.Widget:
    """Every key this app really binds, in the app's own three groupings.

    **Returns a widget, not a `Gtk.Window`, and the type is not the same on every
    libadwaita.** On 1.9 it is a real `Adw.ShortcutsDialog` - the whole dialog the
    platform ships for this, search field and window controls included - and the
    app supplies only the rows. Elsewhere it is the hand-built
    `_FallbackShortcutsWindow`.

    `Adw.ShortcutsDialog` is an `Adw.Dialog`, so it is presented rather than
    packed: `set_child()` on it raises a real GTK error, caught by running this
    rather than by reading it -

        Adwaita-ERROR: Trying to add AdwShortcutsDialog ... to ShortcutsWindow.
        Use adw_dialog_present() to show dialogs.

    - and because an `Adw.Dialog` is not a `Gtk.Window`, a caller cannot port
    `transient_for` onto it; `parent` is honoured by the fallback and ignored
    here, where libadwaita parents the dialog to its own window manager instead.
    Both results are widgets, so `present()` and `close()` work on either.
    """
    if _ADW_SHORTCUTS_API == "1.9":
        dialog = Adw.ShortcutsDialog(title="Keyboard Shortcuts")
        for section in SHORTCUT_SECTIONS:
            dialog.add(_section_19(section))
        return dialog
    return _FallbackShortcutsWindow(application=application, parent=parent)


class _FallbackShortcutsWindow(Gtk.Window):
    """The hand-built window, for a libadwaita without the native dialog.

    This is what the app shipped for as long as the guard was wrong, so it is
    kept: on anything older than 1.8 it is the only way to show these rows, and it
    is what `tests/test_about.py` walks.
    """

    def __init__(self, application=None, parent=None) -> None:
        super().__init__(
            application=application,
            transient_for=parent,
            modal=True,
            title="Keyboard Shortcuts",
        )
        self.set_default_size(560, 620)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        for section in SHORTCUT_SECTIONS:
            page.add(self._build_section(section))
        scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True
        )
        scrolled.set_child(page)
        toolbar.set_content(scrolled)
        self.set_child(toolbar)

    def _build_section(self, section: ShortcutSection) -> Gtk.Widget:
        if _ADW_SHORTCUTS_API == "1.8":
            try:
                return self._adw_section(section)
            except Exception as exc:  # noqa: BLE001 - a newer Adw must not cost the window
                logger.warning(
                    "Adw shortcuts widgets unusable (%s); building the same "
                    "rows from Adw.PreferencesGroup instead", exc,
                )
        return self._preferences_section(section)

    def _adw_section(self, section: ShortcutSection) -> Gtk.Widget:
        """One section in the libadwaita 1.8 shape, for a library that still has it."""
        widget = Adw.ShortcutsSection(section_name=section.title)
        for shortcut in section.shortcuts:
            item = Adw.ShortcutsItem(
                title=shortcut.title, subtitle=shortcut.subtitle or None
            )
            item.add_shortcut(Adw.ShortcutsShortcut(accelerator=shortcut.accelerator))
            widget.add_shortcut(item)
        return widget

    def _preferences_section(self, section: ShortcutSection) -> Gtk.Widget:
        widget = Adw.PreferencesGroup(
            title=section.title, margin_top=12, margin_bottom=12
        )
        for shortcut in section.shortcuts:
            widget.add(self._preferences_row(shortcut))
        return widget

    def _preferences_row(self, shortcut: Shortcut) -> Gtk.Widget:
        row = Adw.ActionRow(title=shortcut.title)
        if shortcut.subtitle:
            row.set_subtitle(shortcut.subtitle)
        row.add_suffix(self._shortcut_label(shortcut))
        return row

    def _shortcut_label(self, shortcut: Shortcut) -> Gtk.Widget:
        # A real accelerator widget either way, so what the row claims is a
        # thing GTK can render - and something a test can read back with
        # `get_accelerator()` rather than trusting this module's own table.
        return Gtk.ShortcutLabel(
            accelerator=shortcut.accelerator,
            disabled_text="" if shortcut.accelerator else "No shortcut",
        )


class AboutWindow(Gtk.AboutDialog):
    """What Chronoa is, in the app's own voice.

    The base is `Gtk.AboutDialog` rather than `Adw.AboutWindow` because of the
    third trap in this module's docstring, measured three ways: the Adw one
    cannot be subclassed, cannot be a child widget, and its page cannot be
    moved out of it without the close button dying. `Gtk.AboutDialog` is a
    `Gtk.Dialog`, so it is a `Gtk.Window` and callers treat it as one.
    """

    def __init__(self, application=None, parent=None) -> None:
        super().__init__(
            application=application,
            transient_for=parent,
            modal=True,
            title=f"About {APP_NAME}",
        )
        self.set_program_name(APP_NAME)
        self.set_logo_icon_name(APP_ICON)
        self.set_version(__version__)
        self.set_comments(WHAT_THIS_IS)
        self.set_license_type(LICENCE)
        self.set_copyright(COPYRIGHT)
        self.set_website(PROJECT_URL)
        # A list of strings, which is what this binding actually marshals.
        #
        # This was `"\n".join(DEVELOPERS)` - one author per line, which is the
        # form GTK's *C* API documents. It raised `TypeError: Unable to marshal
        # str as an array` on PyGObject, so the About window had never actually
        # opened on this GTK: it crashed in its own constructor. Measured, both
        # shapes, on the installed binding:
        #
        #     set_authors(["A", "B"])   -> OK;  get_authors() == ["A", "B"]
        #     set_authors("A\nB")       -> TypeError: Unable to marshal str...
        #     set_authors([b"A", b"B"]) -> TypeError: Must be string, not bytes
        #
        # The joining comment that used to sit here also claimed
        # `get_authors()` returns "a list of single characters" in this
        # PyGObject, so a caller needed `"".join(...)`. Measured, that is not
        # true either: it returns the same list of strings that went in. The
        # claim is gone rather than corrected, because nothing should have to
        # undo it - and `tests/test_about.py` asserts on the real return value
        # rather than on a joining of it.
        self.set_authors(list(DEVELOPERS))
        self.set_default_size(520, 560)