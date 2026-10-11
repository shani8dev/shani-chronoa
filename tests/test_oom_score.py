"""`oom_score`: who the kernel would kill, and what that number is not.

Every fact here is read from `/proc` on this machine, so the fixtures are the
files as the kernel writes them rather than plausible-shaped ones. Three real
facts from the live box, all of which the first version got wrong:

- **chrome carries three different `oom_score_adj` values** - 300 on 15
  processes, 200 on 4, 100 on 4. The first version grouped by name and printed
  `group[0].adj`, so it named one process's policy as though it were all of
  them. `chrome: adj 300 on 15, adj 200 on 4, adj 100 on 4` is the fact;
  `adj 300 on 23` is a confident number about processes carrying something
  else. `TestTheAdjustmentIsADistribution` is that regression.
- **one `dbus-daemon` carries `adj -900`**, the opposite sign - the desktop
  asked the kernel never to kill its own session bus. A reader that reports
  only raised numbers loses the most interesting one on the box.
- **`whisper-cli` was the largest thing here at 927**, holding 7.5 GB, which
  nothing else in the tree would have mentioned.

**`oom_kill: 0` and "no counter" are different answers**, the same
absent-versus-empty split `read_lsms` records, and `0` is the honest state of
this machine rather than a failure to read.
"""

from __future__ import annotations

import sys

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa.skills import oom_score as om  # noqa: E402

# `/proc/pressure/memory` verbatim from this box. **`some` comes first and
# `full` second, which is the order that makes an order-dependent reader read
# the wrong block** - so the fixture keeps it.
PSI_REAL = ("some avg10=0.36 avg60=0.40 avg300=0.20 total=14480282\n"
            "full avg10=0.00 avg60=0.00 avg300=0.04 total=12446230\n")


def _fake_proc(tmp_path, monkeypatch, rows, *, vmstat="oom_kill 0\n",
               psi=None):
    """A `/proc` tree of real `oom_score`/`comm`/`oom_score_adj` files.

    **Real files, not a stubbed `_targets`.** The parse is what is being
    tested, and a stubbed `_targets` hands back `Target` objects and tests the
    renderer alone - the "a helper can be correct and still be unreachable"
    defect this repo records.

    `rows` is `(pid, comm, score, adj, rss_kb)`.
    """
    root = tmp_path / "proc"
    root.mkdir()
    for pid, comm, score, adj, rss in rows:
        entry = root / str(pid)
        entry.mkdir()
        (entry / "oom_score").write_text(f"{score}\n")
        (entry / "comm").write_text(f"{comm}\n")
        (entry / "oom_score_adj").write_text(f"{adj}\n")
        (entry / "status").write_text(
            f"Name:\t{comm}\nUid:\t1001\t1001\t1001\t1001\n"
            f"VmRSS:\t{rss} kB\n")
    vm = tmp_path / "vmstat"
    vm.write_text(vmstat)
    monkeypatch.setattr(om, "_VMSTAT", vm)

    if psi is None:
        monkeypatch.setattr(om, "_PSI", tmp_path / "no-psi")
    else:
        path = tmp_path / "memory"
        path.write_text(psi)
        monkeypatch.setattr(om, "_PSI", path)
    # **The proc root, not `pathlib.Path.iterdir`.** Patching the stdlib's
    # `iterdir` changes it for every file in the process - which is how the
    # first version of this fixture broke twelve tests for reasons unrelated to
    # the parse.
    monkeypatch.setattr(om, "_PROC", root)
    return root


class TestThePressureReading:
    def test_the_some_block_is_read(self, tmp_path, monkeypatch):
        _fake_proc(tmp_path, monkeypatch, [], psi=PSI_REAL)
        reading, why = om._read_pressure()
        assert why == ""
        assert reading.windows["avg10"] == 0.36
        assert reading.windows["avg300"] == 0.20
        assert reading.total_us == 14480282

    def test_the_full_block_is_not_mistaken_for_the_some_block(
            self, tmp_path, monkeypatch):
        """`some` is how often *any* task stalled; `full` is how often *all*
        did. Quoting one as the other overstates an ordinary machine, so the
        reader takes `some` by name rather than by position - and `full`'s
        `total` is 4.6s lower, which is what a wrong-block read shows up as.
        """
        _fake_proc(tmp_path, monkeypatch, [], psi=PSI_REAL)
        reading, _ = om._read_pressure()
        assert reading.total_us == 14480282, "read full's total, not some's"

    def test_a_reversed_file_still_reads_some(self, tmp_path, monkeypatch):
        """A reader that takes the first block is right here only by accident.

        Reordering the file is the control that tells `some`-by-name from
        `some`-by-position apart.
        """
        reordered = (PSI_REAL.splitlines()[1] + "\n"
                     + PSI_REAL.splitlines()[0] + "\n")
        _fake_proc(tmp_path, monkeypatch, [], psi=reordered)
        reading, why = om._read_pressure()
        assert why == "" and reading is not None
        assert reading.windows["avg10"] == 0.36

    def test_a_missing_psi_file_is_not_zero_pressure(self, tmp_path,
                                                     monkeypatch):
        """A machine without PSI reads as *unmeasurable*, not "no pressure".

        Reporting 0.00% would be the absence-shaped wrong answer in the
        reassuring direction - the one nobody notices.
        """
        _fake_proc(tmp_path, monkeypatch, [])
        reading, why = om._read_pressure()
        assert reading is None
        assert "absent" in why and "PSI" in why

    def test_an_unparseable_number_is_refused_not_defaulted(self, tmp_path,
                                                            monkeypatch):
        _fake_proc(tmp_path, monkeypatch, [],
                   psi="some avg10=?? avg60=0.4 avg300=0.2\n")
        reading, why = om._read_pressure()
        assert reading is None
        assert "avg10" in why

    def test_a_file_with_no_avg_window_is_refused(self, tmp_path, monkeypatch):
        _fake_proc(tmp_path, monkeypatch, [], psi="some total=123\n")
        reading, why = om._read_pressure()
        assert reading is None
        assert "no averaged window" in why


class TestTheKillCount:
    def test_zero_kills_is_a_measurement_not_a_failure(self):
        """`oom_kill 0` is this machine's real state.

        Reported as "the kernel has not OOM-killed anything on this boot",
        never as UNKNOWN - a machine whose honest answer is 0 must not read as
        one that could not ask.
        """
        assert om._read_oom_kills() >= 0


class TestReadingProcesses:
    def test_an_unreadable_process_is_skipped_not_counted_as_zero(
            self, tmp_path, monkeypatch):
        """A `0` in the score column reads as "the kernel would never pick
        this", which for a process nobody could read is a claim nobody made.
        """
        root = _fake_proc(tmp_path, monkeypatch, [(1, "readable", 900, 0, 100)])
        (root / "1" / "oom_score").write_text("\x00not a number\n")
        assert om._targets() == []

    def test_a_process_vacated_mid_walk_is_skipped(self, tmp_path, monkeypatch):
        """The exited-mid-walk case: a pid directory with no `oom_score`."""
        root = _fake_proc(tmp_path, monkeypatch, [(1, "alive", 900, 0, 100)])
        (root / "2").mkdir()
        assert [t.pid for t in om._targets()] == [1]

    def test_a_readable_process_is_carried_whole(self, tmp_path, monkeypatch):
        _fake_proc(tmp_path, monkeypatch, [(1, "alive", 900, 200, 100)])
        targets = om._targets()
        assert len(targets) == 1
        assert targets[0].name == "alive"
        assert targets[0].score == 900.0
        assert targets[0].adj == 200
        assert targets[0].rss_kb == 100
        assert targets[0].owner == "uid 1001"

    def test_an_unreadable_adjustment_is_not_an_adjustment(self, tmp_path,
                                                           monkeypatch):
        """`adj` unreadable is `None`, which `_adjusted` treats as absent.

        A reader that coerced it to 0 would silently drop a process whose
        policy it could not read, and one that coerced it to a value would
        invent one.
        """
        root = _fake_proc(tmp_path, monkeypatch, [(1, "alive", 900, 200, 100)])
        (root / "1" / "oom_score_adj").write_text("\x00not a number\n")
        targets = om._targets()
        assert targets[0].adj is None
        assert om._adjusted(targets) == []


class TestTheAdjustmentIsADistribution:
    """The regression this class exists for: chrome carries adj 300 on 15,
    200 on 4 and 100 on 4 processes, and the first version printed one value
    for all 23.
    """

    def _run(self, tmp_path, monkeypatch):
        _fake_proc(tmp_path, monkeypatch, [
            (1, "chrome", 900, 300, 100000),
            (2, "chrome", 880, 300, 90000),
            (3, "chrome", 870, 200, 80000),
            (4, "chrome", 860, 100, 70000),
            (5, "dbus-daemon", 5, -900, 5000),
            (6, "quiet", 2, 0, 100),
        ])
        return om._run({})

    def test_every_distinct_adjustment_is_named(self, tmp_path, monkeypatch):
        out = self._run(tmp_path, monkeypatch)
        assert "chrome: adj 300 on 2, adj 200 on 1, adj 100 on 1" in out

    def test_one_processes_value_is_not_reported_as_all_of_them(
            self, tmp_path, monkeypatch):
        """The exact defect, asserted on the wrong answer by name.

        `adj 300 on 4` is what `group[0].adj` printed - a number about four
        processes when only two carry it.
        """
        out = self._run(tmp_path, monkeypatch)
        assert "adj 300 on 4" not in out
        assert "on 23" not in out

    def test_a_lowered_adjustment_is_reported_too(self, tmp_path, monkeypatch):
        """`-900` is the most interesting value on this box - the desktop
        asking the kernel never to kill its own session bus. A reader that only
        reports raised numbers loses it.
        """
        out = self._run(tmp_path, monkeypatch)
        assert "dbus-daemon: adj -900 on 1" in out
        assert "a raised number" in out and "lowered one" in out

    def test_zero_is_not_an_adjustment(self, tmp_path, monkeypatch):
        """Almost every process carries the default; reporting them buries the
        few that were moved.
        """
        out = self._run(tmp_path, monkeypatch)
        assert "quiet" not in out


class TestTheAnswer:
    def test_nothing_readable_at_all_is_unknown_not_an_empty_table(
            self, tmp_path, monkeypatch):
        _fake_proc(tmp_path, monkeypatch, [])
        out = om._run({})
        assert "UNKNOWN" in out
        assert "Nothing was guessed" in out

    def test_the_ranking_is_stated_not_as_a_countdown(self, tmp_path,
                                                      monkeypatch):
        """The obvious wrong reading of a bare number is the one a person
        would act on, so the sentence has to be there.
        """
        _fake_proc(tmp_path, monkeypatch, [(1, "firefox", 900, 0, 500000)])
        out = om._run({})
        assert "firefox" in out
        assert "proximity to death" in out

    def test_kills_are_reported_with_the_count(self, tmp_path, monkeypatch):
        _fake_proc(tmp_path, monkeypatch, [(1, "firefox", 900, 0, 500000)],
                   vmstat="oom_kill 3\n")
        assert "OOM-killed 3 process(es) since boot" in om._run({})

    def test_no_kills_is_stated_as_a_measurement(self, tmp_path, monkeypatch):
        _fake_proc(tmp_path, monkeypatch, [(1, "firefox", 900, 0, 500000)],
                   vmstat="oom_kill 0\n")
        out = om._run({})
        assert "has not OOM-killed anything on this boot" in out
        assert "UNKNOWN" not in out

    def test_a_missing_counter_is_named_not_shown_as_zero(self, tmp_path,
                                                          monkeypatch):
        _fake_proc(tmp_path, monkeypatch, [(1, "firefox", 900, 0, 500000)],
                   vmstat="pgmajfault 1\n")
        out = om._run({})
        assert "no oom_kill counter" in out
        assert "Partly unreadable" in out
        assert "shown as zero" in out

    def test_pressure_windows_are_all_shown(self, tmp_path, monkeypatch):
        _fake_proc(tmp_path, monkeypatch, [(1, "firefox", 900, 0, 500000)],
                   psi=PSI_REAL)
        out = om._run({})
        assert "0.36% over the last 10s" in out
        assert "0.40% over the last 60s" in out
        assert "0.20% over the last 300s" in out
        assert "14.5s since boot" in out

    def test_pressure_says_what_it_is_not(self, tmp_path, monkeypatch):
        """Free memory and memory pressure are different questions, and the
        answer must not be read as the first.
        """
        _fake_proc(tmp_path, monkeypatch, [(1, "firefox", 900, 0, 500000)],
                   psi=PSI_REAL)
        assert "not how much memory is free" in om._run({})

    def test_a_low_score_is_not_listed_as_a_candidate(self, tmp_path,
                                                     monkeypatch):
        """The defect this pins: a top-N list on a quiet box ends with a
        process the kernel would never consider, and every row above it
        inherits the doubt.

        `quiet` at 2 of 1000 is not a candidate, and printing it under "who
        the OOM killer would pick first" makes the whole section unreliable.
        """
        _fake_proc(tmp_path, monkeypatch, [
            (1, "busy", 900, 0, 500000),
            (6, "quiet", 2, 0, 100),
        ])
        out = om._run({})
        assert "busy" in out
        assert "quiet" not in out

    def test_nothing_above_the_line_says_so(self, tmp_path, monkeypatch):
        """An empty list would read as "nothing was measured".

        The honest sentence names the threshold *and* what it means, so a
        machine with no candidates and a machine nobody could read are
        different answers.
        """
        _fake_proc(tmp_path, monkeypatch, [(6, "quiet", 2, 0, 100)])
        out = om._run({})
        assert "who the oom killer would pick first" not in out.lower()
        assert "no process scores 700 or above" in out.lower()

    def test_the_consent_gate_refuses_by_name(self, tmp_path, monkeypatch):
        """A skill reporting a sense's facts shares that sense's switch.

        Asserted through a real `ChronoaConfig` reading a keyfile with the key
        off, so the refusal is the sense's own sentence and not a free-text
        guess - and the `firefox` row is asserted **absent**, because a refusal
        that still lists the processes has answered the question it refused.
        """
        import os
        import tempfile

        home = tempfile.mkdtemp()
        # **The keyfile backend's real path**, which is
        # `glib-2.0/settings/keyfile`, not `glib-2.0/settings`. The first
        # version wrote the file directly and GSettings reported *"Failed to
        # create .../settings: Not a directory"* - a refusal that looked like
        # a missing switch and was a missing directory.
        os.makedirs(os.path.join(home, "glib-2.0", "settings"))
        with open(os.path.join(home, "glib-2.0", "settings", "keyfile"),
                  "w") as handle:
            handle.write("[org/shani/chronoa]\nmemory-sense-enabled=false\n")
        monkeypatch.setenv("GSETTINGS_BACKEND", "keyfile")
        monkeypatch.setenv("XDG_CONFIG_HOME", home)
        monkeypatch.setenv("GSETTINGS_SCHEMA_DIR",
                           "usr/share/glib-2.0/schemas")
        _fake_proc(tmp_path, monkeypatch, [(1, "firefox", 900, 0, 500000)])
        out = om._run({})
        assert "memory-sense-enabled" in out
        assert "firefox" not in out


class TestTheRegistration:
    def test_it_is_discovered_and_dispatchable(self):
        from shani_chronoa import skills
        tools, handlers = skills.discover_skills()
        names = {tool["function"]["name"] for tool in tools}
        assert "oom_score" in names
        assert "oom_score" in handlers
        assert handlers["oom_score"].__name__ == "_run"

    def test_the_description_carries_the_phrasings_a_person_would_use(self):
        desc = om.SCHEMA["function"]["description"].lower()
        for phrase in ("who would the", "kernel kill", "short on memory",
                       "why did my program disappear",
                       "has anything been killed"):
            assert phrase in desc, f"missing {phrase!r}"

    def test_the_schema_takes_no_required_arguments(self):
        params = om.SCHEMA["function"]["parameters"]
        assert params["properties"] == {}
        assert params.get("required", []) == []

    def test_it_declares_itself_read_only(self):
        desc = om.SCHEMA["function"]["description"].lower()
        assert "read-only" in desc
        assert "nothing is adjusted" in desc
