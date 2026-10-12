"""Mutation set for the prompt override, `shani_chronoa/prompt_pieces.py`.

`harness-study/laya` (local-first AI notification centre, Python engine) states its
headline feature as "Override 20+ pipeline stages individually - change one, keep
the rest as default. **Delete the file to revert** - defaults always come back."
Chronoa's system prompt was one hard-coded string, so there was no lever that
survived an update.

Four of Laya's decisions are what this set pins, plus the one that matters more
than any of them:

1. **The default is byte-identical when no file exists.** A mechanism that
   rewords the prompt when nobody expressed a preference is a change nobody asked
   for, and it would be invisible until someone compared.
2. **Delete to revert**, and *immediately* - Laya's loader runs at engine start so
   its revert needs a restart; `compose()` is called once per assistant, so the
   scan is free and the stronger property is.
3. **A typo'd file name is a warning that lists the recognised ones**, or a person
   tunes a string nobody reads.
4. **An empty file is a warning and not an override** - replacing a prompt with
   nothing is worse than not replacing it.
5. **The safety boundary is not in the registry.** There is no key for
   `provenance.describe_boundary()`, so no file can name it. That is Laya's
   construction for identifier fields ("absent by construction because
   capabilities don't list them as content"): a security statement is not a
   preference, and the way to keep it unoverridable is to leave no door.
"""

TEST_FILE = "tests/test_prompt_pieces.py"

_PP = "usr/lib/shani-chronoa/shani_chronoa/prompt_pieces.py"

MUTATIONS = [
    # -- 1. the default must not change --------------------------------------
    {
        "file": _PP,
        "old": '    return " ".join(pieces) + " " + provenance.describe_boundary()\n',
        "new": '    return " ".join(pieces) + " " + provenance.describe_boundary() + "\\n"\n',
        "label": "**the no-op path is not byte-identical**: a trailing byte "
                 "appears when nobody wrote a file",
        # Every test that asserts `SYSTEM_PROMPT in messages[0]["content"]` would
        # keep passing, which is exactly why this is a mutation and not a review
        # comment.
        "expect": ["test_no_overrides_means_the_exact_prompt"],
    },
    {
        "file": _PP,
        "old": '        "default": "You are Chronoa, a privacy-first local voice assistant "\n'
               '                   "running on Shanios.",\n',
        "new": '        "default": "You are Chronoa, a privacy-first local voice assistant "\n'
               '                   "running on a Linux distribution.",\n',
        "label": "the default's own wording drifts, so a person with no file "
                 "gets a different prompt",
        "expect": ["test_no_overrides_means_the_exact_prompt"],
    },

    # -- 2. delete to revert, immediately ------------------------------------
    {
        "file": _PP,
        "old": "        load_overrides()\n",
        "new": "",
        "label": "**Laya's weaker contract, restored**: the override is cached, "
                 "so deleting the file does not revert until a restart",
        "expect": ["test_deleting_the_file_reverts"],
    },

    # -- 3. a typo'd file name is not silent --------------------------------
    {
        "file": _PP,
        "old": '            unrecognised.append(entry.name)\n            continue\n',
        "new": "            continue\n",
        "label": "a file with a typo'd name is dropped in silence, so a person "
                 "tunes a string nobody reads",
        "expect": ["test_a_typo_file_name_is_named_not_ignored"],
    },
    {
        "file": _PP,
        "old": '        logger.warning(\n            "prompt files not recognised and ignored: %s '
               '(recognised files: %s)",\n            ", ".join(unrecognised), '
               '", ".join(sorted(_FILES.values())))\n',
        "new": '        logger.warning(\n            "prompt files not recognised and ignored: %s",\n'
               '            ", ".join(unrecognised))\n',
        "label": "the unrecognised-file warning names the problem but not the "
                 "recognised names, so it cannot be acted on",
        "expect": ["test_a_typo_file_name_is_named_not_ignored"],
    },

    # -- 4. an empty file is not an override --------------------------------
    {
        "file": _PP,
        "old": "        if not text:\n"
               "            # Replacing a prompt with nothing is worse than not replacing it.\n"
               "            empty.append(entry.name)\n            continue\n",
        "new": "",
        "label": "an empty file replaces the prompt with nothing, which is "
                 "worse than not replacing it",
        "expect": ["test_an_empty_file_is_not_an_override"],
    },

    # -- 5. the safety boundary is not a preference ------------------------
    {
        "file": _PP,
        "old": '    from shani_chronoa import provenance\n\n'
               '    return " ".join(pieces) + " " + provenance.describe_boundary()\n',
        "new": '    return " ".join(pieces)\n',
        "label": "**the safety door, opened**: the provenance boundary is no "
                 "longer in the prompt at all",
        "expect": ["test_the_boundary_is_part_of_the_prompt_and_not_a_piece"],
    },
    {
        "file": _PP,
        "old": '    from shani_chronoa import provenance\n\n'
               '    return " ".join(pieces) + " " + provenance.describe_boundary()\n',
        "new": '    from shani_chronoa import provenance\n\n'
               '    boundary = _overrides.get("boundary", provenance.describe_boundary())\n'
               '    return " ".join(pieces) + " " + boundary\n',
        "label": "the boundary becomes overridable by a key that exists in no "
                 "registry entry, so the door is unannounced",
        "expect": ["test_the_boundary_is_part_of_the_prompt_and_not_a_piece"],
        # **Equivalent, and the reason is the point.** `"boundary"` is a key no
        # file maps to, so `_overrides` never holds it and the expression is inert
        # on every input. It is exactly why the registry is the safety property
        # rather than the lookup: a key that exists in no `_FILES` entry cannot
        # be reached by a file, so putting the lookup back changes nothing until
        # somebody also adds the file name.
        "equivalent": True,
        "why": "no PROMPTS_DIR file maps to the 'boundary' key, so the override "
               "store never holds it and the expression is inert",
    },

    # -- 6. the diagnostics half --------------------------------------------
    {
        "file": _PP,
        "old": '             "current": get_prompt(key, piece["default"]),\n'
               '             "default": piece["default"]}\n',
        "new": '             "current": get_prompt(key, piece["default"])}\n',
        "label": "the status drops the default, so there is nothing to compare "
                 "against and nothing to revert to",
        "expect": ["test_the_status_function_answers_it"],
    },
    {
        "file": _PP,
        "old": "    load_overrides()\n    return [{\"key\": key, \"file\": name, "
               '"overridable": piece["overridable"],\n',
        "new": "    return [{\"key\": key, \"file\": name, "
               '"overridable": piece["overridable"],\n',
        "label": "the status reads a stale cache, so it reports what was true "
                 "when the app started",
        # **Not the file-list test**: that one only compares the registry to the
        # status, which a stale cache satisfies. The witness is the one that
        # writes a file and then asks what is overridden.
        "expect": ["test_the_status_function_answers_it"],
    },

    # -- 7. it must actually reach the model --------------------------------
    {
        "file": _PP,
        "old": "        prompt_pieces.load_overrides()\n"
               "        self._history: list[dict] = [\n"
               '            {"role": "system", "content": prompt_pieces.compose()}]\n',
        "new": '        self._history: list[dict] = [\n'
               '            {"role": "system", "content": SYSTEM_PROMPT}]\n',
        "label": "**the finding, restored**: the assistant ignores the override "
                 "and keeps the hard-coded constant",
        # The mechanism would be complete, correct, tested, and unreachable -
        # which is this repo's most-recorded defect.
        "file2": None,
        "expect": ["test_the_rebuilt_assistant_uses_the_override"],
    },
]

# The last entry names a different file: fix it up rather than leave a wrong
# path in the set.
MUTATIONS[-1]["file"] = "usr/lib/shani-chronoa/shani_chronoa/assistant.py"
