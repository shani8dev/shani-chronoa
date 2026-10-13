"""`check()` documented five verdicts and can produce three.

Its docstring said *"Returns a `GuardrailResult` with one of five verdicts"*, listed
all five with a meaning for each, and then said:

> A returned `GuardrailResult` is the decision the dispatch layer acts on.

Measured by reading every `GuardrailResult(...)` construction in the module:

| verdict | constructed? | where |
|---|---|---|
| `valid` | yes | five sites, the fall-through at the end |
| `invalid` | yes | five sites |
| `retry` | yes | **once** - a string argument starting `ERROR:` |
| `reask` | **never** | — |
| `stop` | **never** | — |

**So two of the five were unreachable, and nothing reads the two properties that
expose them** — `should_reask` and `should_stop` have no reference anywhere in the
tree. A reader taking the docstring at face value would extend the dispatch layer
to act on a `stop` verdict that no code path can produce.

## `should_retry` is reachable but redundant, and that is deliberate

Worth separating, because it looks like the same defect and is not. The RETRY
path **works**: `tools.py` is the single production call site and it does

    said = reason.template or reason.message

with the reason written down there - the template leads because returning the
model's own error text "echoes the failure straight back and burns a round of the
loop on a call the template would have fixed". The template *is* the retry
payload; `should_retry` is a second way to notice the same thing that nobody
takes.

The assertions below are behavioural: they ask `check()` what it can return
rather than reading the docstring, so the table cannot drift from the code.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr/lib/shani-chronoa"))

from shani_chronoa import guardrail  # noqa: E402

SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string"},
                   "action": {"type": "string"}},
}


def _verdict(arguments, schema=None):
    return guardrail.check("files", arguments, SCHEMA if schema is None else schema)


def test_a_well_formed_call_runs():
    assert _verdict({"path": "/tmp/x", "action": "read"}).verdict \
        == guardrail.GuardrailResult.VALID


def test_a_malformed_call_is_invalid():
    assert _verdict({"path": 12345}).verdict == guardrail.GuardrailResult.INVALID


def test_a_parse_error_argument_is_a_retry_with_a_template():
    """The one site that produces RETRY, and the payload it exists for."""
    result = _verdict({"path": "ERROR: Expecting ',' delimiter"})

    assert result.verdict == guardrail.GuardrailResult.RETRY, result.verdict
    assert result.should_retry, "the verdict and its property disagree"
    assert "valid arguments" in (result.template or ""), result.template
    assert not result.should_run, "a RETRY must not run the tool"


def test_reask_and_stop_are_not_produced_by_any_input():
    """**The defect.** Two of the five documented verdicts have no construction
    site, so no argument can produce one."""
    produced = set()
    samples = [
        {"path": "/tmp/x"},
        {"path": 12345},
        {},
        {"path": "ERROR: bad json"},
        {"path": "/tmp/x", "action": "destroy"},
        {"path": "", "action": ""},
        {"surprise": 1},
    ]
    for arguments in samples:
        produced.add(_verdict(arguments).verdict)

    for unreachable in (guardrail.GuardrailResult.REASK,
                        guardrail.GuardrailResult.STOP):
        assert unreachable not in produced, (
            f"{unreachable!r} is now produced - update the docstring's table, "
            f"because it currently says it is not reachable")
    assert produced <= {guardrail.GuardrailResult.VALID,
                        guardrail.GuardrailResult.INVALID,
                        guardrail.GuardrailResult.RETRY}, produced


def test_the_docstring_table_matches_what_check_can_return():
    """So the claim and the code cannot drift apart silently."""
    doc = guardrail.check.__doc__ or ""

    assert "Three of the five declared verdicts are" in doc, (
        "the reachability caveat has been dropped from check()'s docstring")
    for marked_no in ("| `reask` | **no** |", "| `stop` | **no** |"):
        assert marked_no in doc, (
            f"the docstring no longer marks {marked_no.strip()} unreachable")

    # And the old, stronger claim must be gone.
    assert "one of five verdicts" not in doc, (
        "check()'s docstring still claims all five verdicts are reachable")
