"""Does the README still describe the tree?

Every count in it is read out of the code rather than trusted, so the file
cannot drift into looking authoritative while being wrong.

Every number in the README's prose and in its table is read out of the
code rather than trusted: the sense names from `discover_senses()`, the
defaults from the compiled gschema's own `default` attributes, and the
skill count from `tools.TOOLS`. A documentation table that has drifted is
worse than none, because it is read as a check on the code.

This started as a throwaway probe and earned its place: the README claimed
181 skills when the tree had 200, listed 44 of 49 senses, said "6 event
types" against `triggers.EVENT_TYPES`'s 19, and reported an on/off split
that did not sum to its own total. All four were the same failure - a
number typed once and never re-derived.

Not a test file in the strict sense: it reads the repository, so it is a
checker rather than a unit test. Kept here because this repo's rule is
that a claim nobody re-checks rots.

Run: python3 tests/check_readme_numbers.py
"""

import pathlib
import re
import sys
import xml.etree.ElementTree as ET

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "usr/lib/shani-chronoa"))

from shani_chronoa import senses, tools  # noqa: E402

readme = (REPO / "README.md").read_text()
problems = []


def sense_defaults():
    root = ET.parse(REPO / "usr/share/glib-2.0/schemas/"
                    "org.shani.chronoa.gschema.xml").getroot()
    out = {}
    for key in root.iter("key"):
        name = key.get("name") or ""
        if name.endswith("-sense-enabled"):
            default = key.find("default")
            out[name[: -len("-sense-enabled")]] = (
                (default.text or "").strip().lower() if default is not None else "?")
    return out


# --- the skills count ------------------------------------------------------
discovered = senses.discover_senses()
defaults = sense_defaults()
n_skills = len(tools.TOOLS)

claimed = re.search(r"\*\*(\d+) callable\s*\n?\s*skills\*\*", readme)
if not claimed:
    problems.append("the 'callable skills' count is no longer findable in the prose")
elif int(claimed.group(1)) != n_skills:
    problems.append(f"prose says {claimed.group(1)} skills; tools.TOOLS has {n_skills}")

# --- the senses count and its on/off split ---------------------------------
m = re.search(r"\*\*(\d+) senses\*\*.*?\*\*(\d+) default on, (\d+) default\s*\n?\s*off\*\*",
              readme, re.S)
if not m:
    problems.append("the senses count / split is no longer findable in the prose")
else:
    total, on, off = (int(g) for g in m.groups())
    if total != len(discovered):
        problems.append(f"prose says {total} senses; discover_senses() finds {len(discovered)}")
    if on + off != total:
        problems.append(f"prose says {on} on / {off} off, which does not sum to {total}")
    # Against the schema, scoped the way the prose scopes it: the 49 that are
    # modules. The other keys are event types and retired aliases, and the
    # README says so - this checks the scoped count, not all 74.
    on_keys = sum(1 for n, v in defaults.items()
                  if v == "true" and n in discovered)
    off_keys = sum(1 for n, v in defaults.items()
                   if v == "false" and n in discovered)
    if (on, off) != (on_keys, off_keys):
        problems.append(
            f"prose says {on} on / {off} off; the schema says {on_keys} on / "
            f"{off_keys} off for the {len(discovered)} senses that are modules")
    # The extras must still be accounted for, or the count is unexplained.
    extras = sorted(set(defaults) - set(discovered))
    if len(defaults) != len(discovered) and f"**{len(defaults)}**" not in readme:
        problems.append(
            f"the schema has {len(defaults)} sense keys but the prose does not "
            f"mention that number, so the {len(extras)} extras are unexplained")

# --- the table -------------------------------------------------------------
rows = {name: state for name, state in re.findall(
    r"^\| `([a-z0-9-]+)` \| [^|]+ \| (on|off) \|$", readme, re.M)}

missing = sorted(set(discovered) - set(rows))
extra = sorted(set(rows) - set(discovered))
if missing:
    problems.append(f"{len(missing)} sense(s) in the tree are absent from the table: "
                    f"{missing}")
if extra:
    problems.append(f"{len(extra)} table row(s) name no sense module: {extra}")

wrong = []
for name, state in sorted(rows.items()):
    want = "on" if defaults.get(name) == "true" else "off"
    if name in discovered and state != want:
        wrong.append(f"{name}: README says {state}, schema says {want}")
if wrong:
    problems.append(f"{len(wrong)} row(s) disagree with the schema default: {wrong}")

# --- the event-type list ---------------------------------------------------
from shani_chronoa.triggers import common  # noqa: E402

events = sorted(common.EVENT_TYPES)
m = re.search(r"\*\*(\d+) event types\*\*", readme)
if not m:
    problems.append("the event-type count is no longer findable in the prose")
elif int(m.group(1)) != len(events):
    problems.append(f"prose says {m.group(1)} event types; "
                    f"triggers.EVENT_TYPES has {len(events)}")
named = set(re.findall(r"`([a-z]+)`", readme))
unnamed = [e for e in events if e not in named]
if unnamed:
    problems.append(f"{len(unnamed)} event type(s) are not named in the README: "
                    f"{unnamed}")

# --- summary ---------------------------------------------------------------
on_rows = sum(1 for v in rows.values() if v == "on")
print(f"senses discovered : {len(discovered)}")
print(f"table rows        : {len(rows)}  ({on_rows} on, {len(rows)-on_rows} off)")
print(f"tools.TOOLS       : {n_skills}")
print()
if problems:
    print(f"PROBLEMS ({len(problems)}):")
    for p in problems:
        print(f"  - {p}")
    sys.exit(1)
print("README matches the tree: skill count, sense count, every row's "
      "default against the schema, and the event-type list.")