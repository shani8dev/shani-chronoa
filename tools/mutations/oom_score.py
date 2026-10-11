"""Mutation set for the `oom_score` skill.

Two of these are defects the skill actually shipped with before the tests were
written, and both were found by running it on this box rather than by reading
it:

1. **`adj` reported as one value per name.** chrome carries 300 on 15
   processes, 200 on 4 and 100 on 4, and `group[0].adj` printed `300` for all
   23. The distribution is the fact; the single number is a claim about
   processes carrying something else.
2. **Top-N instead of a threshold.** On a box with six readable processes the
   list ended with one scoring 2 of 1000, printed under "who the OOM killer
   would pick first" - the tail of a short list reading as a candidate.

A third, the fixture that patched `pathlib.Path.iterdir`, is not a mutation
here but the reason `_PROC` exists as a named seam.

The rest are the module's own stated invariants: `oom_kill 0` is a measurement
rather than a failure, `some` is not `full`, an unreadable process is not a
zero, and the default adjustment is not a deliberate one.
"""

from __future__ import annotations

TEST_FILE = "tests/test_oom_score.py"

_SKILL = "usr/lib/shani-chronoa/shani_chronoa/skills/oom_score.py"

MUTATIONS = [
    # ---- the two shipped defects ----------------------------------------
    {
        "label": "one process's adjustment is reported as every process's",
        "file": _SKILL,
        "old": '            counts = grouped.setdefault(target.name, {})\n'
               '            counts[target.adj] = counts.get(target.adj, 0) + 1',
        "new": '            counts = grouped.setdefault(target.name, {})\n'
               '            counts[target.adj] = counts.get(target.adj, 0) + 1\n'
               '            grouped[target.name] = {target.adj: len(\n'
               '                [t for t in moved if t.name == target.name])}',
        "expect": ["test_every_distinct_adjustment_is_named",
                   "test_one_processes_value_is_not_reported_as_all_of_them"],
    },
    {
        "label": "the list is the top N rather than the ones above the line",
        "file": _SKILL,
        "old": '    at_risk = sorted((t for t in targets if t.score >= _AT_RISK),\n'
               '                     key=lambda t: -t.score)[:10]',
        "new": '    at_risk = sorted(targets,\n'
               '                     key=lambda t: -t.score)[:10]',
        "expect": ["test_a_low_score_is_not_listed_as_a_candidate",
                   "test_nothing_above_the_line_says_so"],
    },

    # ---- what the numbers mean -----------------------------------------
    {
        "label": "a missing kill counter reads as zero kills",
        "file": _SKILL,
        "old": '            except ValueError:\n'
               '                return None\n'
               '    return None',
        "new": '            except ValueError:\n'
               '                return None\n'
               '    return 0',
        "expect": ["test_a_missing_counter_is_named_not_shown_as_zero"],
    },
    {
        "label": "the some block is read by position instead of by name",
        "file": _SKILL,
        "old": '        if parts[0].rstrip(":") != "some":\n'
               '            continue',
        "new": '        if parts[0].rstrip(":") not in ("some", "full"):\n'
               '            continue',
        # **The reversed-file witness is the load-bearing one.** The first block
        # in `/proc/pressure/memory` *is* `some`, so reading by position agrees
        # with the real file; only the reordering separates the two designs.
        "expect": ["test_a_reversed_file_still_reads_some",
                   "test_the_full_block_is_not_mistaken_for_the_some_block"],
    },
    {
        "label": "a missing PSI file loses its reason rather than being named",
        "file": _SKILL,
        "old": '    except FileNotFoundError:\n'
               '        return None, (f"{_PSI} is absent: this kernel has no PSI, so memory "\n'
               '                      "pressure cannot be measured here")',
        "new": '    except FileNotFoundError:\n'
               '        return None, ""',
        "expect": ["test_a_missing_psi_file_is_not_zero_pressure"],
    },
    {
        "label": "an unreadable process is counted as score zero",
        "file": _SKILL,
        "old": '        try:\n'
               '            score = float((entry / "oom_score").read_text().strip())\n'
               '        except (OSError, ValueError):\n'
               '            continue',
        "new": '        try:\n'
               '            score = float((entry / "oom_score").read_text().strip())\n'
               '        except (OSError, ValueError):\n'
               '            score = 0.0',
        # **A `0` reads as "the kernel would never pick this".** For a process
        # nobody could read that is a claim nobody made, in the reassuring
        # direction - the same absence-shaped answer as a missing kill counter
        # of zero.
        "expect": ["test_an_unreadable_process_is_skipped_not_counted_as_zero"],
    },
    {
        "label": "the default adjustment is reported as a deliberate one",
        "file": _SKILL,
        "old": '    return [t for t in targets if t.adj not in (None, 0)]',
        "new": '    return [t for t in targets if t.adj is not None]',
        "expect": ["test_zero_is_not_an_adjustment"],
    },
    {
        "label": "the ranking is presented as a proximity to death",
        "file": _SKILL,
        "old": '        lines.append(\n'
               '            "  the score is where the kernel would start looking, not a "\n'
               '            "proximity to death: nothing on a machine with free memory is "\n'
               '            "close to being killed")',
        "new": '        lines.append(\n'
               '            "  the score is how close this process is to being killed.")',
        "expect": ["test_the_ranking_is_stated_not_as_a_countdown"],
    },
    {
        "label": "the disclosure that pressure is not free memory is dropped",
        "file": _SKILL,
        "old": '        lines.append(\n'
               '            "  that is the share of time a task was stalled waiting on a "\n'
               '            "memory page - not how much memory is free, which is a different "\n'
               '            "question")',
        "new": '        pass',
        "expect": ["test_pressure_says_what_it_is_not"],
    },
    {
        "label": "a real kill count is not shown",
        "file": _SKILL,
        "old": '    if kills:\n'
               '        lines.append(\n'
               '            f"The kernel has OOM-killed {kills} process(es) since boot.")',
        "new": '    if kills:\n'
               '        pass',
        "expect": ["test_kills_are_reported_with_the_count"],
    },
    {
        "label": "proc is hardcoded instead of read through the named seam",
        "file": _SKILL,
        "old": '        entries = list(_PROC.iterdir())',
        "new": '        entries = list(Path("/proc").iterdir())',
        # **Bare test names, not the class.** The runner matches a witness as a
        # *prefix* of `file.py::TestC::test_y`, so naming `TestReadingProcesses`
        # matched nothing and the run reported a survivor while ten tests were
        # failing for exactly the right reason.
        "expect": ["test_a_readable_process_is_carried_whole",
                   "test_a_process_vacated_mid_walk_is_skipped",
                   "test_an_unreadable_process_is_skipped_not_counted_as_zero"],
    },
]
