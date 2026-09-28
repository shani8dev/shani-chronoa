"""Settings window: real Adwaita pages, every sense reachable, no hardcoded theme.

Three things were wrong with the previous version, and only the first was
cosmetic.

**The consent model was unreachable.** Seventeen senses ship, each behind its
own `*-sense-enabled` key, and the settings window mentioned none of them -
zero. A user could install Chronoa, be told a sense is turned off, and have no
way to turn it on except `gsettings set` from a terminal. The switches here are
generated from the registry and the consent table rather than hand-listed, so a
sense added tomorrow appears without anyone editing this file, and one whose
key is missing from the installed schema is shown as ungrantable rather than
silently missing.

**It hardcoded a dark palette.** `window { background-color: #1a1a2e; }` and
hand-picked entry colours meant a user with a light GTK theme got a dark
dialog, and the window fought Adwaita rather than joining it. There are no
colours here now: the window uses whatever the desktop theme provides, which
is the only version that looks right on the light, dark and high-contrast
themes a user may already have chosen.

**It hand-rolled its rows out of `Gtk.Box`.** Every switch and entry was a
label plus a widget in a box, so there were no Adwaita group semantics, no
keyboard navigation the way a settings dialog is expected to behave, and no
accessibility roles. These are `Adw.SwitchRow` / `Adw.EntryRow` /
`Adw.PreferencesGroup`, which is what they should have been.

On top of that: a search entry that filters as you type, because twenty-odd
rows in one scroll is not navigable; API-key rows that can reveal what you
pasted instead of leaving you unable to check it; and a Models section showing
which model is *actually* in effect and why, via `models.py`, so the hardware
tier's guess is visible as a guess rather than presented as a decision.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # type: ignore

from shani_chronoa import models
from shani_chronoa.config import _SENSE_CONSENT_KEYS
from shani_chronoa.senses import discover_senses


class SettingsWindow(Gtk.Window):
    """Every setting Chronoa has, in one searchable window."""

    def __init__(self, app) -> None:
        super().__init__(application=app, title="Chronoa Settings")
        self.app = app
        self.set_default_size(560, 720)
        if app.window:
            self.set_transient_for(app.window)
        # (widget, lowercase text to match against) for the search filter.
        self._searchable = []
        # Sense switches, so their real state can be re-derived after a change
        # instead of trusting what the user just clicked.
        self._sense_rows = {}
        self._build_ui()

    # -- construction --------------------------------------------------------

    def _build_ui(self) -> None:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=True)
        self._search = Gtk.SearchEntry(hexpand=True)
        self._search.set_placeholder_text("Search settings")
        self._search.connect("search-changed", self._on_search)
        header.pack_start(self._search)
        # Titlebar only. Adding it to `outer` as well is a second parent, and
        # GTK rejects that with "gtk_widget_get_parent (child) == NULL" - a
        # window can only ever have one.
        self.set_titlebar(header)

        page = Adw.PreferencesPage()
        self._build_senses(page)
        self._build_privacy(page)
        self._build_voice(page)
        self._build_models(page)
        self._build_system(page)

        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scrolled.set_child(page)
        scrolled.set_vexpand(True)
        outer.append(scrolled)
        self.set_child(outer)

    def _group(self, page, title, description="") -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title=title, description=description or None, margin_top=14, margin_bottom=14
        )
        page.add(group)
        self._searchable.append((group, f"{title} {description}".lower()))
        group._needle_extra = []  # rows to reveal if only they match
        return group

    def _switch(self, group, title, subtitle, active, on_toggle, enabled=True, tooltip=""):
        row = Adw.SwitchRow(title=title, subtitle=subtitle or None, active=bool(active))
        if not enabled:
            row.set_subtitle("no consent key in the installed schema - this cannot be granted")
            row.set_sensitive(False)
        if tooltip:
            row.set_tooltip_text(tooltip)
        row.connect("notify::active", lambda r, *_: on_toggle(r.get_active()))
        group.add(row)
        group._needle_extra.append((row, f"{title} {subtitle}".lower()))
        return row

    def _entry(self, group, title, subtitle, value, on_changed, secret=False):
        row = Adw.EntryRow(title=title)
        if subtitle:
            row.set_tooltip_text(subtitle)
        entry = Gtk.Entry(text=str(value or ""), hexpand=True)
        if secret:
            entry.set_visibility(False)
        entry.connect("changed", lambda e: on_changed(e.get_text()))
        row.add_suffix(entry)
        if secret:
            reveal = Gtk.ToggleButton(icon_name="view-reveal-symbolic", valign=Gtk.Align.CENTER)
            reveal.add_css_class("flat")
            reveal.set_tooltip_text("Show this key")
            reveal.connect("toggled", lambda b: entry.set_visible(b.get_active()))
            row.add_suffix(reveal)
        group.add(row)
        group._needle_extra.append((row, f"{title}".lower()))
        return row

    def _info_row(self, group, title, subtitle):
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        group.add(row)
        group._needle_extra.append((row, f"{title} {subtitle}".lower()))
        return row

    # -- the consent surface, generated from the registry --------------------

    def _build_senses(self, page) -> None:
        """One switch per registered sense, carrying that sense's own description.

        Generated rather than hand-listed. A hand-written list of seventeen
        rows is a list that is wrong the moment a sense is added, and wrong
        silently - which is precisely what the previous version was.
        """
        try:
            registry = discover_senses()
        except Exception as exc:  # noqa: BLE001 - one broken sense must not blank the window
            group = self._group(page, "Senses", "could not be loaded")
            self._info_row(group, "Sense registry failed to load", str(exc))
            return

        config = self.app.config
        enabled_first = sorted(n for n in registry if config.sense_allowed(n))
        disabled = sorted(n for n in registry if not config.sense_allowed(n))

        group = self._group(
            page, "Senses",
            "What Chronoa is allowed to perceive. All of these are off by default "
            "except memory. A sense that is off is never invoked at all - the "
            "check happens before anything is read, not after.",
        )
        for name in enabled_first + disabled:
            sense = registry[name]
            key = _SENSE_CONSENT_KEYS.get(name)
            description = sense.schema.get("function", {}).get("description", "")
            subtitle = description.split(". ")[0].rstrip(".") if description else ""
            if sense.is_ambient():
                subtitle += f" - polled every {int(sense.poll_interval)}s"
            row = self._switch(
                group, name, subtitle,
                bool(key) and config.sense_allowed(name),
                lambda active, n=name: self._set_sense(n, active),
                enabled=bool(key),
                tooltip=description or None,
            )
            self._sense_rows[name] = row

    def _set_sense(self, name: str, active: bool) -> None:
        key = _SENSE_CONSENT_KEYS.get(name)
        if not key:
            return
        self.app.config.set(key, "true" if active else "false")
        self._refresh_sense_switches()

    def _refresh_sense_switches(self) -> None:
        """Re-derive each switch from the config rather than from the click.

        A switch showing a state the system no longer agrees with is the most
        misleading thing this window can do, and a rejected write is exactly
        when that happens.
        """
        config = self.app.config
        for name, row in self._sense_rows.items():
            if _SENSE_CONSENT_KEYS.get(name):
                row.set_active(config.sense_allowed(name))

    # -- sections ------------------------------------------------------------

    def _build_privacy(self, page) -> None:
        config = self.app.config
        group = self._group(
            page, "Privacy and network",
            "Privacy mode is the master switch: with it on, Chronoa keeps speech, "
            "screen and sensed data on this machine and sends nothing to a cloud provider.",
        )
        self._switch(group, "Privacy mode (local only)", "Master switch for leaving this machine",
                     config.privacy_mode, lambda a: self._app_toggle("toggle-privacy", a))
        self._switch(
            group, "Cloud fallback when Ollama is unavailable",
            "Separate opt-in on purpose - this never turns on from one switch alone",
            config.cloud_fallback_enabled,
            lambda a: self._app_toggle("toggle-cloud-fallback", a))

        free = self._group(
            page, "Free cloud providers",
            "Optional. These work without a key at a lower rate limit; a key raises it. "
            "Applies when the cloud fallback next activates, not to a running session.",
        )
        for label, key in (
            ("LLM7 API key", "llm7-api-key"),
            ("Kilo Gateway API key", "kilo-api-key"),
            ("BlockRun API key", "blockrun-api-key"),
        ):
            self._entry(free, label, "", config.get(key, ""),
                        lambda text, k=key: config.set(k, text.strip()), secret=True)

        byok = self._group(
            page, "Cloud providers that require a key",
            "Anthropic, OpenAI, Google and Groq all rejected an unauthenticated request "
            "when tested live, so a key here is mandatory rather than optional. Tried ahead "
            "of the free providers when set.",
        )
        for label, key in (
            ("Anthropic (Claude) API key", "anthropic-api-key"),
            ("OpenAI API key", "openai-api-key"),
            ("Google Gemini API key", "google-api-key"),
            ("Groq API key", "groq-api-key"),
        ):
            self._entry(byok, label, "", config.get(key, ""),
                        lambda text, k=key: config.set(k, text.strip()), secret=True)

    def _build_voice(self, page) -> None:
        config, app = self.app.config, self.app
        group = self._group(page, "Voice", "How Chronoa listens and speaks.")
        self._switch(group, "Wake-word activation", "Start listening without being clicked",
                     app._wake_word_active, lambda a: self._app_toggle("toggle-wake-word", a))
        self._entry(group, "Wake-word model", "openWakeWord model file",
                    config.wake_word_model, lambda t: config.set("wake-word-model", t.strip()))
        self._switch(
            group, "Interrupt while replying (barge-in)",
            "No echo cancellation, so speaker output can self-interrupt; best with headphones",
            config.barge_in_vad_enabled, lambda a: self._app_toggle("toggle-barge-in-vad", a))
        self._entry(group, "Speech language (whisper.cpp)", "e.g. en, or auto",
                    config.language, lambda t: config.set("language", t.strip()))

    def _build_models(self, page) -> None:
        config = self.app.config
        group = self._group(
            page, "Models",
            "A pin wins over the hardware tier. Blank means no pin, so the tier decides.",
        )
        self._entry(group, "Text and tool-calling model", "Blank = hardware tier decides",
                    config.model, lambda t: config.set("model", t.strip()))
        self._entry(group, "Vision model", "Blank = hardware tier decides",
                    config.vision_model, lambda t: config.set("vision-model", t.strip()))
        self._entry(group, "Whisper model (speech to text)", "Blank = hardware tier decides",
                    config.whisper_model, lambda t: config.set("whisper-model", t.strip()))
        self._entry(group, "Ollama host", "Where the local model server is",
                    config.ollama_host, lambda t: config.set("ollama-host", t.strip()))
        self._entry(group, "Piper voice", "TTS voice name",
                    config.piper_voice, lambda t: config.set("piper-voice", t.strip()))

        # What is actually in effect, and why. A tier that guesses should look
        # like a guess, not be presented as a decision.
        effective = self._group(
            page, "In effect right now",
            "Resolved by Chronoa's own resolver. A pin is your choice; anything "
            "else is the hardware tier's guess.",
        )
        for task in models.tasks():
            chosen = models.resolve(task, config=config)
            self._info_row(
                effective, task,
                f"{chosen} - {models.describe(task)}" if chosen
                else "unknown - no pin and no tier default",
            )
            effective._needle_extra[-1][0].set_tooltip_text(models.explain(task, config=config))

    def _build_system(self, page) -> None:
        config = self.app.config
        group = self._group(page, "System")
        self._switch(group, "Start on login", "Launch Chronoa when you log in",
                     config.auto_start, lambda a: self._app_toggle("toggle-auto-start", a))
        self._switch(group, "Debug logging", "Verbose logs, including tool calls",
                     config.debug_mode, lambda a: self._app_toggle("toggle-debug", a))

    # -- behaviour -----------------------------------------------------------

    def _app_toggle(self, action: str, active: bool) -> None:
        """Route a switch through the app's own action.

        The same path the keyboard shortcut uses, rather than a second one that
        can drift from it.
        """
        current = {
            "toggle-privacy": self.app.config.privacy_mode,
            "toggle-cloud-fallback": self.app.config.cloud_fallback_enabled,
            "toggle-wake-word": self.app._wake_word_active,
            "toggle-barge-in-vad": self.app.config.barge_in_vad_enabled,
            "toggle-auto-start": self.app.config.auto_start,
            "toggle-debug": self.app.config.debug_mode,
        }.get(action)
        if current is not None and bool(current) != bool(active):
            self.app.activate_action(action, None)

    def _on_search(self, entry: Gtk.SearchEntry) -> None:
        """Filter groups and rows as you type.

        A group stays visible when it matches *or* when a row inside it does,
        so a search never leaves an empty heading on screen looking like a bug -
        and a matching row inside a non-matching group is revealed rather than
        hidden behind a heading that does not contain the needle.
        """
        needle = (entry.get_text() or "").strip().lower()
        for group, haystack in self._searchable:
            group_matches = not needle or needle in haystack
            row_hits = [
                (row, text) for row, text in group._needle_extra
                if needle and needle in text
            ]
            group.set_visible(bool(group_matches) or bool(row_hits))
            for row, _text in group._needle_extra:
                # A row is visible when the group matched outright, or when it
                # is itself the hit.
                row.set_visible(bool(group_matches) or bool(row_hits and (row, _text) in row_hits))
