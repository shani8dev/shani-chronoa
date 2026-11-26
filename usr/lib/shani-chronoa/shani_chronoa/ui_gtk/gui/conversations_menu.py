"""The Conversations menu, and redrawing the transcript from a saved conversation."""


import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('GLib', '2.0')

from gi.repository import Gtk, GLib






class ConversationsMenuMixin:
    """The Conversations menu, and redrawing the transcript from a saved conversation. - a part of ChronoaWindow, which mixes it in."""


    def show_conversation(self, turns) -> None:
        """Redraw the transcript from (role, text) pairs - after opening another conversation."""
        self._transcript.show_placeholder()
        for role, text in turns:
            if role == "user":
                self._transcript.add_user_turn(text)
            else:
                self._transcript._current_assistant = None
                self._transcript.add_assistant_turn(text)

    def _fill_conversations(self) -> None:
        """Build the Conversations popover from the index each time it opens."""
        import time as _time
        from shani_chronoa import conversation_store
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=6, margin_bottom=6,
                      margin_start=6, margin_end=6)
        new = Gtk.Button(label="New conversation")
        new.set_action_name("app.reset-conversation")
        new.connect("clicked", lambda _b: self._conversations_popover.popdown())
        box.append(new)
        try:
            listed = conversation_store.list_sessions(conversation_store.session_dir())
        except OSError:
            listed = []
        scroller = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=360,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER)
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for item in listed:
            if not item["messages"] and not item["active"]:
                continue
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            when = _time.strftime("%d %b %H:%M", _time.localtime(item["updated"]))
            open_btn = Gtk.Button(label=f"{'• ' if item['active'] else ''}{item['title']}  ({when})")
            open_btn.add_css_class("flat")
            open_btn.set_hexpand(True)
            open_btn.get_child().set_halign(Gtk.Align.START)
            open_btn.get_child().set_ellipsize(3)  # Pango.EllipsizeMode.END
            open_btn.get_child().set_max_width_chars(40)
            open_btn.set_action_name("app.switch-conversation")
            open_btn.set_action_target_value(GLib.Variant("s", item["id"]))
            open_btn.connect("clicked", lambda _b: self._conversations_popover.popdown())
            delete = Gtk.Button(icon_name="user-trash-symbolic")
            delete.add_css_class("flat")
            delete.set_tooltip_text("Delete this conversation")
            delete.update_property([Gtk.AccessibleProperty.LABEL], [f"Delete {item['title']}"])
            delete.set_action_name("app.delete-conversation")
            delete.set_action_target_value(GLib.Variant("s", item["id"]))
            delete.connect("clicked", lambda _b: self._conversations_popover.popdown())
            row.append(open_btn)
            row.append(delete)
            rows.append(row)
        scroller.set_child(rows)
        box.append(scroller)
        self._conversations_popover.set_child(box)
