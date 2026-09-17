"""Settings window.

Before this, every toggle added to Chronoa (privacy, wake-word,
cloud-fallback, barge-in-VAD, auto-start) was a GAction only reachable by
keyboard accelerator or D-Bus - genuinely invisible in the GUI. Plain GTK4
here, no libadwaita: this repo has no libadwaita dependency anywhere else
and `gui.py` is already plain `Gtk.Window`/`Gtk.Box` with hand-written CSS,
so matching that keeps the dependency footprint and visual style the same
rather than introducing a second toolkit convention for one window.

Design choice: switch rows call the app's own GActions
(`app.activate_action(...)`) instead of writing to gsettings directly, so a
toggle's side effects (re-syncing the autostart entry, dropping/enabling
the cloud LLM chain, etc.) run through the exact same code path as the
keyboard shortcuts - one implementation of "what happens when this
changes," not two. Text-entry settings that don't have live-reconfiguration
wiring (Ollama host, model overrides, Piper voice) are saved straight to
gsettings and take effect on next restart - hot-reloading a running
STT/TTS/LLM connection is a bigger change than this window, not attempted
here (see AGENTS.md's "Deliberately not done" section for the full list of
what's out of scope this pass).
"""

import logging

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk  # type: ignore

logger = logging.getLogger(__name__)


def _section_label(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0)
    label.add_css_class("settings-section")
    return label


def _switch_row(label_text: str, active: bool, on_toggle) -> Gtk.Box:
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    row.add_css_class("settings-row")
    label = Gtk.Label(label=label_text, xalign=0)
    label.set_hexpand(True)
    label.set_wrap(True)
    switch = Gtk.Switch()
    switch.set_active(active)
    switch.set_valign(Gtk.Align.CENTER)

    def _on_state_set(_switch: Gtk.Switch, state: bool) -> bool:
        on_toggle(state)
        return False  # let the switch visually update; we don't override GTK's own state

    switch.connect("state-set", _on_state_set)
    row.append(label)
    row.append(switch)
    return row


def _entry_row(label_text: str, value: str, on_changed, sensitive: bool = False) -> Gtk.Box:
    """`sensitive=True` masks the entry like a password field (for API keys)."""
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    row.add_css_class("settings-row")
    label = Gtk.Label(label=label_text, xalign=0)
    label.set_hexpand(True)
    label.set_wrap(True)
    entry = Gtk.Entry()
    entry.set_text(value)
    entry.set_width_chars(20)
    entry.set_valign(Gtk.Align.CENTER)
    if sensitive:
        entry.set_visibility(False)
        entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
    entry.connect("changed", lambda e: on_changed(e.get_text()))
    row.append(label)
    row.append(entry)
    return row


class SettingsWindow(Gtk.Window):
    """Settings for every toggle/value Chronoa has grown, in one place."""

    def __init__(self, app) -> None:
        super().__init__(application=app, title="Chronoa Settings")
        self.app = app
        self.set_default_size(400, 520)
        self.set_modal(True)
        if app.window:
            self.set_transient_for(app.window)
        self._apply_css()
        self._build_ui()

    def _apply_css(self) -> None:
        css_provider = Gtk.CssProvider()
        css_data = b"""
            window { background-color: #1a1a2e; }
            .settings-section {
                color: #7ea6ff;
                font-size: 13px;
                font-weight: 600;
                margin-top: 14px;
                margin-bottom: 2px;
            }
            .settings-row label { color: #e0e0e0; font-size: 13px; }
            .settings-row entry {
                background-color: #2a2a3e;
                color: #fff;
                border-radius: 6px;
            }
            .settings-hint {
                color: #888;
                font-size: 11px;
                margin-bottom: 4px;
            }
        """
        css_provider.load_from_data(css_data)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _build_ui(self) -> None:
        scrolled = Gtk.ScrolledWindow()
        self.set_child(scrolled)

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )
        scrolled.set_child(box)

        config = self.app.config
        app = self.app

        box.append(_section_label("Privacy & Network"))
        box.append(_switch_row(
            "Privacy mode (local-only)", config.privacy_mode,
            lambda active: self._activate_if_changed("toggle-privacy", config.privacy_mode, active),
        ))
        box.append(_switch_row(
            "Allow free cloud LLM fallback when Ollama is unavailable", config.cloud_fallback_enabled,
            lambda active: self._activate_if_changed("toggle-cloud-fallback", config.cloud_fallback_enabled, active),
        ))
        cloud_keys_hint = Gtk.Label(
            label="Optional API keys (BYOK) for the free cloud providers below - blank uses anonymous "
                  "access; a real key can raise that provider's rate limits. Applies next time the "
                  "cloud fallback (re)activates, not to an already-active session.",
            xalign=0,
        )
        cloud_keys_hint.add_css_class("settings-hint")
        cloud_keys_hint.set_wrap(True)
        box.append(cloud_keys_hint)
        box.append(_entry_row(
            "LLM7 API key", config.get("llm7-api-key", ""),
            lambda text: config.set("llm7-api-key", text), sensitive=True,
        ))
        box.append(_entry_row(
            "Kilo Gateway API key", config.get("kilo-api-key", ""),
            lambda text: config.set("kilo-api-key", text), sensitive=True,
        ))
        box.append(_entry_row(
            "BlockRun API key", config.get("blockrun-api-key", ""),
            lambda text: config.set("blockrun-api-key", text), sensitive=True,
        ))

        box.append(_section_label("Cloud LLM (BYOK - required, no anonymous access)"))
        byok_hint = Gtk.Label(
            label="Anthropic, OpenAI, Google, and Groq all rejected an unauthenticated request "
                  "when tested live - a key here is mandatory, not optional. Tried ahead of the "
                  "free providers above when set.",
            xalign=0,
        )
        byok_hint.add_css_class("settings-hint")
        byok_hint.set_wrap(True)
        box.append(byok_hint)
        box.append(_entry_row(
            "Anthropic (Claude) API key", config.get("anthropic-api-key", ""),
            lambda text: config.set("anthropic-api-key", text), sensitive=True,
        ))
        box.append(_entry_row(
            "OpenAI API key", config.get("openai-api-key", ""),
            lambda text: config.set("openai-api-key", text), sensitive=True,
        ))
        box.append(_entry_row(
            "Google Gemini API key", config.get("google-api-key", ""),
            lambda text: config.set("google-api-key", text), sensitive=True,
        ))
        box.append(_entry_row(
            "Groq API key", config.get("groq-api-key", ""),
            lambda text: config.set("groq-api-key", text), sensitive=True,
        ))

        box.append(_section_label("Voice"))
        box.append(_switch_row(
            "Wake-word activation", app._wake_word_active,
            lambda active: self._activate_if_changed("toggle-wake-word", app._wake_word_active, active),
        ))
        box.append(_entry_row(
            "Wake-word model", config.wake_word_model,
            lambda text: config.set("wake-word-model", text),
        ))
        box.append(_switch_row(
            "Interrupt on speech while replying (barge-in)", config.barge_in_vad_enabled,
            lambda active: self._activate_if_changed("toggle-barge-in-vad", config.barge_in_vad_enabled, active),
        ))
        hint = Gtk.Label(
            label="No echo cancellation - may self-interrupt on speaker output. Best with headphones.",
            xalign=0,
        )
        hint.add_css_class("settings-hint")
        hint.set_wrap(True)
        box.append(hint)
        box.append(_entry_row(
            "Speech language (whisper.cpp)", config.language,
            lambda text: config.set("language", text),
        ))

        box.append(_section_label("Models (apply on restart)"))
        box.append(_entry_row(
            "Ollama host", config.ollama_host,
            lambda text: config.set("ollama-host", text),
        ))
        box.append(_entry_row(
            "Model override (blank = auto-detect)", config.model,
            lambda text: config.set("model", text),
        ))
        box.append(_entry_row(
            "Whisper model override (blank = auto-detect)", config.whisper_model,
            lambda text: config.set("whisper-model", text),
        ))
        box.append(_entry_row(
            "Piper voice", config.piper_voice,
            lambda text: config.set("piper-voice", text),
        ))

        box.append(_section_label("System"))
        box.append(_switch_row(
            "Start on login", config.auto_start,
            lambda active: self._activate_if_changed("toggle-auto-start", config.auto_start, active),
        ))
        box.append(_switch_row(
            "Debug logging", config.debug_mode,
            lambda active: self._activate_if_changed("toggle-debug", config.debug_mode, active),
        ))

        close_button = Gtk.Button(label="Close")
        close_button.add_css_class("flat")
        close_button.set_halign(Gtk.Align.END)
        close_button.set_margin_top(16)
        close_button.connect("clicked", lambda *_: self.close())
        box.append(close_button)

    def _activate_if_changed(self, action_name: str, current: bool, requested: bool) -> None:
        """Only fire the app's toggle action if the switch's new state actually differs.

        `current` is a property access evaluated fresh at call time (not a
        stale snapshot from when the row was built), so this stays correct
        across repeated toggles within one settings-window session.
        """
        if requested != current:
            self.app.activate_action(action_name, None)
