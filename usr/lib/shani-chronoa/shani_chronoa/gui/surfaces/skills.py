"""The skill whitelist as one filterable list.

Every row is one action the assistant can be asked to perform: the registry
name verbatim, the description its own schema carries (the same words the model
is handed), and its gate. The gate is a switch for a skill whose permission is a
real settings key, and an honest "always available" for a skill with none.

**A switch appears only where the permission is a genuine toggle.**
`capabilities.gated_by()` names the key a skill declares; the running settings
schema has to declare it too. A switch for a skill with no gate would be
offering a permission that does not exist, and a switch for a key this build
does not have would be a control that silently does nothing - the settings
window hits the same case and says so on the row (`settings_window/window.py`:
"no consent key in the installed schema - this cannot be granted"). So a gate is
one of three states, not two, exactly as `skills/list_capabilities.py` has it:
allowed, off, and *unknown* - a key the schema does not declare is not the same
as a permission the user chose to withhold, and rendering it as one is the
confident wrong answer this repo keeps writing down. An app that hands this
surface no settings at all is a fourth state and says that instead: it cannot
tell whether the key exists, which is not a claim about the schema.

Rows come from `discover_skills()` - the same live registry the model is handed,
read when the surface is built and never cached - so a skill that failed to load
has no row. Nothing here raises on a malformed entry: a user can drop a module
into `~/.config/shani-chronoa/skills/` (see `skills/__init__.py` for the contract),
and a broken drop-in must cost this surface a row, not the window. When the
whole registry cannot be read the surface says so instead of showing an empty
list, because "no skills" and "we could not look" are different answers.

Read-only apart from the gate switches, which write the same consent key the
settings window writes; no new key is introduced here.

**The page is `surfaces/common`'s.** `build()` returns the
`Adw.NavigationPage` from `common.surface(TITLE, SUBTITLE)`, and the rows are
`common.switch_row()` (`Adw.ActionRow` + switch suffix) for a gated skill and
`common.row()` for an ungated one, inside a `common.group()`
(`Adw.PreferencesGroup`), inside a `common.scrolled()`. What that changes is the
part this panel's whole point depends on: a screen reader announces an
`ActionRow`'s title, subtitle and target as one control, so a skill's name, its
description and the switch that gates it are announced together rather than as
three unrelated strings.

**Titles and subtitles are escaped here, because `common.row()` does not.**
Measured on libadwaita 1.5: `Adw.PreferencesRow.use-markup` defaults to **True**,
so an `Adw.ActionRow` parses both its title and its subtitle as Pango markup. A
skill called `<b>evil</b>` - which is a legal module name, and precisely the case
the escaping test below exists for - is not shown as bold text but silently
*dropped*: the label comes out empty and the only trace is a Gtk-WARNING on
stderr. `_plain()` is where untrusted text enters this file, and it escapes only
when libadwaita built the row: the plain-GTK answer from `common.row()` is a
`Gtk.Label`, which takes no markup and would print the entities themselves.

**The three-part row became an Adw two-part row.** The previous row was a bold
name, a description and a gate line, three lines of hand-built box. An
`Adw.ActionRow` has a title and a subtitle, so the gate state is the second line
of the subtitle - where it is still visible on the row rather than moved into a
tooltip - and the description is the first. Nothing was dropped to make that fit.
"""

from __future__ import annotations

import logging
from typing import Any, List, NamedTuple, Optional, Tuple

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk  # noqa: E402

from shani_chronoa import capabilities, markdown_lite  # noqa: E402
from shani_chronoa.gui.surfaces import common  # noqa: E402
from shani_chronoa.skills import discover_skills, is_valid_schema  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = "Skills"
ICON = "system-run-symbolic"

SUBTITLE = (
    "Everything Chronoa is allowed to be asked to do, read from the same live "
    "registry the model is handed. A gate here writes the same key Settings does."
)

#: Markers a test can find in the built tree, so no assertion has to read a
#: count back out of the object being counted.
ROW_CSS = "skill-row"

#: What a skill with no consent key says on its row. Wording rather than a
#: disabled switch: there is nothing to switch.
ALWAYS_AVAILABLE = "always available - no setting needed"

#: The state a gate is in when the running settings schema does not declare its
#: key. Distinct from "off" on purpose (see the module docstring).
UNKNOWN_GATE = "unknown - this build's settings schema does not declare this key"

#: A different unknown: the surface was handed no settings at all, so it cannot
#: even tell whether the key exists. Not the same claim as the line above, and
#: not the same as "off".
NO_SETTINGS = "unknown - no settings available to read this gate"

_ALLOWED = "allowed"
_OFF = "off - switch it on here or in Settings"

__all__ = ["TITLE", "ICON", "build", "Row"]


def _accessible(widget: Gtk.Widget, label: str) -> None:
    widget.update_property([Gtk.AccessibleProperty.LABEL], [label])


def _plain(text: str) -> str:
    """`text` as an Adw row's title or subtitle, escaped if that row is one.

    See the module docstring: `Adw.PreferencesRow.use-markup` defaults to True
    (measured on libadwaita 1.5), so a skill's own text containing a bare `&`
    or `<` leaves the label empty and silent. The plain-GTK answer from
    `common.row()` is a `Gtk.Label`, which takes no markup and would print the
    entities themselves, so the escape follows whichever branch built the row.
    """
    return markdown_lite.escape(text) if common.adw_ready() else text


def _add(group: Gtk.Widget, child: Gtk.Widget) -> None:
    """Put a row in a group, whichever kind `common.group()` built.

    `Adw.PreferencesGroup` takes rows through `add()`; the plain-GTK answer is a
    `Gtk.Box`, whose rows are appended. Duck-typed rather than tested against
    `Adw.PreferencesGroup`, so this module does not require libadwaita itself -
    `common` treats it as optional and so must everything built on it.
    """
    adder = getattr(group, "add", None)
    if callable(adder):
        adder(child)
    else:
        group.append(child)


def _switch_in(widget: Gtk.Widget) -> Optional[Gtk.Switch]:
    """The `Gtk.Switch` `common.switch_row()` put in this row.

    `common.switch_row()` hands back the row and `Adw.ActionRow` exposes no
    accessor for a suffix widget (`get_title_widget` does not exist on
    libadwaita 1.5 - measured), so it is found by walking. Both answers hold
    one: the Adw row carries it as its suffix, the plain-GTK one as the last
    child of the box `common.row()` builds.
    """
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Switch):
            return child
        found = _switch_in(child)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def _first_label(widget: Gtk.Widget) -> Optional[Gtk.Label]:
    """The first `Gtk.Label` anywhere under `widget`, or None.

    Returns None rather than a placeholder, which matters: a placeholder would
    be a non-None answer, and the caller would take it for the label it asked
    for - which is how the empty prefix box of an `Adw.ActionRow` once answered
    this question instead of the title.
    """
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            return child
        found = _first_label(child)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def _title_label(row: Gtk.Widget) -> Gtk.Label:
    """The `Gtk.Label` an `Adw.ActionRow` renders its title in.

    `Adw.ActionRow` has `get_title()` - a string - and no accessor for the label
    behind it, so it is found by walking. That is safe here for a reason worth
    writing down: an `ActionRow` lays its children out as prefixes, then the
    title and subtitle, then suffixes (measured), and this module adds no prefix,
    so the first label the row holds is its title. If a prefix label is ever
    added here this returns the wrong label, and
    `test_more_than_sixty_skill_rows_from_the_live_registry` fails on the name -
    a loud failure rather than a quiet one.

    A row with no label at all cannot come out of `common.row()`, so the
    fallback is an unattached empty label: `Row.title` stays a label, and the
    test that compares it with the registry's name fails rather than raising.
    """
    return _first_label(row) or Gtk.Label()


def _dim(text: str) -> Gtk.Label:
    """A plain-text label. `set_text` takes no markup at all, which is the one
    call that cannot be half-done for a string a skill module wrote."""
    label = Gtk.Label(xalign=0, hexpand=True, wrap=True)
    label.set_text(text)
    label.add_css_class("dim-label")
    return label


def _matches(haystack: str, query: str) -> bool:
    """True when every whitespace-separated token of `query` is in `haystack`.

    Read as a module global at call time so a test can break it on purpose; a
    filter that cannot fail is not a filter.
    """
    tokens = [token for token in (query or "").lower().split() if token]
    if not tokens:
        return True
    low = (haystack or "").lower()
    return all(token in low for token in tokens)


def _schema_fields(schema: Any) -> Optional[Tuple[str, str]]:
    """`(name, description)` from one registry schema, or None to skip it.

    Every field is read defensively and the whole thing is filtered through the
    loader's own validator first, so a schema that is not a dict, is missing
    its `function`, or names nothing is skipped instead of raising.
    """
    try:
        if not is_valid_schema(schema):
            return None
        function = schema.get("function") or {}
        name = function.get("name", "")
        description = function.get("description", "")
    except Exception:  # noqa: BLE001 - a registry entry nobody can read is skipped
        return None
    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(description, str):
        description = "" if description is None else str(description)
    return name.strip(), description.strip()


def _registry_schemas() -> Tuple[List[Any], Optional[str]]:
    """The live registry's schemas, or the reason there are none.

    `discover_skills()` skips an individual malformed skill module itself, but
    the registry as a whole can still fail. A surface that renders that as an
    empty list states "Chronoa has no skills" about a registry it never managed
    to read.
    """
    try:
        tools, _handlers = discover_skills()
    except Exception as exc:  # noqa: BLE001 - the window must still open
        logger.warning("skill registry unavailable; the skills list cannot be read",
                       exc_info=True)
        return [], f"{type(exc).__name__}: {exc}"
    try:
        return list(tools or ()), None
    except TypeError:
        return [], "the registry returned something that is not a list of schemas"


def _schema_declares(config: Any, key: str) -> bool:
    """Whether the running settings schema declares `key`.

    A config object with no key list at all (a stand-in, or a build whose
    schema could not be read) is taken at its word - `ChronoaConfig` always
    carries the list, empty when it has none, and an empty list is itself the
    answer "nothing can be granted".
    """
    keys = getattr(config, "_valid_keys", None)
    return True if keys is None else key in keys


def _gate_state(config: Any, key: str) -> Optional[str]:
    """`allowed` / `off` / None (unknown). Fails toward unknown, never open."""
    if config is None or not _schema_declares(config, key):
        return None
    try:
        return _ALLOWED if config.get_bool(key, False) else _OFF
    except Exception:  # noqa: BLE001 - a key that cannot be read is not "on"
        logger.debug("could not read the gate %s", key, exc_info=True)
        return None


def _gate_key(name: str, description: str) -> Optional[str]:
    """The consent key gating this skill, or None. A key that cannot be read
    is treated as no gate: the row still shows, it just claims nothing."""
    try:
        return capabilities.gated_by(name, description)
    except Exception:  # noqa: BLE001
        logger.debug("no readable gate for %s", name, exc_info=True)
        return None


class Row(NamedTuple):
    """One built-in skill row, and the facts a test needs to check it."""

    widget: Gtk.Widget
    title: Gtk.Label
    name: str
    description: str
    gate_key: Optional[str]
    switch: Optional[Gtk.Switch]
    haystack: str


class _SkillsSurface:
    """The page's logic, and the page it fills.

    Not a widget any more. `build()` hands back the `Adw.NavigationPage` itself,
    because that is what a window puts in its `Adw.NavigationView`; wrapping it
    in a second box would mean the window got a box and every caller got a
    wrapper. So the rows live in the page's content area and this object keeps
    the state - which rows exist, what each gate reads, what the filter hid -
    with `build()` publishing the handful of read-only accessors onto the page.
    """

    def __init__(self, app: Any, set_content: Any) -> None:
        self._config = getattr(app, "config", None)
        self._rows: List[Row] = []
        self._updating = False
        self.registry_error: Optional[str] = None

        body = common.page_body()
        _margined(body)

        # The panel's own health, above the search and the list: how many
        # skills the whitelist holds, and whether the registry answered.
        # One row, one dot, one word, before the rows the panel exists to
        # show - "is the whitelist readable" is the question the panel is
        # opened for, and it used to be answered only by counting rows.
        #: The panel's health, written once into the row above and read back
        #: for the dot on its sidebar row. Same value, so the dot cannot
        #: disagree with the sentence directly above it.
        self.status_recorder = common.StatusRecorder()
        self._status_slot = common.page_body()
        _margined(self._status_slot)
        body.append(self._status_slot)

        self.search = common.search_entry("Filter skills by name or description")
        # One string as the accessible label and the front of the tooltip -
        # see `_switch_tooltip` for why the tooltip is the readable one.
        spoken = "Filter skills by name or description"
        self.search.set_tooltip_text(f"{spoken} - the list narrows as you type")
        # `changed`, not the debounced `search-changed` that
        # `common.search_entry()` wires: the latter fires 150ms after the last
        # keystroke and `set_text()` emits neither, so a filter bound to it would
        # not narrow as the user types, and a test that set the text would see a
        # list the surface itself never shows. The shared helper still builds the
        # entry and labels it; only the signal this panel filters on is its own.
        self.search.connect("changed",
                            lambda entry: self.apply_filter(entry.get_text()))
        body.append(self.search)

        self.summary = _dim("")
        body.append(self.summary)

        self._group = common.group("Skills", "One row per skill in the whitelist.")
        body.append(self._group)

        self.no_matches = _dim("No skill matches this filter.")
        self.no_matches.set_visible(False)
        body.append(self.no_matches)

        body.append(_dim(
            "A skill that fails to load has no row at all: the list above is the "
            "registry, not a hand-kept copy of it."
        ))

        self._body = body
        set_content(common.scrolled(body))
        self._load()

    # -- building ---------------------------------------------------------
    def _load(self) -> None:
        schemas, self.registry_error = _registry_schemas()
        for schema in schemas:
            fields = _schema_fields(schema)
            if fields is None:
                continue
            name, description = fields
            self._rows.append(self._add_row(name, description))

        # The panel's own health, rendered once the registry has been
        # read: how many skills the whitelist holds, and whether the
        # registry answered at all. The status row is replaced, not
        # appended to, so a reload cannot stack a second one.
        common.clear(self._status_slot)
        if self.registry_error:
            self._status_slot.append(self.status_recorder.row(
                common.STATUS_UNKNOWN,
                "Could not read the registry",
                _plain(self.registry_error)))
        elif self._rows:
            gated = sum(1 for row in self._rows if row.gate_key is not None)
            self._status_slot.append(self.status_recorder.row(
                common.STATUS_OK,
                f"{len(self._rows)} skills in the whitelist",
                f"{gated} of them need a setting switched on first"))
        else:
            self._status_slot.append(self.status_recorder.row(
                common.STATUS_ATTENTION,
                "No skills loaded",
                "The registry returned nothing. That is not the same "
                "as having no capabilities: the skill modules may all "
                "have failed to import."))

        if not self._rows:
            if self.registry_error:
                title = "Could not read the skill registry"
                reason = (f"{self.registry_error}. This is not an empty list of "
                          f"skills - it is a list that could not be read.")
            else:
                title = "No skills loaded"
                reason = ("The registry returned nothing. That is not the same "
                          "as having no capabilities: the skill modules may all "
                          "have failed to import.")
            # The group is taken out and a status page put in its place rather
            # than an emptied list left standing, because a group with nothing
            # in it is precisely the "Chronoa has no skills" reading that page
            # exists to avoid.
            self._body.remove(self._group)
            self._body.append(common.empty_state(ICON, title, _plain(reason)))

        self.apply_filter(self.search.get_text())

    def _add_row(self, name: str, description: str) -> Row:
        gate_key = _gate_key(name, description)
        gate_name = capabilities.GATE_NAMES.get(gate_key, gate_key) if gate_key else ""
        state_text, switchable = self._gate_reading(gate_key)

        # The description is the first line of the subtitle and the gate state
        # the second: an Adw row has one subtitle, and the state is the part that
        # decides whether the row is one a person can act on.
        lines = [description or "(no description)"]
        # The person-facing title, from the same table the Help window uses;
        # the tool's id leads the subtitle so it can still be told apart and
        # searched for. Every row was titled with the id - `airplane_mode`,
        # `analyze_table` - over a description written for the model.
        # A drop-in skill with no entry keeps its name as its title.
        title = capabilities.tool_title(name)
        if title != name:
            lines[0] = f"{name} - {lines[0]}"
        switch = None
        if gate_key is None:
            # No switch for a skill that declares no gate: there is nothing to
            # switch, and offering one would be a permission that does not exist.
            lines.append(ALWAYS_AVAILABLE)
            widget = common.row(title=_plain(title), subtitle=_plain("\n".join(lines)))
        else:
            lines.append(f"{gate_name} - {state_text}")
            widget = common.switch_row(
                title=_plain(title),
                subtitle=_plain("\n".join(lines)),
                on_changed=lambda active, k=gate_key: self._on_toggled(active, k),
            )
            switch = _switch_in(widget)

        widget.add_css_class(ROW_CSS)
        widget.set_tooltip_text(f"{name}\n{description or '(no description)'}")
        _accessible(widget, f"Skill {name}")

        if switch is not None:
            # The opening state is set while `_updating` holds the handler off,
            # so loading a row never writes the setting it just read.
            self._updating = True
            try:
                switch.set_active(state_text == _ALLOWED)
                switch.set_sensitive(switchable)
            finally:
                self._updating = False
            self._switch_tooltip(switch, name, gate_name, gate_key, state_text)

        _add(self._group, widget)
        return Row(widget=widget, title=_title_label(widget), name=name,
                   description=description, gate_key=gate_key, switch=switch,
                   haystack=f"{name} {title} {description}")

    def _gate_reading(self, key: Optional[str]) -> Tuple[str, bool]:
        """What this row says about its gate, and whether a switch may move.

        Two values rather than one because the row's *wording* and the control's
        *sensitivity* answer different questions: a key this build does not
        declare is stated as unknown and its switch is insensitive, and an app
        with no settings at all is stated as a fourth thing it cannot tell.
        """
        if key is None:
            return ALWAYS_AVAILABLE, False
        if self._config is None:
            return NO_SETTINGS, False
        state = _gate_state(self._config, key)
        return (state if state in (_ALLOWED, _OFF) else UNKNOWN_GATE), state in (
            _ALLOWED, _OFF)

    def _switch_tooltip(self, switch: Gtk.Switch, name: str, gate_name: str,
                        key: str, state_text: str) -> None:
        """Name the control, and say why it cannot be moved when it cannot.

        One string, used as the accessible label and as the front of the
        tooltip: the accessible LABEL is the label a screen reader announces,
        and this PyGObject build exposes no getter for it (see
        `tests/test_window_ux.py`), so the tooltip is what can be read back.
        """
        spoken = f"Allow {name}: {gate_name}"
        switch.set_tooltip_text(
            f"{spoken} - switch to let Chronoa do this" if switch.get_sensitive()
            else f"{spoken} - cannot be switched here: {state_text}"
        )
        _accessible(switch, spoken)

    # -- filtering --------------------------------------------------------
    def _on_toggled(self, active: bool, key: str) -> None:
        if self._updating:
            return
        if self._config is None:
            logger.info("no settings available; the gate %s cannot be written", key)
            return
        try:
            self._config.set(key, "true" if active else "false")
        except Exception:  # noqa: BLE001 - a refused write must not kill the window
            logger.warning("could not write the gate %s", key, exc_info=True)
        self._sync_switches()

    def _sync_switches(self) -> None:
        """Re-read every switch: one key can gate several skills."""
        self._updating = True
        try:
            for row in self._rows:
                if row.switch is None or row.gate_key is None:
                    continue
                row.switch.set_active(_gate_state(self._config, row.gate_key) == _ALLOWED)
        finally:
            self._updating = False

    def apply_filter(self, query: str) -> int:
        """Show the rows matching `query`; returns how many are visible.

        Called by the search entry and directly by tests, so the count a test
        asserts is the count the widget itself reports.
        """
        visible = 0
        for row in self._rows:
            show = _matches(row.haystack, query)
            row.widget.set_visible(show)
            if show:
                visible += 1
        gated = sum(1 for row in self._rows if row.gate_key is not None)
        self.summary.set_text(
            f"Showing {visible} of {len(self._rows)} skills "
            f"({gated} of them need a setting switched on first)"
        )
        self.no_matches.set_visible(bool(self._rows) and visible == 0)
        return visible

    # -- public for tests -------------------------------------------------
    def rows(self) -> List[Row]:
        return list(self._rows)

    def visible_row_count(self) -> int:
        return sum(1 for row in self._rows if row.widget.get_visible())

    def gated_row_count(self) -> int:
        """Rows that name a gate, whether or not one is switchable here."""
        return sum(1 for row in self._rows if row.gate_key is not None)

    def switch_count(self) -> int:
        """Rows offering a switch the user can actually move."""
        return sum(1 for row in self._rows
                   if row.switch is not None and row.switch.get_sensitive())


def _margined(widget: Gtk.Widget) -> Gtk.Widget:
    """The margins every panel in this package puts around its content."""
    widget.set_margin_top(12)
    widget.set_margin_bottom(12)
    widget.set_margin_start(12)
    widget.set_margin_end(12)
    return widget


def build(app: Any) -> Gtk.Widget:
    """Build the skills page. `app` is anything with a `config`, including
    nothing at all - without one the gates are shown but none is a switch.

    Returns the `Adw.NavigationPage` a window puts in its
    `Adw.NavigationView`, with this surface's read-only API attached to it so a
    caller (or a test) can ask what the page is showing without the page being
    wrapped in something else first.
    """
    page, set_content = common.surface(TITLE, SUBTITLE)
    surface = _SkillsSurface(app, set_content)
    page.surface = surface
    page.rows = surface.rows
    page.visible_row_count = surface.visible_row_count
    page.gated_row_count = surface.gated_row_count
    page.switch_count = surface.switch_count
    page.apply_filter = surface.apply_filter
    page.search = surface.search
    page.summary = surface.summary
    page.no_matches = surface.no_matches
    # Read once, after the load: `registry_error` is set during it and does not
    # change afterwards, so the page carries the answer rather than a live view
    # of an object it does not own.
    page.registry_error = surface.registry_error
    # What this panel says about itself, for the sidebar's health dot. Read from
    # the same recorder the row at the top of the panel was written through, so
    # the dot and the row are one statement rather than two that can drift.
    page.status = surface.status_recorder.status
    return page