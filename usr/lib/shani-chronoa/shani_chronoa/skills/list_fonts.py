"""Skill: which fonts are installed, and which one is actually used?

Two different questions, both answered by fontconfig, which every GTK and Qt
application consults:

- `fc-list` - which font *families* are installed (optionally matching a name);
- `fc-match` - which font a request actually *resolves to*. "monospace" is not a
  font; it is a request that fontconfig answers with one, and that answer is
  what a terminal or code editor really draws with. A font being installed does
  not mean it is the one in use, and only `fc-match` can tell them apart.

Honesty rule: a family name that `fc-match` answers with a *different* family is
reported as a substitution, because fontconfig never fails a match - it falls
back silently, and "Comic Sans resolves to DejaVu Sans" means it is not installed.
"""

from __future__ import annotations

import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.skills import Skill

_TIMEOUT = 20
_MAX = 80
_GENERIC = ("sans-serif", "serif", "monospace", "emoji")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_fonts",
        "description": (
            "List the font families installed on this machine (optionally only "
            "those whose name contains some text), and which real font the "
            "generic names - sans-serif, serif, monospace, emoji - or a font you "
            "name actually resolve to. Use for 'what fonts do I have', 'is "
            "Fira Code installed', 'which font is used for monospace'. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "Only families containing this text; also checked with fc-match."},
            },
        },
    },
}


def _fc(*args: str) -> "str | None":
    try:
        proc = subprocess.run(list(args), capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def families(stdout: str) -> "list[str]":
    """Unique family names from `fc-list : family` (first name of each line)."""
    seen = {}
    for line in stdout.splitlines():
        name = line.split(",")[0].strip()
        if name:
            seen.setdefault(name.lower(), name)
    return sorted(seen.values(), key=str.lower)


def resolve(request: str) -> "str | None":
    out = _fc("fc-match", request, "--format=%{family[0]}|%{style[0]}|%{file}")
    if not out:
        return None
    family, _, rest = out.partition("|")
    style, _, path = rest.partition("|")
    return f"{family} {style}".strip() + (f" ({path})" if path else "")


def _run(arguments: dict) -> str:
    if shutil.which("fc-list") is None:
        return files.tool_missing("fc-list", "list fonts")
    raw = _fc("fc-list", ":", "family")
    if raw is None:
        return "fc-list failed, so the installed fonts are UNKNOWN rather than none."
    names = families(raw)
    query = (arguments.get("query") or "").strip()
    lines = []
    if query:
        hits = [n for n in names if query.lower() in n.lower()]
        if hits:
            lines.append(f"{len(hits)} installed family(ies) match {query!r}:")
            lines.extend(f"  {n}" for n in hits[:_MAX])
        else:
            lines.append(f"No installed font family contains {query!r}.")
        resolved = resolve(query)
        if resolved:
            same = resolved.lower().startswith(query.lower())
            lines.append(f"Asking for {query!r} gives: {resolved}"
                         + ("" if same or hits else " - a substitute, so the font itself is not installed."))
        return "\n".join(lines)
    lines.append(f"{len(names)} font families installed"
                 + (f"; first {_MAX} shown:" if len(names) > _MAX else ":"))
    lines.extend(f"  {n}" for n in names[:_MAX])
    lines.append("What the generic names resolve to:")
    for generic in _GENERIC:
        lines.append(f"  {generic:<11} -> {resolve(generic) or 'UNKNOWN'}")
    return "\n".join(lines)


SKILLS = [Skill(name="list_fonts", schema=SCHEMA, run=_run)]
