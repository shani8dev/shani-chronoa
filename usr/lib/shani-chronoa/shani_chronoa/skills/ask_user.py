"""Skill: ask the user a clarifying question and wait for the answer.

Speech is ambiguous in a way a text box is not. "Delete the old one" has no
referent on a screen you cannot see, and in a conversation there is no list in
front of the user to disambiguate against. Every harness surveyed for this work
has a tool for exactly this; Chronoa had 68 skills and no way to ask a question,
so an ambiguous request could only be guessed at, refused, or silently resolved
to the wrong file.

The option count is capped at four, and that cap is the point rather than a
limit. A person answering by voice cannot hold ten options in their head, and
cannot read them aloud. Four is about what fits in one utterance and one glance.

Honesty rules:

- **No presenter is not an answer.** Headless - an MCP session, or a trigger
  rule firing at 3am with nobody there - and the tool says so and lets the model
  proceed without guessing. It never picks an option on the user's behalf.
- An empty answer (dismissed, or timed out) is reported as unanswered, which
  is different from the user having chosen the first option.
"""

from __future__ import annotations

from shani_chronoa.skills import Skill

#: Read aloud in one go, and pick from in one look.
MAX_OPTIONS = 4
MIN_OPTIONS = 2

SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": (
            "Ask the user a clarifying question and wait for their answer, with "
            "2 to 4 short options they can pick. Use this whenever a request is "
            "genuinely ambiguous - especially when several files or devices could "
            "match. Do not guess instead, and do not ask when one option is "
            "obviously right. In a headless session there is nobody to answer and "
            "this will say so."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": (
                        "The question to ask, in one sentence, phrased so it can "
                        "be read aloud."
                    ),
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        f"Between {MIN_OPTIONS} and {MAX_OPTIONS} short options, "
                        f"each a few words. Keep them parallel in shape."
                    ),
                },
            },
            "required": ["question", "options"],
        },
    },
}


def _run(arguments: dict) -> str:
    from shani_chronoa import ask_bridge

    question = (arguments.get("question") or "").strip()
    options = [str(o).strip() for o in (arguments.get("options") or []) if str(o).strip()]

    if not question:
        return ("ask_user was called with no question, so there is nothing to "
                "ask. Ask again with a question and 2 to 4 options.")

    if len(options) < MIN_OPTIONS:
        return (f"ask_user was called with {len(options)} option(s). It needs "
                f"at least {MIN_OPTIONS}, because a question with one answer is "
                f"not a question - either decide the answer yourself and act, or "
                f"phrase a question the user can actually choose between.")

    if not ask_bridge.has_presenter():
        return ("There is nobody here to answer a question right now, so none was "
                "asked and no option was chosen. Either pick the most likely "
                "option and say which you picked, or say what you need to know "
                "and let the user come back to it.")

    answer = ask_bridge.ask(question, options)
    if not answer:
        return ("The question was put to the user but no option was chosen - the "
                "prompt was dismissed or went unanswered. Treat that as unknown, "
                "not as the first option. Ask again in a different way, or say "
                "what you would need to know.")
    return f"The user chose: {answer}"


SKILLS = [Skill(name="ask_user", schema=SCHEMA, run=_run)]
