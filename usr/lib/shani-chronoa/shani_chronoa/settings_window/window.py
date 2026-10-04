"""The settings window itself: its pages, search, and the row helpers every page builds with; each page's content is mixed in from senses, privacy, voice and activity."""


import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from shani_chronoa import model_choice

from .senses import SensesPage
from .privacy import PrivacyPage
from .voice import VoicePage
from .activity import ActivityPage




class SettingsWindow(SensesPage, PrivacyPage, VoicePage, ActivityPage, Gtk.Window):
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
        self._build_approvals(page)
        self._build_tool_activity(page)
        self._build_voice(page)
        self._build_models(page)
        self._build_system(page)

        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scrolled.set_child(page)
        scrolled.set_vexpand(True)
        outer.append(scrolled)
        self.set_child(outer)

        self.connect("notify::visible", self._on_window_visible)

    def _group(self, page, title, description="") -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title=title, description=description or None, margin_top=14, margin_bottom=14
        )
        page.add(group)
        self._searchable.append((group, f"{title} {description}".lower()))
        group._needle_extra = []  # rows to reveal if only they match
        return group

    def _action_button(self, group, title, subtitle, action_name,
                       sensitive=True, tooltip=""):
        """A row that fires one of the app's own GActions.

        Same shape as `_switch`, so the button reaches the action through the
        identical path a keyboard shortcut or the D-Bus name would - not by
        writing the setting directly, which is how a control and its shortcut
        drift apart.
        """
        row = Adw.ActionRow(title=title, subtitle=subtitle or None)
        button = Gtk.Button(label="Download", valign=Gtk.Align.CENTER)
        button.set_sensitive(bool(sensitive))
        if tooltip:
            button.set_tooltip_text(tooltip)
            row.set_tooltip_text(tooltip)
        row.add_suffix(button)
        row.set_activatable_widget(button)
        button.connect("clicked", lambda _b: self._app_toggle(action_name, True))
        group.add(row)
        group._needle_extra.append((row, f"{title} {subtitle}".lower()))
        return row

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

    def _number(self, group, title, subtitle, value, low, high, step, on_changed):
        row = Adw.SpinRow.new_with_range(low, high, step)
        row.set_title(title)
        row.set_subtitle(subtitle)
        row.set_digits(1)
        row.set_value(value)
        row.connect("notify::value", lambda r, _p: on_changed(round(r.get_value(), 2)))
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

    def _choice(self, group, title, subtitle, options, selected, on_changed):
        """One row that picks a value from a fixed list.

        Built here rather than in the voice page because it is a general row and
        `voice_style` is not the only thing that will want one. `selected` is
        matched by value, and an unknown value falls back to the first option
        rather than leaving a combo showing a choice that is not there - which is
        what happens when a preset is renamed and someone's setting still names
        the old one.
        """
        row = Adw.ComboRow(title=title)
        if subtitle:
            row.set_tooltip_text(subtitle)
        model = Gtk.StringList()
        for option in options:
            model.append(option)
        row.set_model(model)
        names = [option for option in options]
        index = names.index(selected) if selected in names else 0
        row.set_selected(index)
        row.set_subtitle(subtitle or "")
        row.update_property([Gtk.AccessibleProperty.DESCRIPTION],
                            [f"{names[index]}"])
        row.connect("notify::selected", lambda r, _p: on_changed(
            names[r.get_selected()] if r.get_selected() < len(names) else ""))
        group.add(row)
        group._needle_extra.append((row, f"{title}".lower()))
        return row

    def _info_row(self, group, title, subtitle):
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        group.add(row)
        group._needle_extra.append((row, f"{title} {subtitle}".lower()))
        return row

    def _read_bool(self, key: str) -> bool:
        # get_bool(), not get() == "true": ChronoaConfig.get() is documented
        # for string keys and returns its default for a boolean one, so the
        # comparison would be False forever and this gate would render itself
        # permanently off while the setting was on.
        try:
            return self.app.config.get_bool(key, False)
        except Exception:  # noqa: BLE001 - an absent key is simply off
            return False

    def _set_bool(self, key: str, value: bool) -> None:
        self.app.config.set(key, "true" if value else "false")
        self._refresh_sense_switches()

    def _on_window_visible(self, window, _param) -> None:
        if not window.get_visible():
            return
        if self._session_state() != self._approvals_state:
            self._render_session_answers()
        if self._tool_activity_state() != self._tool_state:
            self._render_tool_activity()

    def _build_models(self, page) -> None:
        config = self.app.config
        group = self._group(
            page, "Models",
            "A pin wins over the hardware tier. Blank means no pin, so the tier decides.",
        )
        setup_row = Adw.ActionRow(title="Set up Chronoa again",
                                  subtitle="Download or change the model, the speech model and the voice")
        setup_button = Gtk.Button(label="Open setup", valign=Gtk.Align.CENTER)
        setup_button.connect("clicked", lambda *_: self.app.activate_action("setup", None))
        setup_row.add_suffix(setup_button)
        group.add(setup_row)
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
        for task in model_choice.tasks():
            chosen = model_choice.resolve(task, config=config)
            self._info_row(
                effective, task,
                f"{chosen} - {model_choice.describe(task)}" if chosen
                else "unknown - no pin and no tier default",
            )
            effective._needle_extra[-1][0].set_tooltip_text(model_choice.explain(task, config=config))

    def _build_system(self, page) -> None:
        config = self.app.config
        group = self._group(page, "System")
        self._switch(group, "Start on login", "Launch Chronoa when you log in",
                     config.auto_start, lambda a: self._app_toggle("toggle-auto-start", a))
        self._switch(group, "Debug logging", "Verbose logs, including tool calls",
                     config.debug_mode, lambda a: self._app_toggle("toggle-debug", a))

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
            "toggle-model-download": self.app.config.model_download_enabled,
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
