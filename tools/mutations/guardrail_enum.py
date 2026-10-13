"""Mutation set for the enum gate `guardrail` had no check for.

`guardrail` is the layer between the model deciding and the tool running, and its
own docstring says the question it answers is *"is the call even well-formed"*,
answered with no model involved. It checked required arguments, per-property
types and `additionalProperties: False`. It did **not** check `enum`, which is
the only schema constraint left unchecked anywhere in the registry - measured:
**682 property nodes across 256 tools, 90 declaring an enum in 73 tools, and
zero declaring a numeric or string bound**.

What each entry covers, and why it is a defect rather than a style preference:

1. **The gap itself, restored verbatim.** An out-of-enum value reached the
   sandbox. This is not hypothetical: `airplane_mode` refuses one itself, in the
   child, so the refusal existed - after dispatch, after the sandbox and after
   the audit origin, where it reads as a tool that ran.
2. **The two directions of the over-fix.** The gate is matched with the skills'
   own tolerance (`strip().lower()`), because 73 tools read the value that way
   and a stricter gate refuses calls that work today. Both an over-strict gate
   and an over-lenient one are live failures and are pinned separately.
3. **The refusal has to be usable.** A message naming neither the field nor the
   alternatives is a message the model cannot use, which is the entire reason
   this module exists instead of a traceback three frames down in a subprocess.
4. **The declaration must equal what the skill accepts.** Two of the 73 accept
   values outside their enum through an alias table - `alarm` took 14 while
   declaring 6, `calendar_edit` took 10 while declaring 3 - so the rule is only
   safe because the schemas were widened. Reverting either is the over-fix that
   would refuse `alarm action=remove`.
"""

TEST_FILE = "tests/test_guardrail.py"

_G = "usr/lib/shani-chronoa/shani_chronoa/guardrail.py"
_ALARM = "usr/lib/shani-chronoa/shani_chronoa/skills/alarm.py"
_CAL = "usr/lib/shani-chronoa/shani_chronoa/skills/calendar_edit.py"

# The first mutation used to anchor on those four lines and replace them with
# one, which left the body of the innermost `if` dangling at 16 spaces. The
# mutated file raised **IndentationError**, every test errored at *collection*,
# and the runner had no test id to match - so a mutation that measured nothing
# came back as a **SURVIVOR**. It anchors on the one line now, and the trap is
# recorded here rather than left for the next person to walk into again.

MUTATIONS = [
    # -- 1. the gap ---------------------------------------------------------
    {
        "file": _G,
        "old": '        legal = declared.get("enum") if isinstance(declared, dict) else None\n',
        "new": "        legal = None\n",
        "label": "**the shipped behaviour restored verbatim**: an action outside "
                 "the declared enum reaches the sandbox, and the only refusal "
                 "left is the one the child makes after dispatch",
        "expect": ["test_a_plausible_value_the_schema_forbids_is_refused",
                   "test_the_refusal_names_the_parameter_and_every_legal_value"],
    },
    {
        "file": _G,
        "old": '        if legal and isinstance(value, str):\n',
        "new": "        if legal and not isinstance(value, str):\n",
        "label": "the gate only fires on values that are not strings, which is "
                 "the opposite of every enum in the tree",
        "expect": ["test_a_plausible_value_the_schema_forbids_is_refused"],
    },

    # -- 2. the two directions of the over-fix -------------------------------
    {
        "file": _G,
        "old": "            said = value.strip().lower()\n",
        "new": "            said = value\n",
        "label": "the gate is stricter than the skills: `airplane_mode` strips "
                 "and lowercases, so `action=\"LIST\"` is a call that works "
                 "today and this would refuse it",
        "expect": ["test_the_spellings_the_skills_accept_are_still_accepted"],
    },
    {
        "file": _G,
        "old": "            if not any(said == str(item).strip().lower() for item in legal):\n",
        "new": "            if False:\n",
        "label": "the gate is computed and then ignored",
        "expect": ["test_a_plausible_value_the_schema_forbids_is_refused"],
    },
    {
        "file": _G,
        "old": '                        f"it accepts: {\', \'.join(str(item) for item in legal)}."\n',
        "new": '                        "it accepts a different value."\n',
        "label": "the refusal stops naming the alternatives, so the model is "
                 "asked again with nothing it can use",
        "expect": ["test_the_refusal_names_the_parameter_and_every_legal_value"],
    },

    # -- 3. the declaration must equal what the skill accepts ---------------
    {
        "file": _ALARM,
        "old": '                    "enum": ["set", "list", "next", "change", "snooze", "delete",\n'
               '                             "remove", "cancel", "edit", "update", "move",\n'
               '                             "add", "create", "when"],\n',
        "new": '                    "enum": ["set", "list", "next", "change", "snooze", "delete"],\n',
        "label": "**the over-fix, as a real user-visible failure**: `alarm` "
                 "declares 6 of the 14 actions it accepts, so a gate written "
                 "against the declaration refuses \"remove my alarm\"",
        "expect": ["test_every_alias_a_skill_accepts_is_declared_in_its_enum"],
    },
    {
        "file": _CAL,
        "old": '            "action": {"type": "string",\n'
               '                       "enum": list(_ACTIONS) + list(_ALIASES)},\n',
        "new": '            "action": {"type": "string", "enum": list(_ACTIONS)},\n',
        "label": "the same over-fix for `calendar_edit`, whose 7 aliases make "
                 "\"schedule a meeting\" the refusal of `create`",
        "expect": ["test_every_alias_a_skill_accepts_is_declared_in_its_enum"],
    },
    {
        "file": _G,
        "old": '        legal = declared.get("enum") if isinstance(declared, dict) else None\n',
        "new": '        legal = declared.get("enum")\n',
        "label": "EQUIVALENT, and measured rather than argued. The `isinstance` "
                 "guard on this line defends against a **property node that is "
                 "not a dict**, and that shape is already broken *above* this "
                 "line: `_type_of()` does `schema.get(\"type\")` on the same "
                 "node and raises `AttributeError: 'str' object has no attribute "
                 "'get'` first, so the enum line is never reached. Measured on "
                 "today\'s code - a non-dict property node raises either way. "
                 "**Pre-existing and deliberately not changed here**: all 682 "
                 "property nodes in the registry measured are dicts, so the "
                 "crash is unreachable from the tree, and repairing `_type_of` "
                 "is a different change from this one. The guard stays as the "
                 "statement of the rule.",
        "equivalent": True,
        "expect": [],
    },
]
