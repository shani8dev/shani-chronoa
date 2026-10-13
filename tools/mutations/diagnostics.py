"""Mutation set for "a model server setup never configured is not a fault".

The Model servers section carries three rows - Eyes, Imagine, Memory - and all
three are the wizard's **opt-in extras**. `_model_server` answered
`STATUS_NOT_WORKING` for every one of them on a default install, because it only
distinguished "answering" from "not answering" and had no third state for
"never set up". So a machine that had installed nothing optional carried **three
red rows** in the panel whose whole job is to say what is broken.

The code distinguishes two facts already and said so in its own docstring: *"a
server with no env file was never configured, and that is a different answer from
one that is configured and not answering."* And the section's own prose says it a
third time: *"Each may be asleep, which is a normal state for all three."*

**The mutation that matters most is the last one in the set.** The obvious
over-fix is to make every non-answering server green, which satisfies "absent is
not a fault" and deletes the alarm about a server somebody configured. This file
records that having happened three times in this panel's history, so the set
carries it explicitly rather than trusting the fix to be narrow.
"""

TEST_FILE = "tests/test_surface_diagnostics.py"

_DIAG = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/diagnostics.py"

_GREEN = "test_a_server_setup_never_configured_is_not_a_fault"

MUTATIONS = [
    {
        "file": _DIAG,
        "old": "        if not binary:\n            # **Never configured is not a fault** - which this docstring says two\n",
        "new": "        if False:\n",
        # **The shipped defect, restored verbatim**: with no binary branch every
        # unconfigured extra falls through to "nothing is answering", which is
        # the red this closes.
        "label": "the never-configured branch is gone (the shipped defect)",
        "expect": [_GREEN, "test_every_declared_extra_answers_the_same_way"],
    },
    {
        "file": _DIAG,
        "old": "        if not binary:\n            # **Never configured is not a fault** - which this docstring says two\n",
        "new": "        if True:\n",
        # **The over-fix.** Every non-answering server reads green, so a server
        # somebody configured and that has gone down is reported as fine. The
        # control that keeps this from shipping is
        # `test_a_configured_server_that_is_not_answering_is_still_not_working`,
        # and this mutation is the reason it exists.
        "label": "every non-answering server is green (the over-fix)",
        "expect": [
            "test_a_configured_server_that_is_not_answering_is_still_not_working",
            "test_the_two_answers_really_are_different",
        ],
    },
    {
        "file": _DIAG,
        "old": "            return STATUS_WORKING, (\n                f\"{unit} is not running, and nothing is wrong: {setup}. This is \"",
        "new": "            return STATUS_UNKNOWN, (\n                f\"{unit} is not running, and nothing is wrong: {setup}. This is \"",
        # `STATUS_UNKNOWN` is the panel's "could not determine", and this
        # determined the answer - it asked `read_env` and there was no env file.
        # It reads as a calmer wrong answer, which is why it needs its own
        # mutation: an absence dressed as an unknown is the same defect as an
        # absence dressed as a fault.
        "label": "an unconfigured extra reports 'could not determine'",
        "expect": [_GREEN, "test_every_declared_extra_answers_the_same_way"],
    },
    {
        "file": _DIAG,
        "old": "f\"{unit} is not running, and nothing is wrong: {setup}. This is \"",
        "new": "f\"{unit} is not running. {setup}\"",
        # The sentence stops saying why the absence is not a fault, so the green
        # becomes unexplained - and a green row with no reason is a row nobody
        # can check against the panel's own prose.
        "label": "the row stops saying the absence is not a fault",
        "expect": [_GREEN],
    },
    {
        "file": _DIAG,
        "old": "\"an optional extra, so leaving it out is a choice rather than a \"\n                \"fault - setup's Optional extras page is what installs it\"",
        "new": "\"an optional extra.\"",
        # **The remedy is the part that makes this a fix rather than a
        # repaint.** A row that says "nothing is wrong" and names no door is a
        # dead end the person has to find elsewhere, which is the failure this
        # panel records more often than any other.
        "label": "the row stops naming the page that installs it",
        "expect": [_GREEN],
    },
    {
        "file": _DIAG,
        "old": "\"fault - setup's Optional extras page is what installs it\"",
        "new": "\"fault\"",
        "label": "the remedy sentence is cut at the fault",
        "expect": [_GREEN],
    },
    {
        "file": _DIAG,
        "old": "            (\"vision server (port 8767)\", _model_server(\"vision\")),\n",
        "new": "            (\"vision server (port 8767)\", _model_server(\"imagine\")),\n",
        # A duplicated row. The panel would show Eyes' status under Imagine's
        # heading, which is only visible by reading the two rows against each
        # other - and the earlier `export`/checkpoint findings were both
        # "the row beside it says something different".
        "label": "the imagine row reports the vision server",
        "expect": ["test_every_declared_extra_answers_the_same_way"],
    },
]