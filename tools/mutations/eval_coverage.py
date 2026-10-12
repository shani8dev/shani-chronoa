"""Mutation set for `gen_eval_cases.py --refresh-coverage`.

`skills_total` sat at **250** against a registry of **257** for as long as it
took to add seven skills, and `tests/test_task_eval.py` was red about it the whole
time. The gate was working. The finding is why nobody fixed it.

**The only tool that wrote the block could not fix a stale count.** It rewrote
`coverage` inside `if added:` - so only when a skill was missing a case - and set
`skills_total = skills_asserted`, because its job is to fill the gaps and leave
coverage complete. Using it to correct one stale number would have appended 47
generated cases and flipped `about` to "COVERAGE IS COMPLETE". A repair tool that
cannot repair is a reason the repair does not happen.

The three numbers are pure functions of two facts already in this repository, so
there is no state in which the recorded value is right and the derived one wrong -
which is the whole argument for generating rather than recording them.

The `--check`-writes mutation is here because the test that guards it originally
compared **parsed JSON**, and a rewrite that only reformats leaves parsed JSON
identical. That mutation ran green against a test written for it: comparing the
file's bytes is what "wrote nothing" actually means.
"""

TEST_FILE = "tests/test_eval_coverage_refresh.py"

_GEN = "tools/gen_eval_cases.py"

MUTATIONS = [
    {
        "file": _GEN,
        "old": '        "skills_total": len(names),\n',
        "new": '        "skills_total": data.get("coverage", {}).get("skills_total", len(names)),\n',
        "label": "the total is read back out of the file instead of the registry",
        "expect": ["test_a_doctored_total_is_corrected_from_the_registry"],
    },
    {
        "file": _GEN,
        "old": '    asserted = covered & set(names)\n',
        "new": "    asserted = covered\n",
        "label": "a case naming a tool that no longer exists counts as coverage",
        "expect": ["test_a_case_naming_a_tool_that_no_longer_exists_does_not_count"],
    },
    {
        "file": _GEN,
        "old": '        before = data.get("coverage", {})\n',
        "new": '        data["cases"].append({"id": "x", "say": "y", "expect": [{"tool": names[0]}]})\n'
               '        before = data.get("coverage", {})\n',
        "label": "the refresh starts adding cases, which is the completer's job",
        "expect": ["test_it_adds_no_cases"],
    },
    {
        "file": _GEN,
        "old": '        before = data.get("coverage", {})\n',
        "new": '        data["about"] = "COVERAGE IS COMPLETE"\n'
               '        before = data.get("coverage", {})\n',
        "label": "the refresh rewrites the prose, making the coverage check the file agreeing with itself",
        "expect": ["test_it_leaves_the_prose_alone"],
    },
    {
        "file": _GEN,
        "old": '        for case in added:\n'
               '            print(f"   {case[\'expect\'][0][\'tool\']}")\n'
               '        return 0\n',
        "new": '        for case in added:\n'
               '            print(f"   {case[\'expect\'][0][\'tool\']}")\n'
               '        CASES.write_text(json.dumps(data, indent=2) + "\\n")\n'
               '        return 0\n',
        "label": "--check writes the file, on a machine whose checkout is read-only",
        "expect": ["test_check_still_writes_nothing"],
    },
]