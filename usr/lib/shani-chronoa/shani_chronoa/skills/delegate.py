"""Skill: hand a self-contained task to a subagent and get back its summary.

**Why this exists.** A long investigation ("read the four config files and
tell me which one sets the proxy") fills the conversation with every file it
read, and a small local model's window is the scarcest thing it has. A
subagent does that work in a **fresh, empty conversation** and returns only its
conclusion, so the person's conversation carries one paragraph instead of four
files. `compression` already tells the model to delegate the re-read of a
clipped result when this tool exists.

**What the subagent is.** Another `Assistant` on the same model, built inside
the running one (`Assistant._delegate`), because it needs the model and the
person's window - which is why this module's `run` only refuses: it is reached
only by a dispatcher with no assistant behind it (the MCP server).

**What it is not.** Not a way around anything. The subagent's tool calls go
through the same dispatcher, the same consent switches and questions, under the
*parent's* turn label - so "Allow for this turn" covers it, its edits are
listed and reverted with the parent turn, and a remote channel's turn stays a
remote turn. It cannot delegate in its turn (depth one), and it runs inside
whatever is left of the parent turn's time budget.
"""
from __future__ import annotations

from shani_chronoa.skills import Skill

NAME = "delegate"

SCHEMA = {
    "type": "function",
    "function": {
        "name": NAME,
        "description": (
            "Hand a self-contained task to a subagent that works in a fresh "
            "conversation with the same tools, and get back only its summary. "
            "Use it to research, investigate, or read many files or pages, "
            "when the details would crowd this conversation; the subagent "
            "cannot see this conversation, so put everything it needs in "
            "`task`. Not for one quick tool call."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {"type": "string",
                         "description": "What to do and what to report back, "
                                        "self-contained: names, paths, the goal."},
                "context": {"type": "string",
                            "description": "Optional facts from this conversation "
                                           "the subagent needs."},
            },
            "required": ["task"],
        },
    },
}


def run(_arguments: dict) -> str:
    return ("Not run. A subagent needs the assistant that asked for it, and "
            "this call came without one; do the task directly instead.")


SKILLS = [Skill(name=NAME, schema=SCHEMA, run=run)]
