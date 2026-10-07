"""Privacy surface: privacy mode, recent egress events, and per-sense consent.

The page is `surfaces/common.py`'s - an `Adw.NavigationPage` over a
`ToolbarView` and a `HeaderBar`, with the three groups below as
`Adw.PreferencesGroup`s - and the three groups are the questions this panel has
always answered: what is allowed to leave, what did leave, and what Chronoa
may read.

**A switch on a consent row is a permission, so its state is never guessed.**
`common.switch_row()` accepts the state to show and does not apply it, and its
callback is connected before a caller could set that state; `_switch_row` below
does both the right way round. Drawing a consent switch the wrong way round is
the worst failure this page has - it tells the user a sense is granted, or not,
when the opposite is true - and setting a switch *after* its handler is
connected fires that handler, which would mean opening this panel wrote a
setting and could turn privacy mode on by itself. Both are one-line repairs in
`common.py`; neither is a reason to keep hand-building the row here.

**Nothing here raises.** A sense registry that cannot be listed falls back to
the consent keys alone, an egress log that cannot be read is a row that says
so, and a privacy-mode probe that fails reports *on* - the safe reading -
rather than taking the window down with it. The three probes are wrapped
wherever they are called, not only where they fail, because a surface that
raises takes the window with it and the window is the product.
"""

from __future__ import annotations

from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from shani_chronoa import egress, markdown_lite
from shani_chronoa.config import _SENSE_CONSENT_KEYS
from shani_chronoa.gui.surfaces import common

TITLE = "Privacy"
ICON = "security-high-symbolic"
SUBTITLE = "What leaves this machine, and what Chronoa is allowed to read."


def _sense_names() -> list:
    """Every sense worth a consent row, from the registry, never raising."""
    try:
        from shani_chronoa.senses import discover_senses

        return sorted(discover_senses())
    except Exception:  # noqa: BLE001 - a surface shows what it can
        return sorted(_SENSE_CONSENT_KEYS)


def _recent_events(limit: int = 8) -> list:
    """The most recent egress events. Never raises: an unreadable log is an
    honest empty list, not a surface that crashes."""
    try:
        return egress.read_events(limit=limit)
    except Exception:  # noqa: BLE001 - the log path must never take the UI down
        return []


def _add(group: Gtk.Widget, row: Gtk.Widget) -> None:
    """Put a row into a group from `common.group()`.

    `Adw.PreferencesGroup` has `add()`; a plain `Gtk.Box` on GTK4 does not -
    `append` replaced `add` - and `common.group()` returns whichever of the two
    libadwaita allowed. This panel used to build `Adw.PreferencesGroup`s
    directly, so the plain-GTK case never arose; taking the row shape from
    `common.py` means it can, and asking which is one line.
    """
    adder = getattr(group, "add", None)
    if callable(adder):
        adder(row)
    else:
        group.append(row)


def _switch_of(row: Gtk.Widget) -> Optional[Gtk.Switch]:
    """The `Gtk.Switch` inside a row `common.switch_row()` built.

    `Adw.ActionRow` will not hand back its suffix widgets - there is no
    `get_suffix_widget` on it (measured on libadwaita 1.5) - and every caller
    here needs the switch itself to read and set the state the row is showing.
    One helper for that walk, rather than three copies of it.
    """
    if isinstance(row, Gtk.Switch):
        return row
    child = row.get_first_child()
    while child is not None:
        found = _switch_of(child)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def _switch_row(title: str, subtitle: str, active: bool) -> Gtk.Widget:
    """A switch row showing `active`, wired afterwards by its caller.

    The state is set here rather than passed to `common.switch_row`, which
    ignores it (measured: `active=True` leaves the switch off), and no
    `on_changed` is passed, because that helper connects its callback before
    returning - so a caller that set the state afterwards would fire its own
    toggle simply by opening the panel.
    """
    row = common.switch_row(title, subtitle)
    switch = _switch_of(row)
    if switch is not None:
        switch.set_active(bool(active))
    return row


def build(app) -> Gtk.Widget:
    config = app.config
    page, set_content = common.surface(TITLE, SUBTITLE)
    body = common.page_body(18)
    #: One recorder, so the sidebar's dot says the same word as the row at the
    #: top of this panel. This panel is the one place where the two matter most:
    #: "privacy mode is on" is the single most consequential claim the app makes
    #: about itself, and a dot that disagreed with the row under it would be the
    #: worst place to have that happen.
    recorder = common.StatusRecorder()

    # The panel's own health, above every group: is
    # privacy mode on, is it keeping data local, or
    # could it not be told? One row, one dot, one
    # word - the question the panel is opened for,
    # before the rows that hold the switches.
    try:
        privacy_on = bool(egress.privacy_mode_enabled())
        body.append(recorder.row(
            common.STATUS_OK if privacy_on else common.STATUS_OFF,
            "Privacy mode is on" if privacy_on else "Privacy mode is off",
            "nothing leaves this machine" if privacy_on
            else "Chronoa may use the network"))
    except Exception:  # noqa: BLE001 - privacy_mode_enabled already fails on, belt and braces
        body.append(recorder.row(
            common.STATUS_UNKNOWN,
            "Privacy mode could not be read",
            "egress.privacy_mode_enabled() raised"))

    # -- privacy mode --------------------------------------------------------
    mode_group = common.group(
        "Privacy mode",
        "With it on, Chronoa keeps data on this machine and sends nothing out.")
    try:
        privacy_on = bool(egress.privacy_mode_enabled())
    except Exception:  # noqa: BLE001 - privacy_mode_enabled already fails on, belt and braces
        privacy_on = True
    mode_row = _switch_row(
        "Privacy mode (local only)",
        "On: nothing leaves this machine" if privacy_on else "Off: Chronoa may use the network",
        privacy_on,
    )
    mode_switch = _switch_of(mode_row)

    def _toggle_privacy(switch, _pspec) -> None:
        target = switch.get_active()
        try:
            current = egress.privacy_mode_enabled()
        except Exception:  # noqa: BLE001
            current = switch.get_active()
        if bool(current) == bool(target):
            return
        activate = getattr(app, "activate_action", None)
        if callable(activate):
            activate("toggle-privacy", None)
        else:
            config.set("privacy-mode", "true" if target else "false")
        # Re-derive from the source of truth rather than trusting the click.
        try:
            switch.set_active(bool(egress.privacy_mode_enabled()))
        except Exception:  # noqa: BLE001
            pass

    if mode_switch is not None:
        mode_switch.connect("notify::active", _toggle_privacy)
    _add(mode_group, mode_row)
    body.append(mode_group)

    # -- recent egress -------------------------------------------------------
    # The path goes into the description rather than onto the group afterwards:
    # `Adw.PreferencesGroup.set_description` has no plain-GTK equivalent, and
    # `common.group()` takes the text as an argument, so this way both the
    # modern and the fallback group carry it.
    description = "From the local egress log."
    try:
        log_path = egress.summary().get("log", "")
    except Exception:  # noqa: BLE001 - why a real one, adw docs.
        log_path = ""
    if log_path:
        description = f"From the local egress log at {log_path}."
    events_group = common.group("Recent network activity", description)
    events = _recent_events()
    if not events:
        # The log could be empty or unreadable, and this row cannot tell the two
        # apart from here - so it says both. "Nothing left" would be the
        # confident wrong answer this repo keeps paying for.
        _add(events_group, common.row(
            "No recorded egress",
            "No outbound requests have been logged, or the log cannot be read."))
    for event in reversed(events):
        host = str(event.get("host", "") or "(unknown)")
        when = event.get("at", "")
        # **The purpose is the point of the row.** It used to be dropped, so an
        # upload of a recording read as
        # `POST https://api.openai.com/v1/audio/transcriptions - 53312 bytes` -
        # which looks like a data POST and not like the voice of the person
        # sitting at the machine. `egress.record` has carried a `purpose` since
        # model downloads, and `cloud_voice` writes `speech-recognition` and
        # `speech-synthesis`; none of it reached this panel.
        purpose = str(event.get("purpose", "") or "")
        # Escaped, because these strings come out of a log file and an
        # `Adw.ActionRow` renders its title and subtitle as Pango markup.
        detail = "{purpose}{method} {url} - {bytes_out} bytes{violation}".format(
            purpose=f"{purpose}: " if purpose else "",
            method=event.get("method", "GET"),
            url=event.get("url", ""),
            bytes_out=event.get("bytes_out", 0),
            violation=" - privacy violation" if event.get("violation") else "",
        )
        label = common.row(markdown_lite.escape(host), markdown_lite.escape(detail))
        if when:
            try:
                import time as _time

                label.set_tooltip_text(_time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(float(when))))
            except (TypeError, ValueError, OverflowError):
                pass
        _add(events_group, label)
    body.append(events_group)

    # -- per-sense consent ---------------------------------------------------
    consent_group = common.group(
        "Sense consent",
        "Each switch says whether Chronoa may use that sense right now.")
    for name in _sense_names():
        key = _SENSE_CONSENT_KEYS.get(name)
        granted = bool(key) and config.sense_allowed(name)
        # The person-facing title, with the sense's id as the subtitle - it was
        # the bare module id ("cgroup", "rfsense") over a "granted" / "not
        # granted" line that repeated what the switch beside it already shows.
        row = _switch_row(common.sense_title(name), name if key else
                          f"{name} - no consent key, so it cannot be granted here",
                          granted)
        switch = _switch_of(row)

        def _make_toggle(sense_name, sense_key, switch_row, sense_switch):
            def _toggle(_s, _pspec) -> None:
                if not sense_key:
                    return
                # Same write the settings window makes in _set_sense.
                config.set(sense_key, "true" if sense_switch.get_active() else "false")
                sense_switch.set_active(config.sense_allowed(sense_name))

            return _toggle

        if switch is not None:
            switch.connect("notify::active", _make_toggle(name, key, row, switch))
        _add(consent_group, row)
    body.append(consent_group)

    set_content(common.scrolled(body))
    page.status = recorder.status
    return page
