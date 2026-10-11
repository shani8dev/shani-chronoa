"""Mutation set for the argv post-condition's exit-code contract.

Replaces a mutation set that was in `/tmp` and lost. Every entry is checked
against the **current** source - the runner asserts each anchor occurs exactly
once - and against a named witness, so a set that drifts from the code is an
error rather than a silent survivor.

What is covered, and why each is a defect rather than a style preference:

1. **A checker that could not answer is not a finding.** This is the transfer
   from oh-my-openagent's `comment-checker-core`, whose `runner.ts` reserves
   exit 2 for findings and returns every other non-zero exit as a `failure`
   carrying the code and the checker's stderr (#8850). Restoring the original
   line here is the *shipped* behaviour verbatim, which is the strongest kind
   of mutation: it is not a hypothetical, it is the bug.
2. **The two codes are the two they claim to be.** A contract nothing asserts
   is a comment, and this set pins the literals.
3. **The reason reaches the person.** `suffix` rendered one fixed sentence for
   every UNVERIFIED, so four different reasons were indistinguishable.
4. **A checker's prose must not decide its own verdict.** Tightening
   `verdict_from_text` to the marker-plus-parenthesis form is only correct
   because a test closes the hazard; putting the bare substring back is what
   re-opens it.
5. **The documented channel carries what it says.** `CHRONOA_VERIFY_ARGS` was
   written and read by nothing; the contract now names it, so the env dict
   itself is load-bearing and has to be asserted.
"""

TEST_FILE = "tests/test_post_condition_exit_codes.py"

_V = "usr/lib/shani-chronoa/shani_chronoa/verification.py"
_SKILLS = "usr/lib/shani-chronoa/shani_chronoa/skills/__init__.py"

MUTATIONS = [
    # -- 1. "could not answer" is not a verdict ------------------------------
    {
        "file": _V,
        "old": "    if completed.returncode == _EXIT_FINDINGS:\n"
               "        return Result(Verdict.FAILED, said or \"the post-condition ran and found none\")\n",
        "new": "    if completed.returncode != 2:\n"
               "        return Result(Verdict.FAILED, said or \"the post-condition ran and found none\")\n",
        "label": "the findings code is inverted, so every unusable exit is a finding",
        "expect": ["test_exit_one_is_not_a_finding",
                   "test_no_other_code_is_a_finding"],
    },
    {
        "file": _V,
        "old": "    return Result(Verdict.UNVERIFIED,\n"
               "                  f\"the post-condition could not answer (exit \"\n"
               "                  f\"{completed.returncode})\" + (f\": {said}\" if said else \"\"))\n",
        "new": "    return Result(Verdict.FAILED,\n"
               "                  said or f\"exit {completed.returncode}\")\n",
        "label": "**the shipped behaviour restored verbatim**: a checker that "
                 "could not answer is reported as the action failing",
        "expect": ["test_a_checker_saying_it_could_not_tell_is_unverified",
                   "test_a_wrapper_whose_dependency_is_missing_is_unverified",
                   "test_exit_one_is_not_a_finding"],
    },
    {
        "file": _V,
        "old": "    if completed.returncode == _EXIT_VERIFIED:\n",
        "new": "    if False:\n",
        "label": "exit 0 no longer verifies, so a clean check falls through to a verdict",
        "expect": ["test_exit_zero_verifies",
                   "test_a_binary_that_is_absent_still_reports_the_same_way"],
    },
    {
        "file": _V,
        "old": "                  f\"{completed.returncode})\" + (f\": {said}\" if said else \"\"))\n",
        "new": "                  f\"{completed.returncode})\")\n",
        "label": "the code is named but the checker's own words are dropped",
        # **The half that says *why*.** A verdict naming a code and a verdict
        # naming the reason are different questions, and the second is the one
        # a person can act on.
        "expect": ["test_a_wrapper_whose_dependency_is_missing_is_unverified"],
    },

    # -- 2. the two codes are the two they claim ----------------------------
    {
        "file": _V,
        "old": "_EXIT_FINDINGS = 2\n",
        "new": "_EXIT_FINDINGS = 1\n",
        "label": "the findings code moves to 1, which is what every unhappy "
                 "program returns",
        # **Not the same as the inversion above.** That one breaks the comparison;
        # this one keeps the comparison and changes what it is compared against,
        # so the docstring, the drop-in contract and the reserved code all stop
        # being true at once while the code still looks coherent.
        "expect": ["test_the_two_codes_are_the_two_they_are_documented_as",
                   "test_exit_two_is_the_declared_findings_code",
                   "test_exit_one_is_not_a_finding"],
    },
    {
        "file": _V,
        "old": "_EXIT_VERIFIED = 0\n_EXIT_FINDINGS = 2\n",
        "new": "_EXIT_VERIFIED = 0\n_EXIT_FINDINGS = 0\n",
        "label": "both codes become 0, so no exit can ever be a finding",
        "expect": ["test_the_two_codes_are_the_two_they_are_documented_as"],
    },

    # -- 3. the reason reaches the person -----------------------------------
    {
        "file": _V,
        "old": "        if self.evidence:\n",
        "new": "        if False:\n",
        "label": "every UNVERIFIED reason collapses to the one fixed sentence",
        "expect": ["test_each_reason_is_distinguishable"],
    },
    {
        "file": _V,
        "old": "        return \" (unverified - this action reports success but nothing observed it)\"\n",
        "new": "        return \"\"\n",
        "label": "an UNVERIFIED with no evidence says nothing at all, which "
                 "reads as the claim having been confirmed",
        # **The dangerous direction.** Every other module here records that an
        # absence presented as a success is the failure this whole module exists
        # to prevent - and a bare empty suffix is exactly that.
        "expect": ["test_the_no_evidence_sentence_is_unchanged"],
    },

    # -- 4. a checker's prose must not decide its own verdict ---------------
    {
        "file": _V,
        "old": "    if f\"({FAILED_MARKER}\" in (text or \"\"):\n",
        "new": "    if FAILED_MARKER in (text or \"\"):\n",
        "label": "the marker is matched as a bare substring again, so a "
                 "checker's own words can turn UNVERIFIED into FAILED",
        "expect": ["test_the_reason_is_not_read_as_a_verdict"],
    },
    {
        "file": _V,
        "old": "    if f\"({IMPOSSIBLE_MARKER}\" in (text or \"\"):\n",
        "new": "    if IMPOSSIBLE_MARKER in (text or \"\"):\n",
        "label": "the impossible marker goes back to a bare substring",
        "expect": ["test_the_same_hazard_for_the_impossible_marker"],
    },

    # -- 5. the documented channel carries what it says --------------------
    {
        "file": _V,
        "old": "            env={**os.environ, \"CHRONOA_VERIFY_ARGS\": repr(arguments or {})},\n",
        "new": "            env=dict(os.environ),\n",
        "label": "the environment a drop-in checker is told to read carries "
                 "nothing, while the contract still says it does",
        "expect": ["test_the_arguments_reach_the_checker_as_documented"],
    },
    {
        "file": _V,
        "old": "            env={**os.environ, \"CHRONOA_VERIFY_ARGS\": repr(arguments or {})},\n",
        "new": "            env={**os.environ, \"CHRONOA_VERIFY_ARGS\": repr({})},\n",
        "label": "the checker is handed an empty argument set, which is a "
                 "confident wrong answer rather than a missing one",
        "expect": ["test_the_arguments_reach_the_checker_as_documented"],
    },

    # -- the contract a drop-in author reads, not the one we read -----------
    {
        "file": _SKILLS,
        "old": "**Exit 1 is not a finding.** It used to be treated as one, because there\n",
        "new": "**Exit 1 is a finding.** It used to be treated as one, because there\n",
        "label": "the drop-in contract tells an author the opposite of the truth "
                 "about the code they will run against",
        "expect": ["test_the_contract_names_the_channel_where_drop_in_authors_read"],
    },
    {
        "file": _SKILLS,
        "old": "`CHRONOA_VERIFY_ARGS` in the environment - a `repr()` of the arguments\n",
        "new": "nothing in the environment - a `repr()` of the arguments\n",
        "label": "the contract names a channel it no longer tells anyone about",
        "expect": ["test_the_contract_names_the_channel_where_drop_in_authors_read"],
    },
]
