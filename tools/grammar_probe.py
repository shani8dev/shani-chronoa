#!/usr/bin/env python3
"""Prove the grammar is actually enforced, by asking for what the grammar forbids.

**Why this exists as a tool and not a sentence.** The three-arm decoder run
measures whether a grammar buys anything. That question is void if the grammar
was silently off, and a tie would then read as *"the grammar bought nothing"* when
the truth was *"nothing was tested"* - which is this repository's most-recorded
failure wearing a measurement's clothes. A three-hour run has no way to notice.

So the lever is verified **first, and independently of the run**, by asking the
model for something the grammar will not let it say:

    schema allows exactly one tool: get_datetime
    user asks about the weather

    grammar off -> prose ("I don't have access to real-time weather data...")
    grammar on  -> {"tool": "get_datetime"} - the only legal value, for a question
                   that is not about the time

**That second line is also the run's finding, not just its precondition.** The
grammar constrained *form* completely and *choice* not at all - it forced the
model to name a tool it had already decided against. So this probe measures two
things at once, and the exit code says which.

**Exits non-zero when the grammar is not enforced**, so this is a gate rather than
a report, and `verdict()` is a pure function of the two replies so the decision
can be unit-tested without a server.

Measured on this box: **ENFORCED**. The first draft of this was a throwaway in
`/tmp` that the next session would have had to re-derive - and the whole run
depends on it.

    ./tools/grammar_probe.py --dry-run          # prints the argv, starts nothing
    ./tools/grammar_probe.py                    # starts its own server, exits 0/1
    ./tools/grammar_probe.py --port 8799        # drive a server that is already up
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import subprocess
import time
import urllib.request

logger = logging.getLogger(__name__)

REPO = pathlib.Path(__file__).resolve().parents[1]

MODELS = REPO / "cache" / "data-home" / "shani-chronoa" / "llm"
BASE = MODELS / "Qwen3-1.7B-Q4_K_M.gguf"

#: The only value the schema permits. Anything else is ungrammatical, so a reply
#: naming it **is** evidence the grammar was applied rather than ignored.
ONLY_TOOL = "get_datetime"

#: A question the model should decline: it is not about the time, so returning
#: the time is a constraint overriding a decision rather than the model agreeing.
OFF_TOPIC = "What is the weather in Bengaluru right now?"

TOOL_SCHEMA = {"type": "object",
               "properties": {"tool": {"type": "string", "enum": [ONLY_TOOL]}},
               "required": ["tool"]}


def response_format() -> dict:
    """The `response_format` llama-server turns into a grammar.

    Named so the probe and `_json_format` in `local_llm` are visibly the same
    shape: if either changes, this one is where you look.
    """
    return {"type": "json_schema", "json_schema": {"name": "answer", "schema": TOOL_SCHEMA}}


def chosen_tool(text: str) -> "str | None":
    """The tool the reply names, or None if it is not JSON naming one.

    A bare `in` test would pass on a reply that merely *contains* the name, which
    is exactly the prose case the off arm produces.
    """
    try:
        return json.loads(text).get("tool")
    except (ValueError, AttributeError):
        return None


def verdict(off_text: str, on_text: str) -> "tuple[bool, str]":
    """(enforced, why). The decision, as a pure function of the two replies.

    **Both directions matter, and neither is reachable by accident.** The positive
    alone would pass on a server that ignored `response_format` and got lucky; the
    negative alone would pass on a server that refused to answer at all. A probe
    that can only ever prove the grammar is on is the control that cannot fail.
    """
    on = chosen_tool(on_text)
    if on != ONLY_TOOL:
        return False, ("with the grammar on, the reply named %r and the only "
                       "legal value is %r" % (on, ONLY_TOOL))
    off = chosen_tool(off_text)
    if off == ONLY_TOOL:
        return False, ("the grammar-off reply also named %r, so the model chose "
                       "it rather than being made to - this is the run's question "
                       "and this probe cannot answer it" % ONLY_TOOL)
    return True, ("grammar-off answered in words (%r); grammar-on answered %r, "
                  "the only value the schema allows, for a question that is not "
                  "about the time" % (off_text[:40], on_text[:40]))


def _wait(port: int, seconds: int = 180) -> bool:
    for _ in range(seconds):
        time.sleep(1.0)
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=2).read()
            return True
        except Exception:
            continue
    return False


def _ask(port: int, body: dict) -> "str | None":
    """The reply, or **None when the server could not be asked**.

    Measured: pointed at a saturated server this raised an unhandled
    `TimeoutError` and the probe died with a traceback - which a caller reading
    the exit status cannot tell from a probe that ran. **A probe that could not
    measure is not a probe that found nothing**, and this is the third time this
    repository has had to write that sentence about something it wrote itself.
    """
    request = urllib.request.Request(
        "http://127.0.0.1:%d/v1/chat/completions" % port,
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        payload = json.loads(urllib.request.urlopen(request, timeout=120).read())
        return payload["choices"][0]["message"].get("content") or ""
    except Exception as exc:  # noqa: BLE001 - any failure is "could not ask"
        logger.warning("could not ask the server: %s: %s",
                       exc.__class__.__name__, str(exc)[:120])
        return None


#: "Could not measure" is its own answer, distinct from "not enforced". The
#: first is a fact about the probe; the second is a fact about the grammar.
UNMEASURED = 2


def probe(port: int) -> "tuple[bool, str, str, str]":
    """(enforced, why, off-reply, on-reply) against a server already listening.

    **Returns `(False, why, "", "")` with `why` naming the failure** when the
    server cannot be reached, so `main` can tell "not enforced" from "not asked".
    """
    body = {"model": "probe", "messages": [{"role": "user", "content": OFF_TOPIC}],
            "temperature": 0}
    off = _ask(port, dict(body))
    if off is None:
        return False, "the server could not be asked (grammar-off arm)", "", ""
    on = _ask(port, dict(body, response_format=response_format()))
    if on is None:
        return False, "the server could not be asked (grammar-on arm)", off, ""
    ok, why = verdict(off, on)
    return ok, why, off, on


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--port", type=int, default=8799,
                    help="drive a server already listening here instead of "
                         "starting one")
    ap.add_argument("--threads", default="4")
    args = ap.parse_args(argv)

    server = ["llama-server", "--model", str(BASE), "--port", str(args.port),
              "--threads", args.threads, "--ctx-size", "2048"]
    print("$ " + " ".join(server))
    if args.dry_run:
        print("(dry run: no server started)")
        return 0

    proc = None
    if not _server_up(args.port):
        proc = subprocess.Popen(server, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            if not _wait(args.port):
                print("the server never came up")
                return 2
            ok, why, off, on = probe(args.port)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
    else:
        ok, why, off, on = probe(args.port)

    print("")
    print("grammar OFF -> %r" % off[:160])
    print("grammar ON  -> %r" % on[:160])
    print("")
    if "could not be asked" in why:
        print("COULD NOT MEASURE: " + why)
        print("")
        print("This is a fact about the probe, not about the grammar. A saturated")
        print("server answers nothing and is not evidence of anything.")
        return UNMEASURED
    print(("ENFORCED" if ok else "NOT ENFORCED") + ": " + why)
    if ok:
        print("")
        print("The second line is also the finding, not only the precondition: the")
        print("grammar forced a tool the model had decided against. It constrains")
        print("form completely and choice not at all.")
    return 0 if ok else 1


def _server_up(port: int) -> bool:
    try:
        urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=2).read()
        return True
    except Exception:
        return False


if __name__ == "__main__":
    raise SystemExit(main())