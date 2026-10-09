"""Skill: what the keyboard shortcuts on this machine actually are.

There was no way to ask. Among 204 skills, none read a keybinding: "what does
Super+Tab do", "what is F4 bound to", "list my shortcuts" all had no answer,
on a machine where the answer is sitting in GSettings in two schemas and is
fully readable by the person who owns it.

It is a **read**, and it is ungated by the same precedent as `list_windows` and
`skill_machine`: reading a preference the user set themselves discloses nothing
nobody already has, and a permission prompt on "what is my keyboard doing" is
friction with nothing behind it.

**The schema's own description is what makes the answer useful**, so it is read
rather than paraphrased: `switch-applications` is `['<Super>Tab']`, which tells
you a key and nothing else, while its description says it *"Switch between
applications of all windows"*. A list of bindings without that is a list of
accords, and the person asking does not know what the switch is *to*.

**Two schemas, and the second is easy to forget.** `org.gnome.desktop.wm.keybindings`
covers windows and workspaces; `org.gnome.settings-daemon.plugins.media-keys`
covers the hardware keys - volume, play/pause, brightness, the custom
launchers. A shortcut list missing the second half is missing the keys a person
is most likely to press by accident.

**Not GNOME is said, not guessed.** The keybinding schemas are GNOME's, so on
Plasma this reports that the scheme does not apply rather than listing GNOME's
defaults on a machine that ignores them - which would be a confident answer to a
different question, the failure this package records for a sense that finds no
camera and calls it absent.

**A shortcut whose schema has no description is still listed**, with the binding
and no gloss, because "bound to something, and I do not know what" is true and
useful; dropping it would under-report the machine's actual state.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
from typing import List, Optional

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402

from shani_chronoa import files
from shani_chronoa.skills import Skill

_TIMEOUT = 10

#: GNOME's two keybinding schemas. The second is `settings-daemon`, not `wm`,
#: and a list without it is missing every hardware key.
#: `id(schema) -> {key name: description}`, built on first use. See
#: `_summary_for` for why it is cached and why it is keyed the way it is.
_SUMMARY_CACHE: dict = {}

_SCHEMAS = (
    "org.gnome.desktop.wm.keybindings",
    "org.gnome.settings-daemon.plugins.media-keys",
)

#: A GSettings accelerator: `<Super>Tab`, `['<Super><Shift>Escape']`,
#: `['XF86AudioPlay']`, `['<Primary>F2']`. Bound with `+` between modifiers.
_ACCEL = re.compile(r"^<?([A-Za-z0-9]+)>?$")


def _gsettings(*args: str) -> Optional[str]:
    """`gsettings get`, or None when gsettings is absent or refused."""
    if shutil.which("gsettings") is None:
        return None
    try:
        proc = subprocess.run(["gsettings", *args], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False)
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _accels(raw: str) -> List[str]:
    """The bindings in one `gsettings get` answer.

    `['<Super>Tab']`, `'@as []'`, `'[]'`. **Parsed as a literal, not split on
    commas.** The GSettings format permits a quoted accelerator to contain one
    (`['XF86Foo,bar']` is two characters of key name in one element), and
    splitting on `,` turns that into two bindings that are not there.

    Measured, and worth stating because the choice looks like a non-choice: **no
    accelerator on this machine contains a comma** - every comma in the real
    listing separates two bindings - so on today's GNOME data both parsers
    agree. `ast.literal_eval` is what the format actually is, and
    `'<Alt>XF86AudioLowerVolume', '<Alt><Ctrl>XF86AudioLowerVolume'` under
    either one yields the same two rows.

    The bug that *did* report 120 shortcuts where about half that many are bound,
    and answered "what does Super+Tab do" with `Tab`, was **not** this split -
    it was the listing parser's field indices, below. Two different mistakes, and
    a comment that credited the wrong one would have misdirected the next fix.

    `[]` is unbound and yields nothing, which is not the same as a parse failure
    - an empty list is a real answer about one key.
    """
    text = (raw or "").strip()
    if text.startswith("@as"):
        text = text[3:].strip()
    if not (text.startswith("[") and text.endswith("]")):
        return []
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        # Unparseable is [] rather than an exception: one odd value must not
        # cost the other hundred bindings.
        return []
    return [str(item) for item in value if isinstance(item, str) and item.strip()]


def _readable(binding: str) -> str:
    """`<Super><Shift>Escape` into something a person can press.

    A GSettings accelerator is written with angle brackets and **no separators**,
    which is precise and unreadable - and my first version left it as bare
    `Tab`, so "what does Super+Tab do" was answered with "Tab".

    Each modifier expands to its own name **with the separator attached**, and
    the trailing one is stripped: `<Super><Shift>Escape` -> `Super+Shift+Escape`.

    **The names are GSettings' own spellings** (`<Super>`, `<Primary>`,
    `<Control>`, **`<Ctrl>`**, `<Alt>`, `<Shift>`), not a lower-cased guess and
    not one spelling per modifier. The first version lower-cased them and
    matched nothing, so every accelerator came back with its brackets stripped
    and no separator at all - `SuperTab`, `AltF4` - which is not a key anybody
    can press and reads like working code; adding `<Control>` without `<Ctrl>`
    then left `Alt+CtrlXF86AudioLowerVolume`, a real binding this machine has
    and no key anyone could press.

    `Above_Tab` is left alone deliberately. It is a real legacy GTK accelerator
    name (the `<Shift>Tab` spelling), it carries no angle brackets, and rewriting
    it into something else would be inventing a binding this machine does not
    have. A key it cannot interpret is reported as it is.
    """
    out = binding
    for tag, word in (("<Primary>", "Ctrl+"), ("<Control>", "Ctrl+"),
                      ("<Ctrl>", "Ctrl+"),
                      ("<Super>", "Super+"), ("<Shift>", "Shift+"),
                      ("<Alt>", "Alt+"), ("<Meta>", "Meta+"),
                      ("<Hyper>", "Hyper+")):
        out = out.replace(tag, word)
    # Anything left in angle brackets is a key name, not a modifier (XF86AudioPlay,
    # Page_Up): keep the word, drop the brackets.
    out = re.sub(r"<([^>]*)>", r"\1", out)
    out = re.sub(r"\++", "+", out).strip("+")
    return out


def _bindings() -> Optional[List[dict]]:
    """Every bound shortcut, or None when neither schema is readable.

    **None is not `[]`.** No readable schema means this is not GNOME - the
    keybinding schemes are GNOME's, and Plasma keeps its own in
    `kglobalshortcutsrc`. `[]` would be a real answer about GNOME ("nothing is
    bound"), so the two are kept apart and the caller refuses to answer rather
    than describing a desktop that is not running.
    """
    rows: List[dict] = []
    seen_any_schema = False
    for schema in _SCHEMAS:
        listing = _gsettings("list-recursively", schema)
        if listing is None:
            continue
        seen_any_schema = True
        for line in listing.splitlines():
            # `gsettings list-recursively` prints, with **no `=` anywhere**:
            #     org.gnome.desktop.wm.keybindings switch-applications ['<Super>Tab']
            # schema, key, value - three fields, and the value may contain
            # spaces. Measured with `od -c` after three wrong parsers: the
            # first read field 0 as the key (it is the schema), the second
            # partitioned on `=` that is not in the output at all, and both
            # returned zero shortcuts while reading like working code.
            fields = line.split(None, 2)
            if len(fields) != 3 or fields[0] != schema:
                continue
            for accel in _accels(fields[2]):
                rows.append({"schema": schema, "key": fields[1], "accel": accel})
    return rows if seen_any_schema else None


def _summary_for(row: dict) -> str:
    """The schema's own description of this key, or "" if it has none.

    **Read from the GSettings schema through `Gio`, not from `gsettings range`** -
    `range` prints the *type* (`type as` for an accelerator array), which is not
    a description. A hand-kept table of ~150 GNOME descriptions would be a table
    that goes stale on the next release, and this is the part that makes the
    answer worth anything: `['<Super>Tab']` tells you a key and nothing else.

    **Cached per schema, not per row.** This is called once per bound shortcut
    and there are ~120 of those, and a schema lookup is not free; the cache is a
    plain dict on the schema *object's identity* rather than the schema id, so
    two rows sharing a schema do the work once.
    """
    try:
        source = Gio.SettingsSchemaSource.get_default()
        schema = source.lookup(row["schema"], True) if source else None
        if schema is None:
            return ""
        if id(schema) not in _SUMMARY_CACHE:
            # `list_keys()` returns key *names* (strings), not key objects, so
            # each still has to be fetched with `get_key`. My first version
            # called `.get_name()` on the string instead, which raised
            # `AttributeError` inside the `except` below and returned "" for
            # all 120 rows - a bare `except` turning a type error into "no
            # schema has a description", which is what made this file look
            # like it had no glosses when the schema has them all along.
            _SUMMARY_CACHE[id(schema)] = {
                name: (key.get_description() or key.get_summary() or "")
                for name in schema.list_keys()
                for key in (schema.get_key(name),)
            }
        text = _SUMMARY_CACHE[id(schema)].get(row["key"], "")
    except Exception:  # noqa: BLE001 - a schema that cannot be read is no text
        return ""
    return " ".join(text.split()).split(". ")[0][:110]


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "list").strip().lower()
    if action not in ("list", "find"):
        return f"Action must be list or find, not {action!r}."

    rows = _bindings()
    if rows is None:
        return (
            "Could not read this machine's keyboard shortcuts. "
            + (files.tool_missing("gsettings", "read your keyboard shortcuts")
               if shutil.which("gsettings") is None else
               "GNOME's keybinding schemas were not readable, so these are "
               "probably KDE's - Plasma keeps its own in kglobalshortcutsrc and "
               "System Settings, and listing GNOME's defaults on this machine "
               "would describe a desktop it is not running.")
            + " Nothing was guessed.")

    if action == "find":
        wanted = (arguments.get("keys") or "").strip()
        if not wanted:
            return ("Name the key to look up, e.g. 'Super Tab' or F4.")
        flat = wanted.replace("-", "").replace("+", "").replace(" ", "").lower()
        hits = []
        for row in rows:
            accel = _readable(row["accel"]).replace("+", "").replace(" ", "").lower()
            if flat and flat in accel or flat in row["accel"].replace(">", "").lower():
                hits.append(row)
        if not hits:
            return (f"Nothing is bound to {wanted!r} in GNOME's keybinding "
                    f"schemas. {len(rows)} other shortcut(s) are bound on this "
                    f"machine - ask for the list to see them.")
        return "\n".join(
            f"{_readable(h['accel'])} — {_summary_for(h) or h['key']}  "
            f"({h['schema'].rsplit('.', 1)[-1]}: {h['key']})" for h in hits[:8])

    if not rows:
        return "No keyboard shortcuts are bound in GNOME's keybinding schemas."
    lines = [f"{len(rows)} keyboard shortcut(s) are bound on this machine:"]
    for row in sorted(rows, key=lambda r: _readable(r["accel"]).lower()):
        lines.append(f"  {_readable(row['accel']):22} {_summary_for(row) or row['key']}")
    lines.append("Ask with action=find and the key, to answer 'what does this do'.")
    return "\n".join(lines)


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_shortcuts",
        "description": (
            "List this machine's keyboard shortcuts, or find what one key does. "
            "Reads GNOME's two keybinding schemas, so it covers both window "
            "management and the hardware media keys, and reports that the scheme "
            "does not apply on a desktop that does not use it rather than "
            "listing defaults for a desktop that is not running. Reading a "
            "shortcut is not a change and needs no permission."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "find"],
                    "description": "'list' every bound shortcut; 'find' what one key does.",
                },
                "keys": {
                    "type": "string",
                    "description": "For 'find': the key to look up, e.g. 'Super Tab' or F4.",
                },
            },
            "required": ["action"],
        },
    },
}

SKILLS = [Skill(name="list_shortcuts", schema=_SCHEMA, run=_run)]
