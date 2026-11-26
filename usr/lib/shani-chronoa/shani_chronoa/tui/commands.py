"""Text commands for Chronoa's terminal client.

This module only *parses* what a person typed and describes which Inventory
sections a panel would read. It never calls the engine, opens a conversation,
or decides anything: the slash commands are sent as plain text to the Agent
turn, and the panel commands name read-only sections the shell renders.

Keeping this as plain data is deliberate. The GTK settings window and the
shell panel must not grow a second copy of "which command shows what" - a
client that parses differently from the one that displays would answer a
question nobody asked.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    name: str
    usage: str
    help: str


COMMANDS: dict[str, Command] = {
    "help": Command("help", "[command]", "list commands, or explain one"),
    "models": Command("models", "[query]", "list model tiers, or filter them"),
    "skills": Command("skills", "[query]", "list skills and their capabilities"),
    "memory": Command("memory", "[query]", "inspect what memory holds"),
    "routines": Command("routines", "[name]", "list saved routines"),
    "triggers": Command("triggers", "[query]", "list configured triggers"),
    "privacy": Command("privacy", "[section]", "read the privacy switches"),
    "senses": Command("senses", "[name]", "read the senses' current state"),
    "status": Command("status", "", "engine and bridge status"),
    "conversations": Command(
        "conversations", "[query]", "list or search conversations"
    ),
    "new": Command("new", "[title]", "start a new conversation"),
    "quit": Command("quit", "", "leave the terminal client"),
    "resume": Command("resume", "[id]", "resume a conversation by id"),
}

#: Commands that read a same-named, read-only Inventory section.
SECTION_COMMANDS: dict[str, str] = {
    "models": "models",
    "skills": "skills",
    "memory": "memory",
    "routines": "routines",
    "triggers": "triggers",
    "privacy": "privacy",
    "senses": "senses",
    "status": "status",
    "conversations": "conversations",
}

#: Some commands read more than one section. Skills are the visible whitelist
#: and the gated capability list - the second is where a disabled skill shows.
EXTRA_SECTIONS: dict[str, tuple[str, ...]] = {
    "skills": ("capabilities",),
}

_COMMAND_RE = re.compile(r"^/(\S+)(?:\s+(.*))?$")


def parse(line: str) -> tuple[str, str]:
    """`('', line)` for plain text; `(name, rest_args)` for `/cmd args`.

    The name is lowercased and has its slash removed. An unknown name still
    parses - deciding what to do with it belongs to the caller. A line of
    only `/` or `//` is text, not a command, so a person can type a bare
    slash without it being eaten as an empty command.
    """
    stripped = line.strip()
    if stripped in ("/", "//"):
        return "", line
    match = _COMMAND_RE.match(stripped)
    if match is None:
        return "", line
    return match.group(1).lower(), match.group(2) or ""


def completions(token: str) -> list[str]:
    """Slash command names (with leading `/`) starting with `token`, sorted."""
    return sorted("/" + name for name in COMMANDS if ("/" + name).startswith(token))


def path_completions(token: str) -> list[str]:
    """Match paths for an `@path` token, expanding `~`; directories keep `/`.

    File-system only: no engine call, no conversation lookup, no filtering by
    what any skill would accept. The caller decides what a chosen path means.
    """
    raw = token[token.rfind("@") + 1 :] if "@" in token else token
    expanded = os.path.expanduser(raw)
    head = os.path.dirname(expanded)
    prefix = os.path.basename(expanded)
    directory = head or "."
    try:
        entries = os.listdir(directory)
    except OSError:
        return []
    found: list[str] = []
    for entry in entries:
        if not entry.startswith(prefix):
            continue
        path = os.path.join(head, entry) if head else entry
        if os.path.isdir(os.path.join(directory, entry)):
            path += os.sep
        found.append(path)
    return sorted(found)


def help_lines() -> list[str]:
    """One `/name args - help` line per command, in definition order."""
    lines: list[str] = []
    for command in COMMANDS.values():
        left = f"/{command.name} {command.usage}".rstrip()
        lines.append(f"{left} - {command.help}")
    return lines
