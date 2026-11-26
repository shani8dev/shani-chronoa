"""The additive Agent capability surface, in one table.

`agent_contract.py` is the wire (the XML the shell compares); this module is
the metadata both clients read: which method answers which slash command and
which CLI subcommand, what argument names it takes, and whether it needs an
explicit confirmation. One table means a new method cannot reach one client
and miss the other without the parity test seeing it.

Nothing here calls the engine. `agent_service.py` owns the server dispatch;
`tui/commands.py` and `tui/cli.py` own the two clients.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Methods that existed before the capability surface. Their clients are the
#: turn loop (`-p`, free text) and the Inventory reader, not the table below.
LEGACY_METHODS = (
    "Turn",
    "TurnWith",
    "Approve",
    "Cancel",
    "Write",
    "FindFiles",
    "Inventory",
)


@dataclass(frozen=True)
class Method:
    name: str
    args: tuple  # (dbus arg name, GLib type letter)
    slash: tuple  # slash command path, e.g. ("settings", "get")
    cli: tuple  # CLI subcommand path (same shape)
    help: str
    write: bool = False
    confirm: bool = False  # needs an explicit yes from the person in the client
    join_last: bool = False  # last slash argument is free text (a title)

    @property
    def signature_in(self) -> str:
        return "(" + "".join(kind for _, kind in self.args) + ")"

    @property
    def signature_out(self) -> str:
        return "(s)"

    @property
    def arg_names(self) -> tuple:
        return tuple(name for name, _ in self.args)


S = "s"
B = "b"

METHODS: dict[str, Method] = {
    m.name: m
    for m in (
        Method(
            "SettingsList",
            (),
            ("settings",),
            ("settings",),
            "list named settings with readable labels",
        ),
        Method(
            "SettingsGet",
            (("name", S),),
            ("settings", "get"),
            ("settings", "get"),
            "read one setting",
        ),
        Method(
            "SettingsSet",
            (("name", S), ("value", S), ("confirm", B)),
            ("settings", "set"),
            ("settings", "set"),
            "write one setting; keys and consent switches ask first",
            write=True,
            confirm=True,
        ),
        Method(
            "SettingsPreset",
            (("name", S), ("confirm", B)),
            ("settings", "preset"),
            ("settings", "preset"),
            "apply a permission preset; switches on many consent keys",
            write=True,
            confirm=True,
        ),
        Method(
            "SettingsEmail",
            (("values_json", S), ("confirm", B)),
            ("settings", "email"),
            ("settings", "email"),
            "set up an email account; the secret travels in the string argument",
            write=True,
            confirm=True,
        ),
        Method(
            "ModelsList",
            (),
            ("models",),
            ("models",),
            "list model engines and which one answers",
        ),
        Method(
            "ModelsUse",
            (("name", S), ("confirm", B)),
            ("models", "use"),
            ("models", "use"),
            "choose local or cloud; cloud sends data off this machine",
            write=True,
            confirm=True,
        ),
        Method(
            "SetupState",
            (),
            ("setup",),
            ("setup",),
            "first-run setup: what is ready and what is missing",
        ),
        Method(
            "SetupStep",
            (("step", S), ("value", S), ("confirm", B)),
            ("setup", "step"),
            ("setup", "step"),
            "run one setup step; downloads ask first",
            write=True,
            confirm=True,
        ),
        Method(
            "Conversations",
            (),
            ("conversations",),
            ("conversations",),
            "list saved conversations",
        ),
        Method(
            "ConversationsResume",
            (("ref", S),),
            ("conversations", "resume"),
            ("conversations", "resume"),
            "continue a saved conversation",
        ),
        Method(
            "ConversationsRename",
            (("ref", S), ("title", S)),
            ("conversations", "rename"),
            ("conversations", "rename"),
            "rename a saved conversation",
            write=True,
            join_last=True,
        ),
        Method(
            "ConversationsDelete",
            (("ref", S), ("confirm", B)),
            ("conversations", "delete"),
            ("conversations", "delete"),
            "delete a saved conversation",
            write=True,
            confirm=True,
        ),
        Method(
            "MemoryList",
            (),
            ("memory",),
            ("memory",),
            "list durable facts and waiting proposals",
        ),
        Method(
            "MemoryDecide",
            (("id", S), ("decision", S), ("confirm", B)),
            ("memory", "decide"),
            ("memory", "decide"),
            "accept or reject a memory proposal",
            write=True,
            confirm=True,
        ),
        Method(
            "Routines",
            (),
            ("routines",),
            ("routines",),
            "list saved routines and their phrases",
        ),
        Method(
            "RoutinesRun",
            (("name", S),),
            ("routines", "run"),
            ("routines", "run"),
            "expand a routine into its request",
            write=True,
        ),
        Method(
            "Triggers",
            (),
            ("triggers",),
            ("triggers",),
            "list configured trigger rules",
        ),
        Method(
            "Notices", (), ("notices",), ("notices",), "list things Chronoa noticed"
        ),
        Method(
            "NoticesDismiss",
            (("id", S),),
            ("notices", "dismiss"),
            ("notices", "dismiss"),
            "dismiss one notice",
            write=True,
        ),
        Method(
            "VoiceEngines",
            (),
            ("voice",),
            ("voice",),
            "list speech engines, devices and voices",
        ),
        Method(
            "Activity",
            (),
            ("activity",),
            ("activity",),
            "list the recent tool-call audit trail",
        ),
        Method(
            "DiagnosticsProbe",
            (),
            ("diagnostics",),
            ("diagnostics",),
            "probe each part of the engine and say why it is not working",
        ),
    )
}


@dataclass(frozen=True)
class View:
    """A read-only Inventory section, drawn by the same name in both clients."""

    section: str
    slash: tuple
    cli: tuple
    help: str
    extra: tuple = ()  # other sections a panel draws with this one


def inventory_views() -> "dict[str, View]":
    """One view per Inventory section. The engine owns the list of sections."""
    from shani_chronoa import inventory

    return {
        section: View(
            section,
            (section,),
            (section,),
            "read the %s inventory section" % section,
            ("capabilities",) if section == "skills" else (),
        )
        for section in inventory.SECTIONS
    }


def by_slash(path: tuple) -> "Method | None":
    """The method whose slash path matches, or None."""
    return next((m for m in METHODS.values() if m.slash == tuple(path)), None)


def by_cli(path: tuple) -> "Method | None":
    """The method whose CLI path matches, or None."""
    return next((m for m in METHODS.values() if m.cli == tuple(path)), None)
