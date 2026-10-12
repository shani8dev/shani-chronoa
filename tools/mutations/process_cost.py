"""Mutation sets for the two pieces the matrix pass added.

- `process_cost` - `pidstat`'s three reports have **different columns**, the
  sample is an interval rather than a snapshot, `%CPU` is per-process, and the
  `Command` column is truncated. Each of those is a way to produce a
  confident wrong number, and each is a mutation here.
- the `security` sense's AppArmor half - the one that must never turn "I could
  not read this" into "there is nothing there".

The AppArmor mutation `loaded` reports the installed count is the important one:
it is the mistake the whole measurement was taken to avoid, and it is exactly
what `aa-status --json` does unprivileged (measured: rc=4, stdout `[]`).
"""

from __future__ import annotations

TEST_FILE = "tests/test_process_cost.py"

_SKILL = "usr/lib/shani-chronoa/shani_chronoa/skills/process_cost.py"

MUTATIONS = [
    # ---- process_cost: the parse ---------------------------------------
    {
        "label": "the report is gated on %CPU, which only one of the three has",
        "file": _SKILL,
        "old": '    if "PID" not in header or "Command" not in header:',
        "new": '    if "PID" not in header or "%CPU" not in header:',
        "expect": ["test_the_memory_report_is_parsed_not_skipped",
                   "test_the_disk_report_is_parsed_not_skipped"],
    },
    {
        "label": "columns are read by position instead of by name",
        "file": _SKILL,
        "old": "        def value(name: str) -> float:\n"
               "            if name not in index:\n"
               "                return 0.0\n"
               "            return _number(cells[index[name]]) or 0.0",
        "new": "        def value(name: str, position: int = 2) -> float:\n"
               "            return _number(cells[position]) or 0.0",
        "expect": ["test_the_memory_report_is_parsed_not_skipped"],
    },
    {
        "label": "the truncated command name is reported as the program",
        "file": _SKILL,
        "old": '            command=_full_name(int(pid_text),\n'
               '                               " ".join(cells[index["Command"]:])),',
        "new": '            command=" ".join(cells[index["Command"]:]),',
        "expect": ["test_the_parsed_row_carries_the_full_name_not_the"
                   "_truncated_one"],
    },

    # ---- process_cost: which sample is "now" ---------------------------
    {
        "label": "the first sample is read instead of the later one",
        "file": _SKILL,
        "old": "        for cost in _parse_table(tables[-1]):",
        "new": "        for cost in _parse_table(tables[0]):",
        "expect": ["test_the_later_sample_is_the_one_that_counts"],
    },
    {
        "label": "Average rows are parsed as if they were samples",
        "equivalent": True,
        "file": _SKILL,
        "old": '        if line.startswith("Linux ") or line.startswith("Average:"):',
        "new": '        if line.startswith("Linux "):',
        # **A deliberate, documented survivor.** `pidstat` prints the Average
        # block on its own after a blank line, so it forms a table that
        # `_parse_table` already rejects on its own terms - it has no `PID`
        # header. The guard is defence in depth against a *different* pidstat
        # layout, not the load-bearing one, and no test can fail on removing it
        # without inventing an output shape the tool does not produce. It is
        # kept for that reason and named here rather than left looking tested.
        # **`equivalent: True` was missing here for the same reason as
        # `privacy_device.py`**: the reason was written in prose where the runner
        # wants a key, so the inventory reported a documented equivalent as a
        # SURVIVOR.
        "equivalent": True,
        "why": "pidstat prints its Average block after a blank line and it carries "
               "no `PID` header, so `_parse_table` already rejects it on its own "
               "terms. The guard is defence in depth against a different pidstat "
               "layout, not the load-bearing check, and no test can fail on "
               "removing it without inventing an output shape the tool does not "
               "produce",
        "expect": [],
    },

    # ---- process_cost: what a number means -----------------------------
    {
        "label": "a program's cost is maxed instead of summed across processes",
        "file": _SKILL,
        "old": "                     sum(item.cpu for item in group),",
        "new": "                     max(item.cpu for item in group),",
        "expect": ["test_several_processes_of_one_program_are_summed_not_maxed"],
    },
    {
        "label": "major faults are not summed across the group",
        "file": _SKILL,
        "old": "                     sum(item.majflt for item in group),",
        "new": "                     max(item.majflt for item in group),",
        "expect": ["test_faults_and_disk_are_summed_across_the_group"],
    },
    {
        "label": "the thrashing threshold is removed",
        "file": _SKILL,
        "old": "        if majflt >= _THRASHING:",
        "new": "        if majflt > 0:",
        # Two witnesses, and the second is the load-bearing one: a *lower*
        # threshold still names 8500 major faults/s as thrashing, so the case
        # above the line cannot tell `>= 10.0` from `> 0`. Only a process that
        # is **not** thrashing draws the distinction - a few faults a second is
        # ordinary, and calling it thrashing would cry wolf on every machine.
        "expect": ["test_a_below_threshold_fault_rate_is_not_called_thrashing",
                   "test_thrashing_is_named_from_major_faults"],
    },
    {
        "label": "a one-megabyte write is no longer reported",
        "file": _SKILL,
        "old": "        if kb_written >= 1024.0 or kb_read >= 1024.0:",
        "new": "        if kb_written >= 1024.0 * 1024.0 or kb_read >= 1024.0 * 1024.0:",
        "expect": ["test_a_writer_over_a_megabyte_is_named"],
    },
    {
        "label": "an idle machine is reported as an empty table",
        "file": _SKILL,
        "old": '        return ("No process was using a measurable amount of CPU in the last "\n'
               '                "second. That is what an idle machine looks like - not a "\n'
               '                "failure to read.")',
        "new": '        return "No processes."',
        "expect": ["test_an_idle_machine_says_idle_not_broken"],
    },
    {
        "label": "a partly unreadable answer hides the half that was lost",
        "file": _SKILL,
        "old": '        lines.append(f"Partly unreadable: {problem}. The rows above are real; "\n'
               '                     "the missing half is named rather than shown as zero.")',
        "new": "        pass",
        "expect": ["test_a_partly_unreadable_answer_names_the_half_it_lost"],
    },
    {
        "label": "a missing pidstat is not named",
        "file": _SKILL,
        "old": '    if shutil.which("pidstat") is None:\n'
               '        return ("Process cost is UNKNOWN: pidstat (the sysstat package) is not "',
        "new": '    if shutil.which("pidstat") is None:\n'
               '        return ("Process cost is UNKNOWN: pidstat is not "',
        "expect": ["test_a_missing_pidstat_is_named_with_the_package_that_ships_it"],
    },
]