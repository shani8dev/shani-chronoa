"""A pending question in the window: shown, answered by click or voice, or abandoned."""


import logging

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('GLib', '2.0')

from gi.repository import Gtk

logger = logging.getLogger(__name__)


from .questions import (  # noqa: F401
    match_spoken_option,
)




#: The composer's resting placeholder. Held here because `asking.py` puts
#: another one up while a question is open and has to put this one back - and
#: because the text now lives in two places that must agree.
_COMPOSER_PLACEHOLDER = "Type a message\u2026"


class _OptionRow(Gtk.FlowBox):
    """The answers to a question, wrapping rather than clipping.

    **This was a `Gtk.Box`, and two of the five answers could not be pressed.**
    A `Gtk.Box` does not wrap and does not shrink: measured, the row asked for
    **799px** inside a window that permits **380px** (the app's own
    `set_size_request`). So `No, and stop this turn` was cut mid-word at the
    right edge and `Provide feedback` - the option that carries the person's
    own objection back to the model - was **entirely off-screen with no way to
    scroll to it**. Every other part of the question fit: the heading asked for
    74px, the diff 117px, the hint 250px. The row of answers was the whole of
    the overflow.

    **`Gtk.FlowBox` alone is the fix**, and the minimum it reports is
    **189px** - one button - so a narrow window allocates one per line and
    wraps. The question row as a whole went **799px -> 250px**.

    **No `do_measure` override, and the reason is the interesting part.**
    `organs.OrganStrip` carries one, for the same GTK rule: a non-homogeneous
    `Gtk.FlowBox` sizes as if every child were as wide as the widest, so eight
    lights of 39-87px reported 815px for 440px of children. That correction was
    written here first, by analogy - and measured against this population it is
    **wrong**: `Gtk.FlowBox.do_measure` answers 678px for five buttons and the
    override made it **829px**, worse than the thing it replaced. The organ
    lights are small and nearly uniform, where widest-times-count overstates
    badly; these buttons vary by 44px and a real line break saves 127px. A
    transfer carried across on the strength of a shared class name, caught by
    measuring it rather than by an argument about GTK.
    """

    def __init__(self) -> None:
        # **`Gtk.FlowBox` has no `spacing` property** - it is `column-spacing`
        # and `row-spacing`, set below. Passing `spacing=` raises inside
        # `show_question`, which `gui/questions.py` catches and answers with an
        # empty string, so the whole question silently never appeared: the row
        # stayed `visible=False` at 0x0 and the caller was told "no answer".
        # Measured by rendering, not by reading: the widgets were built and the
        # row was never shown.
        super().__init__(homogeneous=False)
        self.set_selection_mode(Gtk.SelectionMode.NONE)
        self.set_column_spacing(6)
        self.set_row_spacing(6)
        # Explicit rather than the default, for the reason the organ strip
        # sets its own: the number of options is a property of the question,
        # not of the screen, so a wide window keeps one row and a narrow one
        # wraps - rather than the box choosing a row count nobody asked for.
        self.set_max_children_per_line(4)
        # One per line is the floor, and it is what makes the row's minimum a
        # single button rather than all of them: without it GTK reserves room
        # for the widest *line* it might build.
        self.set_min_children_per_line(1)


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

        # **The question, then what it is about, then the fine print.** The
        # approval's text is nine lines - what "this once" and "this session"
        # each cover, what closing it means - and drawn whole it pushed the
        # conversation off the window (measured: the request it was about
        # scrolled out of sight above it). The first paragraph is the question;
        # the rest is one click away, exactly as the spoken presenter already
        # says only the first paragraph aloud.
        head, _sep, rest = question.strip().partition("\n\n")
        label = Gtk.Label(label=head)
        label.set_wrap(True)
        label.set_xalign(0.0)
        label.add_css_class("heading")
        self._question_widgets.append(label)
        self._question_row.append(label)
        if rest.strip():
            details = Gtk.Expander(label="Details")
            body = Gtk.Label(label=rest.strip(), xalign=0.0, wrap=True)
            body.add_css_class("dim-label")
            details.set_child(body)
            self._question_details = details
        # **What the answer is about, on screen while it is asked** - the
        # edit's diff, the command and where it runs. See
        # `tools.approval_preview`.
        # Drawn here, under the question, but registered in
        # `_question_widgets` only after the buttons: that list is what gets
        # cleared, and readers rely on `[0]` the question and `[1]` the
        # buttons (measured: putting these first left a reader finding no
        # buttons, so nothing was ever answered).
        extras = []
        shown = self._question_preview(question)
        if shown is not None:
            extras.append(shown)
            self._question_row.append(shown)
        details = getattr(self, "_question_details", None)
        if details is not None and rest.strip():
            extras.append(details)
            self._question_row.append(details)
        self._question_details = None

        buttons = _OptionRow()
        for option in options:
            btn = Gtk.Button(label=option)
            btn.connect("clicked", lambda _b, o=option: self._resolve_question(o))
            buttons.append(btn)
        self._question_widgets.append(buttons)
        self._question_widgets.extend(extras)
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

    def _question_preview(self, question: str):
        from shani_chronoa import ask_bridge
        preview = ask_bridge.preview_for(question)
        if preview is None:
            return None
        try:
            if isinstance(preview, tuple) and len(preview) == 3:
                from shani_chronoa.gui.blocks import InlineDiff
                path, before, after = preview
                return InlineDiff(path, before, after,
                                  on_show_full=None)
            if isinstance(preview, dict) and preview.get("command"):
                where = preview.get("where") or "sandbox"
                net = "with network" if preview.get("network") else "no network"
                place = {"sandbox": f"sandboxed, project writable, {net}",
                         "container": f"in a container, {net}",
                         "machine": "on a machine"}.get(where, where)
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                box.add_css_class("inline-diff")
                command = Gtk.Label(label=f"$ {preview['command']}", xalign=0.0,
                                    wrap=True, selectable=True)
                command.add_css_class("monospace")
                box.append(command)
                note = Gtk.Label(label=place, xalign=0.0)
                note.add_css_class("dim-label")
                note.add_css_class("caption")
                box.append(note)
                return box
        except Exception:  # noqa: BLE001 - the question matters more than its preview
            logger.debug("question preview not drawn", exc_info=True)
        return None

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
