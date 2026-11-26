"""The `chronoa` command: parse arguments, then hand over to the client.

Every additive Agent method has a subcommand here, built from the same table
the terminal client reads. The only logic in this file is what a command line
must decide: confirm a consent write with ``--yes``, and read a secret from
stdin instead of argv.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from shani_chronoa.agent_capabilities import METHODS
from shani_chronoa.tui import display
from shani_chronoa.tui.agent import AgentClient, AgentError
from shani_chronoa.tui.app import Interactive, Session
from shani_chronoa.tui.noninteractive import (
    deny_label,
    emit_json,
    emit_reply,
    exit_code,
    refusal_notice,
)
from shani_chronoa.tui.render import Renderer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chronoa",
        description="Chronoa's terminal client: a plain client of the engine.",
    )
    parser.add_argument(
        "-p",
        "--print",
        dest="question",
        metavar="QUESTION",
        help="answer one question and exit; '-' reads it from stdin",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print one JSON line instead of plain words",
    )
    parser.add_argument(
        "--allow", metavar="LABEL", help="with -p, approve with this exact option label"
    )
    parser.add_argument(
        "--yes", action="store_true", help="confirm a consent or privacy write"
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="read a setting value or email details from stdin",
    )
    parser.add_argument(
        "--no-color", action="store_true", help="never emit ANSI colour"
    )

    subparsers = parser.add_subparsers(dest="command")

    def add_leaf(group, method, path):
        leaf = group.add_parser(path[-1], help=method.help)
        for name in method.arg_names:
            if name == "confirm":
                continue  # the flag is --yes, not a positional argument
            leaf.add_argument(name)
        # SUPPRESS: a --json/--yes given before the subcommand still counts.
        leaf.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        leaf.add_argument("--yes", action="store_true", default=argparse.SUPPRESS)
        leaf.add_argument("--stdin", action="store_true", default=argparse.SUPPRESS)
        leaf.set_defaults(method=method.name)

    groups, children = {}, {}
    nested = {m.cli[0] for m in METHODS.values() if len(m.cli) > 1}
    for method in METHODS.values():
        path = method.cli
        head = path[0]
        if head not in groups:
            groups[head] = subparsers.add_parser(head, help=f"{head} capabilities")
            groups[head].set_defaults(method=method.name if len(path) == 1 else None)
            if head in nested:
                children[head] = groups[head].add_subparsers(dest=f"{head}_action")
        if len(path) == 1:
            groups[head].set_defaults(method=method.name)
            groups[head].add_argument(
                "--json", action="store_true", default=argparse.SUPPRESS
            )
            groups[head].add_argument(
                "--yes", action="store_true", default=argparse.SUPPRESS
            )
            groups[head].add_argument(
                "--stdin", action="store_true", default=argparse.SUPPRESS
            )
        else:
            add_leaf(children[head], method, path)
    return parser


def refusal_responder(event: dict, allow):
    """In -p mode an approval is refused unless the exact label was allowed."""
    options = [str(option) for option in (event.get("options") or [])]
    if allow not in options:
        refusal_notice(event, allow, stream=sys.stderr)
    return deny_label(event, allow)


def run_printed(client: AgentClient, question: str, *, jsonl: bool, allow=None) -> int:
    emit = emit_json if jsonl else emit_reply
    session = Session(
        client,
        lambda event: emit(event, sys.stdout),
        lambda event: refusal_responder(event, allow),
    )
    result = session.run_turn(question)
    if not jsonl and result["reply"]:
        print(file=sys.stdout, flush=True)
    if result["error"]:
        print(f"error: {result['error']}", file=sys.stderr)
    return exit_code(result)


def run_capability(client: AgentClient, method: str, args, *, jsonl: bool) -> int:
    """Call one capability, with the confirmation and secret rules of the CLI."""
    spec = METHODS[method]
    values = [getattr(args, name) for name in spec.arg_names if name != "confirm"]
    if spec.confirm and not getattr(args, "yes", False):
        print(
            f"{method} changes consent or sends data off this machine; pass --yes",
            file=sys.stderr,
        )
        return 2
    # A secret never travels in argv. `-` or --stdin reads it from stdin.
    if spec.name == "SettingsSet" and str(values[0]).endswith("-api-key"):
        if not getattr(args, "stdin", False) and values[1] != "-":
            print(
                "API keys must not be in argv; pipe the value with --stdin",
                file=sys.stderr,
            )
            return 2
        values[1] = sys.stdin.read().rstrip("\n")
    if spec.name == "SettingsEmail":
        if not getattr(args, "stdin", False):
            print(
                "email details must be piped with --stdin, not put in argv",
                file=sys.stderr,
            )
            return 2
        try:
            values[0] = json.dumps(json.loads(sys.stdin.read()))
        except ValueError as exc:
            print(f"email details are not JSON: {exc}", file=sys.stderr)
            return 2
    if spec.confirm:
        values.append(True)
    data = client.capability(method, *values)
    if jsonl:
        print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    else:
        display.show(data, sys.stdout, method)
    return 1 if data.get("error") else 0


def run_args(client: AgentClient, args) -> int:
    method = getattr(args, "method", None)
    if method is None:
        print("Choose a capability; see --help.", file=sys.stderr)
        return 2
    names = [n for n in METHODS[method].arg_names if n != "confirm"]
    missing = [n for n in names if getattr(args, n, None) is None]
    if missing:
        print(f"missing argument(s): {', '.join(missing)}", file=sys.stderr)
        return 2
    return run_capability(
        client, method, args, jsonl=bool(getattr(args, "json", False))
    )


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.no_color:
        os.environ["NO_COLOR"] = "1"
    if args.question is not None and args.command:
        parser.error("-p cannot be combined with a capability subcommand")
    if args.question == "-":
        args.question = sys.stdin.read().strip()
    client = AgentClient()
    try:
        client.ensure_started()
        if args.question is not None:
            return run_printed(client, args.question, jsonl=args.json, allow=args.allow)
        if args.command:
            return run_args(client, args)
        return Interactive(client, Renderer(stream=sys.stdout)).run()
    except AgentError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    finally:
        client.close()
