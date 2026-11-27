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

from shani_chronoa.agent_capabilities import METHODS, inventory_views


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
    "settings": Command("settings", "", "read named settings"),
    "setup": Command("setup", "", "first-run setup state"),
    "notices": Command("notices", "", "list things Chronoa noticed"),
    "voice": Command("voice", "", "list speech engines and devices"),
    "activity": Command("activity", "", "list recent tool calls"),
    "diagnostics": Command("diagnostics", "", "probe the engine's parts"),
}

# The additive methods are part of this one table, not a second help list.
# A bare name keeps its hand-written help; a subcommand gets the method's own
# sentence and the arguments a person actually types (never `confirm`).
for _method in METHODS.values():
    _name = " ".join(_method.slash)
    if _name not in COMMANDS:
        COMMANDS[_name] = Command(
            _name,
            " ".join(a for a in _method.arg_names if a != "confirm"),
            _method.help,
        )

# The Inventory views come from the engine's own SECTIONS, so a new panel
# reaches both clients without a hand-edited list here.
_VIEWS = inventory_views()
for _view in _VIEWS.values():
    if _view.section not in COMMANDS:
        COMMANDS[_view.section] = Command(_view.section, "", _view.help)

#: Commands that read a same-named, read-only Inventory section.
SECTION_COMMANDS: dict[str, str] = {name: name for name in _VIEWS}

#: Some commands read more than one section. Skills are the visible whitelist
#: and the gated capability list - the second is where a disabled skill shows.
EXTRA_SECTIONS: dict[str, tuple[str, ...]] = {
    name: view.extra for name, view in _VIEWS.items() if view.extra
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
    """Slash paths starting with `token`, sorted.

    A token with a space is a partly typed method path (`/settings se`), so
    the full additive paths are offered; a bare token offers only top-level
    command names. Both come from the same table the CLI parser reads.
    """
    from shani_chronoa.agent_capabilities import METHODS

    if " " in token:
        paths = {"/" + " ".join(m.slash) for m in METHODS.values()}
    else:
        paths = {"/" + name for name in COMMANDS if " " not in name}
    return sorted(path for path in paths if path.startswith(token))


def resolve(name: str, rest: str) -> "tuple[str | None, list[str]]":
    """Map a parsed slash command to an additive Agent method and its args.

    Returns ``(None, [])`` for the commands the client owns itself (help,
    quit, /new, /resume) and for the read-only Inventory sections that have
    no method yet. The longest matching path wins, so ``/settings get NAME``
    does not resolve to the bare ``/settings`` reader.
    """
    from shani_chronoa.agent_capabilities import METHODS

    tokens = [name, *rest.split()]
    best = None
    for method in METHODS.values():
        path = list(method.slash)
        if tokens[: len(path)] == path and (
            best is None or len(path) > len(best.slash)
        ):
            best = method
    if best is None:
        return None, []
    args = tokens[len(best.slash) :]
    if best.join_last and len(args) > 1:
        args = [args[0], " ".join(args[1:])]
    return best.name, args


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


def palette_commands() -> "list[tuple[str, str]]":
    """`(name, help)` pairs for the command palette, from the one command table."""
    return [(name, command.help) for name, command in COMMANDS.items()]
