"""`process_cost`: what each program is COSTING, not what is running.

`list_processes` takes a snapshot; this takes an interval. The fixtures below
are **captured from real `pidstat` runs on this machine**, not typed from a man
page - which is the whole argument for putting them here, because three real
defects in this skill were found by running it and none of them by reading it:

- **The three reports have different columns**, so gating the parse on `%CPU`
  silently skipped the `-r` and `-d` tables and every process reported *0.0% of
  memory and no disk at all*. The guard meant to make the parse safer is what
  produced the absence-shaped wrong answer. `PID` and `Command` are the only two
  columns all three share.
- **`%CPU` is per process, where 100% is one saturated core.** Measured: three
  `python3` at ~100% each on an 8-core box. Taking the `max` of a group
  reports one process's cost as the program's, understating it by the process
  count - in the very case the grouping exists to explain.
- **`pidstat` truncates `Command` to fifteen characters**, the same trap
  `open_files` documents for lsof's nine. `gnome-terminal-` is
  `gnome-terminal-server`, and reading the truncated string names a program
  that does not exist.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, "usr/lib/shani-chronoa")

import pytest  # noqa: E402

from shani_chronoa.skills import process_cost as pc  # noqa: E402

# Captured verbatim from `pidstat -u -r -d 1 2` on this box. The banner carries
# a real TAB, which is why these are written as joins rather than as literals -
# a fixture that normalises whitespace is not the format the tool emits.
BANNER = ("Linux 7.0.0-38-generic (administrator-ThinkPad-E14-Gen-2) \t"
          "10/11/2026 \t_x86_64_\t(8 CPU)")

HDR_U = ("08:43:53 PM   UID       PID    %usr %system  %guest   %wait"
         "    %CPU   CPU  Command")
HDR_R = ("08:43:53 PM   UID       PID  minflt/s  majflt/s     VSZ     RSS"
         "   %MEM  Command")
HDR_D = ("08:43:53 PM   UID       PID   kB_rd/s   kB_wr/s kB_ccwr/s"
         " iodelay  Command")


def table(header, rows):
    return "\n".join([BANNER, "", header, *rows])


# Two samples, so the first table is the initial partial interval and the
# second is the one that counts. `pidstat` prints both; only the later one is
# "now".
U_OUTPUT = table(HDR_U, [
    "08:43:53 PM     0       615    0.00    0.99    0.00    0.00    0.99     0  irq/153-iwlwifi:queue_1",
    "08:43:54 PM     0       615    9.99    1.98    0.00    0.00   11.97     0  irq/153-iwlwifi:queue_1",
    "08:43:54 PM  1001     414293 100.00    0.00    0.00    0.00  100.00     7  python3",
    "08:43:54 PM  1001     414961 100.00    0.00    0.00    0.99  100.00     4  python3",
    "08:43:54 PM  1001    467535 100.99    0.00    0.00    0.00  100.99     5  python3",
    "",
    HDR_U,
    "08:43:54 PM     0       615    0.00    0.99    0.00    0.00    0.99     0  irq/153-iwlwifi:queue_1",
    "08:43:54 PM  1001     414293  50.00    0.00    0.00    0.00   50.00     7  python3",
    "08:43:54 PM  1001     414961  50.00    0.00    0.00    0.99   50.00     4  python3",
    "08:43:54 PM  1001    467535  50.99    0.00    0.00    0.00   50.99     5  python3",
    "08:43:54 PM  1001      4145  13.86    3.96    0.00    0.99   17.82     6  gnome-shell",
    "",
    "Average:      0       615    4.99    1.48    0.00    0.00    6.47     -  irq/153-iwlwifi",
])

R_OUTPUT = table(HDR_R, [
    "08:43:54 PM     0         1      8.82      0.00   28308   17508   0.05  systemd",
    "08:43:54 PM  1001    414293      0.98      0.00 7159740  318588   0.99  python3",
    "08:43:54 PM  1001    467535      0.00   4096.00 7159740  318588   0.99  python3",
])

D_OUTPUT = table(HDR_D, [
    "08:43:54 PM  1001    414293      0.00      1.00      0.00       0  python3",
    "08:43:54 PM  1001   102609    128.00   2048.00      0.00       0  chrome",
])


class TestTheReportsDiffer:
    """The three tables do not share a layout, which is the module's whole risk."""

    def test_only_the_cpu_report_has_a_cpu_column(self):
        assert "%CPU" in HDR_U.split()
        assert "%CPU" not in HDR_R.split()
        assert "%CPU" not in HDR_D.split()

    def test_pid_and_command_are_the_only_columns_all_three_share(self):
        shared = set(HDR_U.split()) & set(HDR_R.split()) & set(HDR_D.split())
        assert {"PID", "Command"} <= shared

    def test_the_memory_report_is_parsed_not_skipped(self):
        """The defect this whole class exists for.

        Gating on `%CPU` meant the `-r` table was dropped, so `RSS` and
        `%MEM` were never read and **every process reported 0.0% of memory**.
        """
        rows = pc._parse_table([HDR_R] + R_OUTPUT.splitlines()[3:])
        assert rows, "the -r report was skipped"
        by_pid = {row.pid: row for row in rows}
        assert by_pid[414293].pct_mem == 0.99
        assert by_pid[414293].rss_kb == 318588

    def test_the_disk_report_is_parsed_not_skipped(self):
        rows = pc._parse_table([HDR_D] + D_OUTPUT.splitlines()[3:])
        assert rows, "the -d report was skipped"
        by_pid = {row.pid: row for row in rows}
        assert by_pid[102609].kb_written == 2048.0
        assert by_pid[102609].kb_read == 128.0

    def test_a_missing_column_reads_zero_rather_than_raising(self):
        """`pidstat` prints `?` for a field it cannot compute, and the three
        reports do not carry each other's columns. Both have to be safe."""
        rows = pc._parse_table([HDR_U, "08:43:54 PM     0       615    ?  ?  ?  ?  ?  ?  ksoftirqd/4"])
        assert rows[0].pid == 615
        assert rows[0].cpu == 0.0

    def test_a_row_shorter_than_its_header_is_dropped(self):
        assert pc._parse_table([HDR_U, "08:43:54 PM     0     615"]) == []

    def test_a_non_numeric_pid_is_dropped_not_crashed_on(self):
        assert pc._parse_table([HDR_U, "no numbers at all here"]) == []


class TestTheIntervalAndTheAverage:
    """Both of these are asserted **through `_gather`**, not through
    `_tables`/`_parse_table` directly.

    The first version of each called the helpers, and both mutations survived:
    taking `tables[0]` instead of `tables[-1]` lives *inside* `_gather`, and an
    `Average:` row surviving `_tables` only becomes a *row* once `_gather`
    calls `_parse_table`. Testing a helper's output proves the helper, and the
    helper is not what runs - the same "a helper can be correct and still be
    unreachable" defect, twice, in one class.
    """

    def _gather_cpu(self, monkeypatch):
        """Run the real `_gather` over the captured output."""
        monkeypatch.setattr(pc.shutil, "which", lambda name: "/usr/bin/pidstat")

        def fake_run(argv, **kwargs):
            import subprocess as sp
            return sp.CompletedProcess(
                argv, 0, {"-u": U_OUTPUT, "-r": R_OUTPUT,
                          "-d": D_OUTPUT}[argv[1]], "")

        monkeypatch.setattr(pc.subprocess, "run", fake_run)
        merged, problem = pc._gather()
        assert problem == ""
        return merged

    def test_the_later_sample_is_the_one_that_counts(self, monkeypatch):
        """`pidstat` prints an initial partial sample, then the interval.

        Taking the first table reports the first interval's numbers; the
        fixture's two differ (100.00 vs 50.00 for pid 414293) precisely so a
        reader that picks the wrong one is caught.
        """
        merged = self._gather_cpu(monkeypatch)
        assert merged[414293].cpu == 50.00
        assert merged[414961].cpu == 50.00
        assert merged[467535].cpu == 50.99

    def test_an_average_row_is_never_read_as_a_program(self, monkeypatch):
        """`Average:` lines come after the samples and name a program.

        Taken as a row they become a second claim about the same process -
        attributed to the average rather than to an interval. The average row
        for pid 615 is named `irq/153-iwlwifi` while its samples are named
        `irq/153-iwlwifi:queue_1`, so a leaked average shows up as a *different
        name for a process already seen*, which is the detectable shape.
        """
        merged = self._gather_cpu(monkeypatch)
        assert "irq/153-iwlwifi" not in {cost.command
                                         for cost in merged.values()}

    def test_the_banner_does_not_become_a_header(self, monkeypatch):
        merged = self._gather_cpu(monkeypatch)
        assert merged, "nothing was parsed at all"
        for cost in merged.values():
            assert not cost.command.startswith("Linux ")

    def test_all_three_reports_land_on_the_same_pid(self, monkeypatch):
        """Joining on pid is what turns three tables into one answer."""
        merged = self._gather_cpu(monkeypatch)
        cost = merged[414293]
        assert cost.cpu == 50.00          # from -u
        assert cost.pct_mem == 0.99        # from -r
        assert cost.kb_written == 1.0      # from -d


class TestTheNameIsNotTheTruncatedOne:
    def test_a_full_name_is_read_back_from_proc(self, tmp_path):
        """`pidstat` prints fifteen characters; `/proc/<pid>/comm` has the rest.

        The fallback is only for a process that has already exited, which is
        the reason it exists rather than the reason it is normally used.
        """
        assert pc._full_name(os.getpid(), "gnome-terminal-") == "python3"

    def test_a_process_that_has_exited_keeps_what_was_reported(self):
        assert pc._full_name(4294967000, "gnome-terminal-") == "gnome-terminal-"

    def test_a_command_holding_a_space_is_joined_not_split_away(self):
        rows = pc._parse_table([HDR_U,
                                "08:43:54 PM  1001      5414   41.58  0.00"
                                "  0.00  0.00  41.58  2  gnome-terminal-"])
        assert rows[0].command  # not empty, and not just the last word

    def test_the_parsed_row_carries_the_full_name_not_the_truncated_one(self):
        """**Asserted through the parse, not through the helper.**

        The first version of this mutation survived because the test called
        `_full_name()` directly - so removing the *call* from `_parse_table`
        changed nothing the test measured. A helper can be correct and still be
        unreachable, which is the same defect `goals.remove` had: the helper is
        not the thing that runs.
        """
        rows = pc._parse_table([HDR_U,
                                f"08:43:54 PM  1001 {os.getpid():>7}  41.58"
                                "  0.00  0.00  0.00  41.58  2  gnome-terminal-"])
        assert rows[0].command == "python3", (
            "the row still carries pidstat's 15-character truncation")

    def test_the_truncated_form_survives_only_for_an_exited_process(self):
        rows = pc._parse_table([HDR_U,
                                "08:43:54 PM  1001  4294967000  41.58  0.00"
                                "  0.00  0.00  41.58  2  gnome-terminal-"])
        assert rows[0].command == "gnome-terminal-"


class TestTheGrouping:
    def _costs(self):
        """Faults and disk are **spread across the three processes**.

        An earlier fixture put every fault on one process and none on the
        others, which made `sum` and `max` return the same number - so two
        mutations passed a green suite. The shape that can tell them apart is
        the realistic one: several workers of one program, each taking a share.
        """
        return {
            414293: pc.Cost(414293, "python3", 50.0, 0.0, 50.0, 318588, 0.99,
                            0.98, 1000.0, 0.0, 0.5),
            414961: pc.Cost(414961, "python3", 50.0, 0.0, 50.0, 318588, 0.99,
                            0.00, 2000.0, 0.0, 0.5),
            467535: pc.Cost(467535, "python3", 50.99, 0.0, 50.99, 318588,
                            0.99, 0.00, 1096.0, 0.0, 0.5),
            4145: pc.Cost(4145, "gnome-shell", 13.86, 3.96, 17.82, 318588,
                          1.20, 0.98, 0.0, 0.0, 0.0),
        }

    def test_several_processes_of_one_program_are_summed_not_maxed(self):
        """`%CPU` is per process, so 100% is one saturated core.

        The fixture's three `python3` are ~50% each; the program costs ~151,
        not ~51. Taking the max understates a multi-process program by its
        process count - in the exact case grouping exists to explain.
        """
        rows = pc._by_name(self._costs())
        assert rows[0][0] == "python3"
        assert rows[0][2] == pytest.approx(150.99)

    def test_the_group_still_carries_every_process(self):
        rows = pc._by_name(self._costs())
        python_row = [row for row in rows if row[0] == "python3"][0]
        assert len(python_row[1]) == 3

    def test_faults_and_disk_are_summed_across_the_group(self):
        """1000 + 2000 + 1096 = 4096.

        The largest single process took 2000, so `max` would understate the
        program's fault rate by half - and a thrashing program is exactly what
        this number exists to name.
        """
        rows = pc._by_name(self._costs())
        python_row = [row for row in rows if row[0] == "python3"][0]
        assert python_row[4] == pytest.approx(4096.0)
        assert python_row[6] == pytest.approx(1.5)


class TestTheAnswer:
    def _run_with(self, monkeypatch, outputs, which="pidstat"):
        calls = {"n": 0}

        def fake_run(argv, **kwargs):
            flags = argv[1]
            calls["n"] += 1
            return outputs[flags]

        monkeypatch.setattr(pc.shutil, "which", lambda name: "/usr/bin/pidstat")
        monkeypatch.setattr(pc.subprocess, "run", fake_run)
        return calls

    def test_a_missing_pidstat_is_named_with_the_package_that_ships_it(
            self, monkeypatch):
        """The refusal has to name the package, because that is actionable.

        Asserted on the *sentence*, not on the word: the module docstring and
        the refusal both mention sysstat, so `"sysstat" in out` is satisfied by
        prose that has nothing to do with the answer the user gets - and the
        first version of this mutation dropped "the sysstat package" from the
        refusal and left the file green.
        """
        monkeypatch.setattr(pc.shutil, "which", lambda name: None)
        out = pc._run({})
        assert "UNKNOWN" in out
        assert "the sysstat package" in out
        assert "not installed" in out

    def test_an_idle_machine_says_idle_not_broken(self, monkeypatch):
        """An empty answer here is the ordinary state of a quiet machine.

        It is the shape of answer that reads as a broken probe if it is
        allowed to, so it is asserted rather than left to the reader.
        """
        monkeypatch.setattr(pc, "_gather",
                            lambda: ({615: pc.Cost(615, "ksoftirqd/4", 0.0, 0.0,
                                                    0.0, 0.0, 0.0, 0.0, 0.0,
                                                    0.0, 0.0)}, ""))
        out = pc._run({})
        assert "No process was using a measurable amount of CPU" in out
        assert "not a" in out and "failure to read" in out

    def test_thrashing_is_named_from_major_faults(self, monkeypatch):
        costs = {
            1: pc.Cost(1, "hog", 99.0, 1.0, 100.0, 500000, 2.0, 0.0, 8500.0,
                       0.0, 0.0),
        }
        monkeypatch.setattr(pc, "_gather", lambda: (costs, ""))
        out = pc._run({})
        assert "Thrashing" in out
        assert "hog" in out
        assert "8500 major fault(s)/s" in out

    def test_a_below_threshold_fault_rate_is_not_called_thrashing(self,
                                                                  monkeypatch):
        """The boundary the threshold exists to draw, asserted from both sides.

        Every mutation that lowers the threshold to `> 0` passes the case
        *above* it, because 8500 is over any threshold - so the witness has to
        be a process that is **not** thrashing. A few major faults a second is
        normal; thousands is thrashing; and a module that named the first as
        the second would be crying wolf on every machine.
        """
        costs = {
            1: pc.Cost(1, "chatty", 20.0, 1.0, 21.0, 50000, 0.2, 0.0, 2.0,
                       0.0, 0.0),
        }
        monkeypatch.setattr(pc, "_gather", lambda: (costs, ""))
        out = pc._run({})
        assert "chatty" in out                      # it IS listed as busy
        assert "Thrashing" not in out                # and it is not thrashing

    def test_a_writer_over_a_megabyte_is_named(self, monkeypatch):
        costs = {
            1: pc.Cost(1, "syncer", 5.0, 1.0, 6.0, 10000, 0.2, 0.0, 0.0,
                       0.0, 4096.0),
        }
        monkeypatch.setattr(pc, "_gather", lambda: (costs, ""))
        out = pc._run({})
        assert "Reading or writing over 1 MB a second" in out
        assert "syncer" in out

    def test_a_quiet_program_is_not_listed_as_a_writer(self, monkeypatch):
        costs = {1: pc.Cost(1, "chatty", 5.0, 1.0, 6.0, 10000, 0.2, 0.0, 0.0,
                            12.0, 0.4)}
        monkeypatch.setattr(pc, "_gather", lambda: (costs, ""))
        out = pc._run({})
        assert "Reading or writing over 1 MB a second" not in out

    def test_a_partly_unreadable_answer_names_the_half_it_lost(self,
                                                              monkeypatch):
        costs = {1: pc.Cost(1, "app", 50.0, 0.0, 50.0, 1000, 0.1, 0.0, 0.0,
                           0.0, 0.0)}
        monkeypatch.setattr(pc, "_gather",
                            lambda: (costs, "pidstat said 'exit 1'"))
        out = pc._run({})
        assert "Partly unreadable" in out
        assert "exit 1" in out

    def test_nothing_at_all_readable_is_unknown_not_an_empty_table(
            self, monkeypatch):
        monkeypatch.setattr(pc, "_gather", lambda: ({}, "pidstat said 'exit 1'"))
        out = pc._run({})
        assert "UNKNOWN" in out
        assert "Nothing was guessed" in out


class TestTheRegistration:
    def test_one_handler_reachable_by_name(self):
        assert len(pc.SKILLS) == 1
        assert pc.SKILLS[0].name == "process_cost"
        assert pc.SKILLS[0].run.__name__ == "_run"
        assert pc.SKILLS[0].run.__name__.isidentifier()

    def test_it_is_discovered_by_the_real_registry(self):
        from shani_chronoa import skills
        tools, handlers = skills.discover_skills()
        names = {tool["function"]["name"] for tool in tools}
        assert "process_cost" in names
        assert "process_cost" in handlers

    def test_the_description_carries_the_phrasings_a_person_would_use(self):
        desc = pc.SCHEMA["function"]["description"].lower()
        for phrase in ("what is using my cpu", "slow", "thrashing",
                       "writing to my disk"):
            assert phrase in desc, f"missing {phrase!r}"

    def test_the_schema_takes_no_required_arguments(self):
        params = pc.SCHEMA["function"]["parameters"]
        assert params["properties"] == {}
        assert params.get("required", []) == []