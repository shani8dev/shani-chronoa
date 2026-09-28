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
fenced code blocks, and `-`/`*`/`+` bullet lists. Not headings, tables, links,
images or blockquotes. A renderer that half-implements CommonMark is harder to
reason about than one that documents its own twelve rules; if more is needed
that is a dependency decision, not something to grow here.

Links are the interesting non-case: `[text](url)` is left as literal text
rather than turned into an anchor, because a clickable link whose target came
from a model is a prompt-injection route to somewhere the user did not intend.
"""

from __future__ import annotations

import re
from typing import List

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


def to_pango(text: str) -> str:
    """Plain text with this module's markdown subset -> Pango markup.

    Returns markup, not a widget: the transformation is pure and testable on
    its own, which is the only way to be confident about the escaping.
    """
    if not text:
        return ""

    blocks: List[str] = []
    stash: List[str] = []

    def keep_fence(match: "re.Match[str]") -> str:
        stash.append(escape(match.group(2).rstrip("\n")))
        return f"\x00FENCE{len(stash) - 1}\x00"

    working = _FENCE.sub(keep_fence, text)

    out_lines: List[str] = []
    for line in escape(working).split("\n"):
        bullet = _BULLET.match(line)
        if bullet:
            out_lines.append(f"•  {_inline(bullet.group(1))}")
        else:
            out_lines.append(_inline(line))

    body = "\n".join(out_lines)

    for index, code in enumerate(stash):
        body = body.replace(f"\x00FENCE{index}\x00", f"<tt>{code}</tt>")

    return body
