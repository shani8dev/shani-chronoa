"""`lab_network_destroy` refused every call, and nothing tested that it did not.

The bug was one line:

    blocked = (reason if not allowed else _refuse("")) or _root_paths("")

With consent **granted**, `reason` is `""`, so the `or` moved on to
`_refuse("")` - which returns a *truthy* refusal sentence - and `destroy`
answered "needs you to allow it first" to somebody who had just allowed it. It
never reached the name check, the record lookup, or the teardown. Measured
before the fix: that refusal, every single time, granted or not.

Two things about it are why a test is written here rather than a note.

**The module had no test at all.** `test_netprovision_routes.py` covers the
*plan* - the argv the builder emits - and this is the *skill*: the gate, the
record lookup and the call to the privileged helper. Those are different
questions, and a suite can be green about the first while the second is dead.

**The undefined name hid the real fault.** `_root_paths` does not exist
anywhere in the tree; pyflakes reported it, and the name was unreachable on
every call because the truthy `_refuse("")` short-circuited the `or` first. So
linter and runtime agreed on a fact that could never happen, and neither told
the truth.

The tests below drive the real handler through the real consent gate, and stub
only what needs root: the helper invocation and the record on disk. Every one
of them has a control, because the failure this file exists to prevent is a
test that passes for an unrelated reason.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa.skills import lab_network as lab  # noqa: E402

CONSENT_KEY = lab.CONSENT_KEY


def _real_uplink() -> str:
    """An interface this machine actually has.

    `parse_request` refuses an uplink that is not in `/sys/class/net`, so a
    typed-in name would turn every test using this record into a test of that
    refusal - the same "green for the wrong reason" trap, one level down.
    Reserved prefixes are skipped because `netprovision` refuses them as
    uplinks too.
    """
    reserved = ("docker", "virbr", "veth", "br-", "tun", "tap", "chronoa-")
    for path in sorted(Path("/sys/class/net").glob("*")):
        if path.name != "lo" and not path.name.startswith(reserved):
            return path.name
    pytest.skip("no non-reserved network interface to use as an uplink")

# A recorded lab network, in the shape `load_record()` returns - which means the
# shape `usr/bin/shani-chronoa-lab-network:180-188` writes: cidr, nat, **uplink**,
# created, subnets. `uplink` is here because omitting it made this file's
# earliest fixture a *NAT network with no uplink*, which `parse_request`
# correctly rejects - so the plan test was measuring that rejection and calling
# it a plan. The interface is looked up rather than typed, because `parse_request`
# checks that the named interface exists on this machine.
RECORD = {
    "lab1": {
        "cidr": "10.77.0.0/24",
        "nat": True,
        "uplink": _real_uplink(),
        "created": "2026-10-09T00:00:00+00:00",
        "subnets": [{"name": "web", "cidr": "10.77.0.0/26", "public": True}],
    },
}


@pytest.fixture
def granted(gsettings_env):
    """Consent genuinely granted, through the real gate and the real key.

    `gsettings_env` is the **keyfile** backend, and the group is the schema id -
    `memory` is per-process, so a grant made here would be invisible to the
    handler if it ran in a child. `assert ... or True` appears nowhere in this
    fixture on purpose: it would assert nothing while reading as a check that
    the write landed. The write is verified by reading it back, which is the
    only part that can fail.
    """
    from shani_chronoa.config import ChronoaConfig

    ChronoaConfig().set(CONSENT_KEY, "true")
    assert ChronoaConfig().get_bool(CONSENT_KEY, False) is True, (
        f"{CONSENT_KEY} did not read back as true, so the tests below would be "
        f"exercising the refusal path while claiming to grant consent"
    )
    return ChronoaConfig


@pytest.fixture
def helper_calls(monkeypatch):
    """Record what the handler asks the privileged helper to do."""
    calls = []

    def _fake_invoke(subcommand, path=""):
        calls.append((subcommand, path))
        return 0, f"{subcommand} {path} done"

    monkeypatch.setattr(lab, "_invoke", _fake_invoke)
    return calls


@pytest.fixture
def recorded(monkeypatch):
    """A recorded network, so the name check has something to find."""
    monkeypatch.setattr(lab, "load_record", lambda: dict(RECORD))


@pytest.fixture
def can_elevate(monkeypatch):
    """The helper is present and this process is not root.

    Both halves are real refusals for real reasons (`_helper_missing`, then
    `os.geteuid() == 0`), so they are stubbed rather than arranged: neither is
    reachable in a test process, and a test that arranges the environment to
    fake a root helper is testing the arrangement.
    """
    monkeypatch.setattr(lab, "_root_precondition", lambda: None)


# ---------------------------------------------------------------- the bug


def test_destroy_reaches_the_teardown_when_consent_is_granted(
        granted, can_elevate, recorded, helper_calls):
    """The whole point: a granted, valid, recorded destroy actually runs.

    Asserted on `helper_calls` rather than on the return string. The old bug
    returned a *plausible* refusal sentence, so asserting on text would have
    been checking that some sentence came back; the helper being asked is the
    only thing that distinguishes "destroyed" from "explained why not".
    """
    out = lab._lab_network_destroy({"name": "lab1", "apply": True})

    assert helper_calls == [("destroy", "lab1")], (
        f"destroy never reached the helper; it answered {out!r} instead. This is "
        f"the original bug: with consent granted, a truthy refusal short-circuited "
        f"the line and every call was refused."
    )
    assert "not done" not in out.lower(), out


def test_destroy_reports_what_the_helper_reported(granted, can_elevate, recorded,
                                                  helper_calls):
    """The handler returns the helper's own output, not a canned sentence.

    Pairs with the test above: "the helper was asked" and "its answer reached the
    caller" are separate claims, and a handler that asked and then discarded the
    answer would pass the first.
    """
    out = lab._lab_network_destroy({"name": "lab1", "apply": True})
    assert helper_calls == [("destroy", "lab1")]
    assert out.strip() == "destroy lab1 done", out


def test_destroy_without_apply_shows_a_plan_and_removes_nothing(
        granted, can_elevate, recorded, helper_calls):
    """`apply` absent means a plan, and a plan that acts is the worse bug."""
    out = lab._lab_network_destroy({"name": "lab1"})

    assert helper_calls == [], f"a plan destroyed something: {helper_calls}"
    assert "nothing changed" in out.lower(), out
    assert "apply=true" in out.lower(), out


# ---------------------------------------------------------------- controls


def test_control_the_consent_refusal_is_still_a_refusal(gsettings_env,
                                                        monkeypatch):
    """With consent off, destroy refuses and the helper is never asked.

    This is the control the file needs most. Without it, a mutation that
    **removed** the gate entirely - deleting the `if not allowed` branch - would
    pass every test above, because they all grant consent. The refusal and the
    grant are the same code path with one flag apart, and both directions are
    asserted.

    `monkeypatch` rather than a `try`/`finally` restore, deliberately: an
    earlier version of this test restored `_invoke` to itself, so an assertion
    failure would have left the module globally patched for every later test in
    the run - a failure that reads as the next file's bug. That is this repo's
    recorded `NameError`-in-a-`finally` lesson in a different guise.
    """
    from shani_chronoa.config import ChronoaConfig

    assert ChronoaConfig().set(CONSENT_KEY, "false") or True
    assert ChronoaConfig().get_bool(CONSENT_KEY, True) is False, (
        f"{CONSENT_KEY} could not be turned off, so this would be testing the "
        f"granted path under a name that says otherwise"
    )

    asked = []
    monkeypatch.setattr(lab, "load_record", lambda: dict(RECORD))
    monkeypatch.setattr(lab, "_root_precondition", lambda: None)
    monkeypatch.setattr(
        lab, "_invoke", lambda sub, path="": asked.append((sub, path)) or (0, "done"))

    out = lab._lab_network_destroy({"name": "lab1", "apply": True})

    assert asked == [], f"a shut gate still destroyed something: {asked}"
    assert CONSENT_KEY in out, (
        f"the refusal does not name the switch to turn on, so it cannot be acted "
        f"on: {out!r}"
    )
    # The refusal is `_consent`'s own sentence, not `_refuse`'s "Not done: "
    # wrapper - asserted so that this test cannot pass on a refusal from any
    # other cause, which is what the original bug produced.
    assert "switched off" in out, out


def test_control_the_root_precondition_is_still_consulted(
        granted, recorded, monkeypatch):
    """The helper check still refuses, and the refusal is its own sentence.

    This is the half the bug made unreachable. `_root_precondition` replaces
    the undefined `_root_paths`, and a mutation that dropped the call would
    leave every other test here green - the granted path stubs it to `None` and
    never notices it was never asked.
    """
    monkeypatch.setattr(
        lab, "_root_precondition",
        lambda: "Refusing to run: the helper is not installed.")

    asked = []
    monkeypatch.setattr(
        lab, "_invoke", lambda sub, path="": asked.append((sub, path)) or (0, "done"))

    out = lab._lab_network_destroy({"name": "lab1", "apply": True})

    assert asked == [], f"destroy ran despite the root precondition: {asked}"
    assert "helper is not installed" in out, out


def test_control_an_unknown_name_is_refused_without_destroying_anything(
        granted, can_elevate, recorded, helper_calls):
    """A network this tool did not build is never removed by it.

    Stated in the module's own docstring and its own refusal text, and it is
    the guard the old code never reached - so a "fix" that simply deleted the
    lookup would pass the first test and fail this one.
    """
    out = lab._lab_network_destroy({"name": "not-mine", "apply": True})

    assert helper_calls == [], f"destroyed a network it never built: {helper_calls}"
    assert "no network called" in out.lower(), out
    # It lists what it does know, so the refusal is actionable.
    assert "lab1" in out, out


def test_control_a_missing_name_asks_rather_than_guessing(
        granted, can_elevate, recorded, helper_calls):
    """No name means a question, never a wildcard teardown."""
    out = lab._lab_network_destroy({"name": "", "apply": True})

    assert helper_calls == [], f"destroyed something with no name given: {helper_calls}"
    assert "which network" in out.lower(), out
