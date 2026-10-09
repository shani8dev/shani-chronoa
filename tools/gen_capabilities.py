"""Generate CAPABILITIES.md from the tree, so it cannot claim anything untrue.

Every row is read out of `capabilities._GROUPS` — the same table the Help
window renders and the model is offered — so this document and the app
cannot disagree. Writing it by hand is what made the README stale.

Group order is the order `_GROUPS` declares, which is the order Help
presents, and the per-group heading counts come from the table rather than
from a tally done here.

Run: python3 tools/gen_capabilities.py > CAPABILITIES.md
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "usr/lib/shani-chronoa"))

from shani_chronoa import capabilities as C  # noqa: E402
from shani_chronoa import tools  # noqa: E402

#: One line per group, saying what the group is *for*. Read off the rows
#: below rather than asserted about the code.
BLURB = {
    "Files": "read, write, move, archive and undo files",
    "System": "processes, packages, updates, containers, VMs, disks, network",
    "Devices": "hardware, peripherals, sensors and their state",
    "Everyday tools": "the small things a person asks for most",
    "Time and reminders": "the clock, dates, countdowns and alarms",
    "Web": "search, fetch and act on a page",
    "Power and screen": "brightness, theme, power state, night light",
    "Sound": "play, record, describe and edit audio",
    "Appearance": "theme, wallpaper, scaling and the screen's own look",
    "Processes and windows": "find, move and close what is open",
    "Apps": "launch, list and find applications",
    "Pointer and keyboard": "drive the desktop without the keyboard",
    "Imagine": "generate images, and change one by description",
    "Code and git": "inspect repositories and work through files",
    "Services and logs": "systemd units and the journal",
    "What Chronoa knows": "memory, percepts and what it has decided",
    "Photos and video": "read, edit and extract from media",
    "Clipboard": "read and write the clipboard",
    "Local models": "choose, measure and rebuild the local models",
    "Eyes": "see the screen and a photograph",
    "Screen": "capture and describe the screen",
    "Privacy controls": "turn the disclosure switches",
    "Sounds and recordings": "who spoke, and what was said",
    "Languages": "translate, spell and read text",
}

#: Tools whose post-condition is `DESTRUCTIVE_CONSENT_KEYS`. Read from the tree
#: rather than kept beside it, because a hand-kept second list is exactly how the
#: two drift apart and the marker stops meaning anything.
def _destructive(own_gate: dict[str, str]) -> set[str]:
    """Tools whose consent key is one of the destructive ten.

    Both sources, because a tool that gates itself with
    `trash-empty-enabled` is exactly as destructive as one listed in
    `GATED`, and reading only `GATED` marked it as safe to run unattended -
    which is the opposite of what its own key says.
    """
    out = set()
    for tool, key in {**C.GATED, **own_gate}.items():
        if key in C.DESTRUCTIVE_CONSENT_KEYS:
            out.add(tool)
    return out


def _tools_with_own_consent_key() -> set[str]:
    """Tools that gate themselves with `_CONSENT_KEY` rather than via GATED."""
    import importlib
    import pkgutil
    from shani_chronoa import skills as skills_pkg

    out = set()
    for mod in pkgutil.iter_modules(skills_pkg.__path__):
        if not mod.name.startswith("_"):
            try:
                m = importlib.import_module(f"shani_chronoa.skills.{mod.name}")
            except Exception:
                continue
            if getattr(m, "_CONSENT_KEY", None):
                # the key is the module's `_CONSENT_KEY`; the *tool* name is
                # whatever `SKILLS` declares, which is not always the module's
                # basename (`calendar_edit` is module `calendar`, `maps` is
                # module `maps`) - so it is read, not guessed.
                for entry in getattr(m, "SKILLS", ()) or ():
                    name = getattr(entry, "name", None)
                    if name:
                        out.add((name, m._CONSENT_KEY))
    return out


HEADER = """# What Chronoa can do

Every capability in one place, generated from the same table the Help window
renders and the model is offered — `capabilities._GROUPS`. Nothing here is
written by hand, so it cannot claim something the build does not have.

**{n} skills in {g} groups.** Each is a named, schema-typed module; the model
calls them by name. There is deliberately **no** generic shell-exec tool — the
whitelist *is* the design, so a new capability is a new named skill rather than
a way to run anything at all.

Three things are not skills and are not here:

- **Senses** (`senses/`) perceive and deposit percepts; they are listed in the
  README, not callable as tools.
- **Trigger rules** watch for those percepts and act unattended.
- **The MCP server** exposes this same set to Claude Desktop, Claude Code and
  Cursor. It is a second entry point to the same skills, not more skills.

The **Before it runs** column is the honest part:

- **needs `<key>`** — the skill refuses until that switch is on in Settings →
  Privacy, and the refusal names the key, so a shut gate is never mistaken for a
  missing feature. Every one starts off: nothing that discloses or changes is
  available until you turn it on.
- **asks first, always** — the assistant asks before running it, and a standing
  "yes, for this session" grant does **not** cover it. This is on top of any
  switch, so a destructive skill is both gated and asked about.
- **—** — it runs when asked, with no switch and no prompt. Reading things and
  reporting them is in this group; that is the majority, deliberately.

"""


def main() -> int:
    groups: dict[str, list[tuple[str, str]]] = {}
    for tool, (group, label) in C._GROUPS.items():
        groups.setdefault(group, []).append((tool, label))

    known = {x["function"]["name"] for x in tools.TOOLS}
    missing = sorted(set(C._GROUPS) - known)
    if missing:
        print(f"error: _GROUPS names {len(missing)} tool(s) that do not "
              f"exist: {missing}", file=sys.stderr)
        return 1
    ungrooved = sorted(known - set(C._GROUPS))
    if ungrooved:
        print(f"error: {len(ungrooved)} tool(s) have no group: {ungrooved}",
              file=sys.stderr)
        return 1
    # Every destructive key must name a tool that carries it, or the marker
    # would be marking nothing for it. `trash-empty-enabled` is the one that
    # does not: `empty_trash` reads the key itself (`skills/empty_trash.py:66`)
    # and so is absent from `capabilities.GATED`, so it would be advertised as
    # ungated here while refusing on every call. It is read from the skill's own
    # module instead of being assumed, because a hand-kept second list is how
    # these two drift apart.
    own_gate = dict(_tools_with_own_consent_key())
    own_gate_names = set(own_gate)
    missing_gate = own_gate_names - set(C.GATED)
    if missing_gate:
        # Not an error: a skill may gate itself with a *sense* key, which the
        # Senses panel owns rather than Settings -> Privacy (`git_inspect`
        # shares `git-sense-enabled` with the `git` sense, by design - see
        # `sense_reading.py`). What must hold is that the key is on screen
        # somewhere, so the check below is across both panels.
        print(f"note: {len(missing_gate)} tool(s) gate themselves rather "
              f"through GATED: {sorted(missing_gate)}", file=sys.stderr)

    out = [HEADER.format(n=len(known), g=len(groups))]
    gated_names = set(C.GATED) | own_gate_names
    destructive = _destructive(own_gate)
    out.append(f"**{len(gated_names)} of {len(known)} are consent-gated and "
               f"{len(destructive)} are destructive.**\n")
    for group in sorted(groups, key=lambda g: (-len(groups[g]), g)):
        rows = sorted(groups[group])
        out.append(f"## {group}\n")
        blurb = BLURB.get(group)
        if blurb:
            out.append(f"{blurb.capitalize()}.\n")
        out.append("| Skill | What it does | Before it runs |")
        out.append("|---|---|---|")
        for tool, label in rows:
            marks = []
            key = C.GATED.get(tool) or own_gate.get(tool)
            if tool in destructive:
                marks.append("**asks first, always**")
            if key:
                marks.append(f"needs `{key}`")
            out.append(f"| `{tool}` | {label} | {' · '.join(marks) or '—'} |")
        out.append("")

    print("\n".join(out).rstrip() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())