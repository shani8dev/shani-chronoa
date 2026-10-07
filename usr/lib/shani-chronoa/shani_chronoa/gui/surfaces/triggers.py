"""The trigger rules the engine could act on, as one read-only list.

Rows come from `triggers.rules.RuleStore` and `triggers.event_rules.
EventRuleStore` - the same two stores `skills/manage_triggers.py` lists and the
engines fire from, so what this shows and what would run are the same records
rather than a second copy of the truth. Each row names the rule, says whether
it is armed or disarmed, and says what it is waiting on: the sense for a percept
rule, the event type and what it watches for an event rule. Rule names,
substrings, sources and arguments are user text out of a file on disk, so every
one of them is escaped before it reaches a row.

Read-only on purpose. Arming and disarming is gated by
`triggers.TRIGGER_CONTROL_KEY` (`manage_triggers` refuses without it) and
rewrites the user's rules file, so this surface neither starts the engine nor
adds, removes, or flips a rule. The footer says what an armed rule still needs
(`trigger-control-enabled`, plus its own sense and actuator gates), because
"armed" is a record, not a promise that anything will run.

A store that cannot be read - corrupt, truncated, or holding a rule this build
cannot parse - is shown as a plain sentence naming the file, and the other
store still lists. `RuleStore` refuses to load such a file on purpose (an
armed rule that quietly becomes a no-op is worse than an absent one), and the
surface's job here is to say that rather than to show an empty list, which
reads as "nothing is armed".

Importing this module constructs nothing and opens no file: the stores are
opened per `build(app)`, and a caller that already has them can hand them in on
the app (`trigger_rule_store` / `event_rule_store`) instead of letting the
per-user data directory be resolved.

**The page is `surfaces/common`'s now.** `build()` returns the
`Adw.NavigationPage` from `common.surface(TITLE, SUBTITLE)`, the rules sit in a
`common.group()` per kind (`Adw.PreferencesGroup`), each rule is a
`common.row()` (`Adw.ActionRow`) and the whole thing is inside a
`common.scrolled()`. Two consequences worth naming, both measured rather than
assumed:

- **`Adw.PreferencesRow.use-markup` defaults to True** (libadwaita 1.5), so an
  `ActionRow` parses its title and subtitle as Pango markup. A rule named
  `<b>x</b>` used to be escaped on the way into a `set_markup` call; it is now
  escaped by `_plain()` on the way into a widget that parses markup on its own.
  Unescaped it is not merely bold - the label comes out *empty*, with only a
  Gtk-WARNING on stderr, which would be the worst possible answer for a rule
  someone cannot afford to lose track of. The plain-GTK answer from
  `common.row()` is a `Gtk.Label`, which takes no markup, so `_plain()` escapes
  only when libadwaita built the row.
- **`Adw.StatusPage` parses its title and description as markup too** (measured
  the same way, with no `use-markup` property to set). `common.empty_state()` is
  therefore given `_plain()` text here for the same reason, and the local
  `_note()` lines use `set_text`, which takes no markup at all.

**An Adw row has a title and a subtitle, and a rule row wants three lines.**
Name, then state, then what it fires on and what it runs. The state leads the
subtitle (`_detail()` puts it there) rather than moving to a tooltip, because
"disarmed" and "armed, parked (actuator kept failing)" are the answers this
panel exists to give and a tooltip is a footnote nobody reads. What that costs
is the red/grey colouring the old hand-built row gave the state word: an
`ActionRow`'s text slots are plain text, and the alternative - markup in the
subtitle - would put every rule name and argument back on the markup path that
the two points above exist to keep off it. The words carry the state; the
colour did not.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, List

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from shani_chronoa.gui.surfaces import common  # noqa: E402
from shani_chronoa.triggers import (  # noqa: E402
    MATCH_ANY,
    MATCH_SUBSTRING,
    TRIGGER_CONTROL_KEY,
    EventRuleStore,
    RuleStore,
)

logger = logging.getLogger(__name__)

TITLE = "Triggers"
ICON = "preferences-system-time-symbolic"

# Not "read-only" any more: the Arm a rule form below arms through the chat
# skill. Nothing on this page *fires* a rule - firing is the engine's.
SUBTITLE = (
    "The trigger rules this assistant could act on, read from the same two rule "
    "files the engines fire from. Arm or remove rules here; nothing on this page "
    "fires one."
)

#: Markers a test can find in the built tree, so no assertion has to read a
#: count back out of the object being counted.
ROW_CSS = "trigger-rule-row"

#: (heading, app attribute, store constructor) for each kind of rule. Ordered so
#: an event rule cannot make the percept rules unreadable, and so one corrupt
#: file only costs its own kind. The constructor is held rather than an
#: instance: a caller that handed a store in must not also have the per-user
#: data directory resolved behind its back.
_KINDS = (
    ("Percept rules", "trigger_rule_store", RuleStore),
    ("Event rules", "event_rule_store", EventRuleStore),
)


def _plain(text: Any) -> str:
    """`text` as an Adw row's title or subtitle, escaped if that row is one.

    See the module docstring: `Adw.PreferencesRow.use-markup` defaults to True
    (measured on libadwaita 1.5), so an unescaped `&` or `<` out of a rules file
    leaves the label empty and silent. `GLib` does the escaping because it is
    the one that knows Pango's five entities. The plain-GTK answer from
    `common.row()` is a `Gtk.Label`, which takes no markup and would print the
    entities themselves, so the escape follows whichever branch built the row.
    """
    flat = str(text)
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


def _clip(text: str, limit: int = 120) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _rules_for(store: Any) -> List[Any]:
    """Every rule in `store`, in the store's own (sorted) order.

    Separate from the widget-building so the row count has exactly one
    definition, and so a test can break this one line to prove the count
    assertion can fail.
    """
    return list(store.all())


def _open(app: Any, attribute: str, constructor: Any) -> Any:
    """The store to read: the app's own if it has one, else a fresh one.

    `constructor` is called only when nothing was handed in, so a caller that
    supplies both stores never causes the real per-user rules file to be
    resolved, let alone read.
    """
    store = getattr(app, attribute, None)
    return store if store is not None else constructor()


def _arm_state(rule: Any) -> str:
    """The armed/disarmed state as a phrase.

    A parked event rule says so and says why: a rule that stopped on purpose
    and one that stopped because its consent was withdrawn or it kept failing
    are different facts, and "armed" on its own would hide both.
    """
    if not getattr(rule, "enabled", True):
        return "disarmed"
    if getattr(rule, "parked", False):
        reason = getattr(rule, "parked_reason", "") or "stopped after repeated failures"
        return f"armed, parked ({reason})"
    # An armed rule that has failed and is waiting out a backoff is in a third
    # state, and "armed" hides it: the rule is not firing, is not parked, and
    # will start again on its own without anything being re-armed. The delay is
    # computed through the rule's own policy rather than restated here, so the
    # number on screen is the number the engine will wait.
    waiting = _backoff_wait(rule)
    return f"armed, waiting {waiting}" if waiting else "armed"


def _backoff_wait(rule: Any) -> str:
    """How long an armed, unparked rule has left to wait, or "" for none.

    Empty for a rule that has never failed, and for a percept rule rather than an
    event rule - this engine has no backoff counters to wait out, which is why
    `getattr` rather than an `isinstance` is the test.
    """
    attempt = getattr(rule, "attempt", 0)
    retry_at = getattr(rule, "retry_at", None)
    if not attempt or not retry_at:
        return ""
    delay_for = getattr(rule, "backoff_delay", None)
    if not callable(delay_for):
        return ""
    try:
        seconds = float(delay_for())
    except Exception:  # noqa: BLE001 - a broken counter is no counter
        return ""
    if seconds <= 0:
        return ""
    return f"{seconds:.0f}s after {attempt} failure(s)"


def _source_event(rule: Any) -> str:
    """The event this rule fires on: the sense, or the event type and its source.

    `EventRule.sense` is a property returning its event type - it exists so the
    two rule kinds share one consent question - so asking for `sense` works for
    both and asking for `event_type` distinguishes them.
    """
    event_type = getattr(rule, "event_type", "")
    if event_type:
        watched = getattr(rule, "source", "")
        return f"{event_type} on {watched}" if watched else str(event_type)
    sense = getattr(rule, "sense", "")
    return str(sense) if sense else "no source recorded"


def _match_text(rule: Any) -> str:
    mode = getattr(rule, "match_mode", "")
    if mode == MATCH_SUBSTRING:
        return f'substring "{getattr(rule, "substring", "")}"'
    keywords = list(getattr(rule, "keywords", []) or [])
    if mode == MATCH_ANY or not keywords:
        return "any event of that kind"
    return "keywords: " + ", ".join(str(k) for k in keywords)


def _arguments_text(rule: Any) -> str:
    try:
        rendered = json.dumps(dict(getattr(rule, "arguments", {}) or {}), sort_keys=True)
    except (TypeError, ValueError):
        rendered = repr(getattr(rule, "arguments", {}))
    return _clip(rendered)


def _detail(rule: Any) -> str:
    """The lines under the name: its state, then what fires it and what it runs.

    The state comes first because it is the answer that decides whether the rest
    of the line matters - an `Adw.ActionRow` has a title and a subtitle, and a
    rule row wants a state of its own. Read on its own this is still the whole
    truth about the rule: what fires it, what it matches, what it runs, and what
    it is allowed to do about it.
    """
    parts = [
        _arm_state(rule),
        f"fires on: {_source_event(rule)}",
        f"matches: {_match_text(rule)}",
        f"runs: {getattr(rule, 'actuator', 'unknown actuator')}",
        f"cooldown {getattr(rule, 'cooldown_seconds', 0):g}s",
    ]
    if getattr(rule, "event_type", ""):
        parts.append(f"debounce {getattr(rule, 'debounce_seconds', 0):g}s")
        parts.append(f"retry {getattr(rule, 'retry_policy', '')}".strip())
    if getattr(rule, "ask_first", False):
        parts.append("asks before each run")
    if getattr(rule, "allow_destructive", False):
        parts.append("destructive actions allowed")
    return " · ".join(p for p in parts if p)


def _tooltip(rule: Any) -> str:
    bits = [f"source event: {_source_event(rule)}", _detail(rule)]
    arguments = _arguments_text(rule)
    if arguments:
        bits.append(f"arguments: {arguments}")
    bits.append("Read-only here: arm or disarm it with the manage_triggers skill.")
    return "\n".join(bits)


def _access(widget: Gtk.Widget, label: str) -> None:
    """Set the accessible LABEL.

    Kept as its own call because the row's own tooltip carries the same text
    and is the only part of it a test can read back on a PyGObject build with no
    accessible-property getter.
    """
    widget.update_property([Gtk.AccessibleProperty.LABEL], [label])


def _note(text: str) -> Gtk.Label:
    """A dim line of plain text, with any filesystem path in it fixed-width.

    Was `set_text` and nothing else, on purpose: that is the one call that cannot
    be half-done for a string that came out of a file on disk, and every message
    this panel writes is one of those. The source-path line *is* made of such
    strings - it names the rules files it found - and a path set in a fixed-width
    face is the difference between a run of slashes being recognisable and not.

    So the safety property is kept and the face is added: `paths_in` escapes the
    whole text and *then* adds its own tags, so a path containing `&` or `<` is
    displayed literally and cannot introduce markup of its own. That is asserted
    in `tests/test_paths_read_as_paths.py` with a hostile filename rather than
    argued here - a comment like this one has been wrong in this repo before.
    """
    label = common.paths_in(text)
    label.set_hexpand(True)
    label.add_css_class("dim-label")
    return label


def _margined(widget: Gtk.Widget) -> Gtk.Widget:
    """The margins every panel in this package puts around its content."""
    widget.set_margin_top(12)
    widget.set_margin_bottom(12)
    widget.set_margin_start(12)
    widget.set_margin_end(12)
    return widget


class _TriggersSurface:
    """The page's logic, and the page it fills.

    Not a widget any more. `build()` hands back the `Adw.NavigationPage` itself,
    because that is what a window puts in its `Adw.NavigationView`; the state
    this panel has - which rules it listed, which stores it could not read,
    where it read them from - stays here, with `build()` publishing the read-only
    accessors onto the page.
    """

    def __init__(self, app: Any, set_content: Any) -> None:
        self._app = app
        self._row_widgets: List[Gtk.Widget] = []
        self._problems: List[str] = []
        self._source_paths: List[str] = []

        body = _margined(common.page_body())
        self._source_label = _note("")
        body.append(self._source_label)

        # The panel's own health, above every group: how
        # many rules are armed, how many are not, or
        # could it not be told? One row, one dot, one
        # word - the question the panel is opened for,
        # before the rows that hold the rules.
        #: The panel's health, written once into the row above and read back
        #: for the dot on its sidebar row. Same value, so the dot cannot
        #: disagree with the sentence directly above it.
        self.status_recorder = common.StatusRecorder()
        self._status_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        body.append(self._status_slot)

        # One box for the groups and the problems, so a rebuild empties them
        # without disturbing the notes around them.
        self._rules_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        body.append(self._rules_box)

        # Present from the start and hidden by `_refresh()`, not added when the
        # list is empty: a status page that appears by being added cannot be
        # taken away again, and this one has to be able to say both.
        self.empty_state: Gtk.Widget = common.empty_state(
            ICON,
            "No trigger rules armed",
            "Nothing here can act on its own. Ask Chronoa to arm one, or run "
            "shani-chronoa-sense trigger list to see this from the command line.",
        )
        # **"Ask Chronoa to arm one" now has a way to be asked.** That sentence
        # was a dead end: the arming itself is a skill call, not a page, and the
        # panel sent the reader off to a chat window with nothing to type. What
        # *is* reachable is the gate every armed rule has to pass - Settings >
        # Tool activity - so that is the button, and the sentence above it stays
        # true: arming a rule is still a conversation, not a switch.
        #
        # Shown and hidden with the empty state, so the button appears exactly
        # when the panel is claiming there is nothing here.
        self.empty_route: Gtk.Widget = common.banner(
            "Rules need the tool-activity switch before any of them can act.",
            "Manage triggers",
            lambda: common.open_page(self._app, "settings:tool-activity"))
        self.empty_route.set_visible(False)
        self.empty_state.set_visible(False)
        # Appended once, here, and only ever shown or hidden afterwards. Built
        # but not appended is the `Adw.Banner` failure this repo has already paid
        # for twice: in the widget tree, and nowhere on screen.
        body.append(self.empty_route)
        body.append(self.empty_state)

        self._gate_label = _note("")
        body.append(self._gate_label)

        body.append(self._arm_form())

        set_content(common.scrolled(body))
        self._refresh()

    # -- arming a rule --------------------------------------------------------

    def _arm_form(self) -> Gtk.Widget:
        """Arm a percept rule from the panel, through the same skill chat uses.

        The panel could delete a rule and not make one: arming was only
        possible by asking in chat (`manage_triggers`) or from the command line,
        though `build_rule()` has full validation. This form calls
        `tools.execute_tool("manage_triggers", action="add")` - not a copy of
        it - so the consent key, the permission layers, the argument check
        against the actuator's schema, the destructive-actuator refusal and the
        honest "would it fire right now" sentence are all the chat path's own.
        """
        group = common.group(
            "Arm a rule",
            "When a sense reads something containing your text, run one "
            "whitelisted skill. Checked exactly as if you had asked in chat.")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ("start", "end", "top", "bottom"):
            getattr(box, f"set_margin_{side}")(10)

        def field(label: str, widget: Gtk.Widget) -> Gtk.Widget:
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            text = Gtk.Label(label=label, xalign=0.0)
            text.set_size_request(150, -1)
            line.append(text)
            widget.set_hexpand(True)
            widget.update_property([Gtk.AccessibleProperty.LABEL], [label])
            line.append(widget)
            box.append(line)
            return widget

        self._form_name = field("Name", Gtk.Entry(placeholder_text="e.g. low-battery-note"))
        # A sense reading, or one of the machine events an event rule polls.
        # The form only handled sense rules, so the eighteen event types - a
        # screen lock, a USB plug, a schedule - could be armed only from chat.
        events = _event_types()
        self._form_kinds = ["a sense"] + events
        self._form_kind = field("Watch", Gtk.DropDown.new_from_strings(self._form_kinds))
        senses = _sense_names()
        self._form_sense = field("When this sense", Gtk.DropDown.new_from_strings(senses or ["(none)"]))
        self._form_source = field("Event source", Gtk.Entry(
            placeholder_text="locked, on-battery, daily 08:00, a path or a unit"))
        self._form_text = field("reads text containing", Gtk.Entry(placeholder_text="e.g. Discharging"))
        skills = _skill_names()
        self._form_skill = field("run this skill", Gtk.DropDown.new_from_strings(skills or ["(none)"]))
        self._form_args = field("with arguments (JSON)", Gtk.Entry(text="{}"))
        self._form_cooldown = field("at most every (seconds)",
                                    Gtk.SpinButton.new_with_range(30, 86400, 30))
        self._form_senses, self._form_skills = senses, skills

        def on_kind(*_a) -> None:
            event = self._form_kind.get_selected() > 0
            self._form_sense.get_parent().set_visible(not event)
            self._form_source.get_parent().set_visible(event)

        self._form_kind.connect("notify::selected", on_kind)
        on_kind()

        arm = Gtk.Button(label="Arm rule", halign=Gtk.Align.END)
        arm.add_css_class("suggested-action")
        arm.connect("clicked", self._on_arm)
        box.append(arm)
        self._form_result = _note("")
        box.append(self._form_result)
        self._form_arm = arm
        common_add = getattr(group, "add", None)
        (common_add or group.append)(box)
        return group

    def form_arguments(self) -> "dict | str":
        """What the form would send, or the reason it cannot send anything."""
        import json
        try:
            args = json.loads(self._form_args.get_text() or "{}")
        except ValueError as exc:
            return f"The arguments are not valid JSON ({exc.msg}). Nothing was armed."
        if not isinstance(args, dict):
            return "The arguments must be a JSON object, like {}. Nothing was armed."
        sense_i, skill_i = self._form_sense.get_selected(), self._form_skill.get_selected()
        if not self._form_senses or not self._form_skills:
            return "No senses or skills are loaded, so nothing can be armed."
        text = self._form_text.get_text()
        kind = self._form_kind.get_selected()
        if kind > 0:
            return {
                "action": "add",
                "name": self._form_name.get_text().strip(),
                "event_type": self._form_kinds[kind],
                "source": self._form_source.get_text().strip(),
                # An event with no text to match fires on every occurrence.
                "match_mode": "substring" if text else "any",
                "substring": text,
                "actuator": self._form_skills[skill_i],
                "arguments": args,
                "cooldown_seconds": float(self._form_cooldown.get_value()),
            }
        return {
            "action": "add",
            "name": self._form_name.get_text().strip(),
            "sense": self._form_senses[sense_i],
            "match_mode": "substring",
            "substring": text,
            "actuator": self._form_skills[skill_i],
            "arguments": args,
            "cooldown_seconds": float(self._form_cooldown.get_value()),
        }

    def _on_arm(self, button: Gtk.Button) -> None:
        payload = self.form_arguments()
        if isinstance(payload, str):
            self._form_result.set_text(payload)
            return
        button.set_sensitive(False)
        self._form_result.set_text("Arming...")

        def work() -> None:
            try:
                from shani_chronoa import tools
                said = tools.execute_tool("manage_triggers", payload, origin=tools.ORIGIN_USER)
            except Exception as exc:  # noqa: BLE001 - shown, not swallowed
                said = f"{type(exc).__name__}: {exc}"

            def show() -> bool:
                self._form_result.set_text(str(said))
                button.set_sensitive(True)
                self._refresh()
                return False

            GLib.idle_add(show)

        threading.Thread(target=work, daemon=True).start()

    # -- content ------------------------------------------------------------

    def _refresh(self) -> None:
        child = self._rules_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._rules_box.remove(child)
            child = nxt
        self._row_widgets = []
        self._problems = []
        self._source_paths = []
        paths: List[str] = []

        # The panel's own health, before the rules: how many
        # are armed, how many could not be read, or could it
        # not be told? One row, one dot, one word - the
        # question the panel is opened for, before the rows
        # that hold the rules.
        common.clear(self._status_slot)

        for kind, attribute, constructor in _KINDS:
            try:
                store = _open(self._app, attribute, constructor)
                rules = _rules_for(store)
            except Exception as exc:  # noqa: BLE001 - a surface shows what it can
                # `RuleStoreError` is the expected case here and is deliberately
                # not caught separately: the store refuses to load corrupt or
                # truncated input, and its message already names the file. An
                # armed rule the user cannot see is a rule they cannot revoke,
                # so this reports it rather than showing an empty list, which
                # reads as "nothing is armed".
                self._add_problem(f"{kind} could not be read: {exc}")
                continue
            path = str(getattr(store, "path", "") or "")
            if path:
                paths.append(path)
            if not rules:
                self._rules_box.append(_note(f"No {kind.lower()} armed."))
                continue
            group = common.group(kind)
            for rule in rules:
                self._add_row(group, rule, (kind, attribute, constructor))
            self._rules_box.append(group)

        self._source_paths = paths
        self._source_label.set_text(
            "From " + ", ".join(paths) if paths else "No rules file could be located."
        )
        self._gate_label.set_text(self._gate_note())
        nothing = not self._row_widgets and not self._problems
        self.empty_state.set_visible(nothing)
        # The route follows the empty state, rather than being appended when it
        # appears: a banner added on demand has to be taken away again, and one
        # that is only ever appended would stack a second "Manage triggers" under
        # every refresh.
        self.empty_route.set_visible(nothing)

        # The status row is rendered after the rules are read, so it
        # can say how many are armed and how many could not be read.
        total = len(self._row_widgets)
        if self._problems:
            self._status_slot.append(self.status_recorder.row(
                common.STATUS_ATTENTION,
                f"{total} rule(s) armed, {len(self._problems)} could not be read",
                "; ".join(self._problems)))
        elif total:
            self._status_slot.append(self.status_recorder.row(
                common.STATUS_OK,
                f"{total} rule(s) armed",
                f"from {len(paths)} file(s)"))
        else:
            self._status_slot.append(self.status_recorder.row(
                common.STATUS_OK,
                "No rules armed",
                "nothing here can act on its own"))

    def _add_problem(self, text: str) -> None:
        self._problems.append(text)
        self._rules_box.append(_note(text))

    def _add_row(self, group: Gtk.Widget, rule: Any, kind: Any = None) -> None:
        name = getattr(rule, "name", "") or "(unnamed rule)"
        state = _arm_state(rule)
        lines = [_detail(rule)]
        arguments = _arguments_text(rule)
        if arguments:
            lines.append(f"arguments: {arguments}")

        suffix = None
        if kind is not None:
            toggle = Gtk.Switch(active=bool(getattr(rule, "enabled", True)),
                                valign=Gtk.Align.CENTER)
            toggle.set_tooltip_text("Enable or disable this rule")
            toggle.connect("notify::active", lambda s, _p, n=name, a=kind[1], c=kind[2]:
                           self._set_enabled(a, c, n, s.get_active()))
            delete = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
            delete.add_css_class("flat")
            delete.set_tooltip_text("Delete this rule")
            delete.connect("clicked", lambda _b, n=name, a=kind[1], c=kind[2]:
                           self._delete_rule(a, c, n))
            suffix = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            suffix.append(toggle)
            suffix.append(delete)

        row = common.row(title=_plain(name), subtitle=_plain("\n".join(lines)), suffix=suffix)
        row.add_css_class(ROW_CSS)
        row.set_tooltip_text(_tooltip(rule))
        _access(
            row,
            f"Trigger rule {name}, {state}, fires on {_source_event(rule)}, "
            f"runs {getattr(rule, 'actuator', 'an unknown actuator')}",
        )
        _add(group, row)
        self._row_widgets.append(row)

    def _delete_rule(self, attribute: str, constructor: Any, name: str) -> None:
        try:
            store = _open(self._app, attribute, constructor)
            removed = bool(getattr(store, "remove", lambda _n: False)(name))
        except Exception:  # noqa: BLE001 - a refused delete must not take the window down
            logger.warning("could not delete rule %r", name, exc_info=True)
            removed = False
        if not removed:
            logger.warning("deleting rule %r removed nothing", name)
        self._refresh()

    def _set_enabled(self, attribute: str, constructor: Any, name: str, enabled: bool) -> None:
        try:
            store = _open(self._app, attribute, constructor)
            rule = store.get(name)
            if rule is not None:
                rule.enabled = bool(enabled)
                store.add(rule)
        except Exception:  # noqa: BLE001 - a refused change must not take the window down
            logger.warning("could not change rule %r", name, exc_info=True)
        self._refresh()

    def _gate_note(self) -> str:
        """The consent state that decides whether an armed rule could act.

        Unreadable is its own answer. Reporting "off" because a key could not be
        read would be a plausible wrong answer, and this repo's rule is that a
        failure mode which looks like a clean result is worse than a failure.
        """
        getter = getattr(getattr(self._app, "config", None), "get_bool", None)
        if not callable(getter):
            return (
                f"An armed rule only acts while '{TRIGGER_CONTROL_KEY}' is on and its "
                "own sense and actuator gates are permitted; that switch is not "
                "readable from here. Nothing on this surface fires anything."
            )
        try:
            on = bool(getter(TRIGGER_CONTROL_KEY, False))
        except Exception:  # noqa: BLE001 - an unreadable key is unknown, not off
            return (
                f"An armed rule only acts while '{TRIGGER_CONTROL_KEY}' is on and its "
                "own sense and actuator gates are permitted; that switch could not be "
                "read. Nothing on this surface fires anything."
            )
        return (
            f"'{TRIGGER_CONTROL_KEY}' is {'on' if on else 'off'}: "
            + (
                "even so, an armed rule still needs its own sense and actuator "
                "gates permitted. "
                if on
                else "no armed rule can act, including one armed before it was "
                "turned off. "
            )
            + "Nothing on this surface fires anything."
        )

    # -- for tests, and for any surface that wants the same rules ------------

    def rows(self) -> List[Gtk.Widget]:
        return list(self._row_widgets)

    def row_count(self) -> int:
        return len(self._row_widgets)

    def empty(self) -> bool:
        return bool(self.empty_state.get_visible())

    def problems(self) -> List[str]:
        return list(self._problems)

    def source_paths(self) -> List[str]:
        return list(self._source_paths)


def build(app: Any) -> Gtk.Widget:
    """Build the trigger-rules page. Opens the two rule stores, writes nothing.

    Returns the `Adw.NavigationPage` a window puts in its
    `Adw.NavigationView`, with this surface's read-only API attached to it.
    """
    page, set_content = common.surface(TITLE, SUBTITLE)
    surface = _TriggersSurface(app, set_content)
    page.surface = surface
    page.rows = surface.rows
    page.row_count = surface.row_count
    page.empty = surface.empty
    page.problems = surface.problems
    page.source_paths = surface.source_paths
    # What this panel says about itself, for the sidebar's health dot. Read from
    # the same recorder the row at the top of the panel was written through, so
    # the dot and the row are one statement rather than two that can drift.
    page.status = surface.status_recorder.status
    return page


__all__ = ["TITLE", "ICON", "build"]


def _sense_names() -> List[str]:
    try:
        from shani_chronoa.senses import discover_senses
        return sorted(discover_senses())
    except Exception:  # noqa: BLE001
        return []


def _skill_names() -> List[str]:
    try:
        from shani_chronoa import tools
        return sorted(t["function"]["name"] for t in tools.TOOLS)
    except Exception:  # noqa: BLE001
        return []


def _event_types() -> List[str]:
    try:
        from shani_chronoa.triggers.common import EVENT_TYPES
        return sorted(EVENT_TYPES)
    except Exception:  # noqa: BLE001
        return []
