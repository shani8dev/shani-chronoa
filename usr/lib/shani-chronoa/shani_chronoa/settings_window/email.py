"""Email account setup, reachable from Settings.

The email skills refuse every call until a person writes a local account file
by hand. This is that surface: fields for IMAP/SMTP hosts, ports and the user,
a connection-security choice the skill actually implements, an explicit Save,
and an honest status read from the same file the skill reads.

Building the page never talks to a mail server and never sends a message. It
writes only the 0600 JSON the reader parses, and it says so on the page. The
password is not a field here: it stays in the session keyring, and the note
says where. The one network action, "Check account", is a button a person has
to press; it logs in through the keyring and sends nothing.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

import threading

from shani_chronoa import email_setup

GROUP_TITLE = "Email account"
STATUS_TITLE = "Account status"
SAVE_LABEL = "Save account"
CHECK_LABEL = "Check account"


class EmailPage:
    """A part of SettingsWindow, which mixes this in."""

    def _build_email(self, page) -> None:
        self._section_family = "privacy"
        group = self._group(
            page,
            GROUP_TITLE,
            "Read and send email through your own IMAP/SMTP account. "
            "Saving writes a local file only - no mail server is contacted "
            "and nothing is sent.",
        )
        current = email_setup.read_fields()
        entries = {}

        def field(title: str, subtitle: str, key: str) -> None:
            entries[key] = self._entry(
                group, title, subtitle, current.get(key, ""), lambda text: None
            )

        field(
            "IMAP host", "Server used to read mail, e.g. imap.example.org", "imap_host"
        )
        field("IMAP port", "Blank uses 993 with TLS, or 143 without", "imap_port")
        field(
            "SMTP host", "Server used to send mail, e.g. smtp.example.org", "smtp_host"
        )
        field("SMTP port", "Blank uses 465 with TLS, or 587 without", "smtp_port")
        field(
            "Account user",
            "Usually your email address; the keyring entry is email:<user>",
            "user",
        )

        # A hand-written file can encrypt one transport and not the other. The
        # two-mode menu cannot express that, so a third row preserves the file's
        # own pair rather than silently rewriting it the first time Settings is
        # opened - a security change nobody asked for is worse than an extra
        # option in a dropdown.
        modes = list(email_setup.SECURITY_MODES)
        imap_now = bool(current.get("imap_tls", True))
        smtp_now = bool(current.get("smtp_tls", True))
        if imap_now != smtp_now:
            modes.append(("file", "Mixed (as in the file)", imap_now, smtp_now))
        mode_labels = [label for _key, label, _imap, _smtp in modes]
        mode = Adw.ComboRow(title="Connection security")
        mode.set_subtitle(
            "TLS uses SSL for both IMAP and SMTP. There is no STARTTLS mode."
        )
        mode.set_model(Gtk.StringList.new(mode_labels))
        selected = 0
        for index, (_key, _label, imap_tls, smtp_tls) in enumerate(modes):
            if imap_now == imap_tls and smtp_now == smtp_tls:
                selected = index
                break
        mode.set_selected(selected)
        group.add(mode)
        group._needle_extra.append(
            (mode, "connection security tls plain starttls".lower())
        )

        save_row = Adw.ActionRow(
            title="Save this account",
            subtitle="Write the local account file. Nothing is sent from here.",
        )
        button = Gtk.Button(label=SAVE_LABEL)
        button.add_css_class("suggested-action")
        button.update_property([Gtk.AccessibleProperty.LABEL], ["Save email account"])
        save_row.add_suffix(button)
        group.add(save_row)
        group._needle_extra.append((save_row, "save email account".lower()))

        status_row = Adw.ActionRow(title=STATUS_TITLE, subtitle="")
        group.add(status_row)
        group._needle_extra.append((status_row, STATUS_TITLE.lower()))
        self._email_status_row = status_row

        check_row = Adw.ActionRow(
            title="Check account",
            subtitle=(
                "Log in once to both servers with the keyring password. "
                "Nothing is sent; the check has a time limit."
            ),
        )
        check_button = Gtk.Button(label=CHECK_LABEL)
        check_row.add_suffix(check_button)
        group.add(check_row)
        group._needle_extra.append((check_row, "check account login".lower()))

        note = Adw.ActionRow(title="Password", subtitle=email_setup.PASSWORD_NOTE)
        note.set_tooltip_text(email_setup.PASSWORD_NOTE)
        group.add(note)
        group._needle_extra.append((note, "password keyring".lower()))

        def paint_status(word: str, message: str) -> None:
            status_row.set_subtitle(f"{word}: {message}")
            status_row.remove_css_class("status-ok")
            status_row.remove_css_class("status-attention")
            status_row.remove_css_class("status-unknown")
            status_row.add_css_class(
                "status-ok"
                if word in (email_setup.CONFIGURED, email_setup.REACHABLE)
                else "status-unknown"
                if word == email_setup.NOT_CONFIGURED
                else "status-attention"
            )

        def refresh() -> None:
            paint_status(*email_setup.status())

        def on_check(*_args) -> None:
            check_button.set_sensitive(False)
            status_row.set_subtitle("checking: contacting the configured mail servers")

            # A login can block for seconds. Keep it off the GTK thread and
            # come back through the main loop to draw the answer.
            def work() -> None:
                try:
                    result = email_setup.check_account()
                except Exception as exc:  # noqa: BLE001 - never leave the button stuck
                    result = (email_setup.FAILED, f"check failed: {exc}")
                GLib.idle_add(done, result)

            def done(result) -> bool:
                paint_status(*result)
                check_button.set_sensitive(True)
                return False

            threading.Thread(target=work, daemon=True).start()

        def on_save(*_args) -> None:
            values = {key: row.get_text() for key, row in entries.items()}
            _key, _label, imap_tls, smtp_tls = modes[mode.get_selected()]
            values["imap_tls"] = imap_tls
            values["smtp_tls"] = smtp_tls
            try:
                email_setup.save(values)
            except ValueError as exc:
                paint_status(email_setup.FAILED, str(exc))
                return
            refresh()

        button.connect("clicked", on_save)
        check_button.connect("clicked", on_check)
        refresh()


__all__ = ["CHECK_LABEL", "GROUP_TITLE", "STATUS_TITLE", "SAVE_LABEL", "EmailPage"]
