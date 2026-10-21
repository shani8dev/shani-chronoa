"""A reply as blocks, and the widgets they become.

One `Gtk.Label` of markup was enough while a reply was prose. It stops being
enough the moment a reply contains a thing with its own controls - a command
you want to copy without the three sentences around it, a table you want as a
spreadsheet, an equation you want to look at rather than read - and every one of
those is something a model writes without being asked. Alpaca splits a reply into
blocks for exactly this reason; this is the same split, with the escaping still
owned by `markdown_lite`.

**Parsing is separated from building, and that is the point.** `parse()` is a
pure function over a string, so every question that matters can be answered by
running it: which fences are unclosed, what happens to a `$5` price next to a
`$x$` equation, whether a two-line pipe thing is a table. A widget tree answers
none of them without a display.

Three limits are deliberate rather than missing:

- **A code block is never run by the assistant.** "Run" hands the script to a
  terminal the user can see, with the code written out in front of them. This is
  the no-shell-exec boundary in `tools.py`, and a "Run" button that quietly
  pipes model-written text into a shell would be that boundary removed with a
  nicer icon.
- **Mathematics renders only if matplotlib is installed**, because that is a
  download the user has to agree to. Without it the source is shown as written,
  which is honest and still readable; a placeholder claiming an equation is not
  there would not be.
- **Inline markers inside a table cell are shown, not applied** (see
  `markdown_lite`), because bold in one cell of a fixed-width column breaks the
  alignment it exists to provide.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Pango', '1.0')

from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # type: ignore

from shani_chronoa import markdown_lite
from shani_chronoa.gui import code_hl
from shani_chronoa.words import plural

logger = logging.getLogger(__name__)

#: Terminals tried in order for "Run". Anything the image actually ships; a
#: missing one is not an error, it is the next candidate.
TERMINALS = ("kgx", "gnome-terminal", "konsole", "foot", "alacritty", "xterm")

#: A runnable language gets a Run button. Restricted on purpose: this writes a
#: file and opens a terminal, and "run" for a language whose interpreter is a
#: shell alias (`sh`, `bash`, `zsh`, `fish`) is a request to run a script, which
#: is the thing this boundary exists for. Those are not offered.
SCRIPT_LANGUAGES = {
    "python": ("script.py", ["python3"]),
    "py": ("script.py", ["python3"]),
    "javascript": ("script.js", ["node"]),
    "js": ("script.js", ["node"]),
    "typescript": ("script.ts", ["node"]),
    "ts": ("script.ts", ["node"]),
    "ruby": ("script.rb", ["ruby"]),
    "perl": ("script.pl", ["perl"]),
}


@dataclass
class Block:
    """One piece of a reply.

    `kind` is one of text, heading, code, table, math, rule. `payload` is the
    block's own content - for a table, the rows; for a code block, the code
    alone, never the fences.
    """

    kind: str
    payload: object
    language: str = ""
    meta: dict = field(default_factory=dict)


# --- parsing ---------------------------------------------------------------
#
# One pass, fence-aware: a fence's contents are taken whole, so a `#` or a `|`
# inside a shell pipeline cannot be mistaken for a heading or a table.

#: A fence is a line that opens with ``` and a line that closes with ``` on
#: its own. Scanning for that pair line by line (rather than one greedy regex
#: over the whole reply) is what lets an *unclosed* fence fall through as text
#: instead of swallowing everything after it - a reply cut off mid-block is a
#: normal thing to receive, and a parser that eats the rest of it is worse than
#: one that shows the rest as text.
_FENCE_OPEN = re.compile(r"^\s*```(\w*)\s*$")
_FENCE_CLOSE = re.compile(r"^\s*```+\s*$")
_MATH_BLOCK = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_RULE = re.compile(r"^\s*(?:---+|\*\*\*+|___+)\s*$")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")


def _cells(line: str) -> List[str]:
    trimmed = line.strip()
    if trimmed.startswith("|"):
        trimmed = trimmed[1:]
    if trimmed.endswith("|"):
        trimmed = trimmed[:-1]
    return [cell.strip() for cell in trimmed.split("|")]


def _parse_table(lines: List[str]) -> Optional[Block]:
    if len(lines) < 2 or "|" not in lines[0] or "|" not in lines[1]:
        return None
    if not _TABLE_RULE.match(lines[1]):
        return None
    header = _cells(lines[0])
    rows = [_cells(line) for line in lines[2:] if "|" in line]
    if len(header) < 2 or any(len(row) != len(header) for row in rows):
        return None
    return Block("table", [header] + rows, meta={"aligns": markdown_lite._alignments(lines[1])})


#: `$$...$$` and `$...$`. The dollar sign is the most overloaded character in a
#: reply - currency, shell variables and equations all use it - so what follows
#: is the test, not the delimiter.
_MATH_DISPLAY = re.compile(r"\$\$([^$]+)\$\$")
_MATH_INLINE = re.compile(r"\$([^$\n]{1,300})\$")


def _is_equation(body: str) -> bool:
    """Does this look like LaTeX rather than a price?

    The test is a backslash, which every LaTeX command has and no price, path or
    shell variable has. `\frac{a}{b}` is an equation; `$5`, `$/usr` and `$PATH`
    are not. A stricter rule (balanced braces, a known command) rejects real
    equations like `x^2` that a model writes constantly.
    """
    return "\\" in body


def _inline_math(segment: str) -> List[Block]:
    """Equations inside one segment of prose.

    `finditer` resumes after each match's *closing* dollar, so a pair that turns
    out not to be an equation costs only itself: on
    `Cost $5 or $12.50, and $\frac{a}{b}$` the `$5 or $` pair is rejected and
    `$\frac{a}{b}$` is still found. (A hand-written scanner that advanced one
    dollar at a time was written here first, on the belief that it did not -
    checking it against the regex showed the belief was wrong, so the regex
    stayed.)
    """
    out: List[Block] = []
    last = 0
    for match in _MATH_INLINE.finditer(segment):
        if not _is_equation(match.group(1)):
            continue
        out.append(Block("text", segment[last:match.start()]))
        out.append(Block("math", match.group(1).strip()))
        last = match.end()
    out.append(Block("text", segment[last:]))
    return [block for block in out if block.kind != "text" or block.payload]


def _emit_math(text: str) -> List[Block]:
    """A paragraph split around its equations, everything else kept verbatim."""
    out: List[Block] = []
    last = 0
    for match in _MATH_DISPLAY.finditer(text):
        # No backslash test here, and deliberately so: `$$` is a delimiter the
        # model chose on purpose, unlike a lone `$` which is also a price. A
        # display block of `x^2` is an equation; a `$` pair of `x^2` is a guess.
        out.extend(_inline_math(text[last:match.start()]))
        out.append(Block("math", match.group(1).strip()))
        last = match.end()
    out.extend(_inline_math(text[last:]))
    return [block for block in out if block.kind != "text" or block.payload]


def parse(text: str) -> List[Block]:
    """A reply -> its blocks. Pure: no widgets, no I/O, no model.

    Never raises. A reply that cannot be understood comes back as one text
    block of exactly what the model wrote, because a rendering that drops half an
    answer is worse than an ugly one.
    """
    if not (text or "").strip():
        return []
    blocks: List[Block] = []
    pending: List[str] = []

    def flush() -> None:
        if pending:
            joined = "\n".join(pending)
            pending.clear()
            blocks.extend(_emit_math(joined))

    lines = text.split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]

        opening = _FENCE_OPEN.match(line)
        if opening:
            closing = next((offset for offset in range(index + 1, len(lines))
                            if _FENCE_CLOSE.match(lines[offset])), None)
            if closing is not None:
                flush()
                language = opening.group(1).lower()
                body = "\n".join(lines[index + 1:closing]).strip("\n")
                blocks.append(Block("math" if language in ("latex", "tex", "math") else "code",
                                    body, language=language))
                index = closing + 1
                continue
            # Unclosed: fall through and treat the line as text.

        if _RULE.match(line):
            flush()
            blocks.append(Block("rule", ""))
            index += 1
            continue

        heading = _HEADING.match(line)
        if heading:
            flush()
            blocks.append(Block("heading", heading.group(2).strip(),
                                language=str(len(heading.group(1)))))
            index += 1
            continue

        if "|" in line:
            run = []
            while index < len(lines) and "|" in lines[index]:
                run.append(lines[index])
                index += 1
            table = _parse_table(run)
            if table is not None:
                flush()
                blocks.append(table)
                continue
            pending.extend(run)
            continue

        pending.append(line)
        index += 1

    flush()
    return blocks or [Block("text", text)]


# --- widgets ---------------------------------------------------------------


def _flat_button(icon: str, tooltip: str, handler, *args) -> Gtk.Button:
    button = Gtk.Button()
    button.set_icon_name(icon)
    button.add_css_class("flat")
    button.add_css_class("circular")
    button.set_tooltip_text(tooltip)
    button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
    button.connect("clicked", handler, *args)
    return button


def _copy_to_clipboard(widget: Gtk.Widget, text: str) -> None:
    """The clipboard write both buttons use.

    `set_content` is the current API and `set` the pre-4.10 one; the two
    disagree about which exists, so both are tried rather than version-sniffed.
    """
    clipboard = widget.get_clipboard()
    try:
        clipboard.set_content(Gdk.ContentProvider.new_for_value(text))
    except AttributeError:                            # pragma: no cover - old GTK
        clipboard.set(text)


def terminal_available() -> Optional[str]:
    """The first terminal this machine actually has, or None."""
    for name in TERMINALS:
        if shutil.which(name):
            return name
    return None


def write_script(code: str, language: str, directory: Optional[Path] = None) -> Optional[Path]:
    """A runnable script on disk, and nothing is executed.

    Returns None for a language with no interpreter here, rather than writing a
    file nothing can run.
    """
    entry = SCRIPT_LANGUAGES.get((language or "").lower())
    if entry is None:
        return None
    name, _argv = entry
    target = Path(directory or tempfile.mkdtemp(prefix="chronoa-script-")) / name
    target.write_text(code if code.endswith("\n") else code + "\n", encoding="utf-8")
    return target


def open_in_terminal(script: Path, language: str = "") -> str:
    """Show the script to the user in a terminal, with a command to run it.

    The assistant never runs model-written code. This writes the file, asks the
    desktop to open a terminal on it, and returns the command line it used so
    the window can show it - the person runs it, in a window they can see, with
    the code in front of them.
    """
    terminal = terminal_available()
    if terminal is None:
        return ("No terminal emulator was found, so nothing was opened. "
                f"The script is at {script}.")
    entry = SCRIPT_LANGUAGES.get(language.lower())
    command = f"{entry[1][0]} {script.name}" if entry else f"bash {script.name}"
    argv = [terminal]
    if terminal in ("gnome-terminal", "kgx", "konsole", "alacritty"):
        argv += ["--working-directory", str(script.parent)]
    argv.append(command)
    try:
        subprocess.Popen(argv, cwd=str(script.parent),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        return f"Could not open a terminal: {exc}. The script is at {script}."
    return f"Opened {terminal} with: {command}"


def math_png(source: str, directory: Optional[Path] = None) -> Optional[Path]:
    """Render an equation to a PNG with matplotlib, or None if it cannot.

    matplotlib is an opt-in setup extra, so this is expected to be absent on a
    fresh install. None is the honest answer then, and the caller shows the
    source as written rather than an empty box.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                          # noqa: BLE001 - any import failure is the same to a caller
        logger.debug("matplotlib is not available for equation rendering: %s", exc)
        return None
    figure = plt.figure(figsize=(0.01, 0.01), dpi=100)
    figure.patch.set_alpha(0.0)
    try:
        figure.text(0, 0, f"${source}$", fontsize=14)
        target = Path(directory or tempfile.mkdtemp(prefix="chronoa-math-")) / "equation.png"
        figure.savefig(str(target), bbox_inches="tight", pad_inches=0.05, transparent=True)
        return target
    except Exception as exc:                          # noqa: BLE001 - bad LaTeX is text, not a crash
        logger.debug("could not render %r: %s", source, exc)
        return None
    finally:
        plt.close(figure)


class CodeBlock(Gtk.Box):
    """A fenced code block: its language, its code, and three controls.

    Copy is per block because the prose around a command is not part of it. Save
    is per block for the same reason - a script is worth keeping. Run is a
    terminal, not an execution (see the module docstring).
    """

    def __init__(self, block: Block) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("reply-block")
        self.code = str(block.payload)
        self.language = block.language or ""

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        label = Gtk.Label(label=(self.language or "code").title())
        label.add_css_class("reply-block-heading")
        label.set_hexpand(True)
        label.set_xalign(0.0)
        header.append(label)
        if self.language.lower() in SCRIPT_LANGUAGES and terminal_available():
            header.append(_flat_button("system-run-symbolic", "Open this in a terminal",
                                       self._on_run))
        header.append(_flat_button("folder-download-symbolic", "Save this script",
                                   self._on_save))
        header.append(_flat_button("edit-copy-symbolic", "Copy this code",
                                   self._on_copy))
        self.append(header)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroller.set_max_content_height(260)
        body = Gtk.Label()
        body.add_css_class("reply-code")
        body.set_selectable(True)
        body.set_xalign(0.0)
        self._body = body
        self.refresh_highlight()
        scroller.set_child(body)
        self.append(scroller)

    def refresh_highlight(self) -> None:
        """Re-render the code, with the palette for the theme in force right now.

        **Called at construction and again whenever the desktop switches
        between light and dark.** The second is not polish: `code_hl`'s two
        palettes are disjoint in luminance - no colour can be legible on both a
        white and a dark background - so a block built under one theme is
        unreadable under the other. `TranscriptView` owns the one connection
        and walks its blocks, because a block connecting itself to a
        process-wide singleton would keep itself alive for the life of the app.
        """
        self._body.set_markup(
            f"<tt>{code_hl.highlight(self.code, self.language)}</tt>")

    def _on_copy(self, button: Gtk.Button) -> None:
        _copy_to_clipboard(button, self.code)
        self._flash(button, "object-select-symbolic", "edit-copy-symbolic")

    def _on_save(self, button: Gtk.Button) -> None:
        entry = SCRIPT_LANGUAGES.get(self.language.lower())
        default = (entry[0] if entry else "snippet.txt")
        dialog = Gtk.FileDialog()
        dialog.save(self.get_root(), None, lambda _dlg, result: self._saved(dialog, result, default))

    def _saved(self, dialog: Gtk.FileDialog, result, default: str) -> None:
        try:
            file = dialog.save_finish(result)
        except GLib.Error as exc:
            logger.info("script not saved: %s", exc.message)
            return
        try:
            file.replace_contents(self.code.encode("utf-8"), None, False,
                                  Gio.FileCreateFlags.REPLACE_DESTINATION, None)
        except GLib.Error as exc:
            logger.info("script could not be written: %s", exc.message)

    def _on_run(self, _button: Gtk.Button) -> None:
        script = write_script(self.code, self.language)
        if script is None:
            return
        message = open_in_terminal(script, self.language)
        window = self.get_root()
        if isinstance(window, Gtk.Window):
            # The user is told what was opened and what to run. A control that
            # starts something without saying so is the failure this avoids.
            toast = getattr(window, "show_toast", None)
            if callable(toast):
                toast(message)
            else:
                logger.info("Run: %s", message)

    @staticmethod
    def _flash(button: Gtk.Button, icon: str, back: str) -> None:
        button.set_icon_name(icon)
        GLib.timeout_add(1200, lambda: (button.set_icon_name(back), GLib.SOURCE_REMOVE)[1])


class MathBlock(Gtk.Box):
    """An equation: rendered when that is possible, as written when it is not."""

    def __init__(self, block: Block) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.source = str(block.payload)
        # **`self.language` was read below and never set here.** `CodeBlock` sets
        # it from `block.language`; `MathBlock` did not, so the no-matplotlib
        # fallback - which is what every machine without matplotlib gets, and is
        # exactly the path this method exists to serve - raised
        # `AttributeError: 'MathBlock' object has no attribute 'language'` the
        # moment an equation was rendered.
        #
        # The test caught it and had been failing for as long as it had existed;
        # it was recorded as pre-existing rather than fixed. Empty for a math
        # block, since `Block("math", ...)` leaves `language` at its default -
        # and `code_hl.highlight` documents that an unrecognised language returns
        # the escaped source unchanged, which is precisely "the LaTeX, as
        # written" rather than half-right colouring.
        self.language = block.language or ""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        png = math_png(self.source)
        if png is not None:
            try:
                texture = Gdk.Texture.new_from_filename(str(png))
            except GLib.Error as exc:
                logger.debug("could not load the rendered equation: %s", exc.message)
                texture = None
            if texture is not None:
                picture = Gtk.Picture()
                picture.set_paintable(texture)
                picture.set_can_shrink(True)
                picture.add_css_class("reply-block")
                row.append(picture)
        else:
            # No matplotlib: the source, in monospace, exactly as written. A
            # box that says "equation unavailable" would be less useful than the
            # LaTeX, which a person can read.
            label = Gtk.Label()
            label.set_markup(
                f"<tt>{code_hl.highlight(self.source, self.language)}</tt>")
            label.add_css_class("reply-code")
            label.set_selectable(True)
            label.set_xalign(0.0)
            row.append(label)
        row.append(_flat_button("edit-copy-symbolic", "Copy this equation",
                                lambda b: _copy_to_clipboard(b, self.source)))
        self.append(row)


class TableBlock(Gtk.Box):
    """A pipe table as a real grid, with its alignment honoured and a CSV out."""

    def __init__(self, block: Block) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("reply-block")
        rows: List[List[str]] = list(block.payload)      # type: ignore[arg-type]
        self.rows = rows
        aligns: List[str] = block.meta.get("aligns") or ["left"] * len(rows[0])

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        title = Gtk.Label(label="Table")
        title.add_css_class("reply-block-heading")
        title.set_hexpand(True)
        title.set_xalign(0.0)
        header.append(title)
        header.append(_flat_button("edit-copy-symbolic", "Copy as TSV",
                                   self._on_copy))
        header.append(_flat_button("folder-download-symbolic", "Save as CSV",
                                   self._on_save))
        self.append(header)

        grid = Gtk.Grid(column_spacing=14, row_spacing=4)
        _XALIGN = {"left": 0.0, "center": 0.5, "right": 1.0}
        for column, cell in enumerate(rows[0]):
            label = Gtk.Label()
            label.set_markup(f"<b>{markdown_lite._inline(markdown_lite.escape(cell))}</b>")
            label.add_css_class("reply-table-cell")
            label.set_xalign(_XALIGN.get(aligns[column], 0.0))
            grid.attach(label, column, 0, 1, 1)
        for index, row in enumerate(rows[1:], start=1):
            for column, cell in enumerate(row):
                label = Gtk.Label(label=cell)
                label.add_css_class("reply-table-cell")
                label.set_selectable(True)
                label.set_xalign(_XALIGN.get(aligns[column], 0.0))
                grid.attach(label, column, index, 1, 1)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroller.set_child(grid)
        self.append(scroller)

    def as_tsv(self) -> str:
        return "\n".join("\t".join(row) for row in self.rows)

    def as_csv(self) -> str:
        import csv
        import io
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerows(self.rows)
        return buffer.getvalue()

    def _on_copy(self, button: Gtk.Button) -> None:
        _copy_to_clipboard(button, self.as_tsv())
        CodeBlock._flash(button, "object-select-symbolic", "edit-copy-symbolic")

    def _on_save(self, button: Gtk.Button) -> None:
        dialog = Gtk.FileDialog()
        dialog.save(self.get_root(), None, lambda _dlg, result: self._saved(dialog, result))

    def _saved(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            file = dialog.save_finish(result)
            file.replace_contents(self.as_csv().encode("utf-8"), None, False,
                                  Gio.FileCreateFlags.REPLACE_DESTINATION, None)
        except GLib.Error as exc:
            logger.info("table not saved: %s", exc.message)


#: Skills whose one `path` argument names the single file they rewrote, and
#: whose pre-image `undo_last_change.record_preimage` keeps. `find_and_replace`
#: is here for the case where `path` is a file; given a directory it can touch
#: many, and the card then offers the panel rather than guessing which.
#: `office_document` is not: its pre-image is binary, which the ring skips.
FILE_WRITING_TOOLS = frozenset({"edit_file", "write_text_file", "find_and_replace"})

#: Changed lines shown in the card before "Show full diff" takes over.
INLINE_DIFF_LINES = 12


def change_for(name: str, arguments: dict) -> "Optional[tuple]":
    """`(path, before, after)` for the file this call rewrote, or None.

    Read from the undo ring - the same store the Diff panel and `/undo` read -
    and the newest entry for the path, which is the state just before this
    call. None for anything not certainly one file: a card that shows the wrong
    file's diff is worse than one that shows none.
    """
    if name not in FILE_WRITING_TOOLS:
        return None
    raw = str((arguments or {}).get("path") or "").strip()
    if not raw:
        return None
    try:
        from pathlib import Path
        from shani_chronoa.skills.undo_last_change import recent_changes
        target = Path(raw).expanduser().resolve()
        if target.is_dir():
            return None
        found = None
        for path, before, after in recent_changes():
            if Path(path).expanduser().resolve() == target:
                found = (target, before, after)
        return found
    except Exception:  # noqa: BLE001 - a card must never take the turn down
        return None


#: Tool names that read wrongly as words on a card or the status clock.
_TOOL_WORDS = {"delegate": "subagent"}


def tool_words(name: str) -> str:
    """A tool's name as the card and the progress clock say it."""
    return _TOOL_WORDS.get(name, name.replace("_", " "))


def _short_arg(value) -> str:
    """An argument for the card's one-line summary; a path keeps its file name.

    The summary is clipped from the right at 60 characters, so an absolute path
    showed as `path=/tmp/claude-1001/-home-...` - every character of the
    directory and none of the file the card is about. Home becomes `~`, and a
    path still too long keeps its last two parts. The full arguments are one
    click away in the card's details.
    """
    text = str(value)
    if not text.startswith(("/", "~")) or "\n" in text:
        return text
    import os
    home = os.path.expanduser("~")
    if text == home or text.startswith(home + "/"):
        text = "~" + text[len(home):]
    if len(text) > 40:
        parts = text.rstrip("/").split("/")
        if len(parts) > 2:
            text = "…/" + "/".join(parts[-2:])
    return text


def _file(value) -> str:
    """A path as the name a person knows it by: its last part."""
    text = str(value or "").rstrip("/")
    return text.rsplit("/", 1)[-1] or text


def _quoted(value, limit: int = 40) -> str:
    text = " ".join(str(value or "").split())
    return "“" + (markdown_lite.clip(text, limit) if len(text) > limit else text) + "”"


_TODO_STATUS = {"completed": "done", "in_progress": "started", "blocked": "blocked",
                "pending": "to do", "complete": "done", "done": "done", "start": "started"}


def call_phrase(name: str, arguments: dict) -> str:
    """What this call is doing, in words, for the tool card's one line.

    **The card read like a log.** Measured in the 2026-10-15 coding take: six
    rows of `action=add, id=1, content=Fix pricing.py to treat the dis…` and
    `action=list, id=1` - the arguments as the model sent them, which is the
    one view of the agent's work a person is actually watching. The common
    tools get a sentence; every other tool keeps the `key=value` line, and the
    full arguments stay one click away in the card's details either way.
    """
    a = arguments or {}
    try:
        if name == "todo_list" and a:
            action = str(a.get("action") or "list").lower()
            step = f"step {a['id']}" if a.get("id") not in (None, "") else "a step"
            if action == "add":
                return "Planned: " + _quoted(a.get("content"))
            if action == "list":
                return "Checked the plan"
            if action == "clear":
                return "Cleared the plan"
            if action == "remove":
                return f"Removed {step}"
            status = _TODO_STATUS.get(str(a.get("status") or action).lower())
            return (f"Marked {step} {status}" if status else f"Updated {step}")
        if name == "edit_file" and a.get("path"):
            return "Changed " + _file(a.get("path"))
        if name == "write_text_file" and a.get("path"):
            return ("Added to " if a.get("append") else "Wrote ") + _file(a.get("path"))
        if name in ("read_document", "read_text_file") and a.get("path"):
            return "Read " + _file(a.get("path"))
        if name == "list_directory":
            return "Looked in " + (_file(a.get("path")) or "your home")
        if name == "run_command" and a.get("command"):
            where = str(a.get("where") or "sandbox")
            place = {"container": " in a container", "machine": " on a machine"}.get(where, "")
            return "Ran " + _quoted(a.get("command"), 44) + place
        if name == "search_file_contents" and a.get("text"):
            return "Searched for " + _quoted(a.get("text"))
        if name == "find_files" and a.get("pattern"):
            return "Looked for " + _quoted(a.get("pattern"))
        if name == "web_search" and a.get("query"):
            return "Searched the web for " + _quoted(a.get("query"))
        if name == "get_weather":
            return "Weather" + (" in " + str(a["place"]) if a.get("place") else "") \
                + (", " + str(a["when"]) if a.get("when") else "")
        if name == "set_timer" and a.get("seconds"):
            secs = int(float(a["seconds"]))
            span = f"{secs // 60} min" if secs >= 60 and secs % 60 == 0 else f"{secs} s"
            return f"Timer for {span}" + (" - " + str(a["label"]) if a.get("label") else "")
        if name == "open_application" and a.get("name"):
            return "Opened " + str(a["name"])
        if name == "set_volume" and a.get("percent") is not None:
            return f"Volume to {a['percent']}%"
        if name == "delegate" and a.get("task"):
            return "Handed off: " + _quoted(a.get("task"))
        if name == "git_commit" and a.get("message"):
            return "Committed " + _quoted(a.get("message"))
        if name == "disk_usage":
            return "Disk space" + (" on " + _short_arg(a["path"]) if a.get("path") else "")
    except (TypeError, ValueError, KeyError):
        pass  # an odd argument is not a broken card: fall back to the raw line
    return ", ".join(f"{key}={_short_arg(value)}" for key, value in a.items())


class InlineDiff(Gtk.Box):
    """The changed lines of one file write, in the chat, under its tool card.

    The Diff panel had the whole picture and the chat had `path=notes.txt`
    and "Edited". So the one moment a person is looking - right after asking
    for the change - showed nothing of it, and checking meant leaving the
    conversation for a panel. This is the first few changed lines, with the
    panel one click away for the rest.
    """

    def __init__(self, path, before: str, after: str,
                 on_show_full: Optional[Callable[[], None]] = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.add_css_class("inline-diff")
        from shani_chronoa.gui.surfaces.diff import diff_text
        changed = [line for line in diff_text(before, after) if line.tag in "+-"]
        added = sum(1 for line in changed if line.tag == "+")
        removed = len(changed) - added
        self.added, self.removed = added, removed

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name = Gtk.Label(label=getattr(path, "name", str(path)), xalign=0.0)
        name.add_css_class("heading")
        name.set_tooltip_text(str(path))
        # **The header's name was the one label here with no ellipsise**, and it
        # was the diff's whole width: `getattr(path, "name", str(path))` falls
        # back to the **entire path** for a `str`, and a header label lays out
        # in one line however long it is. Measured, a 74-character name asked
        # for **558px**, and the `InlineDiff` carrying it went from **117px to
        # 73px** once this was set - the ellipsise is what bounds it, and it is
        # also what it should have been: the tooltip one line above already
        # carries the whole path, so truncating here loses nothing a reader can
        # see and everything a row needs.
        #
        # A `set_max_width_chars` was tried here first and **measured inert** -
        # 558px with the bound, 15px with the ellipsise, 15px with both. The
        # bound describes how a *wrapping* label lays out; this one does not
        # wrap, so it never applied. Kept as a note because that is the trap.
        name.set_ellipsize(Pango.EllipsizeMode.END)
        head.append(name)
        plus = Gtk.Label(label=f"+{added}")
        plus.add_css_class("diff-count-add")
        head.append(plus)
        minus = Gtk.Label(label=f"\u2212{removed}")
        minus.add_css_class("diff-count-del")
        head.append(minus)
        spacer = Gtk.Box(hexpand=True)
        head.append(spacer)
        if on_show_full is not None:
            full = Gtk.Button(label="Show full diff")
            full.add_css_class("flat")
            full.set_tooltip_text("Open this change in the Diff panel")
            full.connect("clicked", lambda _b: on_show_full())
            head.append(full)
        self.append(head)

        lines = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        lines.add_css_class("inline-diff-lines")
        for line in changed[:INLINE_DIFF_LINES]:
            label = Gtk.Label(xalign=0.0)
            label.set_markup(f"<tt>{markdown_lite.escape(line.tag + ' ' + line.text[:200])}</tt>")
            label.set_selectable(True)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            # Own class names: the Diff panel installs app-wide `.diff-add` /
            # `.diff-del` rules with a pale fill, and under them these lines
            # rendered white on near-white - present and unreadable.
            label.add_css_class("inline-diff-add" if line.tag == "+" else "inline-diff-del")
            lines.append(label)
        if len(changed) > INLINE_DIFF_LINES:
            more = Gtk.Label(xalign=0.0,
                             label=f"{plural(len(changed) - INLINE_DIFF_LINES, 'more changed line')}")
            more.add_css_class("dim-label")
            lines.append(more)
        if not changed:
            same = Gtk.Label(xalign=0.0, label="No line changed")
            same.add_css_class("dim-label")
            lines.append(same)
        self.append(lines)


class ArtifactBlock(Gtk.Box):
    """A file a skill **declared** it produced, shown rather than described.

    **Every media skill in this app used to hand back a sentence** -
    `generate_image` ends with *"saved it to /home/you/pictures/cat.png"* - and
    the transcript rendered that sentence. A picture the assistant just made
    was a line of text with a path in it, and the only way to see the picture
    was to go and open a file manager.

    **What arrives here is the declared path, never one read out of the
    sentence.** `DispatchResult.artifacts` is filled by
    `tools.artifacts_for`, which asks the skill's own module what it wrote and
    drops anything not on disk. A reply containing a path cannot put a file on
    screen here, because a reply is not what gets read.

    Four controls, which is what `CodeBlock` already has for code: **open it**,
    **copy its path**, **save a copy somewhere else**, and for an image a
    thumbnail in the transcript itself.
    """

    #: What gets a thumbnail rather than a name. Everything else is listed by
    #: name, because a preview that silently fails is worse than a name.
    IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")

    def __init__(self, paths) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("reply-block")
        self._paths = [Path(p) for p in paths]
        #: Open previews, keyed by path, so a second click can close the first.
        self._previews: "dict[str, Gtk.Widget]" = {}

        heading = Gtk.Label(label="Made" if len(self._paths) == 1 else
                            f"Made {len(self._paths)} files")
        heading.add_css_class("reply-block-heading")
        heading.set_xalign(0.0)
        self.append(heading)

        for path in self._paths:
            self.append(self._row(path))

    def _row(self, path: Path) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        if path.suffix.lower() in self.IMAGE_SUFFIXES:
            thumb = self._thumbnail(path)
            if thumb is not None:
                row.append(thumb)

        name = Gtk.Label(label=path.name)
        name.set_hexpand(True)
        name.set_xalign(0.0)
        name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        name.set_tooltip_text(str(path))     # the full path, since the name is cut
        name.update_property([Gtk.AccessibleProperty.LABEL],
                             [f"{path.name}, at {path}"])
        row.append(name)

        row.append(_flat_button("document-open-symbolic", f"Open {path.name}",
                                self._on_open, path))
        row.append(_flat_button("edit-copy-symbolic", "Copy this file's path",
                                self._on_copy_path, path))
        # **No "save a copy" for a directory.** `_saved` reads the source with
        # `read_bytes()`, which on a folder raises `IsADirectoryError` - caught, as
        # an `OSError`, and logged, so the button would do nothing and say
        # nothing. Measured by rendering a real directory artifact rather than by
        # reading this. "Open" and "copy its path" are both meaningful for a
        # folder; a single-file copy of one is not, and offering it anyway is a
        # control that lies about what it can do.
        #
        # `is_dir()` rather than `not is_file()` on purpose: a file deleted
        # between the skill answering and the row being built keeps its button
        # and fails in the ordinary way, instead of quietly losing it.
        if not path.is_dir():
            row.append(_flat_button("folder-download-symbolic",
                                    "Save a copy elsewhere", self._on_save, path))
        # Preview is offered only where there is something to show. "Open" hands
        # the file to the desktop, which for a picture means leaving the app and
        # coming back to a conversation you have lost your place in.
        if self._preview_kind(path) is not None:
            button = _flat_button("view-more-symbolic",
                                  f"Preview {path.name}", self._on_preview, path)
            row.append(button)
        return row

    #: Suffixes a preview can honestly show. Anything else gets no control rather
    #: than a viewer that opens something unexpected.
    TEXT_SUFFIXES = (".md", ".txt", ".html", ".htm", ".json", ".csv", ".log",
                     ".toml", ".ini", ".cfg", ".yaml", ".yml", ".xml", ".py",
                     ".sh", ".js", ".ts", ".css", ".sql", ".desktop")

    def _preview_kind(self, path: Path):
        """``"image"``, ``"text"``, or None when there is nothing to preview."""
        try:
            if path.is_dir():
                return None
            suffix = path.suffix.lower()
        except OSError:
            return None
        if suffix in self.IMAGE_SUFFIXES:
            return "image"
        if suffix in self.TEXT_SUFFIXES:
            return "text"
        return None

    def _on_preview(self, button: Gtk.Button, path: Path) -> None:
        """Show it here, or hide it again.

        **The preview lives inside the row's own card**, so it expands in place
        in the conversation rather than opening a window over it. Toggling
        rather than opening is what keeps the reader's place: an "open" that
        leaves the app is the thing this is avoiding.
        """
        existing = getattr(self, "_previews", {}).get(str(path))
        if existing is not None:
            parent = existing.get_parent()
            if parent is not None:
                parent.remove(existing)
            self._previews.pop(str(path), None)
            button.set_icon_name("view-more-symbolic")
            button.set_tooltip_text(f"Preview {path.name}")
            return
        widget = self._build_preview(path)
        if widget is None:
            button.set_sensitive(False)
            return
        self._previews[str(path)] = widget
        self.append(widget)
        button.set_icon_name("view-conceal-symbolic")
        button.set_tooltip_text(f"Hide the preview of {path.name}")

    def _build_preview(self, path: Path):
        kind = self._preview_kind(path)
        if kind == "image":
            return self._image_preview(path)
        if kind == "text":
            return self._text_preview(path)
        return None

    #: How much of a text file to show, in lines. A 40,000-line log in the
    #: transcript is a denial of service on the scroll position, and the point
    #: of a preview is to recognise the file, not to read it here.
    PREVIEW_LINES = 200

    def _image_preview(self, path: Path):
        """The picture, large enough to actually look at.

        **Sized from the file's own dimensions, capped at 480px.** A thumbnail
        scaled to fit a 96px box cannot answer "is this the right picture"; this
        can. And a genuinely enormous image is still capped, because the cost
        here is the decoded surface, not the width on screen.
        """
        try:
            from gi.repository import GdkPixbuf
            info = GdkPixbuf.Pixbuf.get_file_info(str(path))
            if info is None:
                return None
            width, height = info[1], info[2]
            longest = max(width, height) or 1
            scale = min(480.0 / longest, 1.0)
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(path), max(int(width * scale), 1), max(int(height * scale), 1),
                True)
        except Exception as exc:                                   # noqa: BLE001
            logger.info("no preview for %s: %s", path.name, exc)
            return None
        frame = Gtk.Frame()
        frame.add_css_class("artifact-preview")
        image = Gtk.Image.new_from_paintable(Gdk.Texture.new_for_pixbuf(pixbuf))
        image.set_halign(Gtk.Align.START)
        frame.set_child(image)
        frame.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [f"Preview of {path.name}, {width} by {height} pixels"])
        return frame

    def _text_preview(self, path: Path):
        """The first `PREVIEW_LINES` lines, and a truthful note when cut."""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                lines = []
                for index, line in enumerate(handle):
                    if index >= self.PREVIEW_LINES:
                        lines.append(f"... cut off at {self.PREVIEW_LINES} lines. "
                                     f"Open the file to read the rest.")
                        break
                    lines.append(line.rstrip("\n"))
        except OSError as exc:
            logger.info("no preview for %s: %s", path.name, exc)
            return None
        if not lines:
            return None
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_max_content_height(280)
        scroller.set_propagate_natural_height(True)
        # Monospace and selectable, because the reason to preview a file in a
        # conversation is to copy something out of it.
        view = Gtk.TextView()
        view.set_editable(False)
        view.set_monospace(True)
        view.set_wrap_mode(Gtk.WrapMode.NONE)
        view.get_buffer().set_text("\n".join(lines))
        frame = Gtk.Frame()
        frame.add_css_class("artifact-preview")
        frame.set_child(scroller)
        scroller.set_child(view)
        frame.update_property([Gtk.AccessibleProperty.LABEL],
                              [f"Preview of {path.name}"])
        return frame

    def _thumbnail(self, path: Path):
        """A scaled preview, or nothing at all.

        **No placeholder on failure.** An unrenderable image gets its name and
        its three controls, which is what a person can act on; a grey box
        standing in for a picture reads as "this is the picture" and is not.
        """
        try:
            from gi.repository import GdkPixbuf
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(path), 96, 96, True)
            # **A Pixbuf is not a `Gdk.Paintable` in GTK 4** - measured, as a
            # `TypeError: argument paintable: Expected Gdk.Paintable, but got
            # ...Pixbuf`. The texture wrapper is what implements it.
            #
            # This is worth recording for the *shape* of the failure rather than
            # the API: the broad `except` below did exactly what it is written
            # to do - no placeholder, a log line - and in doing so hid a real
            # defect for as long as nobody turned logging on. The same
            # `except Exception: pass` this file records hiding an empty
            # vision list, reached from the opposite direction: a correct
            # fallback is also a way of not noticing that the primary path is
            # broken.
            # **`new_for_pixbuf` is deprecated and kept deliberately.** The two
            # replacements do not work through this PyGObject, both measured:
            # `pixbuf.read_pixels()` returns an **int** - the raw pointer, which
            # cannot be marshalled into bytes - and
            # `Gdk.Texture.new_from_bytes()` is bound with **one** argument
            # against a five-argument C function, so there is no way to pass the
            # width, height and stride it needs. `new_from_resource` needs a
            # compiled GResource and this repository has none.
            #
            # So this is a deprecation warning on a thumbnail, not a defect, and
            # it is written down rather than worked around, because a future
            # pass would otherwise re-derive all three dead ends.
            return Gtk.Image.new_from_paintable(Gdk.Texture.new_for_pixbuf(pixbuf))
        except Exception:                                   # noqa: BLE001
            logger.info("no thumbnail for %s", path, exc_info=True)
            return None

    def _on_open(self, button: Gtk.Button, path: Path) -> None:
        """Open with whatever the desktop uses for this kind of file."""
        try:
            Gtk.show_uri(button.get_root(), path.as_uri(),
                         Gdk.CURRENT_TIME)
        except Exception:                                   # noqa: BLE001
            logger.warning("could not open %s", path, exc_info=True)

    def _on_copy_path(self, button: Gtk.Button, path: Path) -> None:
        _copy_to_clipboard(button, str(path))
        CodeBlock._flash(button, "edit-copy-symbolic", "edit-copy-symbolic")

    def _on_save(self, button: Gtk.Button, path: Path) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_initial_name(path.name)
        dialog.save(button.get_root(), None,
                    lambda dlg, result: self._saved(dlg, result, path))

    def _saved(self, dialog: Gtk.FileDialog, result, source: Path) -> None:
        try:
            target = dialog.save_finish(result)
        except GLib.Error as exc:
            logger.info("artifact not saved: %s", exc.message)
            return
        try:
            target.replace_contents(source.read_bytes(), None, False,
                                    Gio.FileCreateFlags.REPLACE_DESTINATION, None)
        except (GLib.Error, OSError) as exc:
            logger.warning("could not save a copy of %s: %s", source, exc)


def _why_block(selection, name: str) -> Optional[Gtk.Box]:
    """The "why this tool" block, built from the record\'s own sentences.

    **Every word here comes from `Selection.why`.** The sentences are the part
    that can mislead - a number that does not match the decision, a line
    claiming something the turn did not do - and they are built and tested in
    `tool_select` with no display attached. This function only puts lines in a
    box; a sentence written here would be a sentence nothing can check without
    a window, which is the state this file has twice found a panel green and
    wrong.

    **`selection.why` is called, not sniffed for.** A stand-in that does not
    implement it is a test that should be corrected, not defended against here:
    a guard that exists so a fake can pass is a guard that stops saying what the
    product does.
    """
    lines = selection.why(name)
    if not lines:
        return None
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    heading = Gtk.Label(label="Why this tool")
    heading.set_xalign(0.0)
    box.append(heading)
    for line in lines:
        one = Gtk.Label(label=line)
        one.add_css_class("dim-label")
        # Wrapped on a character boundary: a folded revealer still lends the
        # card its child\'s minimum width, so one long path here pushes the
        # whole card off the right of the column - which is the same reason the
        # argument grid below wraps.
        one.set_wrap(True)
        one.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        one.set_xalign(0.0)
        box.append(one)
    return box


class ToolCallCard(Gtk.Box):
    """What a skill did, with the arguments it was given and what came back.

    Chronoa used to show a status line that said "web_search..." and then
    nothing: the user could see that something ran, never what was asked for or
    what came back, which is the information that makes a surprising action
    explicable afterwards. This is that, and it is collapsible so a turn with six
    tool calls does not bury the answer.
    """

    def __init__(self, name: str, arguments: Optional[dict] = None,
                 result: str = "", ok: bool = True, on_open: Optional[Callable] = None,
                 artifacts: tuple = (),
                 on_show_diff: Optional[Callable[[], None]] = None,
                 selection=None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("tool-card")
        self._on_show_diff = on_show_diff
        self.inline_diff: Optional[InlineDiff] = None
        self.name = name
        self.arguments = dict(arguments or {})
        self.result = result
        self.ok = ok
        #: What the turn offered and why, from the round that made this call.
        #: **Passed in, not looked up.** A transcript holds cards for every turn
        #: at once, so "the selection in force" would answer about the wrong one
        #: - and the card for a tool called in round 1 would be explained by
        #: round 3\'s offer set, which is a different question that happens to
        #: have a confident answer. `None` renders no block at all.
        self.selection = selection

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        icon = Gtk.Image.new_from_icon_name(
            "object-select-symbolic" if ok else "dialog-warning-symbolic")
        row.append(icon)
        # **Whether it worked was an icon and nothing else.** A screen reader
        # reached this row as "file write, path=/tmp/report.pdf" with no word
        # saying whether it happened - measured: the card set no accessible
        # label on itself, only its toggle and the copy button have one.
        #
        # And an icon is not enough for a sighted reader either, because the two
        # are the same size, the same weight and sit in the same place, so
        # whether a tool call *failed* is a distinction you have to already know
        # how to read. That is the claim-without-a-mechanism shape this
        # repository keeps finding, in a place nobody had looked: two dead
        # controls and a lying chip were all worse than this, and this one was
        # hiding in plain sight on every tool call the assistant has ever made.
        #
        # Both icons do ship in Adwaita and Yaru, checked rather than assumed -
        # the same check that found `audio-input-microphone-high-symbolic` present
        # only in Yaru.
        icon.set_tooltip_text("This worked" if ok else "This did not work")
        row.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [f"{tool_words(name)}: "
             + ("this worked" if ok else "this did not work")])
        title = Gtk.Label(label=tool_words(name))
        title.add_css_class("heading")
        title.set_hexpand(True)
        title.set_xalign(0.0)
        row.append(title)
        if not ok:
            # **Visible, because an icon is not a word.** Both icons ship in
            # Adwaita and Yaru, so this is not about a missing glyph - it is
            # that the only difference between a tool call that wrote the file
            # and one that silently did not is a symbol a person has to already
            # know how to read. `verification.Verdict` may know more; this says
            # the one thing a person can act on.
            outcome = Gtk.Label(label="did not work")
            outcome.add_css_class("dim-label")
            outcome.set_tooltip_text(
                "Chronoa says this call did not succeed. Its answer below "
                "explains why, if it did.")
            row.append(outcome)
        summary = call_phrase(name, self.arguments)
        if len(summary) > 60:
            # **`markdown_lite.clip`, not a hard slice.** The arguments are
            # model-supplied text, and `[:57]` cuts a grapheme cluster: an emoji
            # argument is seven ZWJ-joined code points and the slice leaves a
            # stranded ZWJ, which draws the missing-glyph box. The ellipsis is
            # inside the width, so the hint never grows past what it was.
            summary = markdown_lite.clip(summary, 60)
        if summary:
            hint = Gtk.Label(label=summary)
            hint.add_css_class("dim-label")
            hint.set_ellipsize(Pango.EllipsizeMode.END)
            row.append(hint)

        toggle = Gtk.ToggleButton()
        toggle.set_icon_name("view-more-symbolic")
        toggle.set_tooltip_text("Show what this ran, and what came back")
        toggle.update_property([Gtk.AccessibleProperty.LABEL],
                               ["Show the details of " + name])
        row.append(toggle)
        self.append(row)

        # **What this call actually produced, shown.** Before this, a picture
        # the assistant had just made was a sentence ending "saved it to
        # /home/you/pictures/cat.png", and the only way to see the picture was
        # to leave the app and open a file manager.
        #
        # It is rendered below the header and **above** the detail toggle,
        # because the toggle is for the parts you want on demand and this is
        # the part the turn was *for*.
        if artifacts:
            self.append(ArtifactBlock(artifacts))

        self.revealer = Gtk.Revealer()
        self.revealer.set_reveal_child(False)
        toggle.bind_property("active", self.revealer, "reveal-child",
                             2 | 1)          # BIDIRECTIONAL | SYNC_CREATE

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        if selection is not None:
            # Above the arguments, because "why this tool" is the context the
            # arguments are read in - and a person checking whether a tool was
            # the right one should not have to scroll past the payload to find
            # out what it was chosen for.
            why = _why_block(selection, name)
            if why is not None:
                inner.append(why)
        grid = Gtk.Grid(column_spacing=10, row_spacing=2)
        for index, (key, value) in enumerate(self.arguments.items()):
            key_label = Gtk.Label(label=key)
            key_label.add_css_class("dim-label")
            grid.attach(key_label, 0, index, 1, 1)
            value_label = Gtk.Label(label=str(value))
            value_label.set_selectable(True)
            value_label.set_xalign(0.0)
            # Wrapped, including inside a word: a folded revealer still lends
            # the card its child's minimum width, so one long path here pushed
            # the whole card - and its buttons - off the right of the column.
            value_label.set_wrap(True)
            value_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            value_label.set_hexpand(True)
            grid.attach(value_label, 1, index, 1, 1)
        inner.append(grid)
        if on_open is not None:
            open_button = Gtk.Button(label="Open")
            open_button.add_css_class("flat")
            open_button.connect("clicked", lambda _b: on_open(name, self.arguments, result))
            inner.append(open_button)
        self.revealer.set_child(inner)
        self.append(self.revealer)
        self._output: Optional[Gtk.Label] = None
        self._scroller: Optional[Gtk.ScrolledWindow] = None
        self._icon = icon
        if result:
            self.update(result, ok)

    def update(self, result: str, ok: bool = True) -> None:
        """Fill in what came back, without disturbing whether it is open.

        The card is built when the call *starts* - the user needs to see that
        something is running and with what arguments - and the result lands
        later, so it has to be addable afterwards. Replacing the card instead
        would close it again, which is the opposite of what a user who opened it
        during the run wants.
        """
        self.result = result
        self.ok = ok
        self._icon.set_from_icon_name(
            "object-select-symbolic" if ok else "dialog-warning-symbolic")
        # The stripe, not just the icon: colour is reinforcement here, but a
        # 16px icon is a small target in a row of 14px text.
        if ok:
            self.remove_css_class("failed")
        else:
            self.add_css_class("failed")
        if not result:
            return
        self._show_change()
        if self._output is None:
            output = Gtk.Label()
            output.add_css_class("tool-card-body")
            output.set_xalign(0.0)
            output.set_selectable(True)
            scroller = Gtk.ScrolledWindow()
            scroller.set_max_content_height(200)
            scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scroller.set_child(output)
            inner = self.revealer.get_child()
            inner.append(scroller)
            self._output, self._scroller = output, scroller
        self._output.set_markup(f"<tt>{markdown_lite.escape(result[:4000])}</tt>")


    def _show_change(self) -> None:
        """Put the file's changed lines under the card - outside the revealer.

        Outside, because it is the part worth seeing without asking: the
        arguments and raw result stay folded, the change does not. Built once;
        a second result for the same call does not stack a second diff.
        """
        if not self.ok or self.inline_diff is not None:
            return
        change = change_for(self.name, self.arguments)
        if change is None:
            if self.name in FILE_WRITING_TOOLS and self._on_show_diff is not None \
                    and not getattr(self, "_panel_link", None):
                # Several files, or one the ring cannot diff: no guessed
                # preview, but the panel that has them is still one click away.
                link = Gtk.Button(label="Show what changed")
                link.add_css_class("flat")
                link.set_halign(Gtk.Align.START)
                link.connect("clicked", lambda _b: self._on_show_diff())
                self._panel_link = link
                self.append(link)
            return
        self.inline_diff = InlineDiff(*change, on_show_full=self._on_show_diff)
        self.append(self.inline_diff)


def widgets_for(text: str, on_tool_open: Optional[Callable] = None) -> List[Gtk.Widget]:
    """A reply -> the widgets to stack under the turn.

    A user turn is not rendered through here: what someone typed is what they
    wrote, and re-rendering it would change what they see into something else.
    """
    out: List[Gtk.Widget] = []
    for block in parse(text):
        if block.kind == "code":
            out.append(CodeBlock(block))
        elif block.kind == "math":
            out.append(MathBlock(block))
        elif block.kind == "table":
            out.append(TableBlock(block))
        elif block.kind == "rule":
            separator = Gtk.Separator()
            separator.set_margin_top(6)
            separator.set_margin_bottom(6)
            out.append(separator)
        elif block.kind == "heading":
            size = int(block.language or 1)
            sizes = {1: "title-2", 2: "title-3", 3: "title-4"}
            label = Gtk.Label()
            label.set_markup(f"<b>{markdown_lite._inline(markdown_lite.escape(str(block.payload)))}</b>")
            label.add_css_class(sizes.get(size, "heading"))
            label.set_wrap(True)
            label.set_xalign(0.0)
            out.append(label)
        else:
            label = Gtk.Label()
            label.set_markup(markdown_lite.to_pango(str(block.payload)))
            label.set_wrap(True)
            label.set_selectable(True)
            label.set_xalign(0.0)
            label.add_css_class("transcript-turn")
            # **A link that renders clickable but does nothing when clicked is
            # the dead-control defect**, and the markup alone produces exactly
            # that: Pango draws the underline and GTK opens nothing on its own.
            # This handler is what makes the `<a>` real.
            label.connect("activate-link", _open_uri)
            label.update_property([Gtk.AccessibleProperty.LABEL],
                                  ["Chronoa said: " + str(block.payload)])
            out.append(label)
    return out


def link_target(uri: str) -> "tuple[Optional[str], str]":
    """(url to open, why-not) for a link a person clicked. No side effects.

    **Split out of `_open_uri` so the decision can be tested without opening
    anything.** The first version put the check and the launch in one function,
    and the only way to exercise it was to emit `activate-link` on a real label —
    which ran the real launcher and **opened the person's browser**. A handler
    that cannot be tested without causing the thing it does is a handler nobody
    should be testing by hand.
    """
    safe = markdown_lite.safe_url(uri)
    if safe is None:
        return None, f"a link outside the http/https allowlist was refused: {uri!r}"
    try:
        from shani_chronoa import egress
        egress.record("gui-link", safe, purpose="user-clicked-link")
    except Exception:  # noqa: BLE001 - the ledger must never block the click
        logger.debug("could not record the clicked link", exc_info=True)
    return safe, ""


def _app_for(widget: Gtk.Widget):
    """The application a widget belongs to, or None.

    Walks to the root window and asks it. Returns None rather than raising,
    because a reply label also exists outside a window — a test, or a widget
    built for a screenshot — and a link in one of those has no app to open a
    browser in.
    """
    try:
        root = widget.get_root()
    except Exception:  # noqa: BLE001 - a detached widget has no root
        return None
    if root is None:
        return None
    try:
        return root.get_application()
    except Exception:  # noqa: BLE001 - not every root is an application window
        return None


def show_in_own_browser(widget: Gtk.Widget, url: str) -> bool:
    """Open `url` in Chronoa's own browser window. False when there is none.

    **A clicked link belongs in the app's browser, not the system's.** Chronoa
    ships one (`gui/browser.py`, the window `browse` drives), and sending a
    reply's links to a separate application would move the person out of the
    conversation they are reading — and out of the egress checks, the activity
    strip and the ability to stop it, all of which that window has.

    False means "could not", never "would not": WebKitGTK is an optional
    dependency, and on a machine without it the caller falls back to the system
    handler rather than dropping the click.
    """
    app = _app_for(widget)
    if app is None:
        return False
    try:
        from shani_chronoa.gui import browser as browser_mod
        if not browser_mod.is_available():
            return False
        ensure = getattr(app, "_ensure_browser_window", None)
        if ensure is None:
            return False
        window = ensure()
        if window is None:
            return False
        window.load_url(url)
        window.present()
        return True
    except Exception as exc:  # noqa: BLE001 - a browser must not eat the click
        logger.warning("could not open %s in Chronoa's browser: %s", url, exc)
        return False


def _open_uri(label: Gtk.Label, uri: str) -> bool:
    """A clicked link in a reply -> Chronoa's browser, or the system's.

    Returns True so GTK considers the link handled. The decision is
    `link_target`'s and the launch is `show_in_own_browser`'s, so both are
    testable without a browser appearing.

    `launch_default_for_uri_async` rather than the synchronous form: measured,
    the synchronous call blocks on this box, and a handler that runs on the GTK
    main loop cannot afford to wait for a browser to spawn.
    """
    safe, _why = link_target(uri)
    if safe is None:
        logger.warning("refusing to open a clicked link: %s", uri)
        return True
    if show_in_own_browser(label, safe):
        return True

    def done(_source, result) -> None:
        try:
            ok = Gio.AppInfo.launch_default_for_uri_finish(result)
        except Exception as exc:  # noqa: BLE001 - reported, not raised into GTK
            logger.warning("could not open %s: %s", safe, exc)
            return
        if not ok:
            logger.info("nothing on this machine handles %s", safe)

    try:
        Gio.AppInfo.launch_default_for_uri_async(safe, None, None, done)
    except Exception as exc:  # noqa: BLE001 - reported, not raised into GTK
        logger.warning("could not start opening %s: %s", safe, exc)
    return True