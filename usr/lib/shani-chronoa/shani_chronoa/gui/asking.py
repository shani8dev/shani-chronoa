"""A pending question in the window: shown, answered by click or voice, or abandoned."""


import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('GLib', '2.0')

from gi.repository import Gtk


from .questions import (  # noqa: F401
    match_spoken_option,
)




#: The composer's resting placeholder. Held here because `asking.py` puts
#: another one up while a question is open and has to put this one back - and
#: because the text now lives in two places that must agree.
_COMPOSER_PLACEHOLDER = "Type a message\u2026"


class AskingMixin:
    """A pending question in the window: shown, answered by click or voice, or abandoned. - a part of ChronoaWindow, which mixes it in."""


    def show_question(self, question: str, options: list, resolve) -> None:
        """Put a question with its options on screen, resolved by `resolve`.

        `resolve` is passed in rather than returned because the caller is
        off-thread: it has to hold the event *now* and queue the widget work for
        the GTK thread, so a method that returned the event would be read before
        GLib had run it.
        """
        if self._pending_question is not None:
            self._resolve_question("")

        self._clear_question_widgets()

        label = Gtk.Label(label=question)
        label.set_wrap(True)
        label.set_xalign(0.0)
        self._question_widgets.append(label)
        self._question_row.append(label)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for option in options:
            btn = Gtk.Button(label=option)
            btn.set_hexpand(True)
            btn.connect("clicked", lambda _b, o=option: self._resolve_question(o))
            buttons.append(btn)
        self._question_widgets.append(buttons)
        self._question_row.append(buttons)

        hint = Gtk.Label(label="Tap an option, or just say your answer.")
        hint.add_css_class("dim-label")
        hint.set_xalign(0.0)
        self._question_widgets.append(hint)
        self._question_row.append(hint)

        self._question_row.set_visible(True)
        # **The composer's own placeholder label, not
        # `_input_entry.set_placeholder_text`.** `_input_entry` is a
        # `Gtk.TextView`, which has no `set_placeholder_text` - that is
        # `Gtk.Entry`'s method - so this raised `AttributeError` every single time
        # a question was asked. `questions.py` catches it, logs "question could
        # not be put on screen", and resolves `""`: **the prompt never appeared
        # and the tool that asked got "no answer"**, silently, in the shipped
        # app. Measured by running `show_question` directly and reading the
        # traceback; four tests reported "the real app produced no result" and
        # were filed as pre-existing.
        #
        # The composer already has a placeholder - a `Gtk.Label` inside the
        # `Gtk.Overlay` around the entry, shown by `_on_input_changed` whenever
        # the buffer is empty. So this sets that label's text and lets the
        # existing logic show it; there is no second placeholder to invent.
        self._input_placeholder.set_label("Say or type your answer…")
        self._on_input_changed(self._input_entry.get_buffer())
        self._pending_question = resolve
        self._pending_options = list(options)

    def has_pending_question(self) -> bool:
        return self._pending_question is not None

    def answer_pending_question(self, spoken: str) -> bool:
        """Answer the question on screen with what was SAID; False if none is open.

        The hint promises "just say your answer", but a voice transcript used to
        open a new turn while the tool sat blocked waiting for this one. And it
        must be mapped to an option: permissions compares the answer exactly,
        and whisper writes "Allow this once." - full stop - which would have
        been read as a refusal.
        """
        if self._pending_question is None:
            return False
        self._resolve_question(match_spoken_option(spoken, getattr(self, "_pending_options", [])))
        return True

    def abandon_pending_question(self) -> None:
        """Resolve a prompt on screen as unanswered, and touch nothing else.

        This is the dismissal path for the one case where the user never gets to
        answer: the window is going away with the question still up. Leaving the
        event unset holds the tool loop for the whole decision timeout
        (`permissions.DECISION_TIMEOUT_SECONDS`) after the user has already quit,
        which is how a prompt turns into a hung exit.

        Deliberately resolves the event and stops there. `_resolve_question`
        would also tear the widgets down, un-hide the input row and steal focus -
        all correct while the window lives, all wrong here, because the window is
        mid-destruction and the widgets are about to die with it anyway.
        """
        resolve = self._pending_question
        if resolve is None:
            return
        self._pending_question = None
        resolve("")

    def _clear_question_widgets(self) -> None:
        """Drop the prompt's widgets.

        GTK4's `Gtk.Box` has no `get_children()` - that is GTK3 - so the widgets
        are tracked as they are added and removed by reference.
        """
        for widget in self._question_widgets:
            self._question_row.remove(widget)
        self._question_widgets = []

    def _resolve_question(self, answer: str) -> None:
        resolve = self._pending_question
        self._pending_question = None
        if resolve is not None:
            resolve(answer)
        self._clear_question_widgets()
        self._question_row.set_visible(False)
        # **Restore the composer's own prompt.** Without this the composer keeps
        # saying "Say or type your answer…" after the question is gone, which is
        # a question with nothing to answer.
        self._input_placeholder.set_label(_COMPOSER_PLACEHOLDER)
        self._on_input_changed(self._input_entry.get_buffer())
        # **The `set_placeholder_text` line that used to be here was the same
        # bug as the one above, one line down**, and it was still in the tree
        # after that fix: `_input_entry` is a `Gtk.TextView`, so it raised
        # `AttributeError` here too - *after* `resolve(answer)` had already run,
        # which is why every test still passed. The visible damage was that
        # `grab_focus()` on the next line never ran, so after typing an answer
        # the caret went nowhere.
        #
        # Found by a static sweep for Entry-only methods called on TextViews
        # across the tree, after finding three of them in one test file.
        self._input_entry.grab_focus()
