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
  +greedy+request      the same request and the same two steps, schema in prose and NO grammar
                       (the control: a grammar win that this arm matches is the request, not the grammar)
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

CONFIGS = ("bare", "select", "select+recover", "+compact", "+greedy", "+greedy+retry",
           "+greedy+constrained", "+greedy+request")
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

#: The id prefix `gen_eval_cases.py` gives a case it derived from a schema rather
#: than from a transcript. Read out of the ids rather than a second hand-kept
#: list of which cases are real, because that list would rot exactly like
#: `coverage.skills_total` did.
_GENERATED_PREFIX = "generated-"


#: **What a run measured, so two runs can be told apart.**
#:
#: `harness-study/laya-decision`'s `laya/evals.py` states the rule this ports:
#:
#: > `compare` reads `overall` and nothing else, so a run that says nothing about
#: *what it measured* makes a gate that can only compare arithmetic. Every report
#: therefore carries a run identity in `config` ... and `EvalReport.comparable_to`
#: refuses a comparison between two runs that are not the same measurement. The
#: identity is deliberately free of anything time-bearing, so a report stays
#: byte-reproducible for a fixed runner.
#:
#: Chronoa already paid for not having it: AGENTS.md records `test_task_eval`
#: failing on **a stale skill count** - a comparison made against a different set
#: of cases reading as a regression. `results[config]` carried `cases`,
#: `right_tool` and `mean_seconds`, and nothing that said *which* cases those
#: were, so any two runs looked like two measurements of the same thing.
#:
#: The digest is over the **ids and requests in canonical order**, not the file
#: bytes: reordering a file is not a different measurement, and editing a comment
#: inside a case is not either. What must change it is the set of things asked, or
#: what was asked. Time and paths are excluded so a report stays
#: byte-reproducible.
#:
#: `generated` is in the identity because the corpus already says 149 of its 226
#: cases are machine-derived: a score over a set that changes by generated cases
#: is a score over a different corpus, even at the same count.


def run_identity(cases: "list[dict]") -> tuple:
    """`(n, sha256, generated)` - what this run measured, in one comparable value."""
    import hashlib

    canonical = sorted((str(c.get("id") or ""), str(c.get("request") or ""))
                       for c in cases)
    sha = hashlib.sha256(repr(canonical).encode("utf-8")).hexdigest()[:16]
    generated = sum(1 for cid, _ in canonical if cid.startswith(_GENERATED_PREFIX))
    return len(cases), sha, generated


def comparable(a: dict, b: dict) -> "tuple[bool, str]":
    """Whether two per-config results are two measurements of the same thing.

    **Refuses rather than comparing, which is the whole point.** `comparable_to`
    exists so a gate cannot report a regression from a set that changed - and a
    caller that ignores the answer gets what it had before, which is a comparison
    of two numbers that were never about the same questions.
    """
    for side, r in (("this run", a), ("that run", b)):
        if "identity" not in r:
            return False, f"{side} carries no measurement identity"
    if a["identity"] == b["identity"]:
        return True, "the same cases"
    left, right = a["identity"], b["identity"]
    if left[0] != right[0]:
        return False, f"different cases: {left[0]} measured against {right[0]}"
    return False, (f"the same number of cases but not the same cases: "
                   f"{left[1]} against {right[1]}")


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
    #: The control arm. Set **after** CONSTRAINED so neither can leak into the
    #: other; `+greedy+constrained` must stay a pure grammar run.
    local_llm.REQUEST_ONLY = config == "+greedy+request"
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
            "identity": run_identity([r for r in rows if not r.get("unavailable")]),
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
    # **Say what the corpus is, next to the scores that depend on it.**
    # Measured 2026-10-12: **149 of the 226 cases are machine-generated**, and
    # each says so in its own `note` - "the wording is a starting point, not a
    # transcript". So a score over this corpus is partly measured on phrasings no
    # person typed, which matters most for exactly the question a refusal rate is
    # being asked here to settle: whether the model declines things a person would
    # actually ask for. `coverage.skills_asserted` counts *which skills* have a
    # case and cannot see this, because both populations name the same skills.
    # Omitted, a reader cannot tell a number about people from one about a
    # generator.
    for config, r in results.items():
        rows = [row for row in r["rows"] if not row.get("unavailable")]
        if not rows:
            continue
        generated = [row["id"] for row in rows if row["id"].startswith(_GENERATED_PREFIX)]
        if generated:
            lines += ["", f"{config}: {len(generated)} of {len(rows)} cases are "
                          f"machine-generated starting points rather than "
                          f"transcripts, so these scores are partly measured on "
                          f"phrasings no person typed."]

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
    #
    # **Counted over the misses only**, which is what the heading says and what
    # makes the number diagnostic. A refusal is only *interesting* on a case the
    # model failed: counted across every case it is diluted by the ones that
    # worked, so "0 refusals" over 226 rows reads as "this model never declines"
    # when the 47 it failed are exactly where a decline would show. The first
    # version counted everything, and a single-case run made the difference
    # visible - `time` scored `tool: True` with `reply: "empty"`, so a table over
    # all rows reports the model as silent on a case it answered correctly.
    said, r_rows = {}, {}
    for config, r in results.items():
        r_rows[config] = r["rows"]
        misses = [row for row in r["rows"] if not row["args"] and not row.get("unavailable")]
        if not misses:
            continue
        counts = {}
        for row in misses:
            if row.get("reply"):
                counts.setdefault(row["reply"], []).append(row["id"])
        # **No table at all when nothing was recorded.** A row with no `reply`
        # means this build did not write the field, and a table of zeros reads as
        # "measured, and there were none" - the same absence-shaped substitution
        # `classify_reply` refuses when it meets an empty string. The count of
        # misses is still reported by the Misses list above, so nothing is lost.
        if not counts:
            continue
        said[config] = (counts, len(misses))
    if said:
        lines += ["", "What the model said on the cases it missed", "",
                  "| config | of N misses | refusals | narrations | empty | plain answers |",
                  "|---|---|---|---|---|---|"]
        for config in sorted(said):
            counts, total = said[config]
            lines.append(
                f"| {config} | {total} | {len(counts.get(REPLY_REFUSAL, []))} | "
                f"{len(counts.get(REPLY_NARRATION, []))} | "
                f"{len(counts.get(REPLY_EMPTY, []))} | "
                f"{len(counts.get(REPLY_ANSWER, []))} |")
        # **The words, beside every classification.** A keyword scorer is a claim
        # about text, and heretic's own `print_responses` exists for the same
        # reason: the number is not auditable without seeing what it counted.
        # Without this the reader has to re-run the model to find out whether a
        # case was really a refusal, which is the same measurement twice.
        for config in sorted(said):
            counts, _ = said[config]
            # **Every classified miss, not just the two alarming ones.** The
            # first version listed refusals and narrations only, so an `empty` or
            # `answer` miss was counted in the table with no way to see which
            # case - and `answer` is the one worth reading, because it means the
            # model said something and it still did not act.
            for state, label in ((REPLY_REFUSAL, "refused outright"),
                                 (REPLY_NARRATION, "said it would act and did not"),
                                 (REPLY_EMPTY, "said nothing at all"),
                                 (REPLY_ANSWER, "answered, but called no tool")):
                if not counts.get(state):
                    continue
                lines += ["", f"{config}: {label} - "
                              + ", ".join(sorted(counts[state]))]
                for row in r_rows[config]:
                    if row["id"] in counts[state]:
                        lines.append(f"  - `{row['id']}`: "
                                     f"{(row.get('text') or '').strip()[:120] or '(no text)'}")

    # **And, separately, how often the model said nothing at all** - a different
    # question from either of the above, and the one this file's own measurement
    # turned up. A thinking model spends its budget on `reasoning_content` and
    # leaves `content` empty, so this counts rows that *acted correctly* and
    # still said nothing, which the miss table above cannot see by construction.
    for config, r in results.items():
        rows = [row for row in r["rows"] if not row.get("unavailable") and row.get("reply")]
        if not rows:
            continue
        silent = [row["id"] for row in rows if row["reply"] == REPLY_EMPTY]
        if silent:
            lines += ["", f"{config}: {len(silent)} of {len(rows)} replies carried no text "
                          f"at all (a thinking model emits reasoning instead) - "
                          + ", ".join(sorted(silent)[:20])
                          + (" ..." if len(silent) > 20 else "")]
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
