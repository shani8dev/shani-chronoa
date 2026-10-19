"""Routines: one phrase that stands for a whole request.

`skills/routines.py` could save one and `routines.expand` could run it, and
nothing anywhere let a person **see** them. Until this panel a routine existed
only as a phrase you had to already remember - you could say "good morning" and
have it work, and there was no screen that listed the others, named the phrases
that trigger them, or showed what each one actually asks for.

**What "Run" does, and why it is not a shortcut.** A routine is a saved
*request*, not a saved list of tool calls - `routines.expand` hands the request
to `Assistant.handle`, so the model plans it like any other turn and every tool
still meets its own consent gate, confirmation and sandbox. So Run goes through
the window's own `submit_text()`, the same path `--ask=` and the desktop entry
use. A panel that ran the request by some other route would be a second
implementation of "turn a request into tools", and it would run the request
*outside* the gates.

**Titles come from the store, so they are escaped.** Routine names are a file a
person or the `routines` skill wrote, not code. `common.row` routes its title
through `set_markup` after escaping and its subtitle through `set_text`, which
takes no markup at all - so a name containing Pango markup is words, not a tag.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from shani_chronoa import routines  # noqa: E402
from shani_chronoa.gui.surfaces import common  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = "Routines"
ICON = "appointment-soon-symbolic"
SECTION = "Acting"
SUBTITLE = ("One phrase that stands for a whole request. Say \"good morning\" "
            "and it asks for the weather, the day's calendar and the news - "
            "planned by the model like anything you typed, so every tool still "
            "meets its own gate.")


def _request_of(routine: Any) -> str:
    """The request a routine stands for, whatever the store actually holds."""
    if not isinstance(routine, dict):
        return ""
    return str(routine.get("request") or "").strip()


def _phrases_of(name: str, routine: Any) -> List[str]:
    """The trigger phrases, as displayed - name first, then the extras."""
    if not isinstance(routine, dict):
        return [name]
    extra = routine.get("phrases")
    said = [name] + ([str(p) for p in extra if isinstance(p, str)]
                     if isinstance(extra, list) else [])
    return [p.strip() for p in said if isinstance(p, str) and p.strip()]


def _subtitle(name: str, routine: Any) -> str:
    """What the row says under the name: the request, then what triggers it.

    A routine whose request is empty is said to be empty, because a row that
    shows a phrase and nothing else reads as one that works.
    """
    request = _request_of(routine)
    parts = [f"asks for: {request}"] if request else ["asks for: nothing yet"]
    parts.append("say: " + ", or ".join(_phrases_of(name, routine)))
    return "  ·  ".join(parts)


class _RoutinesSurface:
    def __init__(self, app: Any, set_content) -> None:
        self._app = app
        self._body = common.page_body()
        for side in ("start", "end", "top", "bottom"):
            getattr(self._body, f"set_margin_{side}")(12)
        set_content(common.scrolled(self._body))
        self._notice = ""
        self._group = common.group()
        self.refresh()

    # -- the store -----------------------------------------------------------

    def _load(self):
        """`(routines, why-not)`. An unreadable store is a sentence, not a crash."""
        try:
            return routines.load(), ""
        except Exception as exc:  # noqa: BLE001 - a broken store must not kill the page
            return {}, f"The routines could not be read ({exc})."

    def _write(self, saved: Dict[str, dict]) -> None:
        routines.save_all(saved)

    # -- drawing -------------------------------------------------------------

    def refresh(self) -> None:
        """Rebuild the page from the store.

        **The whole body is cleared and the group rebuilt, not just the group
        emptied.** The first version called `common.clear(self._group)` and then
        appended a second `_AddGroup` to the body on every pass, so a page left
        open across several refreshes stacked one add-form per refresh; and
        clearing an `Adw.PreferencesGroup` logs *"tried to remove non-child"*.
        This is the shape `goals.py` uses.
        """
        common.clear(self._body)
        self._group = common.group()
        self._body.append(self._group)
        saved, why_not = self._load()

        if self._notice:
            self._group.add(common.row("", self._notice))
            self._notice = ""

        if why_not:
            self._group.add(common.row("", why_not))
            return

        if not saved:
            self._group.add(common.empty_state(
                "view-list-ordered-symbolic", "No routines yet",
                "\"Good morning\" is the one worth having first: save the request "
                "you would otherwise repeat every day, and then just say it."))
            self._body.append(_AddGroup(self))
            return

        for name in sorted(saved):
            self._group.add(self._row(name, saved[name]))
        self._body.append(_AddGroup(self))

    def _row(self, name: str, routine: Any) -> Gtk.Widget:
        request = _request_of(routine)
        run = Gtk.Button(label="Run")
        run.set_tooltip_text("Ask this as if you had typed it")
        run.connect("clicked", lambda _b, r=request: self._run(r))

        remove = Gtk.Button(icon_name="user-trash-symbolic")
        remove.set_tooltip_text("Delete this routine")
        remove.connect("clicked", lambda _b, n=name: self._delete(n))

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        buttons.append(run)
        buttons.append(remove)
        # `common.row` escapes the title, which is the point: a routine name is
        # text from a file, not a constant.
        return common.row(name, _subtitle(name, routine), suffix=buttons)

    # -- the controls --------------------------------------------------------

    def _run(self, request: str) -> None:
        """Send the request as if it had been typed, and say so when it cannot.

        The window is reached by attribute rather than assumed: a panel built
        where there is no window has none, and a Run button that silently does
        nothing is the dead control this repo has recorded five times.
        """
        if not request:
            self._notice = "This routine has nothing saved to run yet."
            self.refresh()
            return
        window = getattr(self._app, "window", None)
        submit = getattr(window, "submit_text", None)
        if not callable(submit):
            self._notice = "This routine could not be run from here."
            self.refresh()
            return
        # The notice first: a refresh after `submit_text` would race the turn.
        self._notice = "Asked it, as if you had typed it."
        self.refresh()
        GLib.idle_add(lambda: (submit(request), False)[1])

    def _save_new(self, name_text: str, request_text: str) -> None:
        name = (name_text or "").strip()
        request = (request_text or "").strip()
        if not name or not request:
            self._notice = "A routine needs a name and a request. Nothing was saved."
            self.refresh()
            return
        saved, why_not = self._load()
        if why_not:
            self._notice = why_not
            self.refresh()
            return
        saved[name] = {"request": request}
        try:
            self._write(saved)
        except Exception as exc:  # noqa: BLE001
            self._notice = f"Could not save it ({exc})."
            self.refresh()
            return
        self._notice = (f"Saved \"{name}\". Say it, or open this page and press "
                        f"Run - it asks for the same thing.")
        self.refresh()

    def _delete(self, name: str) -> None:
        saved, why_not = self._load()
        if why_not:
            self._notice = why_not
            self.refresh()
            return
        saved.pop(name, None)
        try:
            self._write(saved)
        except Exception as exc:  # noqa: BLE001
            self._notice = f"Could not delete it ({exc})."
            self.refresh()
            return
        self._notice = f"Deleted \"{name}\"."
        self.refresh()


class _AddGroup(Gtk.Box):
    """The add-a-routine form, rebuilt with the page so a save clears it."""

    def __init__(self, surface: _RoutinesSurface) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.set_margin_top(24)
        self._surface = surface
        self.append(common.row("", "Add one. A routine is the request you would "
                                 "otherwise repeat every day, saved under a "
                                 "phrase you can say."))
        self._name = Gtk.Entry()
        self._name.set_placeholder_text("good morning")
        self._request = Gtk.Entry()
        self._request.set_placeholder_text("weather, my calendar and the news")
        self.append(common.row("Name", suffix=self._name))
        self.append(common.row("Asks for", suffix=self._request))
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", self._on_save)
        self.append(common.row("", suffix=save))

    def _on_save(self, _button) -> None:
        self._surface._save_new(self._name.get_text(), self._request.get_text())


def build(app: Any) -> Gtk.Widget:
    page, set_content = common.surface(TITLE, SUBTITLE)
    surface = _RoutinesSurface(app, set_content)
    page.surface = surface
    # **A word from the vocabulary, not an empty one.** An unknown word
    # renders an uncoloured dot, which is the failure
    # `common.status_row`'s closed vocabulary exists to prevent.
    page.status = lambda: common.STATUS_UNKNOWN
    # Re-read on every showing: the `routines` skill saves one from a chat turn
    # while this page sits built and cached in the window.
    if hasattr(page, "connect") and common.adw_ready():
        page.connect("showing", lambda *_a: surface.refresh())
    return page


__all__ = ["TITLE", "ICON", "SECTION", "build"]
