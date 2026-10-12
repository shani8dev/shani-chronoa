"""Mutation set for the skill-schema caps.

`harness-study/laya-decision`'s `laya/structured.py` states the principle and its
refusal shape:

> an object of properties, each an enum choice, a boolean, or a bounded integer
> scale. **Anything that cannot be answered from a fixed option set (free strings,
> arrays, nested objects) is rejected with an error that names the path.**

Chronoa had **no caps at all** - `is_valid_schema` checked shape, not size - so a
schema with 200 parameters would register and be sent to a model that cannot hold
it. This repository's own numbers: 131 schemas are already ~19,700 tokens against
an 8k local context, and one schema whose required property followed an optional
one took the **whole MCP server** down.

**The principle transferred; the numbers did not, and measuring said so.**
`solve_math` enumerates its hundred arithmetic operations as one enum, so porting
`MAX_OPTIONS = 32` verbatim dropped a **shipped skill** (257 -> 256, and
`solve_math` was the only name in the warning). The caps are therefore measured
over every built-in skill with the cap bypassed - **22 properties, 100 options,
depth 2** - and then given headroom (32 / **128** / 4).

`test_nothing_that_ships_is_refused` is the control that would have caught the
first version: it runs the real registry through the caps. A cap that drops a
shipped skill is a cap that was not measured.
"""

TEST_FILE = "tests/test_schema_caps.py"

_SK = "usr/lib/shani-chronoa/shani_chronoa/skills/__init__.py"

MUTATIONS = [
    # -- the caps are measured, not copied -----------------------------------
    {
        "file": _SK,
        "old": "_SCHEMA_MAX_OPTIONS = 128\n",
        "new": "_SCHEMA_MAX_OPTIONS = 32\n",
        "label": "**laya's number, restored**: `solve_math`'s hundred-operation "
                 "enum is refused and a shipped skill stops registering",
        "expect": ["test_nothing_that_ships_is_refused"],
    },
    {
        "file": _SK,
        "old": "_SCHEMA_MAX_PROPERTIES = 32\n",
        "new": "_SCHEMA_MAX_PROPERTIES = 5\n",
        "label": "the property cap is below the widest schema that ships",
        "expect": ["test_nothing_that_ships_is_refused",
                   "test_the_caps_have_headroom_over_the_measured_maximum"],
    },
    {
        "file": _SK,
        "old": "_SCHEMA_MAX_PROPERTIES = 32\n",
        "new": "_SCHEMA_MAX_PROPERTIES = 1\n",
        "label": "every multi-argument skill is refused, so almost nothing registers",
        "expect": ["test_nothing_that_ships_is_refused"],
    },

    # -- the refusal exists and names the path ------------------------------
    {
        "file": _SK,
        "old": "        if len(properties) > _SCHEMA_MAX_PROPERTIES:\n"
               "            return (f\"{path} declares {len(properties)} properties, past the \"\n"
               "                    f\"{_SCHEMA_MAX_PROPERTIES} one tool may ask for\")\n",
        "new": "        if False:\n            return \"\"\n",
        "label": "**the finding, restored**: no property cap, so a 200-argument "
                 "schema registers",
        "expect": ["test_too_many_properties_says_the_count_and_the_path",
                   "test_an_oversized_schema_is_refused"],
    },
    {
        "file": _SK,
        "old": "                if len(options) > _SCHEMA_MAX_OPTIONS:\n"
               "                    return (f\"{here} lists {len(options)} options, past the \"\n"
               "                            f\"{_SCHEMA_MAX_OPTIONS} a model may be asked to \"\n"
               "                            f\"choose between in one call\")\n",
        "new": "                if False:\n                    return \"\"\n",
        "label": "**the half that broke `solve_math`, removed here**: no option "
                 "cap, so a five-thousand-option enum registers",
        "expect": ["test_too_many_options_says_the_property_it_is_on"],
    },
    {
        "file": _SK,
        "old": "                    return (f\"{here} lists {len(options)} options, past the \"\n",
        "new": "                    return (f\"an enum lists {len(options)} options, past the \"\n",
        "label": "the option refusal stops naming *which* enum is too big",
        "expect": ["test_too_many_options_says_the_property_it_is_on"],
    },
    {
        "file": _SK,
        # 4-space indent: `schema_refusal` is a module function, not a method.
        "old": "    if depth > _SCHEMA_MAX_DEPTH:\n"
               "        return f\"{path} nests past {_SCHEMA_MAX_DEPTH} levels\"\n",
        "new": "",
        "label": "no depth cap, so a schema nests without limit",
        "expect": ["test_too_deep_says_where_it_was_found"],
    },
    {
        "file": _SK,
        "old": "        said = schema_refusal(parameters)\n        if said:\n            return False\n",
        "new": "",
        "label": "**`is_valid_schema` carries shape only again**: the caps are not "
                 "consulted at registration or by the MCP translator",
        "expect": ["test_an_oversized_schema_is_refused",
                   "test_a_valid_schema_is_unaffected"],
    },
    {
        "file": _SK,
        # The real line assigns `why`, at 12-space indent inside the loader's
        # loop - the first version spelled a `said =` line that does not exist.
        "old": "            why = schema_refusal(parameters) or \"not a valid function schema\"\n",
        "new": "            why = \"not a valid function schema\"\n",
        "label": "the loader's skip stops naming what was wrong, so a person "
                 "adding a big schema learns nothing about what to change",
        "expect": ["test_the_loader_names_the_reason"],
    },

    # -- a valid schema must not be refused ---------------------------------
    {
        "file": _SK,
        "old": "_SCHEMA_MAX_OPTIONS = 128\n",
        "new": "_SCHEMA_MAX_OPTIONS = 100\n",
        "label": "the option cap is exactly at the measured maximum, so the next "
                 "operation on `solve_math` is refused",
        # **Not `test_nothing_that_ships_is_refused`:** `solve_math` lists exactly
        # 100 options and the check is `>`, so at a cap of 100 it still registers
        # and the registry is still 257. The property this pins is the *headroom*,
        # which is what the first version of the port did not have.
        "expect": ["test_the_caps_have_headroom_over_the_measured_maximum"],
    },
    {
        "file": _SK,
        # The real guard keeps `properties is not None` - the half that makes an
        # *absent* `properties` legal. Dropping it is the over-fix: a schema with
        # no parameters at all would be refused, and plenty of skills have none.
        "old": "    if properties is not None and not isinstance(properties, dict):\n"
               "        return f\"{path}.properties is not an object\"\n",
        "new": "    if False:\n        return f\"\"\n",
        "label": "**the over-fix**: a `properties` that is not an object is "
                 "accepted, which is a crash further down rather than a refusal",
        # **Not `test_a_property_that_is_not_an_object_is_refused`** - that one
        # covers the per-property line, a different guard. This top-level guard
        # had no witness at all until the run reported it as a survivor, which is
        # the second time today a mutation found a guard nothing exercises.
        "expect": ["test_a_parameters_block_that_is_not_an_object_is_refused"],
    },
    {
        "file": _SK,
        "old": "                if not isinstance(options, list) or not options:\n"
               "                    return f\"{here}.enum is not a non-empty list\"\n",
        "new": "",
        "label": "an empty or malformed enum is accepted, so `len()` on it later "
                 "is a crash rather than a refusal",
        "expect": ["test_an_empty_and_a_malformed_enum_are_refused"],
    },
]
