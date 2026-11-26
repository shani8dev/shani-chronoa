"""The `chronoa` command: parse arguments, then hand over to the client."""

from __future__ import annotations

import argparse
import os
import sys

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
        help="with -p, print the event stream as JSON lines",
    )
    parser.add_argument(
        "--allow", metavar="LABEL", help="with -p, approve with this exact option label"
    )
    parser.add_argument(
        "--no-color", action="store_true", help="never emit ANSI colour"
    )
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


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.no_color:
        os.environ["NO_COLOR"] = "1"
    if args.question is None and (args.json or args.allow):
        parser.error("--json and --allow need -p")
    question = args.question
    if question == "-":
        question = sys.stdin.read().strip()
    client = AgentClient()
    try:
        client.ensure_started()
        if question is not None:
            return run_printed(client, question, jsonl=args.json, allow=args.allow)
        return Interactive(client, Renderer(stream=sys.stdout)).run()
    except AgentError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    finally:
        client.close()
