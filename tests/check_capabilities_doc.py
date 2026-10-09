"""CAPABILITIES.md is generated, and this is what stops it going stale.

`tools/gen_capabilities.py` writes the document from `capabilities._GROUPS`
and the consent tables beside it, so the file *should* never drift. This
checks the other half: that the regenerated text actually is the file on
disk, and that the properties the prose claims are still the tree's.

It reads the repository rather than importing behaviour, so it is a
checker rather than a unit test - kept beside the generator it guards.

Run: python3 tests/check_capabilities_doc.py
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "usr/lib/shani-chronoa"))
sys.path.insert(0, str(REPO / "tools"))

from shani_chronoa import capabilities as C  # noqa: E402
from shani_chronoa import tools  # noqa: E402

DOC = REPO / "CAPABILITIES.md"
problems: list[str] = []


def regenerate() -> str:
    proc = subprocess.run(
        [sys.executable, str(REPO / "tools/gen_capabilities.py")],
        capture_output=True, text=True, cwd=REPO,
        env={**dict(__import__("os").environ), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if proc.returncode != 0:
        problems.append(f"the generator failed: {proc.stderr.strip()[:400]}")
        return ""
    return proc.stdout


def main() -> int:
    if not DOC.exists():
        print("CAPABILITIES.md is missing; run tools/gen_capabilities.py > CAPABILITIES.md")
        return 1

    fresh = regenerate()
    on_disk = DOC.read_text()

    if fresh and fresh.strip() != on_disk.strip():
        problems.append(
            "CAPABILITIES.md differs from what the generator produces - "
            "regenerate it, or the two are describing different trees")

    # --- the properties the prose claims -----------------------------------
    known = {x["function"]["name"] for x in tools.TOOLS}

    m = re.search(r"\*\*(\d+) skills in (\d+) groups\.\*\*", on_disk)
    if not m:
        problems.append("the skills/groups sentence is missing from the header")
    else:
        claimed_n, claimed_g = int(m.group(1)), int(m.group(2))
        actual_g = len({g for g, _ in C._GROUPS.values()})
        if claimed_n != len(known):
            problems.append(f"header says {claimed_n} skills; tools.TOOLS has {len(known)}")
        if claimed_g != actual_g:
            problems.append(f"header says {claimed_g} groups; _GROUPS has {actual_g}")

    # --- every skill appears exactly once, and nothing else does -------------
    rows = re.findall(r"^\| `([a-z0-9_]+)` \|", on_disk, re.M)
    missing = sorted(known - set(rows))
    extra = sorted(set(rows) - known)
    duplicated = sorted({r for r in rows if rows.count(r) > 1})
    if missing:
        problems.append(f"{len(missing)} skill(s) are not advertised: {missing}")
    if extra:
        problems.append(f"{len(extra)} advertised name(s) are not real skills: {extra}")
    if duplicated:
        problems.append(f"advertised more than once: {duplicated}")

    # --- the marks ----------------------------------------------------------
    gated_rows = {r for r in re.findall(r"^\| `([a-z0-9_]+)` \|[^|]*\|[^|]*needs `", on_disk, re.M)}
    import gen_capabilities as G
    own = dict(G._tools_with_own_consent_key())
    # A skill that gates only *part* of itself still needs a `needs \`key\``
    # mark, so it is read from the same helper the generator reads - the
    # alternative is a second list here, which is how this file's own subject
    # (a hand-kept table drifting from the tree) happens in the first place.
    partial = G._tools_with_partial_gate()
    # a destructive row must also carry its switch, not only the "asks" mark
    should_be_gated = set(C.GATED) | set(own) | set(partial)
    if gated_rows != should_be_gated:
        wrong = sorted(should_be_gated - gated_rows)
        extra_marks = sorted(gated_rows - should_be_gated)
        if wrong:
            problems.append(f"{len(wrong)} gated skill(s) not marked: {wrong}")
        if extra_marks:
            problems.append(f"{len(extra_marks)} skill(s) marked gated but are not: {extra_marks}")

    destructive = {r for r in re.findall(
        r"^\| `([a-z0-9_]+)` \|[^|]*\|[^|]*asks first, always", on_disk, re.M)}
    should_be_destr = {t for t in should_be_gated
                       if (C.GATED.get(t) or own.get(t)) in C.DESTRUCTIVE_CONSENT_KEYS}
    both = {r for r in re.findall(
        r"^\| `([a-z0-9_]+)` \|[^|]*\|[^|]*asks first, always[^|]*needs `", on_disk, re.M)}
    if both != should_be_destr:
        problems.append(
            f"a destructive skill must also name its switch; {len(should_be_destr - both)} "
            f"do not: {sorted(should_be_destr - both)}")
    if destructive != should_be_destr:
        wrong = sorted(should_be_destr - destructive)
        if wrong:
            problems.append(f"{len(wrong)} destructive skill(s) not marked: {wrong}")

    if problems:
        print(f"PROBLEMS ({len(problems)}):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"CAPABILITIES.md matches the tree: {len(rows)} skills, "
          f"{len(gated_rows)} gated, {len(destructive)} destructive, and it is "
          f"byte-identical to what the generator produces.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())