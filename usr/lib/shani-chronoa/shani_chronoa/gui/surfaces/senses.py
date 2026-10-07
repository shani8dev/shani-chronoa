"""The Senses surface: every sense the registry knows, and the switch that consents to it.

One row per entry in `shani_chronoa.senses.discover_senses()`, each with its
own switch, and each switch reading and writing that sense's own
`<name>-sense-enabled` key through `ChronoaConfig.get_bool`/`set` - the same two
calls the settings window's `_read_bool`/`_set_bool` make, deliberately *not*
`sense_allowed()`. `sense_allowed()` also folds in `privacy-mode` and the
retired-key aliases, so it answers "may this sense act right now", which is a
different question from the one this surface asks: "which permissions has this
person granted". The footer says when the two answers differ, rather than
silently showing a switch that is on and a sense that is still refused.

Rows sort consent-on first and then by name, so a granted permission is visible
without scrolling to look for it.

**The page is `surfaces/common`'s now, and what that changed.** `build()` returns
the `Adw.NavigationPage` from `common.surface(TITLE, SUBTITLE)` - a title, a
subtitle and an `Adw.ToolbarView` under an `Adw.HeaderBar` - and every row is a
`common.switch_row()` (an `Adw.ActionRow` whose suffix is a `Gtk.Switch`) inside
a `common.group()` (`Adw.PreferencesGroup`), all of it inside a
`common.scrolled()`. This module used to hand-build `Gtk.Box` / `Gtk.Label` /
`Gtk.Switch`, and the reason it gave for that was sound: every Adw widget needs
`Adw.init()` to have run before it is constructed, and this surface has to be
constructible by anything that imports it - a bare `Gtk.Application`, a test
process, a tool. That reasoning has not gone away, it is `common`'s problem
now. `common` calls `Adw.init()` at import time, before a single widget exists,
so a surface built first in a test process still cannot be the one that breaks;
and every helper in it has a plain-GTK answer, so a build with no libadwaita is
a panel that looks plain rather than a window that will not open. What the Adw
rows bought is the part a box of labels cannot: a screen reader announces an
`ActionRow`'s title, subtitle and target as one control, and forty-five of them
as forty-five unrelated strings.

**Titles and subtitles are escaped here, because `common.row()` does not.**
Measured on libadwaita 1.5: `Adw.PreferencesRow.use-markup` defaults to **True**,
so an `Adw.ActionRow` parses both its title and its subtitle as Pango markup.
A sense name or a sense module's own description containing a bare `&` is not
shown at all in that case - the label comes out empty, and the only trace is a
Gtk-WARNING on stderr, which is this repo's "plausible wrong answer" wearing a
silent face. `_plain()` is therefore where untrusted text enters this file, and
it escapes only when libadwaita built the row: the plain-GTK answer from
`common.row()` is a `Gtk.Label`, which takes no markup and would print the
entities themselves.

Every failure mode here is a state, not an exception: a sense with no row in
`config._SENSE_CONSENT_KEYS` is shown `UNKNOWN` and its switch is insensitive,
because a row that claimed to be off while being ungrantable would send someone
looking for a switch that does not exist.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Dict, List, Optional, Tuple

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from shani_chronoa.config import _SENSE_CONSENT_KEYS  # noqa: E402
from shani_chronoa.gui.surfaces import common  # noqa: E402
from shani_chronoa.senses import discover_senses  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = "Senses"
ICON = "preferences-desktop-screensaver-symbolic"

SUBTITLE = (
    "Every sense Chronoa has, and whether you have agreed to let it run. "
    "One switch per sense; a sense that is off is never invoked at all."
)

#: Markers a test can find in the built tree, so no assertion has to read a
#: count back out of the object being counted.
ROW_CSS = "sense-row"

#: Longest description a row shows before it is cut on a word boundary. A sense
#: docstring's opening paragraph is written to be read, not to be a label, and
#: several of them run to several lines.
SUMMARY_LIMIT = 110


def _plain(text: Any) -> str:
    """`text` as an Adw row's title or subtitle, escaped if that row is one.

    See the module docstring: `Adw.PreferencesRow.use-markup` defaults to True
    (measured on libadwaita 1.5), so a bare `&` or `<` in a sense's own text
    empties the label silently. `common.row()`'s plain-GTK answer is a
    `Gtk.Label`, which takes no markup and would print the entities themselves,
    so the escape follows whichever branch built the row.
    """
    flat = str(text or "")
    return GLib.markup_escape_text(flat, -1) if common.adw_ready() else flat


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

    `common.switch_row()` hands back the row, and `Adw.ActionRow` exposes no
    accessor for a suffix widget (`get_title_widget` does not exist on
    libadwaita 1.5 - measured), so it is found by walking - which is also how
    the tests find them. Both answers hold one: the Adw row carries it as its
    suffix, the plain-GTK one as the last child of the box `common.row()`
    builds. `None` means the row has no switch to configure, which a caller
    reports as a row with no switch rather than as an exception into the window.
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


def _clip(text: Any, limit: int = SUMMARY_LIMIT) -> str:
    """One line of `text`, collapsed, cut at a word boundary with an ellipsis."""
    flat = " ".join(str(text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"


def _first_paragraph(doc: Any) -> str:
    """The opening paragraph of a docstring, as a single line."""
    if not isinstance(doc, str) or not doc.strip():
        return ""
    return " ".join(doc.strip().split("\n\n", 1)[0].split())


def _schema_summary(sense: Any) -> str:
    """The schema description's first sentence, for a sense with no docstring."""
    schema = getattr(sense, "schema", None)
    function = schema.get("function") if isinstance(schema, dict) else None
    description = function.get("description") if isinstance(function, dict) else None
    if not isinstance(description, str) or not description.strip():
        return ""
    return description.strip().split(". ", 1)[0].rstrip(".")


def _unprefixed(summary: str) -> str:
    """`summary` without the "Sense:" / "Display sense:" opener most modules use.

    Every row on this page is a sense, so the word in front of each description
    is the page's own title repeated forty times. Only a leading "...sense:" is
    taken; a colon later in the sentence is part of what it says.
    """
    head, sep, rest = summary.partition(": ")
    if sep and head.lower().endswith("sense") and len(head) <= 24 and rest:
        return rest[:1].upper() + rest[1:]
    return summary


def _describe(sense: Any) -> str:
    """One line about `sense`, from the sense's own text rather than from here.

    Its module docstring first, because every sense module opens with a
    deliberate one-line summary; the schema description is the fallback, since
    a user drop-in loaded from `~/.config/shani-chronoa/senses/` is not in
    `sys.modules` under its own name and so has no docstring to read.
    """
    run = getattr(sense, "run", None)
    module = sys.modules.get(getattr(run, "__module__", "") or "") if run is not None else None
    summary = _clip(_first_paragraph(getattr(module, "__doc__", None)))
    return summary or _clip(_schema_summary(sense)) or "No description available."


class _Consent:
    """One sense's consent key: what it is, what it says, and how to write it.

    `ChronoaConfig.get_bool` already answers its `default` for a key the running
    schema does not declare and `set` logs and returns for one it does not know,
    so neither of those needs guarding. What does need it is a `config` that is
    absent entirely - a window constructed before settings loaded, a stub - and
    the rule this file follows everywhere else is that a control which cannot
    read or write its own state says so rather than raising into the window.
    """

    def __init__(self, config: Any, key: Optional[str]) -> None:
        self._config = config
        self._key = key or ""

    @property
    def mapped(self) -> bool:
        """False when no consent key names this sense at all."""
        return bool(self._key)

    @property
    def key(self) -> str:
        """The key, or the one this sense's name implies, for the UNKNOWN row."""
        return self._key or ""

    def granted(self) -> bool:
        if not self._key or self._config is None:
            return False
        try:
            return bool(self._config.get_bool(self._key, False))
        except Exception:  # noqa: BLE001 - an unreadable key is denied, never raised
            logger.debug("cannot read consent key %s", self._key, exc_info=True)
            return False

    def write(self, value: bool) -> None:
        if not self._key or self._config is None:
            return
        try:
            self._config.set(self._key, "true" if value else "false")
        except Exception:  # noqa: BLE001 - a rejected write is re-derived, not raised
            logger.debug("cannot write consent key %s", self._key, exc_info=True)

    def rederive(self, switch: Gtk.Switch) -> None:
        """Put the switch back to what the config says, not to what was clicked.

        The settings window does this too, for the same reason: a switch showing
        a state the system does not agree with is the most misleading thing this
        surface can do, and a refused write is exactly when it happens.
        """
        switch.set_active(self.granted())


class _Toggle:
    """One row's switch handler, and the flag that keeps the first write quiet.

    `common.switch_row()` wires `on_changed` before it hands the row back, so
    setting the switch's opening state afterwards would fire that handler and
    write the very key the row just read. `quiet` is the guard: it is up while
    the row is being built and down once the switch shows what the config said.
    Loading this panel therefore writes nothing at all, which is the same
    property the skills surface keeps and the settings window keeps.
    """

    def __init__(self, consent: _Consent) -> None:
        self.consent = consent
        self.quiet = True
        self.switch: Optional[Gtk.Switch] = None

    def __call__(self, active: bool) -> None:
        if self.quiet or self.switch is None:
            return
        self.consent.write(bool(active))
        self.consent.rederive(self.switch)


def _state_text(name: str, consent: _Consent, granted: bool) -> Tuple[str, str]:
    """The (state line, accessible label) pair for one row."""
    if not consent.mapped:
        return (
            f"UNKNOWN - no consent key names this sense, so {name}-sense-enabled "
            "would have to exist in the installed schema before it can be granted",
            f"Consent switch for the {name} sense: unavailable, no consent key",
        )
    state = "granted" if granted else "not granted"
    # The visible state line is empty for a mapped sense: the switch beside the
    # row already says on or off, and a third subtitle line reading "granted"
    # under every switch that is on was the same fact twice. The accessible
    # label keeps it, because a screen reader announces the label, not the knob.
    return "", f"Consent switch for the {name} sense: {state}"


def _sense_row(name: str, sense: Any, consent: _Consent) -> Gtk.Widget:
    """One sense: its name, what it is for in a line, its state, and its switch."""
    summary = _describe(sense)
    granted = consent.granted() if consent.mapped else False
    state, accessible = _state_text(name, consent, granted)
    key = consent.key or f"{name}-sense-enabled"

    handler = _Toggle(consent)
    row = common.switch_row(
        title=_plain(common.sense_title(name)),
        subtitle=_plain(f"{_unprefixed(summary)}\n{state}" if state else _unprefixed(summary)),
        on_changed=handler,
    )
    row.add_css_class(ROW_CSS)
    row.set_tooltip_text(f"{name} ({key})\n{summary}\n{state}")

    toggle = _switch_in(row)
    handler.switch = toggle
    if toggle is not None:
        # Opening state first, then the tooltip, then the handler is released:
        # an UNKNOWN row's switch is insensitive, and a click that could grant a
        # key the schema does not declare would be the worst thing this panel
        # could do.
        toggle.set_active(granted)
        toggle.set_sensitive(consent.mapped)
        toggle.set_tooltip_text(f"Allow or deny the {name} sense ({key}). {summary}")
        toggle.update_property([Gtk.AccessibleProperty.LABEL], [accessible])
    handler.quiet = False

    row._sense_name = name
    row._consent_key = key
    row._switch = toggle
    return row


def _registry() -> Optional[Dict[str, Any]]:
    """The discovered senses, or None if the registry could not be loaded at all.

    `discover_senses` skips a malformed sense with a log line rather than
    raising, so the failure worth surviving here is the package import itself
    failing - and then this surface has nothing to list and has to say so
    instead of raising into the window. An empty dict is *not* that failure: it
    is a registry that loaded and found nothing, and it gets its own page.
    """
    try:
        registry = discover_senses()
    except Exception:  # noqa: BLE001 - a surface shows what it can, never crashes the window
        logger.warning("sense registry failed to load", exc_info=True)
        return None
    return registry if isinstance(registry, dict) else None


def _ordered(registry: Dict[str, Any], config: Any) -> List[Tuple[bool, str, Any, _Consent]]:
    """Every sense as (granted, name, sense, consent), consent-on first.

    Reading every consent key is what the sort costs; it is one `get_bool` per
    sense and it is also the only way the summary can say how many are on.
    """
    entries = []
    for name, sense in registry.items():
        consent = _Consent(config, _SENSE_CONSENT_KEYS.get(str(name)))
        entries.append((consent.granted(), str(name), sense, consent))
    entries.sort(key=lambda entry: (not entry[0], entry[1]))
    return entries


def _footer(config: Any) -> str:
    """The one line where this surface and `sense_allowed()` can disagree."""
    text = "Each switch writes its own key, so there is no global permission here. "
    try:
        privacy = config is not None and bool(config.get_bool("privacy-mode", True))
    except Exception:  # noqa: BLE001 - a config that cannot be read is not a crash
        privacy = False
    if privacy:
        text += (
            "Privacy mode is on, so a sense that reaches outside this machine "
            "stays refused even when it is granted here. "
        )
    return text + "A sense that is off is never invoked at all."


def _margined(widget: Gtk.Widget) -> Gtk.Widget:
    """The margins every panel in this package puts around its content."""
    widget.set_margin_top(12)
    widget.set_margin_bottom(12)
    widget.set_margin_start(12)
    widget.set_margin_end(12)
    return widget


def _note(text: str) -> Gtk.Label:
    """A dim line of plain text. `set_text` takes no markup at all, which is
    the one call that cannot be half-done for a string that came from a file."""
    label = Gtk.Label(xalign=0.0, hexpand=True, wrap=True)
    label.set_text(text)
    label.add_css_class("dim-label")
    return label


def _summary(granted: int, total: int) -> Gtk.Widget:
    """The line above the rows: what this panel is, in a word and a count.

    A panel that lists sixteen permissions and renders every one of them the
    same shade of grey cannot be scanned - a person has to read all sixteen
    subtitles to learn that nothing is switched on. So the state comes first,
    in the one place the eye lands, and the sentence becomes the detail under
    it rather than the only thing there was.

    "Ready" here means *every sense has consent*, not *everything works*: this
    panel's question is permission, and the answer to "can this read my
    calendar" is a separate panel with its own answer. Saying so here is the
    honest version of a green light.
    """
    if total == 0:
        return common.status_row(
            common.STATUS_UNKNOWN,
            "No senses are registered",
            "there is nothing to grant, which is not the same as nothing granted",
        )
    if granted == total:
        return common.status_row(
            common.STATUS_OK,
            f"All {total} senses have consent granted",
            "a sense with no permission is never invoked at all",
        )
    # Most senses default off on purpose, so "some are off" is the shipped
    # state and not something to fix. Only a registry with nothing granted at
    # all is called off; neither case is red.
    return common.status_row(
        common.STATUS_OK if granted else common.STATUS_OFF,
        f"{granted} of {total} senses have consent granted",
        "rows with a granted permission are first; the rest are never invoked",
    )


def build(app) -> Gtk.Widget:
    """The senses page for `app` - anything with a `config` will do, and one
    without it builds every row disabled rather than raising.

    The return value is the `Adw.NavigationPage` a window puts in its
    `Adw.NavigationView`, carrying `_sense_rows` for callers that need the rows
    themselves.
    """
    page, set_content = common.surface(TITLE, SUBTITLE)
    config = getattr(app, "config", None)
    rows: List[Gtk.Widget] = []

    body = _margined(common.page_body())
    registry = _registry()
    # Holds this panel's verdict for `status()` below. A one-slot list rather
    # than a nonlocal, because the closure is attached to the page after the
    # body is built and the two must not be able to disagree.
    _status_state: List[Optional[str]] = [None]
    if registry is None:
        body.append(_margined(common.empty_state(
            ICON,
            "The sense registry could not be loaded",
            "No sense can be listed or granted from here. This is a failure to "
            "load, not an absence of senses.",
        )))
    elif not registry:
        body.append(_margined(common.empty_state(
            ICON,
            "No senses are registered",
            "No module under shani_chronoa/senses/ declares a SENSES list and "
            "~/.config/shani-chronoa/senses/ is empty, so there is nothing to "
            "grant either.",
        )))
    else:
        entries = _ordered(registry, config)
        granted = sum(1 for entry in entries if entry[0])
        body.append(_summary(granted, len(entries)))
        _status_state[0] = (
            common.STATUS_OK if granted else common.STATUS_OFF
        )
        group = common.group("Senses", "One switch per sense, granted first.")
        for _granted, name, sense, consent in entries:
            row = _sense_row(name, sense, consent)
            rows.append(row)
            _add(group, row)
        body.append(group)

    body.append(_note(_footer(config)))

    set_content(common.scrolled(body))
    page._sense_rows = rows

    # What this panel says about itself, for the sidebar's health dot. Computed
    # from the same `entries` the rows were built from, so the dot cannot
    # disagree with the panel - there is no second count to fall out of date.
    def status() -> str:
        live = _status_state[0]
        if live is None:
            return common.STATUS_UNKNOWN
        return live

    page.status = status
    return page


# Kept for tests and other surfaces that want the same row semantics.
__all__ = ["TITLE", "ICON", "build"]