"""Fill in an eval case for every skill that has none.

The benchmark is the instrument for the project's stated goal - "the harness
should be good enough that the smallest model beats bigger ones" - and it
covered 55 of 200 skills for most of its life. Nothing in the tree could have
said so: the suite proves every skill is reachable and that every skill
dispatches, and neither of those asks whether the model *chooses* it. The gap
was recorded in `eval_cases.json` and asserted by `tests/test_task_eval.py`, so
it was visible and still sat there.

**Hand-writing 145 more cases is where mistakes would hide**, so they are
generated from the same table the rest of the documents come from -
`capabilities._GROUPS` for the wording and each skill's own schema for the
arguments. Two rules follow from what the scorer can actually judge:

- **A generated case never asserts a value the model could not derive from the
  request.** A case saying `set_volume(percent=40)` with a request that does
  not mention 40 asserts a coincidence, and would report a miss for a model
  that behaved correctly. So numbers and enums are *put into the request*, and
  a string argument is scored presence-only (`"*"`), which is the scorer's own
  rule for "there is an argument here and it is the right one".
- **Existing cases are never touched.** Anything with a hand-written case keeps
  it, and the generator only fills gaps. Deleting a hand-written case to
  replace it with a generated one would trade a real transcript for a plausible
  sentence, which is the opposite of what the eval is for.

Run it to fill gaps, or `--check` to report what is missing without writing:

    python3 tools/gen_eval_cases.py            # write the missing cases
    python3 tools/gen_eval_cases.py --check    # report coverage, change nothing

Every case it writes carries a `note` saying it is generated, so a reader can
tell a transcript from a starting point and improve the wording. That is the
honest way to ship coverage: measured everywhere, deep where it matters.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import capabilities as C  # noqa: E402
from shani_chronoa import tools as T  # noqa: E402

CASES = _ROOT / "tools" / "eval_cases.json"

def _schema_for(name: str) -> dict:
    for entry in T.TOOLS:
        if entry["function"]["name"] == name:
            return entry["function"].get("parameters") or {}
    return {}


def _request_phrase(name: str) -> str:
    """A sentence a person would plausibly say, from the skill's own label.

    `capabilities._EXAMPLES` holds a hand-written one-click suggestion for some
    skills and is preferred; otherwise the group label is turned into a request,
    which is why the labels were written as what the skill *does* rather than
    what it is called.
    """
    example = C._EXAMPLES.get(name)
    if example:
        return example
    label = C._GROUPS.get(name, ("", name))[1]
    label = label.rstrip(".")
    if not label:
        label = name.replace("_", " ")
    label = label[0].upper() + label[1:]
    return f"{label}."


def _arguments(name: str) -> dict:
    """Required arguments, scored the only way a generated case honestly can.

    Every key the schema marks required is present, because "it did not pass
    the argument" is a real failure worth catching. **No value is invented.**

    The first version of this appended the enum value or a number to the
    request as a parenthetical - and produced a case that read *"Turn on the
    screen reader"* while asserting `feature=high_contrast`. A hand-written
    `_EXAMPLES` entry and a schema's first enum value have no reason to agree,
    so those cases could only ever report a miss for a model that behaved
    correctly. **A case that cannot pass teaches the benchmark nothing and
    buries the signal it was added to find.**

    So every argument is scored presence-only, which is the scorer's own `"*"`
    rule: there is an argument here and it is the right one. An array is the
    one exception - `"*"` requires a non-empty value, so an empty array would
    fail a rule meant to say "an argument was passed".

    The cost is real and worth stating: a generated case cannot tell
    `cleanup_apply(cache)` from `cleanup_apply(flatpak)`. The hand-written
    cases can, and they are the ones that discriminate; the generated ones
    assert tool selection, which is what this benchmark measures.
    """
    params = _schema_for(name)
    args: dict = {}
    for key in params.get("required") or ():
        spec = (params.get("properties") or {}).get(key) or {}
        args[key] = ["*"] if spec.get("type") == "array" else "*"
    return args


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def main(argv: list[str]) -> int:
    check_only = "--check" in argv
    data = json.loads(CASES.read_text())
    covered = {want["tool"] for case in data["cases"] for want in case.get("expect", [])}
    names = sorted(entry["function"]["name"] for entry in T.TOOLS)

    added = []
    for name in names:
        if name in covered:
            continue
        phrase = _request_phrase(name)
        args = _arguments(name)
        added.append({
            "id": f"generated-{_slug(name)}",
            "say": phrase,
            "note": ("generated from the skill's schema and its capability label by "
                     "tools/gen_eval_cases.py - the wording is a starting point, "
                     "not a transcript; improve it here"),
            "expect": [{"tool": name, **({"args": args} if args else {})}],
        })

    if check_only:
        print(f"skills: {len(names)} | with a case: {len(covered)} | missing: {len(added)}")
        for case in added:
            print(f"   {case['expect'][0]['tool']}")
        return 0

    if added:
        data["cases"].extend(added)
        data["coverage"]["cases"] = len(data["cases"])
        data["coverage"]["skills_asserted"] = len(names)
        data["coverage"]["skills_total"] = len(names)
        text = re.sub(r"COVERAGE IS PARTIAL.*$", (
            "COVERAGE IS COMPLETE: every skill now has at least one case, so a "
            "regression in tool selection is visible anywhere in the list. The "
            "wording of the generated cases is a starting point rather than a "
            "transcript - each says so in its own `note` - so the depth is "
            "uneven on purpose: the hand-written cases are transcripts, and the "
            "generated ones assert the tool and the presence of its required "
            "arguments. `tools/gen_eval_cases.py --check` reports what is "
            "missing and `tests/test_task_eval.py` asserts the counts against "
            "this file."), data["about"], flags=re.S)
        data["about"] = text
        CASES.write_text(json.dumps(data, indent=2) + "\n")
    print(f"cases: {len(data['cases'])} | skills covered: {len(covered) + len(added)} of {len(names)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))