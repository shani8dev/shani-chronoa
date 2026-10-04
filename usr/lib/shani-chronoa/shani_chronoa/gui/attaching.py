"""Files attached to the next message: dropped, picked, listed and removed."""

import logging

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('GLib', '2.0')

from gi.repository import Gtk, GLib



logger = logging.getLogger(__name__)




class AttachingMixin:
    """Files attached to the next message: dropped, picked, listed and removed. - a part of ChronoaWindow, which mixes it in."""


    def add_attachments(self, paths) -> None:
        from shani_chronoa import attachments
        self._attachments = attachments.accept(paths, self._attachments)
        self._refresh_attach_bar()

    def take_attachments(self):
        """(names, block) for the message being sent, and clear the chips."""
        from shani_chronoa import attachments
        names, block = attachments.take(self._attachments)
        self._attachments = []
        self._refresh_attach_bar()
        return names, block

    def _refresh_attach_bar(self) -> None:
        child = self._attach_bar.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._attach_bar.remove(child)
            child = nxt
        for path in self._attachments:
            chip = Gtk.Button(label=f"{path.name}  ✕")
            chip.add_css_class("pill")
            chip.set_tooltip_text(str(path))
            chip.update_property([Gtk.AccessibleProperty.LABEL], [f"Remove attachment {path.name}"])
            chip.connect("clicked", lambda _b, p=path: self._remove_attachment(p))
            self._attach_bar.append(chip)
        self._attach_bar.set_visible(bool(self._attachments))

    def _remove_attachment(self, path) -> None:
        self._attachments = [p for p in self._attachments if p != path]
        self._refresh_attach_bar()

    def _on_files_dropped(self, _target, value, _x, _y) -> bool:
        try:
            self.add_attachments(f.get_path() for f in value.get_files() if f.get_path())
        except Exception as e:  # noqa: BLE001 - a bad drop must not break the window
            logger.warning(f"Could not take the dropped files: {e}")
            return False
        return True

    def _pick_files(self) -> None:
        dialog = Gtk.FileDialog(title="Attach files", modal=True)

        def done(dlg, result):
            try:
                chosen = dlg.open_multiple_finish(result)
            except GLib.Error:
                return  # cancelled
            self.add_attachments(chosen.get_item(i).get_path() for i in range(chosen.get_n_items())
                                 if chosen.get_item(i).get_path())
        dialog.open_multiple(self, None, done)
