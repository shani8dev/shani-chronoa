"""The Sandbox surface: where commands run, and proof of what that means.

The README's security table promises that a command cannot read your private
files, cannot reach the network and cannot write the system. Until 2026-10-15
the only place any of it showed was one seccomp switch in Privacy and a few
lines in Diagnostics. This page shows the layers this machine can provide, the
other places a command can run, the recent runs - and a **Test the sandbox**
button that runs a probe inside the very sandbox `run_command` uses
(`sandbox.selfcheck`) and shows what it found, instead of what is configured.

The probe is run on a press, not on build: it starts a confined child, and a
page should not do that every time it is opened.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, List

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from shani_chronoa.gui.surfaces import common  # noqa: E402
from shani_chronoa.sandbox import selfcheck  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = "Sandbox"
ICON = "security-medium-symbolic"
SECTION = "Health and trust"
SUBTITLE = "Where Chronoa runs commands, and a test of what they can reach."

SECCOMP_KEY = "sandbox-seccomp-enabled"

#: How many recent runs are listed.
RECENT = 8


def _add(group: Gtk.Widget, child: Gtk.Widget) -> None:
    (group.add if hasattr(group, "add") else group.append)(child)


def _mark(ok: Any) -> str:
    return {True: "✓", False: "✗"}.get(ok, "–")


def _check_row(check: "selfcheck.Check") -> Gtk.Widget:
    mark = Gtk.Label(label=_mark(check.ok), valign=Gtk.Align.CENTER)
    mark.add_css_class({True: "success", False: "error"}.get(check.ok, "dim-label"))
    return common.row(check.name, common.home_paths(check.detail), suffix=mark)


def _recent_runs() -> List[dict]:
    try:
        from shani_chronoa import tool_tracking
        from shani_chronoa.gui.surfaces import activity
        records = activity._records(tool_tracking.ToolTracker())  # noqa: SLF001
    except Exception:  # noqa: BLE001 - an unreadable log is no runs, not a crash
        logger.debug("could not read the tool log", exc_info=True)
        return []
    return [r for r in records if r.get("tool_name") == "run_command"][:RECENT]


def _run_row(record: dict) -> Gtk.Widget:
    args = record.get("args") or {}
    command = str(args.get("command") or "?")
    where = str(args.get("where") or "sandbox")
    result = str(record.get("result") or "")
    failed = result.startswith(("ERROR", "Not run")) or record.get("verdict") == "failed"
    when = str(record.get("timestamp") or "")[:16].replace("T", " ")
    subtitle = f"{where} · {when}" + (" · did not succeed" if failed else "")
    return common.row(command[:80], subtitle)


class _Page:
    def __init__(self, app: Any, set_content) -> None:
        self.app = app
        self.recorder = common.StatusRecorder()
        self.body = common.page_body()
        for side in ("start", "end", "top", "bottom"):
            getattr(self.body, f"set_margin_{side}")(12)
        set_content(common.scrolled(self.body))
        self._build()

    def _project(self) -> str:
        try:
            from shani_chronoa import buildmode
            return buildmode.project()
        except Exception:  # noqa: BLE001
            return ""

    def _build(self) -> None:
        layers = selfcheck.layers()
        missing = [c.name for c in layers if c.ok is False]
        if missing:
            self.body.append(self.recorder.row(
                common.STATUS_ATTENTION,
                f"{len(missing)} of {len(layers)} protections are not available here",
                "Missing: " + ", ".join(missing) + ". Commands still run under the rest."))
        else:
            self.body.append(self.recorder.row(
                common.STATUS_OK, "Every protection is available on this machine",
                "Press Test the sandbox to see them hold."))

        # The probe, on a press.
        test = common.group("Test the sandbox",
                            "Runs a small check inside the same sandbox your "
                            "project's commands use. Nothing is sent anywhere.")
        self.test_button = Gtk.Button(label="Test the sandbox", halign=Gtk.Align.START)
        self.test_button.add_css_class("pill")
        self.test_button.add_css_class("suggested-action")
        self.test_button.connect("clicked", lambda *_a: self.run_test())
        self.results = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        _add(test, self.test_button)
        self.body.append(test)
        self.body.append(self.results)

        group = common.group("Layers on this machine",
                             "Each command gets all of these that the machine can provide.")
        for check in layers:
            _add(group, _check_row(check))
        config = getattr(self.app, "config", None)
        if config is not None:
            def toggled(on: bool) -> None:
                try:
                    config.set(SECCOMP_KEY, "true" if on else "false")
                except Exception:  # noqa: BLE001
                    logger.warning("could not change %s", SECCOMP_KEY, exc_info=True)
            try:
                active = bool(config.get_bool(SECCOMP_KEY, False))
            except Exception:  # noqa: BLE001
                active = False
            # **Said as it behaves.** Under bubblewrap the filter is not
            # installed: bubblewrap's own setup needs mount(2), which the filter
            # denies (the executor logs this on every such call). So "in every
            # command" would be false on exactly the machines with all layers.
            bwrap = next((c.ok for c in layers if c.name == "bubblewrap"), False)
            _add(group, common.switch_row(
                "Add the seccomp filter",
                ("Denies 28 risky system calls. Here bubblewrap confines commands "
                 "instead - it needs a call the filter denies - so this applies "
                 "only where bubblewrap cannot run." if bwrap else
                 "Denies 28 risky system calls in every command. Off by default."),
                active, toggled))
        self.body.append(group)

        self.body.append(self._limits_group())

        places = common.group("Other places a command can run",
                              "Ask for a container or a machine and the command "
                              "runs there instead, isolated by it.")
        for check in selfcheck.places():
            _add(places, _check_row(check))
        self.body.append(places)

        runs = _recent_runs()
        recent = common.group("Recent commands",
                              "What run_command ran, newest first." if runs
                              else "Nothing has been run yet.")
        for record in runs:
            _add(recent, _run_row(record))
        self.body.append(recent)

    def _limits_group(self) -> Gtk.Widget:
        """What a command may use: editable for project commands, shown for the rest.

        The project-command numbers are the two settings `sandbox.profiles.
        command_limits` reads per call, so a change here applies to the next
        command. The other skills' limits are fixed in the code and shown so the
        page says what actually bounds them.
        """
        from shani_chronoa.sandbox import profiles
        group = common.group(
            "Limits",
            "Project commands are your tests, builds and linters (run_command). "
            "Raise these for large builds; a command over its limit is stopped.")
        config = getattr(self.app, "config", None)
        memory, timeout = profiles.command_limits(config)
        cores, low_priority = profiles.command_cpu(config)
        for title, subtitle, key, value, low, high, step in (
                ("Memory for project commands", "Megabytes", profiles.COMMAND_MEMORY_KEY,
                 memory, 512, 65536, 512),
                ("Time limit for project commands", "Seconds", profiles.COMMAND_TIMEOUT_KEY,
                 timeout, 60, 7200, 60),
                ("CPU for project commands", "Cores' worth of CPU time, for the whole time limit",
                 profiles.COMMAND_CORES_KEY, cores, 1, 256, 1)):
            adjustment = Gtk.Adjustment(value=value, lower=low, upper=high,
                                        step_increment=step, page_increment=step * 4)
            spin = Gtk.SpinButton(adjustment=adjustment, valign=Gtk.Align.CENTER, numeric=True)
            spin.update_property([Gtk.AccessibleProperty.LABEL], [title])

            def changed(widget, key=key):
                if config is not None:
                    try:
                        config.set(key, str(int(widget.get_value())))
                    except Exception:  # noqa: BLE001 - a refused write keeps the old value
                        logger.warning("could not change %s", key, exc_info=True)
            spin.connect("value-changed", changed)
            _add(group, common.row(title, subtitle, suffix=spin))
        def priority(on: bool) -> None:
            if config is not None:
                try:
                    config.set(profiles.COMMAND_LOW_PRIORITY_KEY, "true" if on else "false")
                except Exception:  # noqa: BLE001
                    logger.warning("could not change the command priority", exc_info=True)
        _add(group, common.switch_row(
            "Run project commands at low priority",
            "A build still uses every free core, but gives way to what you are doing "
            "(nice 10, idle disk priority).", low_priority, priority))
        default = profiles.PROFILE_DEFAULT
        _add(group, common.row(
            "Every other skill",
            f"{default.memory_limit} MB and up to {default.timeout_seconds} s - fixed"))
        return group

    def run_test(self) -> None:
        self.test_button.set_sensitive(False)
        self.test_button.set_label("Testing…")
        project = self._project()

        def work() -> None:
            result = selfcheck.run(project)
            GLib.idle_add(self.show_result, result)
        threading.Thread(target=work, daemon=True).start()

    def show_result(self, result: "selfcheck.SelfCheck") -> bool:
        child = self.results.get_first_child()
        while child is not None:
            self.results.remove(child)
            child = self.results.get_first_child()
        group = common.group(
            "What the test found" if result.ran else "The test could not run",
            "" if result.ran else common.home_paths(result.error))
        for check in result.checks:
            _add(group, _check_row(check))
        self.results.append(group)
        self.recorder.set(common.STATUS_OK if result.all_ok else common.STATUS_ATTENTION)
        self.test_button.set_label("Test again")
        self.test_button.set_sensitive(True)
        self.last_result = result
        return False


def build(app) -> Gtk.Widget:
    page, set_content = common.surface(TITLE, SUBTITLE)
    surface = _Page(app, set_content)
    page.status = surface.recorder.status
    page.sandbox = surface
    return page
