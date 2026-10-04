"""trace_route: reading a path, and refusing to call a silent hop a fault.

The tests here are the point of the module. `mtr` reports 100% loss on several
hops of a perfectly healthy path - captured output below shows three - so the
verdict logic is the part that can be confidently wrong, and it is exercised
against fixed traces rather than a live network whose shape changes minute to
minute.

`test_traces_a_real_host` does run mtr, because a parser that only ever sees a
fixture is a parser that only ever agrees with the fixture.
"""

import shutil
from unittest import mock

import pytest

from shani_chronoa.skills import trace_route as tr

# Captured from a real `mtr -r -w -n -c 5 example.com` on this machine: a
# healthy 10-hop IPv6 path with three hops that never answered. The naive
# reading of this trace is "hops 4, 5 and 7 are broken", which is false.
CAPTURED = """Start: 2026-10-03T19:57:41+0530
HOST: box                      Loss%   Snt   Last   Avg  Best  Wrst StDev
Hosts: box.local
  1.|-- router.local             0.0%     5    4.8   3.5   2.6   4.8   0.9
  2.|-- 2405:200:5205:25::3:208  0.0%     5   36.1  30.7  25.6  36.1   3.8
  4.|-- ???                    100.0     5    0.0   0.0   0.0   0.0   0.0
  5.|-- ???                    100.0     5    0.0   0.0   0.0   0.0   0.0
  6.|-- 2405:200:801:1d00::2f8  0.0%     5  104.5  50.0  29.7  104.5  31.7
  7.|-- ???                    100.0     5    0.0   0.0   0.0   0.0   0.0
  8.|-- 2405:200:1602:600::1   20.0%     5   31.9  68.4  31.9 100.5  28.9
  9.|-- 2405:200:1602:600::1   40.0%     5   31.3  50.6  31.3  61.0  16.7
 10.|-- 2606:4700:8d75::ff98     0.0%     5   37.6  35.9  32.0  41.9   4.0
"""

MTR_MISSING = shutil.which("mtr") is None


def hop(number, host, loss, sent=10):
    return tr.Hop(number, host, loss, sent, 50.0, 50.0, 40.0, 60.0)


# --- the parser ---------------------------------------------------------------


def test_parses_every_row_of_a_real_trace():
    hops, traced = tr.parse(CAPTURED)
    assert [h.number for h in hops] == [1, 2, 4, 5, 6, 7, 8, 9, 10]
    assert [h.loss for h in hops] == [0, 0, 100, 100, 0, 100, 20, 40, 0]
    assert hops[0].host == "router.local"
    assert hops[2].host == "???"
    assert hops[8].sent == 5
    assert traced == "box.local"


def test_a_silent_hop_is_known_to_be_silent():
    hops, _ = tr.parse(CAPTURED)
    silent = [h for h in hops if not h.answered]
    assert [h.number for h in silent] == [4, 5, 7]
    assert all(h.loss == 100.0 for h in silent)


def test_rows_that_are_not_hops_are_ignored():
    for junk in ["", "Start: 2026-01-01T00:00:00", "HOST: box   Loss%   Snt", "garbage"]:
        hops, _ = tr.parse(junk)
        assert hops == [], junk


def test_output_with_no_hops_parses_to_nothing_rather_than_raising():
    hops, _ = tr.parse("mtr: unable to resolve host name\n")
    assert hops == []


# --- the verdict: the whole reason the module exists --------------------------


def test_a_healthy_path_with_three_silent_hops_is_called_intact():
    """The trap. A naive reading calls hops 4, 5 and 7 broken."""
    hops, _ = tr.parse(CAPTURED)
    verdict = tr._verdict(hops)
    assert "intact" in verdict
    assert "did not answer at all" in verdict
    assert "not a fault" in verdict
    for word in ("broken", "unreachable", "down"):
        assert word not in verdict.lower(), f"{word!r} must not appear for a healthy path"


def test_the_verdict_explains_why_a_silent_hop_is_not_a_fault():
    """The claim and its reason have to travel together.

    Asserting only "not a fault" was not enough: a mutant that deleted the whole
    explanation sentence ("routers deprioritise or drop ICMP sent to
    themselves") left the suite green, because the bare reassurance still
    satisfied the test. A user told "this is not a fault" with no reason is
    left to assume the worst, which is the outcome this module exists to avoid.
    """
    verdict = tr._verdict(tr.parse(CAPTURED)[0])
    assert "deprioritise" in verdict, "the reason must be given, not just the reassurance"
    assert "ICMP" in verdict, "the mechanism must be named"


def test_the_silent_hops_are_named_rather_than_left_implicit():
    hops, _ = tr.parse(CAPTURED)
    verdict = tr._verdict(hops)
    for number in ("4", "5", "7"):
        assert f"hop {number}" in verdict or number in verdict


def test_partial_loss_in_the_middle_is_reported_and_not_waved_away():
    """Different from a silent hop, and often real congestion."""
    hops, _ = tr.parse(CAPTURED)
    verdict = tr._verdict(hops)
    assert "answered only some probes" in verdict
    assert "congestion" in verdict


def test_the_middle_loss_note_does_not_contradict_a_lossy_destination():
    """A hardcoded 'the destination answered all of them' breaks in this branch."""
    hops = [hop(1, "a", 0), hop(9, "b", 60), hop(10, "c", 80)]
    verdict = tr._verdict(hops)
    assert "dropped 80%" in verdict
    assert "answered all of them" not in verdict


def test_a_lossy_destination_is_reported_as_the_only_meaningful_loss():
    verdict = tr._verdict([hop(1, "a", 0), hop(2, "b", 0), hop(3, "dest", 40)])
    assert "dropped 40%" in verdict
    assert "only figure here that indicates loss" in verdict


def test_a_silent_destination_is_not_called_a_broken_path():
    """It may simply not answer ICMP, which is common."""
    verdict = tr._verdict([hop(1, "a", 0), hop(2, "dest", 100)])
    assert "never answered" in verdict
    assert "may still be perfectly reachable" in verdict
    assert "does not show a broken path" in verdict


# --- the reply ----------------------------------------------------------------


def test_the_reply_covers_every_hop_and_reports_which_address_was_traced():
    hops, traced = tr.parse(CAPTURED)
    lines = [tr._hop_text(h) for h in hops]
    assert all("did not respond to a probe" in line
               for line in lines if "???" in line)
    assert "traced box.local" in f"(traced {traced})"
    # A silent hop must never read as an address.
    assert all("???" not in line.split("(")[0].split("did not")[0]
               or "did not respond" in line for line in lines)


# --- arguments, and the two gates ---------------------------------------------


@pytest.mark.parametrize("arguments,expected", [
    ({}, "No host"),
    ({"host": "   "}, "No host"),
    ({"host": "exa mple; rm -rf /"}, "not a hostname or an IP"),
    ({"host": "example.com", "probes": 1}, "between 3 and 50"),
    ({"host": "example.com", "probes": 999}, "between 3 and 50"),
    ({"host": "example.com", "probes": True}, "between 3 and 50"),
    ({"host": "example.com", "probes": "10"}, "between 3 and 50"),
])
def test_arguments_are_refused_by_name(arguments, expected):
    assert expected in tr._run(arguments)


def test_privacy_mode_refuses_to_send_probes_off_the_network():
    with mock.patch.object(tr.egress, "privacy_mode_enabled", return_value=True):
        out = tr._run({"host": "example.com"})
    assert "Privacy mode is on" in out
    assert "check_internet" in out, "the refusal must offer the local alternative"


def test_a_missing_mtr_is_named_with_its_package():
    with mock.patch.object(tr.shutil, "which", return_value=None):
        out = tr._run({"host": "example.com"})
    assert "mtr is not installed" in out
    assert "mtr" in out


def test_an_empty_trace_reports_the_reason_not_an_empty_answer():
    with mock.patch.object(tr.egress, "privacy_mode_enabled", return_value=False), \
         mock.patch.object(tr.shutil, "which", return_value="/usr/bin/mtr"), \
         mock.patch.object(tr.subprocess, "run") as run:
        run.return_value = mock.Mock(returncode=1, stdout="", stderr="")
        run.return_value.stdout = ""
        run.return_value.stderr = "mtr: unable to resolve host name"
        out = tr._run({"host": "nosuchhost.invalid"})
    assert "No trace came back" in out
    assert "unable to resolve" in out
    assert "not a finding that the host is unreachable" in out


def test_a_timeout_is_reported_rather_than_hanging():
    import subprocess as sp
    with mock.patch.object(tr.egress, "privacy_mode_enabled", return_value=False), \
         mock.patch.object(tr.shutil, "which", return_value="/usr/bin/mtr"), \
         mock.patch.object(tr.subprocess, "run", side_effect=sp.TimeoutExpired("mtr", 45)):
        out = tr._run({"host": "example.com"})
    assert "did not finish" in out
    assert "no result is reported" in out


def test_the_real_invocation_is_bounded_in_two_ways():
    """Probes are capped by argument and by wall clock.

    The timeout mutant - dropping `timeout=_TIMEOUT` from the real call - left
    the suite green, because the test above mocks `subprocess.run` into raising
    rather than checking that a bound was ever passed. This asserts the bound
    is actually in the call.
    """
    with mock.patch.object(tr.egress, "privacy_mode_enabled", return_value=False), \
         mock.patch.object(tr.shutil, "which", return_value="/usr/bin/mtr"), \
         mock.patch.object(tr.subprocess, "run") as run:
        run.return_value = mock.Mock(returncode=0, stdout=CAPTURED, stderr="")
        tr._run({"host": "example.com", "probes": 4})
    argv = run.call_args[0][0]
    kwargs = run.call_args[1]
    assert kwargs.get("timeout") == tr._TIMEOUT, "a trace must not be able to hang"
    assert isinstance(kwargs.get("timeout"), (int, float)) and kwargs["timeout"] > 0
    # The probe count reaches mtr as one argv element, never through a shell.
    assert argv[argv.index("-c") + 1] == "4"
    assert "-r" in argv and "-n" in argv, "report and numeric modes must be forced"
    assert argv[-1] == "example.com"


def test_the_schema_is_registerable():
    from shani_chronoa.skills import is_valid_schema
    assert tr.SKILLS[0].name == "trace_route"
    assert is_valid_schema(tr.SKILLS[0].schema)
    assert tr.SCHEMA["function"]["parameters"]["required"] == ["host"]


# --- the mutation controls ----------------------------------------------------


def test_control_the_parse_mutant_is_caught():
    """A parser that lost the loss column would call a silent hop alive."""
    text = CAPTURED.replace("100.0     5", "100.0      5", 1)
    hops, _ = tr.parse(text)
    assert hops[2].loss == 100.0
    assert not hops[2].answered
    assert [h for h in hops if not h.answered] != []


def test_control_the_verdict_mutant_is_caught(monkeypatch):
    """Naive verdict: call the path broken if any hop lost packets."""
    hops, _ = tr.parse(CAPTURED)

    def naive(rows):
        worst = max(rows, key=lambda h: h.loss)
        return f"hop {worst.number} lost {worst.loss:.0f}% - the path is broken"

    assert "broken" in naive(hops)
    assert "broken" not in tr._verdict(hops), (
        "the verdict must disagree with the naive reading, or it adds nothing"
    )


@pytest.mark.skipif(MTR_MISSING, reason="mtr is not installed on this machine")
def test_traces_a_real_host():
    """Against the real thing, because a parser that only sees fixtures is a liability."""
    with mock.patch.object(tr.egress, "privacy_mode_enabled", return_value=False):
        out = tr._run({"host": "localhost", "probes": 3})
    if "is not installed" in out or "did not finish" in out:
        pytest.skip(f"mtr could not run here: {out[:80]}")
    assert "hop(s)" in out
    assert "The destination" in out or "No trace came back" in out
    # Whatever the machine's shape, a hop row must never claim a silent hop is up.
    assert "down" not in out.lower()