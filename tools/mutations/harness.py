"""Mutation set for the mutation harness itself.

The file this one checks is `tools/mutate.py` - the tool every other set in
this directory is evidence produced by. A claim that a harness "cannot lie" is
worth least when nothing tests the harness, and OmO's own rule is the reason:
*"A failing run is not a successful negative control unless the intended check
failed **and** both the process and fixture directory were cleaned up."*

Three things are covered, and the third is the one the harness got wrong:

1. **The lock refuses a second run**, naming the holder.
2. **A stale lock is taken over** - a crashed run must not deadlock the next
   attempt, and taking it silently would be the same class as silently deleting
   a rotated anchor.
3. **The journal is written and cleared in that order.** The order is a real
   window: cleared first, there is a gap in which the tree is restored and the
   journal still says otherwise, and a run killed in that gap recovers nothing -
   which is the entire failure the journal exists to catch.

**And, because `run_set` restores the file it mutated, a set that aims the
harness at itself is safe to run** - the journal records `tools/mutate.py`, and
the next run puts it back even if this one is killed. That is not a claim: it is
what happened on 2026-10-11, when two overlapping runs left two mutations stuck
applied to `fm_radio.py` and `fmdsp.py` and this directory's whole guarantee had
to be re-established by hand.
"""

TEST_FILE = "tests/test_mutation_harness_can_fail.py"

_RUNNER = "tools/mutate.py"

#: **A witness that fires for every mutation in this set, and that is the
#: honest reason it is here.** Every entry below edits `tools/mutate.py`, and
#: every one of its anchors is text `test_no_anchor_has_rot` reads - so the
#: rot check correctly reports rot the moment any of them is applied. It
#: cannot say *which* mutation broke what, and a constant witness is not
#: evidence about a particular mutation. What it does establish is that a
#: mutation was applied and the harness noticed, which is what "caught"
#: means; the specific test each mutation targets is named alongside it.
#:
#: **The first version of this set had no such entry**, and four of seven
#: mutations came back survivors reporting exactly this one failure - the
#: mutation broke the code, the check that should have said so did say so,
#: and the runner called it a survivor because a *named* witness did not
#: fail. That is the runner's rule working as designed (a whole-file red for
#: another reason is not a catch) meeting a case where the other reason *is*
#: this mutation.
ROT_CHECKS_THE_FILE_TOO = "test_no_anchor_has_rot"

MUTATIONS = [
    # -- 1. a second run is refused ------------------------------------------
    {
        "file": _RUNNER,
        "old": "        try:\n            os.kill(pid, 0)\n        except OSError:\n"
               "            return True\n        return False\n",
        "new": "        return True\n",
        "label": "every lock is stale, so a second run never waits or refuses",
        # **The first version of the busy-path mutation replaced the deadline
        # test with `if False:`, and it hung the whole run** - the loop then
        # spins on `FileExistsError` forever instead of raising, and the test
        # that is supposed to see the refusal waits with it. A mutation that
        # makes the code hang is still a mutation, and `RUN_TIMEOUT` (900 s,
        # lowered to 300 below) is the only thing that eventually ends it.
        # Breaking `_stale` instead breaks the same rule without hanging: every
        # lock looks dead, so the second run takes it and the refusal never
        # happens.
        "expect": ["test_a_second_run_is_refused_and_says_whose"],
    },

    # -- 2. a stale lock is taken over ---------------------------------------
    {
        "file": _RUNNER,
        "old": "        try:\n            os.kill(pid, 0)\n        except OSError:\n"
               "            return True\n        return False\n",
        "new": "        return False\n",
        "label": "no lock is ever stale, so a crashed run blocks the next one",
        # **The mirror of the mutation above, from the other side**: the same
        # function, answering the opposite way, and witnessed by the other test.
        # Two sets' worth of meaning in one four-line predicate.
        "expect": ["test_a_stale_lock_is_taken_over_and_says_so"],
    },
    {
        "file": _RUNNER,
        "old": "                    self.path.unlink(missing_ok=True)\n"
               "                    continue\n",
        "new": "                    pass\n",
        "label": "a stale lock is noticed and then not taken over",
        "expect": ["test_a_stale_lock_is_taken_over_and_says_so"],
    },

    # -- 3. the journal, and its order ----------------------------------------
    {
        "file": _RUNNER,
        "old": "            _journal_write(originals)\n",
        "new": "",
        "label": "nothing is journalled, so a killed run leaves no trace",
        # **Not the recovery test**, which writes the journal itself and so
        # passes either way. The claim is that `run_set` writes it, and before
        # it mutates, which is the order this records.
        "expect": ["test_the_journal_brackets_the_test_run"],
    },
    {
        "file": _RUNNER,
        "old": "        path.write_text(original, encoding=\"utf-8\")\n"
               "        recovered.append(name)\n",
        "new": "        recovered.append(name)\n",
        "label": "the recovery reports success without restoring anything",
        "expect": ["test_a_killed_runs_mutations_are_put_back_by_the_next_run"],
    },
    {
        "file": _RUNNER,
        "old": "            _journal_clear()\n",
        "new": "",
        "label": "the journal is never cleared, so the next run repeats a recovery",
        # **Not the recovery test**: that one calls `_journal_clear` itself, so
        # removing the call inside `run_set` leaves it green. The claim is that
        # `run_set` clears it *last*.
        "expect": ["test_the_journal_brackets_the_test_run"],
    },

    # -- the count rule itself, which every other set depends on -------------
    # **Aimed at `_apply`, not at `_check_anchors` - and the first version was
    # not.** The two functions state the same rule, and relaxing it in the one
    # the fixture test does not reach left the mutation unwitnessed while
    # looking exactly as load-bearing as the other six. The witness runs the real
    # dispatch path, so only the copy on that path can break it.
    {
        "file": _RUNNER,
        "old": "        assert text.count(old) == 1, (\n",
        "new": "        assert text.count(old) != 0, (\n",
        "label": "an anchor occurring twice is accepted, so both sites are rewritten",
        # **Not the absent-anchor test**: that one covers count 0, which
        # `!= 0` still catches. "Exactly once" has two halves and the second had
        # no witness until this.
        "expect": ["test_an_anchor_occurring_twice_is_refused"],
    },

    # -- the two rules `count == 1` does not imply --------------------------
    # Both were the same shape of false evidence: a survivor reported against a
    # file that was never modified. `str.replace(old, old)` matches once and
    # rewrites the file with itself, and the no-op guard passes it; a splice that
    # does not parse is refused downstream by `MutationDidNotParse` **after** the
    # malformed file is on disk, where pytest cannot collect it.
    {
        "file": _RUNNER,
        "old": "        assert new != old, (\n",
        "new": "        assert new is not old or True, (\n",
        "label": "the no-op guard is gone, so a mutation can replace its anchor with itself",
        # Three witnesses, and they are three different levels on purpose: the
        # run's *report* (a survivor is the defect), the refusal itself, and the
        # message a person has to act on. Byte-identity is not among them,
        # because `str.replace(old, old)` leaves the file identical either way -
        # which is why the file-level test asserts the refusal and not the bytes.
        "expect": [
            "test_a_mutation_that_replaces_its_anchor_with_itself_is_refused",
            "test_a_no_op_mutation_is_refused_at_the_source",
            "test_the_refusal_says_why_rather_than_only_that_it_failed",
        ],
    },
    {
        "file": _RUNNER,
        "old": "    try:\n        ast.parse(text)\n",
        "new": "    try:\n        pass\n",
        "label": "the parse guard is gone, so a malformed mutation is written first",
        # The same three levels. `test_a_malformed_mutation_never_reaches_the_file`
        # is the only one that can see *never written*: through `run_set` a
        # `finally` restores the file, so byte-identity there is consistent with
        # both "never written" and "written then put back".
        "expect": [
            "test_a_malformed_mutation_is_refused_before_it_is_run",
            "test_a_malformed_mutation_never_reaches_the_file",
            "test_the_refusal_says_why_rather_than_only_that_it_failed",
        ],
    },
    {
        "file": _RUNNER,
        # **A well-meaning addition, not a deletion.** The whole-text parse is
        # *kept* and a per-pair parse added beside the no-op guard - which is what
        # "validate each edit as we go" looks like. Deleting the whole-text parse
        # instead would be the same defect as the mutation above and would be
        # caught by the same three tests, so it measures nothing about placement.
        #
        # **`pairs` is `[old, new] = zip(...)`, so one mutation may be several
        # edits that must land together.** A per-pair parse demands that each half
        # be *independently* valid Python, which the format does not promise: a
        # half that opens a construct is legal in a pair and unparseable alone.
        # Measured on this tree: 4 pair-anchor mutations across 3 sets and 0
        # halves that fail to parse alone - so a per-pair parse breaks nothing
        # *today*, and this is the only witness for the shape.
        "old": "        assert new != old, (\n",
        "new": (
            "        assert new != old, (\n"
            "        try:\n"
            "            ast.parse(text)\n"
            "        except SyntaxError as exc:\n"
            "            raise AssertionError('refused per pair') from exc\n"
        ),
        "label": "each pair is parsed as it is applied, so a pair that is only "
                 "jointly valid is refused",
        # **One witness, and it is the only test in the file that can see this.**
        # Measured: 1 failed, and the three tests above stay green - the
        # placement mutation is invisible to every other assertion here.
        "expect": [
            "test_a_pair_anchor_whose_halves_do_not_parse_alone_still_applies",
        ],
    },
]
