"""Every destructive tool asks before it runs, whatever its switch says.

Dispatch used to ask only when a tool's consent key was off. When the
destructive keys (delete files, stop processes, power, services, mounts, bulk
edits, run commands, ...) became on by default, every one of those actions
would have run with no question at all, and the one test that could have
noticed had only ever run with the key off.

This walks every tool whose consent key is destructive, read from the
capability tables the app itself enforces, so a destructive tool added later
is covered the day it is registered.
"""

import pytest

from shani_chronoa import ask_bridge, capabilities, permissions, tools
from shani_chronoa.config import ChronoaConfig


def _tool_names():
    """The registered tools' names (TOOLS holds their function schemas)."""
    return sorted(t["function"]["name"] for t in tools.TOOLS)


def _destructive_tools():
    return [
        name for name in _tool_names()
        if tools._consent_key_for(name) in capabilities.DESTRUCTIVE_CONSENT_KEYS
    ]


DESTRUCTIVE = _destructive_tools()


def _somebody_answers():
    def show(question, options):
        done, resolve = ask_bridge.make_event()
        resolve("Deny")
        return done
    return show


@pytest.fixture(autouse=True)
def _no_presenter_left_behind():
    ask_bridge.set_presenter(None)
    yield
    ask_bridge.set_presenter(None)


def test_the_list_is_not_empty():
    # the control: an empty walk would pass everything below
    assert len(DESTRUCTIVE) >= 8, DESTRUCTIVE
    assert "delete_file" in DESTRUCTIVE


def test_every_destructive_tool_is_bypass_immune():
    """A standing "yes for this session" must never cover one of them."""
    not_immune = [n for n in DESTRUCTIVE if not permissions.is_bypass_immune(n)]
    assert not_immune == []


@pytest.mark.parametrize("name", DESTRUCTIVE)
def test_switch_on_and_somebody_here_means_ask(name):
    ChronoaConfig().set(tools._consent_key_for(name), True)
    ask_bridge.set_presenter(_somebody_answers())
    assert tools.approval_step(name) == "ask", (
        f"{name} would run without asking while its switch is on"
    )


@pytest.mark.parametrize("name", DESTRUCTIVE)
def test_switch_off_and_somebody_here_means_ask(name):
    ChronoaConfig().set(tools._consent_key_for(name), False)
    ask_bridge.set_presenter(_somebody_answers())
    assert tools.approval_step(name) == "ask"


@pytest.mark.parametrize("name", DESTRUCTIVE)
def test_nobody_to_ask_means_refuse(name):
    ChronoaConfig().set(tools._consent_key_for(name), True)
    assert tools.approval_step(name) == "refuse", (
        f"{name} would run with its switch on and nobody to say yes"
    )


@pytest.mark.parametrize("name", DESTRUCTIVE)
def test_a_remote_turn_asks_too(name):
    ChronoaConfig().set(tools._consent_key_for(name), True)
    ask_bridge.set_presenter(_somebody_answers())
    assert tools.approval_step(name, remote=True) == "ask"


def test_a_read_only_gated_tool_with_its_switch_on_runs_without_a_question():
    """The other side: the rule is for destructive tools, not every gated one.

    A read-only sense that the person switched on must not ask on every read,
    or every question would be a prompt.
    """
    readers = [
        n for n in _tool_names()
        if tools._consent_key_for(n)
        and tools._consent_key_for(n) not in capabilities.DESTRUCTIVE_CONSENT_KEYS
        and not permissions.is_bypass_immune(n)
    ]
    assert readers, "no read-only gated tool to check against"
    name = "git_inspect" if "git_inspect" in readers else readers[0]
    ChronoaConfig().set(tools._consent_key_for(name), True)
    ask_bridge.set_presenter(_somebody_answers())
    assert tools.approval_step(name) == ""
