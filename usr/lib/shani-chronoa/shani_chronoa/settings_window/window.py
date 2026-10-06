"""The settings window itself: its pages, search, and the row helpers every page builds with; each page's content is mixed in from senses, privacy, voice and activity."""


import gi
import pathlib
import re
import threading

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from shani_chronoa import local_llm, model_choice

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
        #: section id -> group, including alias ids that resolve to a live group
        self._by_id = {}
        #: retired id -> the id that replaced it
        self._aliases = {"tool-activity": "tool-activity",
                         "privacy": "privacy", "sense": "senses",
                         "model": "models", "voice": "voice"}
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
        # The placeholder is what a person sees; the label is what a screen
        # reader announces, and it is not the same thing. The main window's own
        # search entry sets one and this one did not, so the settings search was
        # the one control in the app whose name came only from the greyed-out
        # hint that disappears the moment you type in it.
        self._search.update_property([Gtk.AccessibleProperty.LABEL],
                                     ["Search settings"])
        self._search.connect("search-changed", self._on_search)
        header.pack_start(self._search)
        # Titlebar only. Adding it to `outer` as well is a second parent, and
        # GTK rejects that with "gtk_widget_get_parent (child) == NULL" - a
        # window can only ever have one.
        self.set_titlebar(header)

        page = Adw.PreferencesPage()
        from shani_chronoa import pages as page_registry
        page_registry.register(
            "settings",
            # Declared up front and deliberately: the ids a caller may use are a
            # promise, and deriving them from the widgets that happen to be built
            # would make the set depend on which sub-builder succeeded.
            [("senses", "Senses"), ("privacy", "Privacy"),
             ("approvals", "Approvals"), ("tool-activity", "Tool activity"),
             ("voice", "Voice and speech"), ("models", "Models"),
             ("system", "System")],
            factory=lambda app, config=None: type(self)(app),
            aliases={"model": "models", "tool": "tool-activity",
                     "sense": "senses", "speech": "voice", "activity": "tool-activity"},
        )
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

    # ── section ids ────────────────────────────────────────────────────────
    #
    # Every group has a stable id, and the window can be *told* to show one.
    #
    # **They were reachable only by typing into the search box**, which is fine
    # for a person and useless for anything else: a notification cannot say
    # "open Settings on Privacy", a keybinding cannot, and neither could a test
    # - which is why driving this window meant a search box and a guessed
    # selector, and why several screenshots in a row came out identical. shani-
    # cassini has had this for a while (`notebook.py`'s `PAGES`/`page_ids()`/
    # `select(pid)` plus `--section=` and a `show-section` action), so this is
    # that shape rather than a new one.
    def _group(self, page, title, description="", section_id="") -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title=title, description=description or None, margin_top=14, margin_bottom=14
        )
        page.add(group)
        self._searchable.append((group, f"{title} {description}".lower()))
        group._needle_extra = []  # rows to reveal if only they match
        # Ids are lower-cased titles with the punctuation a shell would mangle
        # removed, so `Tool activity` is `tool-activity` and a remembered id
        # keeps working when the title is reworded slightly.
        sid = section_id or re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        group._section_id = sid
        self._by_id[sid] = group
        # A retired id keeps working: it names the section that absorbed it, the
        # way cassini's `ALIASES` does, because a stored id that opens nothing
        # is worse than one that opens somewhere honest.
        for alias in self._aliases:
            if self._aliases[alias] == sid:
                self._by_id.setdefault(alias, group)
        return group

    def show_page(self, page_id: str) -> bool:
        """The registry calls every window the same thing; this is that name."""
        return self.show_section(page_id)

    def section_ids(self) -> list:
        """Every section id, in the order they appear."""
        return [g._section_id for g, _ in self._searchable]

    def show_section(self, section_id: str) -> bool:
        """Show one section and hide the rest. False if the id is unknown.

        Filtering rather than scrolling is deliberate: a 9,000px page scrolled to
        an offset is not a landing, and the search box already does exactly this
        filtering - so showing a section is the same operation with an argument,
        and there is one implementation of "what does 'Privacy' match" rather
        than two that can disagree.
        """
        group = self._by_id.get(section_id)
        if group is None:
            return False
        needle = (group.get_title() or section_id).lower()
        self._search.set_text(needle)
        self._search.grab_focus()
        self.present()
        return True

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

    def _cycle_presence(self, button: Gtk.Button, row: Adw.ActionRow) -> None:
        """Move to the next presence state, and say what happened.

        The button's label is the *next* action, not the current state, because
        a button reading "Drowsy" is a button whose meaning depends on what the
        machine is already doing. And the row's subtitle always restates the
        state in a sentence, because "the model was released" is a claim about
        memory and has to be checkable.

        Runs off the main loop: stopping a server is a `systemctl` call, and
        waking one waits for a cold start.
        """
        from shani_chronoa import local_llm, presence
        current = presence.detect(local_llm.is_up)
        target = current.next()
        row.set_subtitle("Working...")
        button.set_sensitive(False)

        def work() -> None:
            reached, reason = presence.apply(
                target, is_up=local_llm.is_up,
                wake=local_llm.start_service, sleep=local_llm.stop_service)
            def done() -> bool:
                now = presence.detect(local_llm.is_up)
                row.set_subtitle(now.detail() if reached
                                 else f"Could not do that: {reason}")
                button.set_label(now.action())
                button.set_sensitive(True)
                return False
            GLib.idle_add(done)

        threading.Thread(target=work, daemon=True).start()

    def _model_choices(self, group, title: str, subtitle: str, keys,
                       installed, on_pick, config=None) -> None:
        """Pick a model from the ones that exist, rather than only typing one.

        **This row exists because the entries above accept any string.** Typing
        `qwen3:4b` when that model is not downloaded produced "llama.cpp is not
        answering" in the log and "No model yet" on the orb, with the fix -
        download it - three screens away in the setup wizard. Alpaca's whole
        interface is this idea: you manage the models you have, not a name you
        hope for.

        Each button says whether that model is on disk and how big it is, and
        marks the one in use, so the list is a state display and a picker in one.
        Nothing is chosen for the person: the current pin stays until a button is
        pressed.
        """
        from shani_chronoa import local_llm
        present = [key for key in keys if installed(key)]
        row = Adw.ActionRow(
            title=title,
            subtitle=(subtitle if present else
                      f"{subtitle} Nothing is downloaded yet - open setup."))
        row._in_use_key = ""
        if present:
            picked = Gtk.DropDown.new_from_strings(present)
            current = local_llm.active() or ((config.model if config else "") or "")
            if current in present:
                picked.set_selected(present.index(current))
            picked.connect("notify::selected", lambda w, *_: on_pick(
                w.get_selected_item().get_string()))
            picked.set_valign(Gtk.Align.CENTER)
            row.add_suffix(picked)
        group.add(row)
        group._needle_extra.append((row, f"{title} {row.get_subtitle()}".lower()))
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

    def _read_string(self, key: str, default: str = "") -> str:
        """A string setting. Same "absent is the default" contract as `_read_bool`.

        Added with the `gateways` row. `_read_bool` exists because `config.get()`
        is documented for string keys and answers a boolean one with its default -
        so a row reading a *string* setting has the mirror-image trap, and this is
        the one place that gets it right.
        """
        try:
            return self.app.config.get(key, default) or default
        except Exception:  # noqa: BLE001 - an absent key is simply empty
            return default

    def _set_string(self, key: str, value: str) -> None:
        self.app.config.set(key, value or "")

    def _on_window_visible(self, window, _param) -> None:
        if not window.get_visible():
            return
        if self._session_state() != self._approvals_state:
            self._render_session_answers()
        if self._tool_activity_state() != self._tool_state:
            self._render_tool_activity()

    # -- model quality: measured, never assumed -------------------------------

    @staticmethod
    def _off_main(button: Gtk.Button, work) -> None:
        """Run `work` on a worker thread, report into `button`, re-enable it."""
        button.set_sensitive(False)

        def report(text: str) -> None:
            GLib.idle_add(
                lambda: (setattr(button, "label", str(text)[:90]), False)[1])

        def run() -> None:
            try:
                work(report)
            except Exception as exc:  # noqa: BLE001 - a worker must still report
                report(f"Failed: {type(exc).__name__}")
            GLib.idle_add(lambda: (button.set_sensitive(True), False)[1])

        threading.Thread(target=run, daemon=True).start()

    @staticmethod
    def _measure_quality(report) -> None:
        """Perplexity of the model in use, against another one installed here."""
        link = local_llm.current_link()
        if not (link.exists() or link.is_symlink()):
            report("No model is installed yet")
            return
        target = pathlib.Path(link).resolve()
        others = [p for p in sorted(local_llm.model_dir().glob("*.gguf"))
                  if p.resolve() != target]
        report("Measuring perplexity...")
        verdict = local_llm.quality_verdict(target, others[0] if others else None)
        if not verdict.get("measured"):
            report("Not measurable")
            return
        report(f"{float(verdict['perplexity']):.3f} - {str(verdict['why'])[:70]}")

    @staticmethod
    def _rebuild_calibrated(report) -> None:
        """Produce a calibrated quantization of the largest un-quantized model here.

        Only a **higher-precision source** is offered: re-quantizing an already
        quantized file is meaningless, and the error would not be obvious - the
        output would simply be worse than the input while claiming to be an
        improvement.
        """
        if local_llm.shutil.which("llama-imatrix") is None:
            report("llama-imatrix is not installed")
            return
        sources = [p for p in sorted(local_llm.model_dir().glob("*.gguf"))
                   if p.stem.endswith(("-f16", "-bf16"))
                   or "F16" in p.name or "BF16" in p.name]
        if not sources:
            report("No F16/BF16 model to re-quantize")
            return
        source = sources[0]
        report("Fitting an importance matrix...")
        out = local_llm.calibrated_quantize(source, "Q4_K_M")
        if not out.get("ok"):
            report(f"Refused: {str(out.get('why'))[:110]}")
            return
        # **Say where the file is, and let it be used.** `calibrated_quantize()`
        # builds a model the `SPECS` catalogue cannot name - it re-quantizes
        # whatever source was on disk - and `current.gguf` is a symlink, so
        # before `adopt_path()` existed the result was unreachable: this button
        # reported "Done: Q4_K_M" and left the file where nothing could load it.
        # The path is in the report *and* adopted here, because a rebuilt model
        # that is not the one in use has not fixed anything.
        built = out.get("calibrated_path")
        report(f"Built {out.get('target')} at {built}")
        if not built:
            report("but no path was reported, so nothing was adopted")
            return
        verdict = local_llm.adopt_path(pathlib.Path(built), gate=True)
        if verdict.get("promote"):
            report(f"In use now: {pathlib.Path(built).name} "
                   f"({str(verdict.get('why'))[:60]})")
        else:
            report(f"Built, but NOT put into use: {str(verdict.get('why'))[:90]}")
            report(f"It is at {built} if you want it anyway.")

    @staticmethod
    def _reclaim_scratch(report) -> None:
        """Delete the copies a calibration run left behind, and say what happened.

        **A staticmethod for the same reason `_rebuild_calibrated` is one.** It
        was a closure inside `_build_models`, which meant no test could reach it -
        and a mutation that replaced the call with a hardcoded "nothing to do"
        passed the whole suite. A UI affordance that silently does nothing is
        worse than one that is missing, because the button still looks right.

        The report names every directory that was **kept** as well as every one
        removed. That is the whole point of the refusal in
        `reclaim_quant_scratch()`: if it declines to delete the directory a
        loaded model lives in, the person pressing the button needs to know,
        or "reclaimed" would be a claim it cannot back.
        """
        report("Checking for scratch directories...")
        if not local_llm.quant_scratch_dirs():
            report("Nothing to reclaim.")
            return
        out = local_llm.reclaim_quant_scratch()
        report(str(out.get("why")))
        for name in out.get("kept", []):
            report(f"  kept (a model is loaded from it): {name}")

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
        self._model_choices(
            group, "Text and tool-calling model, from what is installed",
            "Installed models, newest capability first. A name typed into the box "
            "above that is not on this list is why the app says no model is "
            "answering - and this row is the way out of that without opening the "
            "setup wizard.",
            [spec.key for spec, _label, _ram in local_llm.TIERS],
            lambda key: local_llm.verify(key),
            lambda key: config.set("model", key),
            config=config)
        self._entry(group, "Vision model", "Blank = hardware tier decides",
                    config.vision_model, lambda t: config.set("vision-model", t.strip()))
        self._entry(group, "Whisper model (speech to text)", "Blank = hardware tier decides",
                    config.whisper_model, lambda t: config.set("whisper-model", t.strip()))
        self._entry(group, "Ollama host", "Where the local model server is",
                    config.ollama_host, lambda t: config.set("ollama-host", t.strip()))
        self._entry(group, "Piper voice", "TTS voice name",
                    config.piper_voice, lambda t: config.set("piper-voice", t.strip()))

        # **Quality, measured.** `local_llm.perplexity()` and
        # `quality_verdict()` were written to run `llama-perplexity` - which is
        # installed on this machine and was never invoked anywhere in this
        # repository - and were reachable only through `use()`, i.e. only as a
        # side effect of switching models. `calibrated_quantize()` had no caller
        # at all. Both are actions a person may want without changing anything, so
        # they are buttons here.
        #
        # Off the main loop: perplexity on a real model takes seconds and
        # calibration takes minutes.
        quality_row = Adw.ActionRow(
            title="Is this model any good?",
            subtitle="Measures its perplexity against the other model installed "
                     "here. Provisioning checks a digest, which says the file is "
                     "intact and nothing about whether the quantization is any "
                     "good.")
        quality_button = Gtk.Button(label="Measure", valign=Gtk.Align.CENTER)
        quality_button.connect(
            "clicked", lambda b: self._off_main(
                b, lambda report: self._measure_quality(report)))
        quality_row.add_suffix(quality_button)

        rebuild_row = Adw.ActionRow(
            title="Rebuild it with calibration",
            subtitle="Re-quantizes an un-quantized model with an importance "
                     "matrix fitted to this system's own source, so the "
                     "precision budget goes where the activations say it "
                     "matters. Needs llama-imatrix; without it this refuses "
                     "rather than handing back an uncalibrated file.")
        rebuild_button = Gtk.Button(label="Rebuild", valign=Gtk.Align.CENTER)
        rebuild_button.connect(
            "clicked", lambda b: self._off_main(
                b, lambda report: self._rebuild_calibrated(report)))
        rebuild_row.add_suffix(rebuild_button)
        group.add(quality_row)
        group.add(rebuild_row)

        # **Reclaiming the room a Rebuild takes.** Measured before this row: every
        # call to `calibrated_quantize()` without an explicit `work=` left a
        # directory holding a corpus, an `imatrix.dat` and **two** ~100 MB GGUFs,
        # and nothing removed them on any exit path. One press, 200 MB. The
        # directory the *current* model lives in is kept even if you ask.
        scratch_row = Adw.ActionRow(
            title="Reclaim rebuild scratch",
            subtitle="Each calibration run keeps the uncalibrated build beside "
                     "the calibrated one so the two can be compared. This deletes "
                     "those copies - except any directory a model is loaded from.")
        scratch_button = Gtk.Button(label="Reclaim", valign=Gtk.Align.CENTER)
        scratch_button.connect(
            "clicked", lambda b: self._off_main(
                b, lambda report: self._reclaim_scratch(report)))
        scratch_row.add_suffix(scratch_button)
        group.add(scratch_row)

        # Presence: one control for how much of the machine Chronoa is holding.
        # It belongs here because it is a fact about the *model*, and a person
        # looking at a model list is the person who knows whether they want it
        # resident.
        presence_row = Adw.ActionRow(
            title="Right now",
            subtitle="A loaded model costs about as much memory as it is large.")
        button = Gtk.Button(label="Free the model", valign=Gtk.Align.CENTER)
        button.connect("clicked", lambda _b: self._cycle_presence(button, presence_row))
        presence_row.add_suffix(button)
        group.add(presence_row)
        self._presence_row = presence_row

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
