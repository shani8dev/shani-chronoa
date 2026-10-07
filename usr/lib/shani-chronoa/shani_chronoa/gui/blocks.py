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

from gi.repository import Gdk, Gio, GLib, Gtk  # type: ignore

from shani_chronoa import markdown_lite

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
        body.set_markup(f"<tt>{markdown_lite.escape(self.code)}</tt>")
        body.add_css_class("reply-code")
        body.set_selectable(True)
        body.set_xalign(0.0)
        scroller.set_child(body)
        self.append(scroller)

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
            label.set_markup(f"<tt>{markdown_lite.escape(self.source)}</tt>")
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


class ToolCallCard(Gtk.Box):
    """What a skill did, with the arguments it was given and what came back.

    Chronoa used to show a status line that said "web_search..." and then
    nothing: the user could see that something ran, never what was asked for or
    what came back, which is the information that makes a surprising action
    explicable afterwards. This is that, and it is collapsible so a turn with six
    tool calls does not bury the answer.
    """

    def __init__(self, name: str, arguments: Optional[dict] = None,
                 result: str = "", ok: bool = True, on_open: Optional[Callable] = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("tool-card")
        self.name = name
        self.arguments = dict(arguments or {})
        self.result = result
        self.ok = ok

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        icon = Gtk.Image.new_from_icon_name(
            "emblem-ok-symbolic" if ok else "dialog-warning-symbolic")
        row.append(icon)
        title = Gtk.Label(label=name.replace("_", " "))
        title.add_css_class("heading")
        title.set_hexpand(True)
        title.set_xalign(0.0)
        row.append(title)
        summary = ", ".join(f"{key}={value}" for key, value in self.arguments.items())
        if len(summary) > 60:
            summary = summary[:57] + "..."
        if summary:
            hint = Gtk.Label(label=summary)
            hint.add_css_class("dim-label")
            hint.set_ellipsize(3)
            row.append(hint)

        toggle = Gtk.ToggleButton()
        toggle.set_icon_name("view-more-symbolic")
        toggle.set_tooltip_text("Show what this ran, and what came back")
        toggle.update_property([Gtk.AccessibleProperty.LABEL],
                               ["Show the details of " + name])
        row.append(toggle)
        self.append(row)

        self.revealer = Gtk.Revealer()
        self.revealer.set_reveal_child(False)
        toggle.bind_property("active", self.revealer, "reveal-child",
                             2 | 1)          # BIDIRECTIONAL | SYNC_CREATE

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        grid = Gtk.Grid(column_spacing=10, row_spacing=2)
        for index, (key, value) in enumerate(self.arguments.items()):
            key_label = Gtk.Label(label=key)
            key_label.add_css_class("dim-label")
            grid.attach(key_label, 0, index, 1, 1)
            value_label = Gtk.Label(label=str(value))
            value_label.set_selectable(True)
            value_label.set_xalign(0.0)
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
            "emblem-ok-symbolic" if ok else "dialog-warning-symbolic")
        # The stripe, not just the icon: colour is reinforcement here, but a
        # 16px icon is a small target in a row of 14px text.
        if ok:
            self.remove_css_class("failed")
        else:
            self.add_css_class("failed")
        if not result:
            return
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
            label.update_property([Gtk.AccessibleProperty.LABEL],
                                  ["Chronoa said: " + str(block.payload)])
            out.append(label)
    return out