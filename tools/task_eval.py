#!/usr/bin/env python3
"""Chronoa's task eval: does the model, with this harness, make the call a correct assistant would?

The goal it measures (2026-10-02): a harness good enough that the smallest
local model beats much bigger models on what people actually ask Chronoa. So the
same cases (tools/eval_cases.json) run through:

  bare            every tool schema, nothing else - what a model gets with no harness
  select          only the schemas this request could use (tool_select)
  select+recover  ... and calls a small model wrote as text turned into real ones (the default)
  +compact        ... and schema descriptions cut to one sentence
  +greedy         select+recover at temperature 0 (llama-server's default is 0.8)
  +greedy+retry   ... and a narrower forced retry when a clear command was answered in words
  +greedy+constrained  the tool's name then its arguments, each under a JSON schema (grammar)
  cloud           a bigger cloud model with every schema and no harness (--cloud)
  cloud+select    the same cloud model with Chronoa's tool selection, to see what the harness gives it

--cloud is one of Chronoa's own cloud providers. The baseline is the free,
keyless ones (llm7, kilo, blockrun - rate-limited, and some are good); a BYOK
one (anthropic, openai, groq, google, openrouter, key in the environment) can
be named too.

Each case is scored on the tool chosen and on its arguments; nothing is ever
executed, so it is safe to run anywhere and fast. A call that cannot even be
sent - every schema is ~19,700 tokens, past an 8k local context - counts as a
failure, because for the person it is one.

    task_eval.py [--configs=bare,select,select+recover,+compact] [--cases=ID,...]
                 [--cloud=llm7|kilo|blockrun|anthropic|openai|...] [--pause=S] [--repeat=N]
                 [--json=out.json] [--markdown=out.md]

The local model is the one llama-server is serving (shani-chronoa-llm, port
8765). A BYOK cloud key comes from the environment (ANTHROPIC_API_KEY,
OPENAI_API_KEY, GROQ_API_KEY, GOOGLE_API_KEY, OPENROUTER_API_KEY); the cases
hold no personal data, and nothing is sent anywhere without --cloud.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "usr" / "lib" / "shani-chronoa"))

CONFIGS = ("bare", "select", "select+recover", "+compact", "+greedy", "+greedy+retry", "+greedy+constrained")
LOCAL_CONFIGS = set(CONFIGS)


def load_cases(path: Path, only: "set[str]") -> "list[dict]":
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    return [c for c in cases if not only or c["id"] in only]


def _match(rule, value) -> bool:
    if rule == "*":
        return value not in (None, "", [], {})
    if isinstance(rule, bool):
        return value is rule or str(value).lower() == str(rule).lower()
    if isinstance(rule, (int, float)):
        try:
            return abs(float(value) - float(rule)) < 1e-6
        except (TypeError, ValueError):
            return False
    if isinstance(rule, list):
        return any(_match(r, value) for r in rule)
    text = str(value if value is not None else "").lower()
    rule = str(rule).lower()
    return rule[1:] in text if rule.startswith("~") else text == rule


def score(case: dict, calls: "list[dict]") -> "tuple[bool, bool, str]":
    """(right tool, right arguments, what was seen) for the first call the model made."""
    if case.get("none"):
        if not calls:
            return True, True, "no tool"
        name = calls[0]["name"]
        ok = case.get("ask") and name == "ask_user"
        return bool(ok), bool(ok), f"called {name}"
    if not calls:
        return False, False, "no tool"
    first = calls[0]
    for want in case["expect"]:
        if first["name"] != want["tool"]:
            continue
        missing = [k for k, rule in (want.get("args") or {}).items() if not _match(rule, first["args"].get(k))]
        return True, not missing, f"{first['name']}({json.dumps(first['args'])[:120]})" + (
            f" wrong {','.join(missing)}" if missing else "")
    return False, False, f"{first['name']}({json.dumps(first['args'])[:80]})"


def _calls(message: dict) -> "list[dict]":
    out = []
    for call in (message or {}).get("tool_calls") or []:
        fn = call.get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except ValueError:
                args = {"__unparsed__": args}
        out.append({"name": fn.get("name", ""), "args": args if isinstance(args, dict) else {}})
    return out


#: provider failures that are about the service, not the model's answer
_UNAVAILABLE = ("busy", "quota", "rate limit", "rate_limit", "too many", "timeout", "temporarily", "exhausted", "capacity",
                "unavailable", "overloaded", "429", "502", "503", "504")


# --------------------------------------------------------------------------
# What the model said when it did not act.
#
# `score` reports "no tool" for every case where no call was made, which
# discards the only evidence there was. "I can't tell you the time" and "Let me
# check that for you" are both `no tool`, and they are opposite failures: one is
# a refusal to answer a request the assistant exists to answer, the other is the
# model promising an action it never takes. AGENTS.md records the second as a
# live failure (`test_routines_live` on "weather in Pune and what time is it").
#
# The classification is heretic's (`scorers/keyword_rate.py`) applied to this
# harness's own cases: normalise the text, then match markers. The normalisation
# is not decoration - a model writes "I *can't*" and "I can’t", and an unquoted
# marker list misses both.
# --------------------------------------------------------------------------

def _normalise(text: str) -> str:
    """Lowercase, drop emphasis, straighten typographic apostrophes, collapse space.

    Every step is one a real reply was observed to need: markdown emphasis
    splits a marker's own word apart (`I *can't*`), and a phone or a document
    gives "can't" with U+2019 where the marker list has U+0027.
    """
    return " ".join((text or "").lower().replace("*", "").replace("\u2019", "'").split())


#: A refusal. Trimmed to the ways an assistant declines a request it should
#: answer; heretic's list is built for harmful prompts, and its safety-policy
#: vocabulary ("i'm an ai", "violat", "ethical boundaries") is a different
#: question from "did this decline to tell me the time".
_REFUSAL_MARKERS = (
    "i can't", "i cant", "i cannot",
    "i won't", "i wont", "i will not",
    "i'm unable", "i am unable", "im unable",
    "i'm not able", "i am not able",
    "i don't have access", "i do not have access",
    "i'm sorry, but", "i am sorry, but", "unfortunately, i",
)

#: A narration: the model states an intention and performs nothing. Not a
#: refusal and not an answer, and the person is left holding a promise.
_NARRATION_MARKERS = (
    "let me", "i'll check", "i will check", "i'll look", "i will look",
    "one moment", "give me a moment", "hang on", "i'll find", "i will find",
    "i'll pull", "i will pull",
)

#: The four answers, and why "empty" is one of them rather than a fifth way of
#: saying "answer".
#:
#: **Measured on this box, 2026-10-12**: Qwen3 is a thinking model, and with
#: `max_tokens: 80` a completely ordinary "What time is it?" came back with
#: `content == ""` - the budget spent on `reasoning_content` before it spoke.
#: A refusal-marker check that treated empty as compliant would have scored that
#: silence as an answer. heretic reaches the same conclusion from the other side,
#: counting an empty response as a match "to avoid optimizing for them"; here it
#: is its own state, because "did not speak" and "declined to speak" and "spoke
#: plainly" are three different facts about a run.
REPLY_EMPTY = "empty"
REPLY_REFUSAL = "refusal"
REPLY_NARRATION = "narration"
REPLY_ANSWER = "answer"


def classify_reply(text: str) -> str:
    """One of the four states, from the reply's own words.

    `empty` is tested **first**: an empty string contains no marker at all, so
    every other branch would pass it straight through as an answer.
    """
    flat = _normalise(text)
    if not flat:
        return REPLY_EMPTY
    if any(marker in flat for marker in _REFUSAL_MARKERS):
        return REPLY_REFUSAL
    if any(marker in flat for marker in _NARRATION_MARKERS):
        return REPLY_NARRATION
    return REPLY_ANSWER


#: How much of the reply to keep in the JSON. Enough to read what happened on a
#: miss, short enough that 226 cases do not produce a report nobody opens.
REPLY_EXCERPT_CHARS = 200


def build_llm(config: str, cloud: str):
    from shani_chronoa import cloud_llm, local_llm
    if config.startswith("cloud"):
        if cloud in ("llm7", "kilo", "blockrun"):
            return cloud_llm.OpenAICompatibleLLM(cloud_llm.PROVIDERS[cloud])
        key_var = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "groq": "GROQ_API_KEY",
                   "google": "GOOGLE_API_KEY", "openrouter": "OPENROUTER_API_KEY"}[cloud]
        key = os.environ.get(key_var, "")
        if not key:
            raise SystemExit(f"--cloud={cloud} needs {key_var} in the environment")
        if cloud == "anthropic":
            return cloud_llm.AnthropicLLM(api_key=key)
        return cloud_llm.OpenAICompatibleLLM(cloud_llm.PROVIDERS[cloud], api_key=key)
    local_llm.COMPACT_TOOLS = config == "+compact"
    local_llm.TEMPERATURE = 0.0 if config.startswith("+greedy") else None
    local_llm.FORCE_RETRY = config == "+greedy+retry"
    local_llm.CONSTRAINED = config == "+greedy+constrained"
    llm = local_llm.LocalLLM()
    if config in ("bare", "select"):
        llm.postprocess = lambda message, tools: message  # no recovery of calls written as text
    return llm


async def run_case(llm, config: str, case: dict) -> dict:
    from shani_chronoa.assistant import SYSTEM_PROMPT
    from shani_chronoa.tool_select import select_tools
    from shani_chronoa.tools import TOOLS
    tools = TOOLS if config in ("bare", "cloud") else select_tools(case["say"], TOOLS)
    # a provider that cannot take 131 schemas fails the case, as it would for a person
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": case["say"]}]
    error, unavailable = "", False
    for attempt in range(4):
        started = time.monotonic()
        try:
            reply = await llm.chat_message(messages, tools=tools)
            error, unavailable = "", False
            break
        except Exception as exc:  # noqa: BLE001 - a request that fails is a failed case, with the reason kept
            reply, error = {}, f"{type(exc).__name__}: {str(exc)[:160]}"
            # a free gateway being busy or over quota says nothing about the model:
            # retried, and if it stays down the case is not measured rather than failed
            unavailable = config.startswith("cloud") and any(
                s in error.lower() for s in _UNAVAILABLE)
            if not unavailable or attempt == 3:
                break
            await asyncio.sleep(10 * (attempt + 1))
    seconds = time.monotonic() - started
    # a request that failed is a failed case, even one that expected no tool:
    # "no answer" is not "correctly chose not to act"
    tool_ok, args_ok, seen = (False, False, "") if error else score(case, _calls(reply))
    text = (reply or {}).get("content") or ""
    return {"id": case["id"], "tool": tool_ok, "args": args_ok, "seconds": round(seconds, 2),
            "seen": error or seen, "schemas": len(tools), "unavailable": unavailable,
            # What it said, kept because `seen` throws it away. A miss recorded
            # only as "no tool" cannot be told from a refusal.
            "reply": "" if error else classify_reply(text),
            "text": "" if error else text[:REPLY_EXCERPT_CHARS]}


async def run(configs, cases, cloud: str, repeat: int, pause: float = 0.0) -> dict:
    results = {}
    for config in configs:
        llm = build_llm(config, cloud)
        rows = []
        for _ in range(repeat):
            for case in cases:
                rows.append(await run_case(llm, config, case))
                if pause:
                    await asyncio.sleep(pause)
                print(f"{config:15s} {rows[-1]['id']:16s} {'ok ' if rows[-1]['args'] else ('tool' if rows[-1]['tool'] else 'FAIL')} "
                      f"{rows[-1]['seconds']:6.1f}s  {rows[-1]['seen'][:90]}", flush=True)
        measured = [r for r in rows if not r.get("unavailable")]
        n = len(measured)
        results[config] = {
            "cases": n,
            "not_measured": len(rows) - n,
            "right_tool": sum(r["tool"] for r in measured),
            "right_call": sum(r["args"] for r in measured),
            "mean_seconds": round(sum(r["seconds"] for r in measured) / max(1, n), 2),
            "rows": rows,
        }
    return results


def markdown(results: dict) -> str:
    lines = ["| config | right tool | right tool and arguments | mean seconds | not measured (service down) |",
             "|---|---|---|---|---|"]
    for config, r in results.items():
        lines.append(f"| {config} | {r['right_tool']}/{r['cases']} | **{r['right_call']}/{r['cases']}** | "
                     f"{r['mean_seconds']} | {r.get('not_measured', 0)} |")
    fails = {}
    for config, r in results.items():
        for row in r["rows"]:
            if not row["args"] and not row.get("unavailable"):
                fails.setdefault(row["id"], []).append(
                    f"{config}: {row['seen']}"
                    + (f" ({row['reply']})" if row.get("reply") else ""))
    if fails:
        lines += ["", "Misses:", ""] + [f"- **{cid}** - " + "; ".join(v) for cid, v in sorted(fails.items())]

    # **A refusal rate beside the accuracy, because accuracy alone cannot see
    # this.** "no tool" is the same word for declining the request and for
    # promising it and never doing it, and the fix for each is different.
    said = {}
    for config, r in results.items():
        for row in r["rows"]:
            if row.get("unavailable") or not row.get("reply"):
                continue
            said.setdefault(config, {}).setdefault(row["reply"], []).append(row["id"])
    if said:
        lines += ["", "What the model said when it did not act", "",
                  "| config | refusals | narrations | empty | plain answers |"]
        lines.append("|---|---|---|---|---|")
        for config in sorted(said):
            counts = said[config]
            lines.append(
                f"| {config} | {len(counts.get(REPLY_REFUSAL, []))} | "
                f"{len(counts.get(REPLY_NARRATION, []))} | "
                f"{len(counts.get(REPLY_EMPTY, []))} | "
                f"{len(counts.get(REPLY_ANSWER, []))} |")
        for config in sorted(said):
            bad = said[config].get(REPLY_REFUSAL) or []
            if bad:
                lines += ["", f"{config}: refused outright - " + ", ".join(sorted(bad))]
            gaps = said[config].get(REPLY_NARRATION) or []
            if gaps:
                lines += ["", f"{config}: said it would act and did not - "
                              + ", ".join(sorted(gaps))]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--configs", default=",".join(CONFIGS))
    ap.add_argument("--cases", default="")
    ap.add_argument("--cloud", default="", choices=["", "llm7", "kilo", "blockrun", "anthropic", "openai",
                                                        "groq", "google", "openrouter"])
    ap.add_argument("--pause", type=float, default=0.0, help="seconds between requests (free providers rate-limit)")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--file", default=str(HERE / "eval_cases.json"))
    ap.add_argument("--port", type=int, default=0, help="the local llama-server's port, when not 8765")
    ap.add_argument("--json", default="")
    ap.add_argument("--markdown", default="")
    a = ap.parse_args()
    if a.port:
        from shani_chronoa import local_llm
        local_llm.PORT, local_llm.BASE_URL = a.port, f"http://{local_llm.HOST}:{a.port}/v1"
    configs = [c for c in a.configs.split(",") if c] + (["cloud", "cloud+select"] if a.cloud else [])
    cases = load_cases(Path(a.file), {c for c in a.cases.split(",") if c})
    results = asyncio.run(run(configs, cases, a.cloud, a.repeat, a.pause))
    table = markdown(results)
    print("\n" + table)
    if a.json:
        Path(a.json).write_text(json.dumps(results, indent=1), encoding="utf-8")
    if a.markdown:
        Path(a.markdown).write_text(table, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
