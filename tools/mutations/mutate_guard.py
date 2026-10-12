"""Mutation set for the guard that stops a reader reading a mutated tree.

`tools/mutate.py` rewrites each source file in place, restores it in a `finally`,
and holds `build/mutate/run.lock` so that two *runs* cannot overwrite each other's
restore. **Nothing told a reader.** Measured 2026-10-12: a suite started beside a
mutation run read mutated code and reported three confident failures on a suite
that had just been green - not flaky, not mine, and indistinguishable from a real
regression.

`pytest_configure` now refuses to collect while the lock is held by a live pid.

**The last mutation here is not in `conftest.py`**, and that is the point. The
guard has to exempt the runner's own pytest, because the runner *is* a pytest
holding the lock on purpose - so if `mutate.py` stops setting
`CHRONOA_MUTATION_RUN`, every mutation run is refused by the guard added beside
it. The marker is a named constant shared by both files and a test holds the two
spellings together, precisely because that failure is silent from the conftest's
side.

**One mutation is deliberately absent, and running it is what showed why.**
Removing the exemption (`if os.environ.get(MUTATION_MARKER)` -> `if False`) is
the obvious mutation to want, and it is **unwitnessable from inside the runner**:
the runner's own pytest would then be refused by the guard, exit 3, print no
summary, and there is nothing to measure. The harness reports that as
*could not parse* rather than as a survivor, which is the correct verdict and
exactly the rule this repository keeps re-learning - but it means the set cannot
carry it. The behaviour is covered instead by
`test_the_marker_wins_over_a_held_lock`, which passes under a plain shell and is
skipped under the runner (the fixture above), and the *coupling* - the half that
can actually rot - is mutation 5 below.
"""

TEST_FILE = "tests/test_mutate_lock_guards_readers.py"

_CONFTEST = "tests/conftest.py"
_MUTATE = "tools/mutate.py"

MUTATIONS = [
    {
        "file": _CONFTEST,
        "old": "    if os.environ.get(MUTATION_MARKER):\n        return None\n",
        "new": "    return None\n    if os.environ.get(MUTATION_MARKER):\n        return None\n",
        "label": "the guard never consults the lock, so it refuses nothing at all",
        "expect": ["test_a_live_lock_is_a_refusal", "test_the_reasons_are_not_about_a_lock_file",
                   "TestTheHookActuallyFires"],
    },
    {
        "file": _CONFTEST,
        "old": "    except OSError:\n"
               "        return None                      # stale - the runner takes these over\n",
        "new": "    except OSError:\n        return \"held by pid \" + str(pid)\n",
        "label": "a stale lock refuses too, so a crashed run blocks every later suite",
        "expect": ["test_a_stale_lock_is_not"],
    },
    {
        "file": _CONFTEST,
        "old": "        pytest.exit(held, returncode=3)\n",
        "new": "        raise RuntimeError(held)\n",
        "label": "a raise in pytest_configure is an INTERNALERROR, not 'wait for the other run'",
        # **A test id, not the class.** The first version of this witness was
        # `["TestTheHookActuallyFires"]` and the runner called the mutation a
        # SURVIVOR while printing two failures - both the right tests. A named
        # witness that matches no test id is a witness that never ran.
        "expect": ["test_configure_exits_while_a_run_holds_the_tree"],
    },
    {
        "file": _MUTATE,
        "old": "    env[MUTATION_MARKER_NAME] = str(os.getpid())\n",
        "new": "    pass\n",
        "label": "the runner stops announcing itself, and the guard refuses every run it spawns",
        "expect": ["test_the_mutation_runner_sets_the_marker"],
    },
]