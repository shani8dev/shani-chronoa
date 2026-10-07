"""RestartPolicy (T2.15) and sandbox qualification (T2.10), verified against
fake clocks and the real /proc of this machine. Both are policy-pure: the
tests prove the decision tables, and on this machine the qualification probe
proves its negative honestly (it does not pretend a filter exists when none
is installed)."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.restart import RestartPolicy  # noqa: E402
from shani_chronoa.sandbox.qualify import qualify  # noqa: E402


class TestRestartPolicy:
    def test_first_failure_waits_a_second(self):
        p = RestartPolicy()
        p.note_started(100.0)
        d = p.note_exit(100.2)
        assert d.delay == 1.0 and d.refusal is None

    def test_consecutive_failures_double(self):
        p = RestartPolicy()
        for expected in (1.0, 2.0, 4.0, 8.0):
            p.note_started(100.0)
            d = p.note_exit(100.1)
            assert d.delay == expected

    def test_a_healthy_run_clears_the_streak(self):
        p = RestartPolicy()
        p.note_started(100.0); p.note_exit(100.1)  # streak = 1
        p.note_started(200.0)
        d = p.note_exit(250.0)  # five minutes later, healthy: new incident
        assert d.delay == 1.0

    def test_a_short_run_does_not_clear(self):
        p = RestartPolicy()
        p.note_started(100.0); p.note_exit(100.1)
        p.note_started(200.0)
        d = p.note_exit(205.0)
        assert d.delay == 2.0

    def test_consecutive_cap_refuses(self):
        p = RestartPolicy()
        for _ in range(7):
            p.note_started(100.0); p.note_exit(100.1)
        assert p.note_exit(100.1).refusal is not None

    def test_rolling_window_stops_the_pace(self):
        p = RestartPolicy(window_max=3, window_seconds=60.0)
        t = 0.0
        for _ in range(3):
            p.note_started(t); d = p.note_exit(t + 1.0); t += 1.0
            assert d.delay is not None
        d = p.note_exit(t)
        assert d.delay is None and "restarts inside" in d.refusal


class TestQualification:
    def test_reads_this_machine_and_never_pretends(self):
        q = qualify()
        # Every field, either honestly measured or reported unknown:
        assert q.no_new_privs in (True, False)
        assert q.seccomp_mode in (0, 1, 2)
        assert q.landlock_abi is None or q.landlock_abi >= 0
        # This machine is unprivileged, so the proof must NOT say yes:
        assert q.proven is False
        assert q.missing  # and it says what is missing

    def test_missing_lists_what_a_claim_depended_on(self):
        q = qualify()
        if not q.seccomp_is_filter:
            assert "seccomp_filter" in q.missing
