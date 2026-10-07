"""The user's own words for Chronoa: a rules file, `/commands`, and `@mentions`.

Three surfaces every coding agent in harness-study grew, brought to a desktop
assistant and kept inside Chronoa's rule that nothing but the user starts a
free-form prompt:

- **Rules** (`~/.config/shani-chronoa/rules.md`; cline's `.clinerules`, codex's
  AGENTS.md, gemini's GEMINI.md): standing instructions read on every turn -
  "answer in British English", "my work laptop is the one called atlas". Sent
  as a system message after the system prompt, capped, never written to the
  transcript, re-read each turn so an edit applies at once.
- **Commands** (`~/.config/shani-chronoa/commands/<name>.md`; gemini's `.toml`
  commands, opencode's `commands/*.md`, goose recipes): typing `/standup
  yesterday` expands that file's text, with `$ARGUMENTS` / `$1`... filled in.
  The person typed the command, so its text is theirs. `@{file}` in a command
  pulls in a file; gemini's `!{shell}` is **not** supported - a command file
  is a prompt, never a program.
- **Mentions** (cline's `@file`, `@problems`, `@terminal`): `@clipboard` and
  `@~/path/file.txt` in what the person types are replaced by that text,
  bounded, before the turn is sent.

Everything is read from the user's own home; nothing here leaves the machine
except as part of the message the person chose to send.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from shani_chronoa import files

RULES_MAX = 4000
INSERT_MAX = 20000
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
_FILE_MENTION = re.compile(r"(?<![\w@])@(~/[^\s]+|/[^\s]+)")


def config_dir() -> Path:
    return files.config_home() / "shani-chronoa"


def rules_text() -> str:
    """The rules file, capped, or '' - read now, so an edit applies to the next turn."""
    path = config_dir() / "rules.md"
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""
    if len(text) > RULES_MAX:
        text = text[:RULES_MAX] + "\n[rules cut at 4000 characters]"
    return text


def rules_message() -> Optional[dict]:
    text = rules_text()
    if not text:
        return None
    return {"role": "system", "content": "The user's standing rules for you (from their rules file; "
                                         "follow them unless they conflict with safety):\n" + text}


def commands() -> "dict[str, Path]":
    folder = config_dir() / "commands"
    try:
        return {p.stem.lower(): p for p in sorted(folder.glob("*.md")) if _NAME.match(p.stem.lower())}
    except OSError:
        return {}


def command_summary(path: Path, limit: int = 60) -> str:
    """The first non-empty line of a command file, for a menu to show.

    Headings and comment markers are stripped; an unreadable file reads as ""
    rather than raising into a menu that is being drawn.
    """
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip().lstrip("#").strip()
                if line:
                    return line if len(line) <= limit else line[: limit - 1] + "\u2026"
    except OSError:
        pass
    return ""


def _read_bounded(path: Path) -> str:
    data = path.read_bytes()[: INSERT_MAX + 1]
    if b"\0" in data[:4096]:
        raise ValueError("binary file")
    text = data.decode("utf-8", errors="replace")
    return text[:INSERT_MAX] + ("\n[cut at 20000 characters]" if len(text) > INSERT_MAX else "")


def _file_block(raw: str) -> str:
    try:
        path = files.resolve_in_home(raw)
        return f"\n--- {path} ---\n{_read_bounded(path)}\n--- end of {path.name} ---\n"
    except (files.PathProblem, OSError, ValueError) as exc:
        return f"[{raw}: not included - {exc}]"


def _clipboard() -> str:
    for argv in (["wl-paste", "--no-newline", "--type", "text"], ["xclip", "-o", "-selection", "clipboard"]):
        if shutil.which(argv[0]):
            try:
                proc = subprocess.run(argv, capture_output=True, text=True, timeout=5, check=False)
            except subprocess.TimeoutExpired:
                continue
            if proc.returncode == 0:
                return proc.stdout[:INSERT_MAX]
    return ""


def expand_command(text: str) -> "tuple[str, str]":
    """(expanded text, note) for '/name args'; (text, '') when it is not a known command."""
    m = re.match(r"^/([a-z0-9][a-z0-9_-]*)\b\s*(.*)$", text.strip(), re.S)
    if not m:
        return text, ""
    name, args = m.group(1).lower(), m.group(2).strip()
    if name == "commands":
        listed = commands()
        return ("", "Your commands: " + ", ".join("/" + n for n in listed)) if listed else \
            ("", f"No commands yet - put a .md file in {config_dir() / 'commands'}; $ARGUMENTS is what follows the name.")
    path = commands().get(name)
    if path is None:
        return text, ""
    try:
        template = _read_bounded(path)
    except (OSError, ValueError) as exc:
        return text, f"/{name} could not be read: {exc}"
    template = re.sub(r"\A---\n.*?\n---\n", "", template, flags=re.S)  # optional front matter
    words = args.split()
    body = template.replace("$ARGUMENTS", args)
    body = re.sub(r"\$([1-9])", lambda mm: words[int(mm.group(1)) - 1] if len(words) >= int(mm.group(1)) else "", body)
    body = re.sub(r"!\{[^}]*\}", "[shell expansion is not supported in Chronoa commands]", body)
    body = re.sub(r"@\{([^}]+)\}", lambda mm: _file_block(mm.group(1).strip()), body)
    if "$ARGUMENTS" not in template and not re.search(r"\$[1-9]", template) and args:
        body += f"\n\n{args}"
    return body.strip(), f"/{name}"


def forced_tool(text: str) -> "tuple[str, str]":
    """('set_timer', '5 minutes') for '/set_timer 5 minutes' or '/timer 5 minutes'; ('', text) otherwise.

    Alpaca lets a person pick the tool for a message; a person who knows what
    they want makes a small model near-perfect, because only the arguments are
    left to it. The word may be a tool's full name or one word of exactly one
    tool's name ('/timer' is set_timer); an ambiguous word, a user command of
    the same name, or no match leaves the text as it was.
    """
    m = re.match(r"^/([a-z][a-z0-9_]*)\b\s*(.*)$", (text or "").strip(), re.S)
    if not m:
        return "", text
    word, rest = m.group(1).lower(), m.group(2).strip()
    if word == "commands" or word in commands():
        return "", text
    from shani_chronoa.tools import TOOLS
    names = [t["function"]["name"] for t in TOOLS]
    if word in names:
        return word, rest
    hits = [n for n in names if word in n.split("_")]
    return (hits[0], rest) if len(hits) == 1 else ("", text)


def expand_mentions(text: str) -> str:
    """@clipboard and @~/file in typed text, replaced by their content (bounded)."""
    if "@" not in text:
        return text
    if re.search(r"(?<![\w@])@clipboard\b", text):
        clip = _clipboard()
        text = re.sub(r"(?<![\w@])@clipboard\b",
                      lambda _m: f"\n--- clipboard ---\n{clip}\n--- end of clipboard ---\n" if clip else "[clipboard is empty]",
                      text)
    return _FILE_MENTION.sub(lambda m: _file_block(m.group(1).rstrip(".,;:!?)")), text)


def prepare(text: str) -> "tuple[str, str]":
    """What a typed message becomes before it is sent: a command expanded, mentions filled in."""
    expanded, note = expand_command(text)
    if not expanded:
        return "", note
    return expand_mentions(expanded), note
