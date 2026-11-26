"""Syntax highlighting for the code in a reply.

**Why this is here and not `GtkSourceView`.** Measured on Arch: `gtksourceview5`
is available (`extra`, 5.22.0-1) and cheap - of its 13 declared dependencies,
GTK4 already pulls 9, so the marginal cost is `hicolor-icon-theme`, `libxml2` and
`pcre2`. But Arch's own PKGBUILD is a vanilla `meson install` with nothing added
for language specs, and the package ships **zero** `.lang` definitions where the
GTK3 line ships **173**. So the widget would arrive with no knowledge of what a
keyword is, and the highlighting would be hand-authored into the application.

Meanwhile the thing the widget is actually wanted for - scrolling and selection -
`CodeBlock` already has: a selectable label inside a 260px scroller. And
`CodeBlock` is **read-only**. An editor widget answers "I want to type here";
this is a transcript.

**Why the palette is per-theme, and not a preference.** A fixed hex cannot work,
and that is arithmetic rather than taste. For 4.5:1 against white, a colour's
relative luminance must be **<= 0.1833**; against Adwaita's dark background
`#242424`, **>= 0.2544**. Those bounds are disjoint, so **no single colour is
legible on both themes** - a one-palette highlighter is wrong on one of them,
which is the light-theme-hex-on-a-dark-desktop defect this repository already
records once in the Diff panel.

So `highlight()` reads the scheme from `Adw.StyleManager` and picks. Both
palettes are measured in `tests/test_code_highlighting.py`: every token clears
4.5:1 on its own background, and the control asserts each palette **fails** on
the other's, which is what makes the switch load-bearing rather than decorative.

**Stdlib only, like everything else shipped here.** `python-pygments` exists on
Arch (2.21.0, `extra`) and would highlight today with no authoring - and it would
also put a Python dependency in a package whose rule is that shipped code uses
the standard library. `AGENTS.md`'s packaging section already treats `sox` as an
optdepend rather than a hard one for the same reason.
"""

from __future__ import annotations

import re
from typing import Iterator, NamedTuple, Optional

from shani_chronoa import markdown_lite

__all__ = ["highlight", "languages", "supports"]

# The seven kinds of token a person can actually tell apart at a glance. Kept
# small on purpose: a palette with nine entries is a palette nobody reads, and
# two kinds that are both "pale blue" are worse than one kind.
COMMENT = "comment"
STRING = "string"
NUMBER = "number"
KEYWORD = "keyword"
PREPROC = "preproc"
NAME = "name"          # an identifier being called
ERROR = "error"        # a removed line, in a diff

_KINDS = (COMMENT, STRING, NUMBER, KEYWORD, PREPROC, NAME, ERROR)

# **Measured, not chosen by eye.** See the module docstring for why there are
# two of these and why one cannot do.
_ON_LIGHT = {
    COMMENT: "#6a737d",   # 4.82:1 on white
    STRING:  "#0a7d33",   # 5.26:1
    NUMBER:  "#0550ae",   # 7.59:1
    KEYWORD: "#b02a8b",   # 5.92:1
    PREPROC: "#7a4100",   # 8.13:1
    NAME:    "#953800",   # 7.39:1
    ERROR:   "#b3121a",   # 6.96:1
}
_ON_DARK = {
    COMMENT: "#9aa4b0",   # 6.14:1 on #242424
    STRING:  "#7ee787",   # 10.10:1
    NUMBER:  "#79c0ff",   # 7.98:1
    KEYWORD: "#ff7b72",   # 6.16:1
    PREPROC: "#ffa657",   # 8.02:1
    NAME:    "#d2a8ff",   # 7.97:1
    ERROR:   "#ffa198",   # 6.54:1
}

_IDENT_START = re.compile(r"[A-Za-z_]")
_IDENT = re.compile(r"[A-Za-z0-9_]")
_NUMBER = re.compile(r"[0-9]")


class Spec(NamedTuple):
    """What one language looks like. The scanner below is one implementation.

    A tokenizer per language would be sixteen near-copies of the same loop, and
    this repository has been bitten by that shape repeatedly - the `wpctl`
    triplication, the finite check written out four times. The *scanning* is the
    hard part and it is written once; a language is a table.
    """
    # Empty by default: three of the ten languages highlight comments, strings
    # and numbers and have no reserved words worth colouring (yaml, toml, xml).
    keywords: frozenset = frozenset()
    line_comment: str = ""
    block_comment: tuple = ()          # (open, close)
    quotes: tuple = ('"', "'")
    triple: tuple = ()                 # python's """ and '''
    preproc: str = ""                  # marker that begins a directive line
    numbers: bool = True
    # ``line_based`` languages (a diff) are not token streams at all, so they
    # get their own scanner rather than a contorted Spec.
    line_based: bool = False


def _words(text: str) -> frozenset:
    return frozenset(text.split())


# The languages an assistant actually emits in a fenced block. Not "every
# language there is": a table entry costs a keyword set to keep honest, and a
# language nobody receives highlighted is a table row that rots.
LANGS = {
    "python": Spec(
        keywords=_words("""and as assert async await break class continue def del
            elif else except False finally for from global if import in is
            lambda None nonlocal not or pass raise return True try while with
            yield match case"""),
        line_comment="#", triple=('"""', "'''"),
    ),
    "bash": Spec(
        keywords=_words("""if then else elif fi for while do done case esac
            function in return export local readonly declare source alias set
            unset shift exit trap"""),
        line_comment="#", quotes=('"', "'"),
    ),
    "json": Spec(
        keywords=_words("true false null"),
        quotes=('"',),
    ),
    "xml": Spec(
        block_comment=("<!--", "-->"), quotes=('"', "'"),
    ),
    "yaml": Spec(line_comment="#", quotes=('"', "'")),
    "sql": Spec(
        keywords=_words("""SELECT FROM WHERE INSERT INTO VALUES UPDATE SET
            DELETE CREATE TABLE DROP ALTER JOIN LEFT RIGHT INNER OUTER ON GROUP
            BY ORDER HAVING LIMIT OFFSET AND OR NOT NULL AS DISTINCT COUNT SUM
            AVG MIN MAX"""),
        line_comment="--", block_comment=("/*", "*/"), quotes=("'", '"'),
    ),
    "javascript": Spec(
        keywords=_words("""async await break case catch class const continue
            debugger default delete do else export extends false finally for
            from function get if import in instanceof let new null of return
            set static super switch this throw true try typeof var void while
            yield"""),
        line_comment="//", block_comment=("/*", "*/"),
    ),
    "c": Spec(
        keywords=_words("""auto break case char const continue default do
            double else enum extern float for goto if inline int long register
            restrict return short signed sizeof static struct switch typedef
            union unsigned void volatile while class public private protected
            new template typename namespace using friend virtual operator
            override final public_rust let mut fn impl match use mod pub self
            where as dyn ref move crate unsafe"""),
        line_comment="//", block_comment=("/*", "*/"), preproc="#",
    ),
    "css": Spec(
        keywords=_words("""important media import charset keyframes supports
            font-face"""),
        block_comment=("/*", "*/"), quotes=('"', "'"),
    ),
    "toml": Spec(line_comment="#", quotes=('"', "'")),
    "diff": Spec(line_based=True),
}

# The names people and models actually write on a fence.
ALIASES = {
    "py": "python", "python3": "python", "pycon": "python",
    "sh": "bash", "shell": "bash", "zsh": "bash", "console": "bash",
    "jsonc": "json", "json5": "json",
    "html": "xml", "xhtml": "xml", "svg": "xml",
    "yml": "yaml",
    "js": "javascript", "jsx": "javascript", "mjs": "javascript",
    "ts": "javascript", "typescript": "javascript", "tsx": "javascript",
    "node": "javascript",
    "cpp": "c", "c++": "c", "cc": "c", "h": "c", "hpp": "c",
    "java": "c", "rust": "c", "rs": "c", "go": "c", "golang": "c",
    "scss": "css", "less": "css",
    "patch": "diff", "udiff": "diff",
}


def supports(language: str) -> bool:
    """Whether this fence gets colour. An unknown language is left alone."""
    return _canonical(language) is not None


def _canonical(language: Optional[str]) -> Optional[str]:
    if not language:
        return None
    name = language.strip().lower()
    name = ALIASES.get(name, name)
    return name if name in LANGS else None


def _is_dark() -> bool:
    """The scheme, asked of GTK rather than guessed from the environment.

    `ADW_DEBUG_COLOR_SCHEME` exists and this ignores it on purpose: the question
    is what the window is actually drawing with, and `Adw.StyleManager` is the
    only thing that knows that.
    """
    try:
        import gi
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        return bool(Adw.StyleManager.get_default().get_dark())
    except Exception:                                   # noqa: BLE001
        # No libadwaita, or not initialised: the light palette is the safer
        # default because a transcript is read against a document, not a
        # terminal. A wrong guess here is a contrast number, not a crash.
        return False


def _scan(code: str, spec: Spec) -> Iterator[tuple]:
    """Yield ``(kind or None, text)`` for one language. One implementation."""
    i, n = 0, len(code)
    at_line_start = True
    while i < n:
        ch = code[i]

        # A directive line, before anything else can claim the marker.
        if at_line_start and spec.preproc and code.startswith(spec.preproc, i):
            j = code.find("\n", i)
            j = n if j < 0 else j
            yield PREPROC, code[i:j]
            i = j
            at_line_start = True
            continue

        # Line comment.
        if spec.line_comment and code.startswith(spec.line_comment, i):
            j = code.find("\n", i)
            j = n if j < 0 else j
            yield COMMENT, code[i:j]
            i = j
            at_line_start = True
            continue

        # Block comment. Unterminated runs to the end rather than hanging.
        if spec.block_comment:
            open_, close = spec.block_comment
            if code.startswith(open_, i):
                j = code.find(close, i + len(open_))
                j = n if j < 0 else j + len(close)
                yield COMMENT, code[i:j]
                i = j
                at_line_start = False
                continue

        # Triple-quoted, before the single-quote scan or `"""` reads as `""`
        # followed by a stray quote.
        #
        # **`continue`, and it is load-bearing.** This branch used to `break` out
        # of the `for` and then fall through to the single-quote scan below,
        # which still saw the *stale* `ch` - a `"` from the triple-quote marker
        # that had just been consumed. Measured on `code_hl.tokenize` against
        # real source: a docstring coloured the entire rest of the file as one
        # string.
        #
        #     'return x + 1  # note'      <- keyword, name, number, comment
        # became
        #     '\n    return x + 1  # note\n'   <- one STRING token
        #
        # It passed every test here because the only Python sample any of them
        # used had no docstring in it, and a lexer that colours too much is
        # indistinguishable from one that colours correctly as long as you only
        # look at the colours. `tests/test_code_hl_tokenize.py` asserts the
        # join and the kinds on source that has one, which is what it takes to
        # see it.
        if spec.triple:
            marker = next((m for m in spec.triple if code.startswith(m, i)), None)
            if marker is not None:
                j = code.find(marker, i + len(marker))
                j = n if j < 0 else j + len(marker)
                yield STRING, code[i:j]
                i = j
                at_line_start = False
                continue

        # A quoted string, honouring backslash escapes so a quote inside one
        # does not end it early.
        if ch in spec.quotes:
            j = i + 1
            while j < n:
                if code[j] == "\\":
                    j += 2
                    continue
                if code[j] == ch:
                    j += 1
                    break
                j += 1
            else:
                j = n
            yield STRING, code[i:min(j, n)]
            i = min(j, n)
            at_line_start = False
            continue

        # A number, or a dot followed by one.
        if spec.numbers and (ch.isdigit() or (ch == "." and i + 1 < n
                                               and code[i + 1].isdigit())):
            j = i + 1
            while j < n and (code[j].isalnum() or code[j] in "._"):
                j += 1
            yield NUMBER, code[i:j]
            i = j
            at_line_start = False
            continue

        # An identifier: a keyword, or a call if something opens a paren.
        if _IDENT_START.match(ch):
            j = i + 1
            while j < n and _IDENT.match(code[j]):
                j += 1
            word = code[i:j]
            if word in spec.keywords:
                yield KEYWORD, word
            else:
                k = j
                while k < n and code[k] in " \t":
                    k += 1
                if k < n and code[k] == "(":
                    yield NAME, word
                else:
                    yield None, word
            i = j
            at_line_start = False
            continue

        # **One character at a time, and that is load-bearing twice over.**
        #
        # The first version coalesced a run of plain text up to the next
        # newline. That is wrong because a run *contains* the characters a
        # comment, a string or a number starts with: `    # note` was yielded
        # whole as plain text, so an indented comment was never coloured at all.
        # Measured before the fix - a four-line Python sample carrying a
        # keyword, a comment, a string, a number and a call produced **1 span**
        # where it should have produced 6.
        #
        # Coalescing also looped forever on the newline itself, because
        # `code.find("\n", i)` returns `i` when `code[i]` is that newline, so the
        # run was empty and `i` never advanced.
        #
        # Emitting one character at a time costs a tuple per character and makes
        # every position a chance for the branches above to claim it, which is
        # the only way the "does it colour anything" property can be true.
        yield None, ch
        at_line_start = ch == "\n"
        i += 1


def _scan_diff(code: str) -> Iterator[tuple]:
    """A diff is per-line, not per-token, so it gets its own loop.

    Seven lines instead of a `Spec` with three more fields that only this one
    language would ever read.
    """
    for line in code.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith(("+++", "---")):
            yield PREPROC, line
        elif line.startswith("@@"):
            yield NAME, line
        elif line.startswith("+"):
            yield STRING, line
        elif line.startswith("-"):
            yield ERROR, line
        elif stripped.startswith("\\"):     # "\ No newline at end of file"
            yield COMMENT, line
        else:
            yield None, line


def highlight(code: str, language: Optional[str] = None,
              dark: Optional[bool] = None) -> str:
    """Pango markup for ``code``, coloured for ``language``.

    **An unrecognised language returns the escaped code unchanged** - no
    half-right colouring. A fence with no language, or one this table has never
    heard of, must look exactly as it did before this module existed.

    The token text is escaped **per token**, after tokenising and before it goes
    inside a tag, so model-supplied markup cannot escape into the label. Every
    token ends up either escaped plain text or inside a ``<span>`` whose only
    attribute is a hex colour from our own table - so nothing a model writes is
    ever inside an attribute value, where a quote would matter.
    """
    escaped_only = markdown_lite.escape(code)
    name = _canonical(language)
    if name is None:
        return escaped_only

    spec = LANGS[name]
    is_dark = _is_dark() if dark is None else dark
    palette = _ON_DARK if is_dark else _ON_LIGHT

    parts = []
    for kind, text in (_scan_diff(code) if spec.line_based
                       else _scan(code, spec)):
        if not text:
            continue
        safe = markdown_lite.escape(text)
        if kind is None:
            parts.append(safe)
        else:
            parts.append(f'<span foreground="{palette[kind]}">{safe}</span>')
    return "".join(parts)


def tokenize(code: str, language: Optional[str] = None) -> list:
    """``[(kind or None, text), ...]`` for ``code`` - the stream, not the markup.

    **This is the API every renderer other than the transcript wants.** Until it
    existed the only public output was `highlight()`'s Pango markup, so the
    `(kind, text)` pairs were reachable only from inside this module: a
    consumer outside it had to parse markup back into tokens, or colour nothing.
    That is what made an editor surface impossible - see `gui/surfaces/editor.py`,
    which tags a `Gtk.TextBuffer` from this stream precisely because Arch's
    `gtksourceview5` ships zero `.lang` definitions (module docstring above) and
    so `GtkSourceView` has no language to load.

    An unrecognised language yields one untagged token, matching
    `highlight()`'s promise that unknown input comes back unchanged rather than
    half-coloured. Consumers that need the real language name can ask
    `_canonical`; this keeps the same "you asked, you got the truth" contract by
    returning the raw text as a single untyped token.
    """
    name = _canonical(language)
    if name is None:
        return [(None, code)] if code else []
    spec = LANGS[name]
    tokens = (_scan_diff(code) if spec.line_based else _scan(code, spec))
    return [(kind, text) for kind, text in tokens if text]


def palette(dark: Optional[bool] = None) -> dict:
    """The colour table `highlight()` and `tokenize()`'s callers both use.

    Exposed because a consumer holding tokens now has to colour them itself, and
    a second copy of these hexes would drift from the transcript's without
    anything failing. `None` asks GTK what scheme the window is actually
    drawing with, exactly as `highlight()` does.
    """
    is_dark = _is_dark() if dark is None else dark
    return dict(_ON_DARK if is_dark else _ON_LIGHT)


def languages() -> tuple:
    """Every fence name this module colours, aliases included.

    Sorted so the test that pins the vocabulary cannot pass on a prefix.
    """
    return tuple(sorted(set(LANGS) | set(ALIASES)))