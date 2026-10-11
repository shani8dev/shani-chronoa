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
]
