"""Pure output formatting for ``chronoa -p`` (print mode).

No D-Bus, no decisions. The session orchestrator drives the engine and hands
this module the events and the final result dict; everything here only writes
text to a stream. Kept free of GTK and gi so it runs on a bare interpreter.
"""

import json
import sys


def emit_reply(event: dict, stream=None) -> None:
    """Write only event['text'] for event['event'] == 'reply'; nothing for any other kind."""
    if event.get("event") != "reply":
        return
    stream = sys.stdout if stream is None else stream
    text = event.get("text")
    if text:
        stream.write(text)
    stream.flush()


def emit_json(event: dict, stream=None) -> None:
    """Write one compact JSON line per event (json.dumps, ensure_ascii=False), flush it."""
    stream = sys.stdout if stream is None else stream
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    stream.write(line + "\n")
    stream.flush()


def refusal_notice(event: dict, allow: str | None, stream=None) -> None:
    """Write ONE line to stream (default sys.stderr) explaining why an approval was
    refused in -p mode: include the event's option labels and say to pass --allow "<label>".
    When allow is given but not in the options, say that it did not match and list the options."""
    stream = sys.stderr if stream is None else stream
    options = list(event.get("options") or [])
    labels = ", ".join(f'"{option}"' for option in options)
    if allow and allow not in options:
        message = (
            f'Approval refused: --allow "{allow}" did not match any option. '
            f'Options: {labels}. Pass --allow "<label>" with one of {labels}.'
        )
    else:
        example = options[0] if options else "<label>"
        message = (
            f'Approval refused in -p mode. Pass --allow "{example}" (one of: {labels}).'
        )
    stream.write(message.replace("\n", " ").replace("\r", " ") + "\n")


def deny_label(event: dict, allow: str | None) -> str:
    """The label to send to Agent.Approve: allow when it exactly matches an option;
    otherwise a deny option from the list (case-insensitive 'deny'/'no'/'reject'/'refuse'),
    otherwise '' (which the engine reads as no answer). Never invent a label not in the
    options except the empty string."""
    options = list(event.get("options") or [])
    if allow and allow in options:
        return allow
    deny_words = {"deny", "no", "reject", "refuse"}
    for option in options:
        if str(option).strip().lower() in deny_words:
            return option
    return ""


def exit_code(result: dict) -> int:
    """0 unless result.get('error') is truthy, then 1."""
    return 1 if (result or {}).get("error") else 0
