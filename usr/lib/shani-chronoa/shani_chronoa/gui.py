"""GTK4 GUI module for Shani Chronoa.

Provides the orb widget and cajita window interface.
"""

import logging
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('GLib', '2.0')

from gi.repository import Gtk, Gdk, GLib, GObject  # type: ignore

logger = logging.getLogger(__name__)


class ChronoaOrbWidget(Gtk.Button):
    """The orb widget - central interactive element of the UI.

    A circular button that pulses and responds to user interaction,
    representing the AI assistant's active state.
    """

    __gtype_name__ = 'ChronoaOrbWidget'

    def __init__(self) -> None:
        super().__init__()
        self._pulse_animation_id = 0
        self._is_listening = False
        self._setup_ui()
        self._setup_animation()

    def _setup_ui(self) -> None:
        """Set up the orb widget appearance."""
        self.set_size_request(80, 80)
        self.add_css_class("flat")

        # Create CSS for the orb
        css_provider = Gtk.CssProvider()
        css_data = b"""
            .chronoa-orb {
                background-color: #2196F3;
                border-radius: 50%;
                min-width: 80px;
                min-height: 80px;
                box-shadow: 0 0 20px rgba(33, 150, 243, 0.5);
                transition: all 0.3s ease;
            }
            .chronoa-orb:hover {
                background-color: #42A5F5;
                box-shadow: 0 0 30px rgba(33, 150, 243, 0.7);
            }
            .chronoa-orb:active {
                background-color: #1976D2;
                box-shadow: 0 0 40px rgba(33, 150, 243, 0.9);
            }
            .chronoa-orb.listening {
                background-color: #4CAF50;
                box-shadow: 0 0 25px rgba(76, 175, 80, 0.6);
                animation: pulse 1.5s ease-in-out infinite;
            }
            .chronoa-orb.processing {
                background-color: #FF9800;
                box-shadow: 0 0 25px rgba(255, 152, 0, 0.6);
            }
            .chronoa-orb.error {
                background-color: #F44336;
                box-shadow: 0 0 25px rgba(244, 67, 54, 0.6);
            }
            @keyframes pulse {
                0% { box-shadow: 0 0 20px rgba(76, 175, 80, 0.4); }
                50% { box-shadow: 0 0 40px rgba(76, 175, 80, 0.8); }
                100% { box-shadow: 0 0 20px rgba(76, 175, 80, 0.4); }
            }
        """
        css_provider.load_from_data(css_data)
        style_context = self.get_style_context()
        style_context.add_provider(
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        style_context.add_class("chronoa-orb")

        # Add icon
        icon = Gtk.Image.new_from_icon_name("audio-input-microphone-symbolic")
        icon.set_pixel_size(32)
        self.set_child(icon)

    def _setup_animation(self) -> None:
        """Set up pulse animation."""
        pass  # CSS handles the animation via keyframes

    def set_listening(self, listening: bool) -> None:
        """Set the orb to listening state."""
        self._is_listening = listening
        style_context = self.get_style_context()
        if listening:
            style_context.add_class("listening")
            style_context.remove_class("processing")
            style_context.remove_class("error")
        else:
            style_context.remove_class("listening")

    def set_processing(self, processing: bool) -> None:
        """Set the orb to processing state."""
        style_context = self.get_style_context()
        if processing:
            style_context.add_class("processing")
            style_context.remove_class("listening")
            style_context.remove_class("error")
        else:
            style_context.remove_class("processing")

    def set_error(self, error: bool) -> None:
        """Set the orb to error state."""
        style_context = self.get_style_context()
        if error:
            style_context.add_class("error")
            style_context.remove_class("listening")
            style_context.remove_class("processing")
        else:
            style_context.remove_class("error")

    def reset(self) -> None:
        """Reset orb to default state."""
        self._is_listening = False
        style_context = self.get_style_context()
        style_context.remove_class("listening")
        style_context.remove_class("processing")
        style_context.remove_class("error")


class CajitaWindow(Gtk.ApplicationWindow):
    """The cajita window - main application window.

    A cajita (small box) style window that contains the orb widget
    and provides the AI assistant interface.
    """

    def __init__(self, application: Gtk.Application) -> None:
        super().__init__(application=application)
        self._setup_window()
        self._setup_ui()

    def _setup_window(self) -> None:
        """Configure window properties."""
        self.set_title("Shani Chronoa")
        self.set_default_size(400, 500)
        self.set_size_request(350, 450)
        self.set_decorated(True)

        # Center on screen
        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)

        # Apply CSS
        self._apply_css()

    def _apply_css(self) -> None:
        """Apply CSS styling to the window."""
        css_provider = Gtk.CssProvider()
        css_data = b"""
            window.cajita-window {
                background-color: #1a1a2e;
                border-radius: 20px;
                padding: 10px;
            }
            .cajita-header {
                color: #e0e0e0;
                font-size: 18px;
                font-weight: 500;
                padding: 8px;
            }
            .cajita-status {
                color: #888;
                font-size: 12px;
                padding: 4px;
            }
            .cajita-response {
                color: #e0e0e0;
                font-size: 14px;
                padding: 12px;
                background-color: #2a2a3e;
                border-radius: 12px;
                margin: 8px;
            }
            .cajita-input {
                color: #fff;
                font-size: 14px;
                padding: 8px;
                background-color: #2a2a3e;
                border-radius: 8px;
                border: 1px solid #444;
            }
        """
        css_provider.load_from_data(css_data)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _setup_ui(self) -> None:
        """Set up the cajita window UI."""
        main_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )
        self.set_child(main_box)

        # Header row: title + settings button. Every toggle Chronoa has
        # grown (privacy, wake-word, cloud-fallback, barge-in-VAD,
        # auto-start) was previously reachable only by keyboard accelerator
        # or D-Bus - this is the first actual GUI entry point to any of them.
        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header = Gtk.Label(label="Shani Chronoa")
        header.add_css_class("cajita-header")
        header.set_hexpand(True)
        header.set_halign(Gtk.Align.CENTER)
        settings_button = Gtk.Button()
        settings_button.set_icon_name("emblem-system-symbolic")
        settings_button.add_css_class("flat")
        settings_button.set_valign(Gtk.Align.CENTER)
        settings_button.set_action_name("app.open-settings")
        header_row.append(header)
        header_row.append(settings_button)
        main_box.append(header_row)

        # Orb widget (center) - a Gtk.Button wired straight to the app's
        # toggle-listening action. It was previously never connected to
        # anything, so clicking it did nothing at all.
        self._orb = ChronoaOrbWidget()
        self._orb.set_halign(Gtk.Align.CENTER)
        self._orb.set_valign(Gtk.Align.CENTER)
        self._orb.set_action_name("app.toggle-listening")
        main_box.append(self._orb)

        # Status label
        self._status_label = Gtk.Label(label="Ready")
        self._status_label.add_css_class("cajita-status")
        self._status_label.set_halign(Gtk.Align.CENTER)
        main_box.append(self._status_label)

        # Response area
        self._response_scrolled = Gtk.ScrolledWindow()
        self._response_scrolled.set_vexpand(True)
        self._response_label = Gtk.Label(label="")
        self._response_label.set_wrap(True)
        self._response_label.add_css_class("cajita-response")
        self._response_scrolled.set_child(self._response_label)
        main_box.append(self._response_scrolled)

        # Input area
        self._input_entry = Gtk.Entry()
        self._input_entry.set_placeholder_text("Type or speak...")
        self._input_entry.add_css_class("cajita-input")
        self._input_entry.connect("activate", self._on_input_activate)
        main_box.append(self._input_entry)

    def _on_input_activate(self, entry: Gtk.Entry) -> None:
        """Handle input activation."""
        text = entry.get_text()
        if text.strip():
            entry.set_text("")
            # Emit signal for parent to handle
            self.emit("user-input", text)

    def set_status(self, status: str) -> None:
        """Update the status label."""
        self._status_label.set_label(status)

    def set_response(self, text: str) -> None:
        """Set the response text."""
        self._response_label.set_label(text)

    def set_orb_state(self, state: str) -> None:
        """Set the orb widget state."""
        if state == "listening":
            self._orb.set_listening(True)
            self._orb.set_processing(False)
            self._orb.set_error(False)
        elif state == "processing":
            self._orb.set_listening(False)
            self._orb.set_processing(True)
            self._orb.set_error(False)
        elif state == "error":
            self._orb.set_listening(False)
            self._orb.set_processing(False)
            self._orb.set_error(True)
        else:
            self._orb.reset()

    def get_input_text(self) -> str:
        """Get the current input text."""
        return self._input_entry.get_text()

    def clear_input(self) -> None:
        """Clear the input field."""
        self._input_entry.set_text("")

    # Custom signal
    __gsignals__ = {
        "user-input": (GObject.SignalFlags.RUN_FIRST, str, (str,)),
    }
