"""The help window opens with everyday jobs, and every job is backed by real skills.

"What can you do?" used to be answered with 184 tool names. The page now starts
with jobs a person wants done, each with a request ready to send - and a job
may only name skills that exist, so a card cannot promise what Chronoa lacks.
"""

import pytest

from shani_chronoa import capabilities, tools


def test_every_task_uses_only_real_skills():
    real = {t["function"]["name"] for t in tools.TOOLS}
    for task in capabilities.EVERYDAY_TASKS:
        missing = [s for s in task.skills if s not in real]
        assert not missing, f"{task.title!r} relies on skills that do not exist: {missing}"


def test_every_task_is_a_request_you_can_send():
    titles = [t.title for t in capabilities.EVERYDAY_TASKS]
    assert len(titles) == len(set(titles)) and len(titles) >= 10
    for task in capabilities.EVERYDAY_TASKS:
        assert task.how and len(task.prompt.split()) >= 4, task.title


def test_a_task_names_the_switch_it_still_needs(gsettings_env):
    from shani_chronoa.config import ChronoaConfig
    trip = next(t for t in capabilities.EVERYDAY_TASKS if t.title == "Plan a trip")
    config = ChronoaConfig()
    config.set("web-sense-enabled", "false")
    needs = capabilities.task_needs(trip, config)
    assert needs and any("web" in n.lower() or "browse" in n.lower() for n in needs), needs
    config.set("web-sense-enabled", "true")
    assert capabilities.task_needs(trip, config) == []


def test_the_two_volume_skills_are_told_apart():
    assert capabilities._GROUPS["get_volume"][1] != capabilities._GROUPS["set_volume"][1]


def test_the_help_window_opens_with_the_jobs(gsettings_env):
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    from shani_chronoa.config import ChronoaConfig
    from shani_chronoa.gui.widgets import HelpWindow
    caps = capabilities.find_capabilities(tools.TOOLS)
    sent = []
    win = HelpWindow(caps, ChronoaConfig(), on_try=lambda _b, text: sent.append(text))

    def walk(node):
        yield node
        child = node.get_first_child()
        while child is not None:
            yield from walk(child)
            child = child.get_next_sibling()

    labels = [n.get_label() for n in walk(win) if isinstance(n, Gtk.Label)]
    assert labels.index("Get things done") < labels.index("Everything Chronoa can do")
    tries = [n for n in walk(win) if isinstance(n, Gtk.Button) and n.get_label() == "Try it"]
    assert len(tries) == len(capabilities.EVERYDAY_TASKS)
    tries[0].emit("clicked")
    assert sent == [capabilities.EVERYDAY_TASKS[0].prompt]
    assert win.filter("internet") >= 1
