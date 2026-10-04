"""A deliberately small markdown subset, for reply text only.

An assistant's answer arrives as plain text with light formatting in it, and
putting that in a `Gtk.Label` verbatim shows the user `**38G**` and `- / is 32%
full` - which is the single most visibly unfinished thing about a chat window.

**Everything is escaped before any markup is added.** `&`, `<` and `>` are
turned into entities first and only then are a fixed set of tags introduced, so
the output cannot contain a tag the caller did not ask for. Reply text comes
from a local model and can contain anything, including a literal `<b>`, and an
unescaped `set_markup` on model output is an injection point.

Supported, and nothing else: `**bold**`, `*italic*`/`_italic_`, `` `code` ``,
fenced code blocks, `-`/`*`/`+` bullet lists, and pipe tables. Not headings,
links, images or blockquotes. A renderer that half-implements CommonMark is
harder to reason about than one that documents its own twelve rules; if more is
needed that is a dependency decision, not something to grow here.

A table is rendered as an aligned monospace block, not as a `Gtk.Grid`: a
`Gtk.Label` is one widget and the transcript streams reply text into it, so
fixed-width text is what makes columns line up. Two honest limits follow from
that choice. Inline markers inside a cell are shown literally (`**` stays
`**`), because bold in one cell of a fixed-width column breaks the alignment it
exists to provide; and a table wider than the window wraps rather than
scrolling, because the label wraps. Escaping still happens before the cells
are emitted, so the model cannot introduce a tag through a table either - that
is why this is in this module at all rather than in the widget.

**The padding is `U+00A0`, not `#x20`, and both of the obvious reasons for that
were checked on this machine's Pango rather than assumed.**

- `xml:space="preserve"` is the answer everyone reaches for and Pango does not
  have it: `set_markup` rejects the attribute on `<span>` *and* on `<tt>`
  ("Attribute 'xml:space' is not allowed on the <span> tag"). A rejected markup
  string does not leave the label empty either - `get_text()` then returns the
  raw `<span xml:space="preserve"><tt>Mount...` text, which the user reads as
  the assistant's answer. So the obvious fix is worse than no fix, and a test
  that only inspected this module's output would have shipped it.
- Pango does *not* collapse runs of `#x20` in markup (measured: `<tt>a   b</tt>`
  lays out and reads back with all three spaces), so "spaces get collapsed" -
  the usual reason for reaching for `xml:space` - is not what is happening here.

What a no-break space does buy is wrapping. The transcript's label wraps, and a
wrapped row breaks at any space it can find - which in a padded table is the
middle of a column gap, so a figure lands under the wrong heading. Measured on
a 240px wrapping label, the same four-row table laid out as **13** line boxes
padded with `#x20` and **7** padded with `U+00A0`, because a no-break space
offers no break opportunity. It is a reduction, not a fix: a table wider than
the window still breaks somewhere, and Pango will break mid-word to do it.

Links are the interesting non-case: `[text](url)` is left as literal text
rather than turned into an anchor, because a clickable link whose target came
from a model is a prompt-injection route to somewhere the user did not intend.
"""

from __future__ import annotations

import re
import unicodedata
from typing import List, Optional, Tuple

_ESCAPES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"))


def escape(text: str) -> str:
    """Escape the three characters Pango markup treats specially."""
    for char, entity in _ESCAPES:
        text = text.replace(char, entity)
    return text


# Fenced blocks are pulled out first and held aside, so their contents are never
# scanned for inline markers - code containing `**` is asterisks, not bold.
_FENCE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)

_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_STAR = re.compile(r"\*([^*\n]+)\*")
_ITALIC_UNDER = re.compile(r"(?<![\w_])_([^_\n]+)_(?![\w_])")
_CODE = re.compile(r"`([^`\n]+)`")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")


def _inline(text: str) -> str:
    """Apply the inline markers to already-escaped text."""
    text = _CODE.sub(lambda m: f"<tt>{m.group(1)}</tt>", text)
    text = _BOLD.sub(lambda m: f"<b>{m.group(1)}</b>", text)
    text = _ITALIC_STAR.sub(lambda m: f"<i>{m.group(1)}</i>", text)
    text = _ITALIC_UNDER.sub(lambda m: f"<i>{m.group(1)}</i>", text)
    return text


# A pipe table: a header row, then a row of dashes, then the body. Both are
# required, and both must contain a pipe - `---` on its own is a horizontal rule,
# which `to_speech` drops and this leaves alone, not an empty table.
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
#: How many columns a table may have before it is left as the text it arrived
#: as. A model that emits one-cell-per-line junk should not be turned into a
#: wide monospace slab; the fallback is the model's own text, unchanged.
_MAX_TABLE_COLUMNS = 12
#: Same, for rows - a 900-row "table" is a data dump, and reading it as prose is
#: no worse than rendering a widget the user has to scroll for a minute.
_MAX_TABLE_ROWS = 60


def _display_width(text: str) -> int:
    """Columns a monospace cell takes up.

    `len()` is wrong for anything outside ASCII: a CJK character is one Python
    character and two terminal columns, so a table of file names in Japanese
    would drift out of alignment on the first full-width character.
    """
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _cells(line: str) -> List[str]:
    """One pipe-table row -> its cells, with the outer pipes and padding gone."""
    trimmed = line.strip()
    if trimmed.startswith("|"):
        trimmed = trimmed[1:]
    if trimmed.endswith("|") and not trimmed.endswith("\\|"):
        trimmed = trimmed[:-1]
    return [cell.strip() for cell in trimmed.split("|")]


def _alignments(rule: str) -> List[str]:
    """`:---` left, `---:` right, `:---:` centre, `---` left, per column."""
    out = []
    for cell in _cells(rule):
        left, right = cell.startswith(":"), cell.endswith(":")
        out.append("center" if left and right else "right" if right else "left")
    return out


#: The padding character, and the column separator. Not `#x20` - see the module
#: docstring for what Pango does with a run of ordinary spaces.
_PAD = "\u00a0"


def _pad(cell: str, width: int, how: str) -> str:
    gap = _PAD * max(0, width - _display_width(cell))
    if how == "right":
        return gap + cell
    if how == "center":
        left = len(gap) // 2
        return " " * left + cell + " " * (len(gap) - left)
    return cell + gap


def _render_table(lines: List[str]) -> Optional[str]:
    """A pipe table -> aligned monospace text, or None if this is not one.

    None is the honest answer for anything malformed: a table this module cannot
    measure is left as the text the model wrote, which the caller already knows
    how to render.
    """
    if len(lines) < 2 or "|" not in lines[0] or not _TABLE_RULE.match(lines[1]) or "|" not in lines[1]:
        return None
    header = _cells(lines[0])
    aligns = _alignments(lines[1])
    body = [_cells(line) for line in lines[2:] if "|" in line]
    rows = [header] + body
    if not 2 <= len(header) <= _MAX_TABLE_COLUMNS or len(rows) > _MAX_TABLE_ROWS:
        return None
    if any(len(row) != len(header) for row in rows):
        # Ragged rows are what a model produces when it improvises; guessing which
        # column a short row meant would invent data.
        return None
    widths = [max(_display_width(row[column]) for row in rows) for column in range(len(header))]
    out = []
    for row in rows:
        cells = [_pad(row[column], widths[column], aligns[column] or "left")
                 for column in range(len(header))]
        out.append((_PAD * 2).join(cells).rstrip(_PAD))
    # A rule under the header is what makes a table legible once the pipes are
    # gone, and it is the only decoration this module ever adds.
    out.insert(1, (_PAD * 2).join("-" * width for width in widths))
    return "\n".join(out)


def _map_tables(text: str, render) -> str:
    """Rewrite every pipe table with `render`, leaving everything else alone.

    A run of lines that contain a pipe is only offered to `render`; whatever it
    returns None for is passed through as the model wrote it. Both the display
    and the spoken path go through here, so the two cannot disagree about which
    runs are tables.
    """
    lines = text.split("\n")
    out: List[str] = []
    index = 0
    while index < len(lines):
        block = []
        while index < len(lines) and "|" in lines[index]:
            block.append(lines[index])
            index += 1
        if not block:
            # Always consume a line: a reply with no pipe in it at all would
            # otherwise never reach the end of the list.
            out.append(lines[index])
            index += 1
            continue
        if len(block) >= 2:
            rendered = render(block)
            if rendered is not None:
                out.extend(rendered.split("\n"))
                continue
        out.extend(block)
    return "\n".join(out)


def _stash_tables(text: str, stash: "List[Tuple[str, str]]") -> str:
    """Pull every pipe table out of the text, replaced by a placeholder.

    Like fences, tables are held aside before anything else is scanned, so a
    `**` or a backtick inside a cell is never interpreted and the column
    measurement sees the model's own characters.
    """
    def render(block: List[str]) -> "Optional[str]":
        rendered = _render_table(block)
        if rendered is None:
            return None
        stash.append(("table", rendered))
        return f"\x00TABLE{len(stash) - 1}\x00"

    return _map_tables(text, render)


def to_pango(text: str) -> str:
    """Plain text with this module's markdown subset -> Pango markup.

    Returns markup, not a widget: the transformation is pure and testable on
    its own, which is the only way to be confident about the escaping.
    """
    if not text:
        return ""

    #: ("fence"|"table", rendered). Both are held aside before escaping and put
    #: back after, for the same reason: nothing inside them is markup.
    stash: "List[Tuple[str, str]]" = []

    def keep_fence(match: "re.Match[str]") -> str:
        stash.append(("fence", escape(match.group(2).rstrip("\n"))))
        return f"\x00FENCE{len(stash) - 1}\x00"

    working = _FENCE.sub(keep_fence, text)
    working = _stash_tables(working, stash)

    out_lines: List[str] = []
    for line in escape(working).split("\n"):
        bullet = _BULLET.match(line)
        if bullet:
            out_lines.append(f"•  {_inline(bullet.group(1))}")
        else:
            out_lines.append(_inline(line))

    body = "\n".join(out_lines)

    for index, (kind, rendered) in enumerate(stash):
        if kind == "fence":
            body = body.replace(f"\x00FENCE{index}\x00", f"<tt>{rendered}</tt>")
        else:
            # The rendered block is already escaped except for its own padding,
            # which is a character rather than markup - escaped here anyway so
            # that stays true by construction rather than by convention.
            body = body.replace(f"\x00TABLE{index}\x00", f"<tt>{escape(rendered)}</tt>")

    return body


#: Fence and code-span markers, with their contents preserved. Same subset as
#: `to_pango` handles, so the spoken and displayed forms cannot drift apart.
_SPOKEN_FENCE = re.compile(r"```(\w*)\n?(.*?)```", re.DOTALL)
_SPOKEN_CODE = re.compile(r"`([^`\n]+)`")
_SPOKEN_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_SPOKEN_ITALIC_STAR = re.compile(r"\*([^*\n]+)\*")
_SPOKEN_ITALIC_UNDER = re.compile(r"(?<![\w_])_([^_\n]+)_(?![\w_])")
_SPOKEN_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_SPOKEN_RULE = re.compile(r"^\s*(?:---+|\*\*\*+|___+)\s*$", re.MULTILINE)
_SPOKEN_HEADING = re.compile(r"^\s*#{1,6}\s+(.*)$", re.MULTILINE)
_SPOKEN_LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")

#: A bare web address, which a model writes unlinked as often as not. The
#: character before the scheme must not be alphanumeric, or `ahttp://x` and an
#: address inside a longer token both get mangled.
_BARE_URL = re.compile(r"(?<![A-Za-z0-9])https?://\S+")


def _say_link(match: "re.Match[str]") -> str:
    return "link"


def _say_table(lines: List[str]) -> "Optional[str]":
    """A pipe table -> rows a person can hear.

    "Disk | Size | Free" spoken as three columns of letters and a pause is worse
    than no table at all, so each row becomes its cells separated by commas and
    the rule row is dropped. The column names are still spoken, once, as the
    header row, which is the part a listener actually needs.

    A row is dropped if it has the wrong number of cells, for the same reason
    `to_pango` refuses to render one: a half-row read aloud is invented data.
    """
    if len(lines) < 2 or "|" not in lines[0] or not _TABLE_RULE.match(lines[1]) or "|" not in lines[1]:
        return None
    header = _cells(lines[0])
    spoken = [", ".join(cell for cell in header if cell)]
    for line in lines[2:]:
        if "|" not in line:
            continue
        row = _cells(line)
        if len(row) != len(header):
            continue
        spoken.append(", ".join(cell for cell in row if cell))
    return "\n".join(spoken)


def to_speech(text: str) -> str:
    """Plain text with this module's markdown subset -> text worth hearing.

    The window renders a reply through `to_pango`; the voice path used to hand a
    TTS engine the raw string, so every `**` and backtick was pronounced. Measured
    on this machine: `You have **3** updates` synthesised to 164,464 bytes of
    audio against 81,218 for the same sentence unformatted - exactly double, and
    the difference is the word "asterisk".

    This is the same reduction the display path performs, so the two cannot drift
    into disagreeing about what the reply said. Fence and code *contents* are
    kept: dropping them would make the spoken answer omit a path or a package
    name the user needs. Only the markers go.
    """
    if not text:
        return ""

    def flatten_fence(match: "re.Match[str]") -> str:
        # A one- or two-line block is a command or a path the person needs to
        # hear; anything longer is code read character by character for a
        # minute, so it is named instead and left on screen (assistd's
        # sentence.rs "Code block in {lang}" summary).
        body = match.group(2).strip("\n")
        if body.count("\n") < 2:
            return body
        return "There is a code block on screen."

    working = _SPOKEN_FENCE.sub(flatten_fence, text)
    # Before the rule strip: a table's own `|---|---|` row is what `_SPOKEN_RULE`
    # deletes, and a table reduced afterwards would have lost its shape.
    working = _map_tables(working, _say_table)
    working = _SPOKEN_RULE.sub("", working)
    working = _SPOKEN_HEADING.sub(r"\1", working)
    # A link's visible text is what the window shows; the target is not read out.
    # A URL spoken aloud is a wall of noise, and it may also be a model-supplied
    # destination, which is the injection route `to_pango` refuses for the same
    # reason.
    working = _SPOKEN_LINK.sub(r"\1", working)
    working = _BARE_URL.sub(_say_link, working)

    out_lines: List[str] = []
    for line in working.split("\n"):
        bullet = _SPOKEN_BULLET.match(line)
        out_lines.append(bullet.group(1) if bullet else line)

    body = "\n".join(out_lines)
    body = _SPOKEN_CODE.sub(r"\1", body)
    body = _SPOKEN_BOLD.sub(r"\1", body)
    body = _SPOKEN_ITALIC_STAR.sub(r"\1", body)
    body = _SPOKEN_ITALIC_UNDER.sub(r"\1", body)

    return "\n".join(line.rstrip() for line in body.split("\n") if line.strip()).strip()
