"""Sense: what this user may do on this machine without being asked for a password.

Found by comparing the capability surface against the matrix's `os_surfaces`:
that block carries **491 polkit actions** on a real GNOME image, each with the
rule that decides whether polkit asks for authentication, and **nothing in
Chronoa reported any of it**. The four modules that mention polkit *use* it -
`pkexec` this, a portal there - and `privilege` is a different question
entirely: it reports *processes holding a dangerous Linux capability*, which is
who has power, not what the policy says about the person asking.

The question this answers is the one somebody asks about their own machine: *what
can I just do?* Everything else in this package answers either "what is the
state" (a sense) or "do this thing" (a skill, behind a consent key). This is the
only kind of question whose answer is a **permission**, and it is the one that
decides what the other two are worth: a switch that always prompts is friction,
and a switch nobody was ever offered is a dead end.

**Read from the policy files, not from a cache of them.** `/usr/share/polkit-1/
actions/*.policy` is what polkit itself reads, so a package that ships a policy
and a package that is merely installed are not confused - which is the
mistake this package records for `whisper-cli`/`whisper-cpp` and for `wpctl`
in `wireplumber`. Reading dconf's compiled database instead would answer a
different question about a different store.

**`allow_active` is the column that matters and it is easy to read wrong.**
polkit has three rules per action - `allow_any`, `allow_inactive`,
`allow_active` - and the one that governs a person at their own desk is
`allow_active`. `yes` means no prompt at all; `auth_self` means this user may
authenticate as themselves, which is *still a prompt*; `auth_admin` and
`auth_admin_keep` mean an administrator must go first. So the report counts
**`yes` alone** as "no password", and says `auth_self` separately, because
collapsing those two is how a machine ends up looking either alarmist or
carefree about the same policy.

**"Nothing is listed" is not the same as "you can do nothing."** A machine with
no polkit policies at all is a machine where the question does not apply, and it
is reported as UNKNOWN rather than as zero permissions - the distinction this
repository insists on everywhere else, and the reason a sense that cannot read
its source says so rather than returning an empty list.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Optional

from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 900.0
_POLL_INTERVAL = None

_POLICY_DIRS = (
    Path("/usr/share/polkit-1/actions"),
    Path("/usr/local/share/polkit-1/actions"),
)

#: The polkit vocabulary. `yes` is the only value that means no authentication;
#: everything else is a prompt of some kind, and saying so is the whole point.
_NO_PASSWORD = {"yes"}
_SELF_AUTH = {"auth_self", "auth_self_keep"}
_ADMIN_AUTH = {"auth_admin", "auth_admin_keep"}
#: `no` is a real answer, not an absence: the action is **never** allowed. It
#: was falling into "other" and being reported as a rule this could not
#: classify, which is the confident-unknown-answer in its purest form - five
#: actions on this image that are permanently denied were being described as
#: five the reader could not work out.
_NEVER = {"no"}

_ACTION_ID = re.compile(r'<action\s+id="([^"]+)"', re.I)
_DESCRIPTION = re.compile(r"<description[^>]*>(.*?)</description>", re.I | re.S)


def _policy_files() -> List[Path]:
    """Every `.policy` file polkit would actually read."""
    out: List[Path] = []
    for directory in _POLICY_DIRS:
        if directory.is_dir():
            out.extend(sorted(directory.glob("*.policy")))
    return out


def _classify(value: str) -> str:
    """`yes` / `self` / `admin` / `other` for one polkit rule value."""
    text = (value or "").strip()
    if text in _NO_PASSWORD:
        return "yes"
    if text in _SELF_AUTH:
        return "self"
    if text in _ADMIN_AUTH:
        return "admin"
    if text in _NEVER:
        return "never"
    return "other"


def _describe(element: ET.Element) -> str:
    """An action's human description, with entities resolved.

    The element carries a `gettext-domain` attribute and the text may hold
    entities; `itertext` on the parsed tree gives both for free, which a regex
    over the raw file would not.
    """
    found = element.find("description")
    if found is None:
        return ""
    return " ".join("".join(found.itertext()).split())


def read_actions() -> Optional[List[dict]]:
    """Every polkit action and its `allow_active` rule, or None if unreadable.

    None means the policy directory could not be read at all. A list is the
    answer, and an empty list means a machine with no policies - which is a real
    state, not a failure, and is why the two are not the same return.
    """
    # **A directory that exists but holds no policies is `[]`, not None.**
    # `_policy_files()` returns [] both when no directory exists and when the
    # directories exist and are empty, and those are different claims: "the
    # policies could not be read" versus "there are none here". My own test
    # caught this - an empty temp directory came back UNKNOWN, which is the
    # confident-unknown answer in the place it costs the most.
    present = [d for d in _POLICY_DIRS if d.is_dir()]
    files = _policy_files()
    if not files:
        return [] if present else None
    actions: List[dict] = []
    for path in files:
        try:
            tree = ET.parse(path)
        except (ET.ParseError, OSError):
            # One unreadable file among 58 is not "the policy could not be
            # read"; the rest are still worth reporting, and the count below
            # says how many actions were actually found.
            continue
        root = tree.getroot()
        if not root.tag.endswith("policyconfig"):
            continue
        for element in root.findall("action"):
            defaults = element.find("defaults")
            active = defaults.findtext("allow_active") if defaults is not None else ""
            actions.append({
                "id": element.get("id") or "",
                "description": _describe(element),
                "allow_active": (active or "").strip(),
                "kind": _classify(active),
            })
    return actions


def _summary(actions: List[dict]) -> str:
    """The honest sentence, with the three cases kept apart."""
    total = len(actions)
    if total == 0:
        return ("No polkit policies are installed on this machine, so the "
                "question of what can be done without a password does not "
                "apply here.")
    yes = [a for a in actions if a["kind"] == "yes"]
    own = [a for a in actions if a["kind"] == "self"]
    admin = [a for a in actions if a["kind"] == "admin"]
    never = [a for a in actions if a["kind"] == "never"]
    other = [a for a in actions if a["kind"] == "other"]

    parts = [f"{len(yes)} of {total} installed polkit actions need no "
             f"authentication for an active session on this machine."]
    if own:
        parts.append(f"{len(own)} need your own password and nothing else.")
    if admin:
        parts.append(f"{len(admin)} need an administrator first.")
    if never:
        parts.append(f"{len(never)} are never allowed to anyone.")
    if other:
        parts.append(f"{len(other)} use a rule this could not classify.")
    if yes:
        parts.append("No password needed: "
                     + "; ".join(a["description"] or a["id"] for a in yes[:6])
                     + ("." if len(yes) <= 6 else ", and more."))
    return " ".join(parts)


_UNKNOWN = (
    "UNKNOWN - no polkit policy directory was readable at "
    "/usr/share/polkit-1/actions, so nothing could be said about what this "
    "machine allows. That is not a machine where you can do nothing; it is a "
    "machine whose permissions could not be read.")


def _run(arguments: dict) -> str:
    actions = read_actions()
    if actions is None:
        return _UNKNOWN
    summary = _summary(actions)
    # The schema declares `detail`, so the handler reads it. A declared argument
    # no code consults is the "an option whose label promises something the
    # program does not do" failure this repository records for the tray icon.
    if not (arguments or {}).get("detail"):
        return summary
    rows = sorted(actions, key=lambda a: (a["kind"], a["description"] or a["id"]))
    return summary + "\n\nEvery installed action:\n" + "\n".join(
        f"  {a['kind']:5} {a['allow_active'] or '(unset)':16} "
        f"{a['description'] or a['id']}" for a in rows)


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "polkitpolicy",
        "description": (
            "Report what this user may do on this machine without being asked "
            "for a password, read from the polkit policies actually installed. "
            "Separates actions that need no authentication from ones that "
            "need your own password and ones that need an administrator. "
            "Answers UNKNOWN when the policies could not be read, which is not "
            "the same as nothing being allowed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "detail": {
                    "type": "boolean",
                    "description": ("Also list the individual actions and the "
                                    "rule each one carries, rather than only "
                                    "the counts."),
                },
            },
        },
    },
}


_SENSE = Sense(
    name="polkitpolicy",
    kind=KIND,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    ttl_seconds=_TTL_SECONDS,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
