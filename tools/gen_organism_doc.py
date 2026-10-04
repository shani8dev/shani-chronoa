"""Generate Part 5 of ARCHITECTURE-TARGET.md from `organism.py`.

The document and the code have to agree, and a hand-maintained table is a table
that stops agreeing. This prints the markdown; a test asserts the panel and the
inventory still match, which is the part that can be checked automatically.

    python3 tools/gen_organism_doc.py >> ARCHITECTURE-TARGET.md

It is appended once; regenerating means removing the old Part 5 first.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import organism  # noqa: E402

BT = chr(96)
MARK = {organism.BUILT: "built", organism.PART: "part", organism.ABSENT: "absent"}


def main() -> None:
    counts = organism.tally()
    out = [
        "",
        "## Part 3: the anatomy, and which of it you can see",
        "",
        'Part 2 answers "which boxes are done". This part answers a different',
        "question - **what is Chronoa, in the vocabulary of a body** - and the",
        "answer is machine-readable, in `organism.py`, because a table in a",
        "document cannot be kept true and one sitting next to the code can.",
        "",
        f'{counts["total"]} functions: **{counts[organism.BUILT]} built, '
        f'{counts[organism.PART]} part, {counts[organism.ABSENT]} absent.**',
        "",
        "Eight of them have a light in the window's organ strip; the rest have",
        "no indicator, and the Inventory panel says so rather than implying they",
        "are idle. The anatomy here is *standard physiology*, not a poetic gloss:",
        'the useful property of "amygdala" is that it means fast evaluation of',
        "danger, which is exactly what `guardrail.py` and `permissions.py` do.",
        "Where a metaphor would be flattering and wrong, no organ is used at all -",
        "there is no left pinky, because nothing in here has one.",
        "",
        "| System | Organ | What it is for | State | Light | Code |",
        "|---|---|---|---|---|---|",
    ]
    for system, members in organism.by_system():
        for organ in members:
            light = BT + organ.indicator + BT if organ.indicator else "-"
            code = ", ".join(BT + path + BT for path in organ.code[:2])
            note = "" if organ.state == organism.BUILT else " " + organ.missing
            out.append(f"| {system} | {organ.anatomy} | {organ.function}. | "
                       f"{MARK[organ.state]} | {light} | {code} |{note}")

    out += [
        "",
        "### The gaps, in one place",
        "",
        "The two `absent` ones first, because they are the ones worth building:",
        "",
    ]
    for organ in organism.INVENTORY:
        if organ.state == organism.ABSENT:
            out.append(f'- **{organ.anatomy}** - {organ.missing}.')
    out += [
        "",
        "And the nine that are part-built, which is a different problem: each one",
        "works and stops short of something specific.",
        "",
    ]
    for organ in organism.INVENTORY:
        if organ.state == organism.PART:
            out.append(f'- **{organ.anatomy}** - {organ.missing}.')
    out += [
        "",
        "### How to keep this true",
        "",
        f"- `{BT}tests/test_body_and_organs.py::TestTheInventoryIsTrue{BT}` fails if",
        "  an organ names a file that is not in the tree, invents a state, omits",
        "  what is missing, points at a light that does not exist, or gets dropped",
        "  when the panel groups it. It also fails if the inventory stops being",
        "  larger than the strip, which is what would make it redundant.",
        f"- `body.ORGAN_NOUNS` and `body.ORGAN_SOURCE` are *derived* from the",
        "  inventory, so the strip cannot describe itself in words that have",
        "  stopped matching the code lighting it.",
        "- The strip answers **\"is it doing something now\"**. The Inventory panel",
        "  (sidebar, What Chronoa knows, Inventory) answers **\"what is it\"**. Two",
        "  pages on purpose: they disagree whenever something is half-built, and",
        "  that disagreement is the useful part.",
        "",
    ]
    print("\n".join(out))


if __name__ == "__main__":
    main()