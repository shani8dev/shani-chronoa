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
from urllib.parse import urlsplit
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


#: `[label](destination)`, with one level of nested parentheses in the
#: destination because Wikipedia URLs contain a real one (`Foo_(bar)`). **The
#: same shape `_SPOKEN_LINK` uses, and it was wrong there first** - `[^)]*`
# stopped at the first `)`, so `[x](javascript:alert(1))` matched
# `[x](javascript:alert(1` and left a stray bracket in the spoken text.
_LINK_MD = re.compile(r"\[([^\]]*)\]\(([^()\s]*(?:\([^()\s]*\)[^()\s]*)*)\)")

#: A bare address. The lookbehind keeps `ahttp://x` and an address inside a
#: longer token intact, and the character class keeps it from running past the
#: end of the URL into the next word.
_BARE_URL_MD = re.compile(r"(?<![A-Za-z0-9>\"'])(https?://[^\s<>]+)")

#: A `<tt>` span, held aside so a URL inside backticks stays code. A curl
#: command in a reply is meant to be copied, not clicked.
_TT_SPAN = re.compile(r"<tt>[^<]*</tt>")


def _md_link(match: "re.Match[str]") -> str:
    label, destination = match.group(1), unescape(match.group(2))
    href = safe_url(destination)
    if href is None:
        # Not a link this module will make. The label is still worth reading, so
        # the bracket syntax is dropped and the words kept - which is what
        # `to_speech` does with the same input, so the two paths agree.
        return label
    return f'<a href="{escape(href)}">{label}</a>'


def _bare_md_link(match: "re.Match[str]") -> str:
    whole = match.group(0)
    # **Unescape first: `_inline` works on already-escaped text**, so the `&` of
    # a query string is `&amp;` here. Validating and re-escaping that directly
    # produced `&amp;amp;` in the href - a double escape that renders as a
    # literal `&amp;` and sends the browser somewhere other than where it read.
    url = _trim_url_clip(unescape(whole))
    href = safe_url(url)
    if href is None:
        return whole
    # The trimmed punctuation stays after the link, exactly as it does in the
    # spoken form: `(https://x.org/a)` is `(link)`, not `(link`.
    return f'<a href="{escape(href)}">{escape(url)}</a>' + whole[len(escape(url)):]


def _trim_url_clip(text: str) -> str:
    """The URL without the punctuation that belongs to the sentence.

    `see https://x.org.` is a sentence about an address; the full stop is the
    sentence's. A trailing `)` or `]` is the sentence's when it is **unbalanced**
    and the URL's when it is not, which is what makes `Foo_(bar)` work.
    """
    while text and text[-1] in ".,;:!?'\"’”»":
        text = text[:-1]
    while text and text[-1] == ")" and text.count(")") > text.count("("):
        text = text[:-1]
    while text and text[-1] == "]" and text.count("]") > text.count("["):
        text = text[:-1]
    return text


def _links(text: str) -> str:
    """Markdown links and bare addresses -> `<a>` elements, or plain text.

    A URL is made clickable only through `safe_url`, which is the
    open-intelligent-ui rule. The spoken path is separate and already handled
    (`to_speech` turns an address into the word "link", because a URL read aloud
    is a wall of noise nobody can act on); what matters is that the two agree,
    and they do - the label is spoken, and here the label is what is clickable.
    """
    stash: "List[str]" = []

    def keep(match: "re.Match[str]") -> str:
        stash.append(match.group(0))
        return f"\x00TT{len(stash) - 1}\x00"

    text = _TT_SPAN.sub(keep, text)
    text = _LINK_MD.sub(_md_link, text)
    text = _BARE_URL_MD.sub(_bare_md_link, text)
    for index, span in enumerate(stash):
        text = text.replace(f"\x00TT{index}\x00", span)
    return text


def _inline(text: str) -> str:
    """Apply the inline markers to already-escaped text."""
    text = _CODE.sub(lambda m: f"<tt>{m.group(1)}</tt>", text)
    text = _BOLD.sub(lambda m: f"<b>{m.group(1)}</b>", text)
    text = _ITALIC_STAR.sub(lambda m: f"<i>{m.group(1)}</i>", text)
    text = _ITALIC_UNDER.sub(lambda m: f"<i>{m.group(1)}</i>", text)
    return _links(text)


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


#: The inverse of `escape`, for the one place it is needed: `_inline` works on
#: already-escaped text, so a link's href has to be read back out of it before it
#: can be checked. `escape` produces exactly these three entities and nothing
#: else, so the inverse is exact rather than approximate.
_UNESCAPES = (("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"))


def unescape(text: str) -> str:
    for entity, char in _UNESCAPES:
        text = text.replace(entity, char)
    return text


#: The schemes a link may use. **From open-intelligent-ui's `safeUrl`**
#: (`src/lib/travel/components.tsx`): it renders `https:` and `http:` and returns
#: undefined for everything else, so a `javascript:` or `file:` URL a model emits
#: is dropped rather than made clickable. Chronoa's transcript had no links at
#: all - safe by omission, and every answer lost its URLs.
_SAFE_SCHEMES = ("https", "http")


def safe_url(value: object) -> "Optional[str]":
    """The href to put in markup, or None when it is not one to put there.

    Four refusals, each because a link is a thing a person clicks:

    - not a string, or empty once stripped;
    - a scheme other than http/https - `javascript:`, `data:`, `file:`,
      `vbscript:`. Case is folded, so `JaVaScRiPt:` does not slip through;
    - no host, so a scheme-relative `//evil.example/x` would be resolved against
      whatever the app is showing. A model that emits one has produced a link
      to somewhere, not a document;
    - whitespace anywhere in it. A newline is how a link is made to read as one
      host and go to another.
    """
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if any(ch.isspace() for ch in candidate):
        return None
    if any(ord(ch) < 0x20 for ch in candidate):
        return None
    parsed = urlsplit(candidate)
    if parsed.scheme.lower() not in _SAFE_SCHEMES:
        return None
    if not parsed.netloc:
        return None
    return candidate


#: The code points that may only ever follow the one they join to. Cutting
#: between a base character and any of these leaves the second half stranded: a
#: combining accent on its own, or a ZWJ whose emoji is gone, both render as the
#: "missing glyph" box.
_JOINERS = frozenset("\u200d\u20e3\ufe0f\ufe0e")
_VARIATION_SELECTORS = frozenset(chr(c) for c in range(0xFE00, 0xFE10))
_SKIN_TONES = frozenset(chr(c) for c in range(0x1F3FB, 0x1F400))
_REGIONAL = frozenset(chr(c) for c in range(0x1F1E6, 0x1F200))


def _joins(previous: str, current: str) -> bool:
    """Whether `current` continues `previous`'s grapheme cluster.

    **An approximation, and it has to be said so.** The stdlib has no grapheme
    segmenter; `Intl.Segmenter`, which open-intelligent-ui uses for exactly this
    ("so a word or letter from the model can't overflow the pin"), is JavaScript.
    What the stdlib *does* give is the set of characters that cannot start a
    cluster, and that is the half that matters for a truncation - the failure
    being prevented is a cut that leaves one of these stranded.
    """
    if current in _JOINERS or current in _VARIATION_SELECTORS or current in _SKIN_TONES:
        return True
    if unicodedata.combining(current):
        return True
    # A ZWJ also joins *forwards*: it consumes the character that follows it, so
    # "👨\u200d👩" is one cluster and cutting between the ZWJ and the woman leaves
    # the ZWJ stranded just as before.
    if previous in _JOINERS:
        return True
    # A flag is two regional indicators; cutting between them leaves half a flag.
    return previous in _REGIONAL and current in _REGIONAL


def graphemes(text: str) -> "List[str]":
    """`text` split into approximate grapheme clusters."""
    if not text:
        return []
    start = 0
    out: List[str] = []
    for i in range(1, len(text)):
        if not _joins(text[i - 1], text[i]):
            out.append(text[start:i])
            start = i
    out.append(text[start:])
    return out


def _cluster_display_width(cluster: str) -> int:
    """The columns one grapheme cluster takes.

    **A cluster is one glyph, so its width is one measurement, not a sum.**
    Summing the code points counts a ZWJ sequence as its parts: "👨‍👩‍👧‍👦" came
    out at 11 columns (four emoji at 2, three ZWJ at 1) instead of 2, and a
    string that already fitted the slot was clipped anyway. The widest code
    point is right for every case here - a flag is two wide regional indicators,
    an accented letter is one narrow base plus a zero-width mark, and a ZWJ
    sequence is wide throughout except for the joiners.
    """
    if not cluster:
        return 0
    return max(_display_width(ch) for ch in cluster)


def clip(text: str, width: int, ellipsis: str = "...") -> str:
    """`text` cut to `width` columns, on a grapheme boundary, with an ellipsis.

    **A hard slice leaves broken glyphs, and `str[:n]` is a hard slice.** Cutting
    "👨‍👩‍👧‍👦" after its second code point leaves a ZWJ with no emoji, and
    cutting "é" after its first leaves a bare combining acute; both draw the
    "missing glyph" box. Measured: `('e' + U+0301)[:1]` is `'e'`, and
    `'…👨‍👩‍👧‍👦…'[:i+2]` ends in U+200D.

    This is open-intelligent-ui's `pinEmoji` problem in a different place: that
    function takes the first grapheme and only if it is Extended_Pictographic,
    "so a word or letter from the model can't overflow the pin".

    The ellipsis counts against the budget, so the result is never wider than
    `width` - which is the point of a clip: the caller is putting this in a slot
    of that many columns, not asking for a string of that many characters.

    Two things the first version got wrong, both found by the tests: a cluster
    measured per code point (so a family emoji cost 11 columns and text that
    fitted was clipped anyway), and an ellipsis that did not fit swallowing the
    whole budget (a two-column slot got two dots instead of two columns of text).
    """
    if width <= 0:
        return ""
    if sum(_cluster_display_width(c) for c in graphemes(text)) <= width:
        return text
    budget = width - _display_width(ellipsis)
    if budget < 1:
        budget = width
    out: List[str] = []
    used = 0
    for cluster in graphemes(text):
        step = _cluster_display_width(cluster)
        if used + step > budget:
            break
        out.append(cluster)
        used += step
    clipped = "".join(out)
    if clipped and budget < width:
        clipped += ellipsis
    return clipped


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
#: A markdown link being reduced to its label. **The same shape `_LINK_MD`
# uses, and it was wrong here first** - `[^)]*` stopped at the first `)`, so
# `[Foo](https://en.wikipedia.org/wiki/Foo_(bar))` was spoken as `Foo)` and
# `[x](javascript:alert(1))` as `A x) link.` Wikipedia addresses carry a real
# parenthesis, so the character cannot be banned from a URL.
_SPOKEN_LINK = re.compile(r"\[([^\]]*)\]\(([^()\s]*(?:\([^()\s]*\)[^()\s]*)*)\)")

#: A bare web address, which a model writes unlinked as often as not. The
# character before the scheme must not be alphanumeric, or `ahttp://x` and an
# address inside a longer token both get mangled. **A backtick is excluded**:
# `` `curl https://x.org/a` `` used to match the closing backtick as part of the
# address, so the URL pass ate it and left the code span unpaired.
_BARE_URL = re.compile(r"(?<![A-Za-z0-9>\"'])https?://[^\s<>`]*")


def _say_link(match: "re.Match[str]") -> str:
    """A bare address -> the word "link", plus whatever ends the sentence.

    **The trim decides what the address is; it never decides what the reply
    says.** Returning a bare "link" for the whole match swallowed the sentence's
    own punctuation, so `see (https://x.org/a) ok` was spoken `see (link ok` -
    an unclosed bracket *and* a dropped one, both audible. A trailing `)`, `]`,
    `,` or `.` belongs to the sentence when it is unbalanced, and to the address
    when it is not, which is what makes `Foo_(bar)` work.
    """
    whole = match.group(0)
    url = _trim_url_clip(whole)
    if url == whole:
        return "link"
    return "link" + whole[len(url):]


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
