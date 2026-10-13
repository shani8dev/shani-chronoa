"""`check()` used to document five verdicts and produce three. It now produces all
five - and this file is the drift detector for that claim, in both directions.

It was originally written to catch the opposite defect: `reask` and `stop` had **no
construction site anywhere**, and nothing read `should_reask`/`should_stop`, so the
docstring's "one of five verdicts" was a claim about two verdicts that could not
occur. It asserted they were *not* reachable.

It **failed the moment they became reachable** - which is the point of writing the
assertions against `check()` rather than against the docstring. So the same test
now asserts the opposite, and the table in the docstring cannot change without it
failing.

## What reaches each verdict, and why those two were chosen

| verdict | what reaches it |
|---|---|
| `valid` | the fall-through |
| `reask` | a **required argument is missing** - a question the model can answer |
| `retry` | a string argument starting `ERROR:` - a parse failure a template fixes |
| `stop` | a **required argument present but blank** - supplied, and says nothing |
| `invalid` | wrong type, or an extra argument the schema forbids |

Every one is a **shape test**, which is the module's stated thesis: *"deliberately
dumb and deliberately deterministic. No model call, no heuristic scoring, no
confidence threshold."*

**`stop` was not an invented category.** Measured before the change, on the real
function:

| argument | verdict then |
|---|---|
| `path=""` | **`valid`** |
| `path="   "` | **`valid`** |

A required argument that is blank passed, so the tool ran with an empty path and
the failure surfaced from the tool rather than from the layer that exists to catch
it. It cannot be `reask` (the model *did* supply the argument) and no template can
put a value into a string the model left empty.

Producing the verdicts also needed the dispatch to **act** on them: every
non-VALID verdict used to become the same `Not run. <reason>`, which is why two of
them could sit unused for so long - the dispatch could not tell them apart.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr/lib/shani-chronoa"))

from shani_chronoa import guardrail  # noqa: E402

SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string"},
                   "action": {"type": "string"}},
    # **Without this, nothing is required** and `check({})` is `valid` - the
    # rewrite of this file dropped it and every "missing" case passed vacuously.
    "required": ["path"],
}


def _verdict(arguments, schema=None):
    return guardrail.check("files", arguments, SCHEMA if schema is None else schema)


def test_a_well_formed_call_runs():
    assert _verdict({"path": "/tmp/x", "action": "read"}).verdict \
        == guardrail.GuardrailResult.VALID


def test_a_malformed_call_is_invalid():
    """A supplied value that is actually wrong is a *fact to report*."""
    assert _verdict({"path": 12345}).verdict == guardrail.GuardrailResult.INVALID


def test_a_missing_required_argument_is_a_reask():
    """**The audience split.** A missing argument is a question the model can
    answer - "which path?" - so it is put back, not reported as a fault."""
    result = _verdict({})

    assert result.verdict == guardrail.GuardrailResult.REASK, result.verdict
    assert result.should_reask and not result.should_run
    assert "path" in result.message, result.message
    # The wording an existing test already insisted on.
    assert "required" in result.message, result.message


def test_a_blank_required_argument_is_a_stop():
    """**The hole this closes.** Measured before the change: `path=""` and
    `path="   "` both returned `valid`, so the tool ran with an empty path and the
    failure came from the tool rather than from the layer meant to catch it."""
    for blank in ("", "   ", "\t\n"):
        result = _verdict({"path": blank})
        assert result.verdict == guardrail.GuardrailResult.STOP, (
            f"a blank required argument ({blank!r}) gave {result.verdict}")
        assert result.should_stop and not result.should_run


def test_a_blank_optional_argument_is_fine():
    """The rule is **required**, not *everywhere*: an optional argument may
    legitimately be empty, and refusing that would break real calls."""
    schema = {"type": "object",
              "properties": {"path": {"type": "string"},
                             "note": {"type": "string"}},
              "required": ["path"]}

    assert _verdict({"path": "/a", "note": "  "}, schema).should_run


def test_a_parse_error_argument_is_a_retry_with_a_template():
    """The one site that produces RETRY, and the payload it exists for."""
    result = _verdict({"path": "ERROR: Expecting ',' delimiter"})

    assert result.verdict == guardrail.GuardrailResult.RETRY, result.verdict
    assert result.should_retry, "the verdict and its property disagree"
    assert "valid arguments" in (result.template or ""), result.template
    assert not result.should_run, "a RETRY must not run the tool"


def test_all_five_verdicts_are_reachable():
    """**What this file was written to catch, now caught in the other
    direction.** It originally asserted `reask` and `stop` were *not* produced,
    and failed the moment they were - which is the drift detector working.

    Each verdict is produced by feeding `check()` real arguments, so the table in
    the docstring cannot drift from the code without this failing.
    """
    produced = set()
    for arguments in ({"path": "/tmp/x"}, {"path": 12345}, {},
                      {"path": "ERROR: bad json"}, {"path": ""},
                      {"surprise": 1}):
        produced.add(_verdict(arguments).verdict)

    assert produced == {
        guardrail.GuardrailResult.VALID,
        guardrail.GuardrailResult.INVALID,
        guardrail.GuardrailResult.RETRY,
        guardrail.GuardrailResult.REASK,
        guardrail.GuardrailResult.STOP,
    }, produced


def test_the_docstring_table_describes_what_reaches_each_verdict():
    doc = guardrail.check.__doc__ or ""

    assert "All five verdicts are reachable" in doc, (
        "check()'s docstring no longer claims all five are reachable")
    for row in ("| `reask` |", "| `stop` |", "| `retry` |", "| `invalid` |"):
        assert row in doc, f"the verdict table lost its {row} row"

    # And the stale "unreachable" marks must be gone.
    assert "| `reask` | **no** |" not in doc
    assert "one of five verdicts" not in doc
# --- the dispatch half -------------------------------------------------------
# Producing a verdict changes nothing if the dispatch cannot tell them apart, and
# that is *why* two of them sat unused: every non-VALID verdict became the same
# `Not run. <reason>`. A mutation that deleted the `should_stop` branch passed every
# other test in this file, because nothing here called `_guardrail_refuses`.

SCHEMA_WITH_COUNT = {
    "type": "object",
    "properties": {"path": {"type": "string"}, "count": {"type": "integer"}},
    "required": ["path"],
}


@pytest.fixture
def dispatch(monkeypatch):
    """`_guardrail_refuses` with a known schema behind `_schema_for`."""
    from shani_chronoa import tools

    monkeypatch.setattr(tools, "_schema_for", lambda name: SCHEMA_WITH_COUNT)
    return tools._guardrail_refuses


def _refusal(refuses, arguments):
    result = refuses("files", arguments)
    assert result is not None, "the guardrail let the call through"
    assert result.ran is False, result.ran
    return result.text


def test_a_valid_call_is_not_refused(dispatch):
    """The control: the refusal helper returns None when there is nothing to say,
    so the other assertions are not all reading one hard-coded string."""
    assert dispatch("files", {"path": "/tmp/x"}) is None


def test_each_refusal_says_something_different(dispatch):
    """**The assertion M4 exposed the absence of.** All four non-run outcomes
    produce distinct text, so the model is told which kind of problem it is."""
    texts = {
        "reask": _refusal(dispatch, {}),
        "stop": _refusal(dispatch, {"path": "   "}),
        "retry": _refusal(dispatch, {"path": "ERROR: bad json"}),
        "invalid": _refusal(dispatch, {"path": 12345}),
    }

    assert len(set(texts.values())) == 4, (
        "two verdicts produce the same refusal, so producing them changes nothing "
        f"for the caller: {texts}")

    # And the distinctions are the ones the verdicts are *for*.
    assert "required" in texts["reask"], texts["reask"]
    assert "empty value" in texts["stop"], texts["stop"]
    assert "Please provide valid arguments" in texts["retry"], texts["retry"]
    assert "wrong type" in texts["invalid"], texts["invalid"]

    # **Distinctness alone is not the contract, and finding that out is why this
    # line exists.** With the `should_stop` branch deleted, `stop` fell through to
    # the generic `"Not run. " + said` - and the four texts were *still* all
    # different, because the four messages differ. So "the texts differ" was
    # satisfied by something that had nothing to do with the branch.
    #
    # What pins it is that `stop` must **not** be prefixed: its message already
    # ends "...so it was not run - asking again needs a real value", and the
    # generic prefix would say "Not run." twice in one refusal.
    assert not texts["stop"].startswith("Not run."), (
        "the STOP refusal took the generic prefix as well, so it reports the same "
        f"way a fault does: {texts['stop']!r}")
    assert texts["invalid"].startswith("Not run."), texts["invalid"]


def test_the_reask_refusal_asks_for_the_value(dispatch):
    """A question the model can answer, so the refusal asks rather than reports -
    and does not stutter the tool's own name."""
    text = _refusal(dispatch, {})

    assert text.endswith("Please supply it and call again."), text
    assert "files did not run: files" not in text, (
        f"the refusal prefixes the name onto a message that already opens with "
        f"it: {text!r}")


def test_the_stop_refusal_says_asking_again_will_not_help(dispatch):
    """This is what separates `stop` from `reask` at the caller: the value is not
    missing, it is empty, so another identical attempt changes nothing."""
    text = _refusal(dispatch, {"path": ""})

    assert "empty value" in text, text
    assert "asking again needs a real value" in text, text
