"""A Git panel: the state of a repository, and the two writes behind their gates.

No GUI surface named `git_write` or `git_push` before this. The only git surface
was chat's `git_inspect`, and the Work chip's menu, which says which project the
window is on but offers no git action. So "what changed?" had an answer and
"commit that" did not, which is the wrong way round for an assistant on a
working machine.

**Consent is not redrawn here.** Committing and pushing are reached through the
chat path - `git_write` and `git_push` - with their own keys
(`git-write-enabled`, `git-push-enabled`) and their own prompts. A panel that
wrote a commit with its own `git` argv would be a second path around both gates
and a second place the argv could drift from `senses.git.run_git`. So each action
has a **Try it** button that fills the composer with the real skill's name and
the arguments a person can edit, and the consent banner that names the switch
sits above it.

**The read uses the Work chip's own reader.** `vcs_status.git_state(folder)`
is what the chip's tooltip calls, in one subprocess, so the panel and the chip
cannot disagree about the branch or the changed-file count. A folder that is
not a checkout stays silent rather than reporting a clean tree.

**No force push, no `-A`, no default remote.** Those are the skills' own rules;
the prompts name one remote and one set of files, so a button here cannot widen
what the chat path would have allowed.
"""

from __future__ import annotations
from shani_chronoa.ui_gtk.gui.locale import _

import logging
from pathlib import Path
from typing import Any

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa import files  # noqa: E402
from shani_chronoa.ui_gtk.gui.surfaces import common  # noqa: E402

logger = logging.getLogger("shani_chronoa.gui.surfaces.git_panel")

TITLE = _('Git')

ICON = "folder-symbolic"
SECTION = "Making"
SUBTITLE = _('The repository Chronoa is working in, and the two git writes. Committing and pushing both ask in chat, behind their own switches.')


WRITE_KEY = "git-write-enabled"
PUSH_KEY = "git-push-enabled"

_COMMIT_PROMPT = 'git_write with action=commit, message="", files=[] in this repository'
_PUSH_PROMPT = 'git_push with remote="" in this repository'


def _repository(app: Any) -> Path:
    """The folder the Work chip names, or the home directory."""
    window = getattr(app, "window", None)
    strip = getattr(window, "_mode_strip", None)
    for name in ("_project_path", "_project_dir", "_project_folder"):
        value = getattr(strip, name, None)
        if isinstance(value, Path) and value.is_dir():
            return value
        if isinstance(value, str) and value:
            try:
                target = files.resolve_in_home(value)
                if target.is_dir():
                    return target
            except Exception:  # noqa: BLE001
                pass
    return Path.home()


def _git_state(folder: Path) -> "tuple[str, int]":
    try:
        from shani_chronoa.skills import vcs_status

        branch, changed = vcs_status.git_state(folder)
        return str(branch or ""), int(changed or 0)
    except Exception:  # noqa: BLE001 - a glance must not take the page down
        logger.debug("git panel: state unreadable", exc_info=True)
        return "", 0


def _try_button(window: Any, prompt: str) -> Gtk.Button:
    button = Gtk.Button(label="Try it")
    button.add_css_class("flat")
    button.set_valign(Gtk.Align.CENTER)
    tip = f"Put “{prompt}” in the composer to ask Chronoa"
    button.set_tooltip_text(tip)
    button.update_property([Gtk.AccessibleProperty.LABEL], [tip])

    def clicked(_button: Gtk.Button) -> None:
        try:
            window.set_input_text(prompt)
        except Exception:  # noqa: BLE001
            logger.debug("git panel: could not fill the composer", exc_info=True)

    button.connect("clicked", clicked)
    return button


def build(app: Any) -> Gtk.Widget:
    page, set_content = common.surface(TITLE, SUBTITLE)
    recorder = common.StatusRecorder()
    page.status = recorder.status
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    set_content(common.scrolled(content))

    window = getattr(app, "window", None)
    path = _repository(app)
    branch, changed = _git_state(path)
    # The panel's own health, from the same read as the rows. A repository the
    # state reader could not answer is "could not determine", not "clean":
    # a green dot on a folder nobody could inspect would be the wrong claim.
    status = common.STATUS_OK if branch else common.STATUS_UNKNOWN
    detail = (
        f"Branch {branch}; {changed} changed file(s)."
        if branch
        else "This folder is not a checkout, or git could not answer."
    )
    content.append(recorder.row(status, "Working tree", detail))

    status = common.group("Working tree")
    folder = common.home_paths(str(path))
    row = common.row(folder, "The project on the Work chip; full path on hover.")
    row.set_tooltip_text(str(path))
    adder = getattr(status, "add", None) or status.append
    adder(row)
    if branch:
        adder(
            common.row(
                f"Branch {branch}",
                f"{changed} changed file{'s' if changed != 1 else ''}"
                if changed
                else "No changes to commit.",
            )
        )
    else:
        adder(
            common.row(
                "Not a checkout here",
                "Point the Work chip at a repository to see its state.",
            )
        )
    content.append(status)

    def gate(key: str, why: str) -> None:
        content.append(common.consent_banner(app, key, why))

    gate(WRITE_KEY, "so a commit can be recorded in this repository.")
    gate(PUSH_KEY, "so a branch can be pushed to a named remote.")

    actions = common.group("Actions")
    actions_adder = getattr(actions, "add", None) or actions.append
    actions_adder(
        common.row(
            "Commit",
            "Fills the composer; files are named, never swept.",
            suffix=_try_button(window, _COMMIT_PROMPT),
        )
    )
    actions_adder(
        common.row(
            "Push",
            "Fills the composer; the remote is named and asked first.",
            suffix=_try_button(window, _PUSH_PROMPT),
        )
    )
    content.append(actions)
    return page
