"""CLI for the sysadmin tooling that was moved out of Chronoa.

Usage (from a checkout)::

    python3 -m shani_sysadmin list
    python3 -m shani_sysadmin show <skill>
    python3 -m shani_sysadmin run <skill> '{"arg": "value"}'
    python3 -m shani_sysadmin senses
    python3 -m shani_sysadmin check

Nothing here registers skills with Chronoa's assistant or MCP server. That is
the point of the split: these tools are run deliberately by an administrator,
not offered to a model as part of the everyday whitelist.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import bootstrap, discover_senses, discover_skills, run_skill

bootstrap.install()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shani-sysadmin",
        description="Run the sysadmin skills moved out of Chronoa.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list the skills this app provides")
    show = sub.add_parser("show", help="show one skill's schema")
    show.add_argument("skill")
    run = sub.add_parser("run", help="run one skill with a JSON argument object")
    run.add_argument("skill")
    run.add_argument("arguments", nargs="?", default="{}")
    sub.add_parser("senses", help="list the senses this app provides")
    check = sub.add_parser("check", help="load everything and print counts")
    check.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    schemas, handlers = discover_skills()

    if args.command == "list":
        for name in sorted(handlers):
            print(name)
        return 0

    if args.command == "show":
        if args.skill not in handlers:
            print(f"unknown skill: {args.skill}", file=sys.stderr)
            return 2
        schema = next(s for s in schemas if s["function"]["name"] == args.skill)
        print(json.dumps(schema, indent=2, ensure_ascii=False))
        return 0

    if args.command == "run":
        if args.skill not in handlers:
            print(f"unknown skill: {args.skill}", file=sys.stderr)
            return 2
        try:
            arguments = json.loads(args.arguments)
        except json.JSONDecodeError as exc:
            print(f"arguments must be JSON: {exc}", file=sys.stderr)
            return 2
        if not isinstance(arguments, dict):
            print("arguments must be a JSON object", file=sys.stderr)
            return 2
        outcome = run_skill(args.skill, arguments)
        print(outcome.text)
        return 0 if outcome.ran else 1

    if args.command == "senses":
        for name in sorted(discover_senses()):
            print(name)
        return 0

    if args.command == "check":
        senses = discover_senses()
        report = {
            "skills": sorted(handlers),
            "skill_count": len(handlers),
            "senses": sorted(senses),
            "sense_count": len(senses),
        }
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"{report['skill_count']} skills, {report['sense_count']} senses")
            for name in report["skills"]:
                print(f"  {name}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
