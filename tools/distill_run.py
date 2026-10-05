#!/usr/bin/env python3
"""Distil the local model into a routing student, and report what it learned.

Run against a real model, not a stub: the whole question is whether a teacher's
*decisions* carry more than its words do, and a stub cannot answer that. The
runner exists because the measurement has to be repeatable off a session - it is
what `shani-testbed`'s `slot-tests/chronoa-distill.sh` runs.

It prints a JSON summary and, with `--markdown`, a file a person can read. Every
number in that summary is measured here and now; nothing is carried over.

    tools/distill_run.py --cases=tools/route_cases.json --json=out.json
    tools/distill_run.py --cases=tools/eval_cases.json    # the smoke-test shape

Two runs of the same command are not comparable: the first writes a student and
the second finds one already there, so `--fresh` is how you ask the question
twice. That flag exists because "it did not learn anything" and "it already
knew" look identical from the outside otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

def _find_package() -> str:
    """Where `shani_chronoa` actually is, from wherever this script was run.

    Two real locations, not one: the checkout's own `usr/lib/shani-chronoa`,
    and the installed `/usr/lib/shani-chronoa` that the Chronoa source overlay
    creates in a slot. A first version computed the checkout-relative path only,
    so in a slot - the one place this is worth running - it could not import the
    package it was measuring.
    """
    here = Path(__file__).resolve()
    for candidate in (here.parents[1] / "usr/lib/shani-chronoa",
                      Path("/usr/lib/shani-chronoa")):
        if (candidate / "shani_chronoa" / "__init__.py").exists():
            return str(candidate)
    return str(here.parents[1] / "usr/lib/shani-chronoa")


sys.path.insert(0, _find_package())

from shani_chronoa import distill, learning, tool_select  # noqa: E402


def _clear_students() -> None:
    """Remove any student, so this run measures training and not a previous file."""
    for path in learning.models_dir().glob("router-*.json"):
        try:
            path.unlink()
            print(f"removed the previous student {path.name}", file=sys.stderr)
        except OSError as exc:
            print(f"could not remove {path.name}: {exc}", file=sys.stderr)


async def _ask_teacher(report) -> str:
    """Which model this actually used, or the reason there is none."""
    return await distill.resolve().ask(report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default="tools/route_cases.json")
    parser.add_argument("--teacher", default="", help="teacher id, e.g. llama.cpp")
    parser.add_argument("--json", default="")
    parser.add_argument("--markdown", default="")
    parser.add_argument("--fresh", action="store_true",
                        help="delete any existing student first, so the run measures training")
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args()

    if args.fresh:
        _clear_students()

    cases = distill.load_cases(Path(args.cases))
    summary = {
        "cases_file": args.cases,
        "cases": len(cases),
        "skills": len({n for c in cases for n in c.expect}),
        "teachers": [{"id": t.id, "label": t.label, "on_this_machine": t.on_this_machine}
                     for t in distill.available_teachers()],
        "notes": distill.teacher_notes(),
        "fresh": bool(args.fresh),
    }
    if not cases:
        summary["error"] = f"no cases at {args.cases}"
        print(json.dumps(summary, indent=1))
        return 1

    started = time.monotonic()

    async def run() -> dict:
        report = await asyncio.wait_for(
            distill.distil(cases, teacher_id=args.teacher or None),
            timeout=args.timeout)
        return report

    try:
        report = asyncio.run(run())
    except Exception as exc:  # noqa: BLE001 - the report carries the failure
        summary["error"] = f"{type(exc).__name__}: {exc}"
        print(json.dumps(summary, indent=1))
        return 1
    summary["seconds"] = round(time.monotonic() - started, 1)
    summary["teacher"] = report.get("teacher")
    summary["agreed"] = report.get("agreed")
    summary["agreement"] = report.get("agreement")
    summary["disagreements"] = report.get("disagreements")
    summary["student"] = report.get("student")

    student = report.get("student") if isinstance(report.get("student"), dict) else {}
    summary["student_written"] = bool(student.get("saved"))

    if student.get("saved"):
        router = distill.load_router()
        summary["student_loads_back"] = router is not None
        if router is not None:
            summary["student_classes"] = router.classes
            held = [(c.say, c.expect[0]) for c in cases]
            correct = sum(1 for say, want in held
                          if router.score(say) and router.score(say)[0][0] == want)
            summary["student_right_on_all_cases"] = correct
            summary["student_all_cases"] = len(held)
            # And through the path the product actually uses, not through the
            # model object: a student the selector cannot see is the outcome
            # model's bug all over again.
            tool_select._ROUTER, tool_select._ROUTER_TRIED = None, False
            seen = tool_select.distilled_router()
            summary["selector_sees_it"] = seen is not None
            summary["selector_picks"] = tool_select.distilled(
                "what time is it", [{"function": {"name": n, "description": ""}}
                                    for n in router.classes])

    print(json.dumps(summary, indent=1, default=str))
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(summary, indent=1, default=str),
                                   encoding="utf-8")
    if args.markdown:
        lines = [f"# Distillation: {summary['teacher']}", "",
                 f"- cases: {summary['cases']} across {summary['skills']} skills",
                 f"- agreed with the human label: {summary['agreed']}"
                 f" ({summary['agreement']:.0%})",
                 f"- seconds: {summary.get('seconds')}"]
        if summary["student_written"]:
            provenance = student.get("provenance", {})
            lines += [f"- student written: {provenance.get('skills')} skills, "
                      f"{provenance.get('accuracy'):.0%} against a "
                      f"{provenance.get('baseline'):.0%} baseline on "
                      f"{provenance.get('held_out')} held-out requests",
                      f"- right on {summary.get('student_right_on_all_cases')}"
                      f"/{summary.get('student_all_cases')} of the cases",
                      f"- the selector sees it: {summary.get('selector_sees_it')}",
                      f"- and it picks: {summary.get('selector_picks')}"]
        else:
            lines.append(f"- no student: {student.get('reason')}")
        for item in (summary.get("disagreements") or [])[:20]:
            lines.append(f"  - {item['say']}: {item['reason']}")
        Path(args.markdown).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())