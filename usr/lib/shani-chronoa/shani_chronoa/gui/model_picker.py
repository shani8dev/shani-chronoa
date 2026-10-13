"""The model picker: which model answers, and why that one.

A `Gio.Menu` of names was the old shape, and it could answer exactly one
question - *what are the choices*. It could not say which is in use, which is
installed, or which the quality gate would refuse, and all three are things a
person wants before clicking.

This is a `Gtk.Popover` of `Adw.ActionRow`s, which is the libadwaita pattern for
a choice with detail: a title, a subtitle, a leading figure for state, and a row
dimmed when it is not a real option. The reasons come from the modules that own
them rather than from anything guessed here:

- **installed or not** - `local_llm.installed()`. An uninstalled tier is a
  download and not a mode, so its subtitle says so instead of it being a dead
  item.
- **in use** - `local_llm.active()`.
- **usable** - `local_llm.use(key)` returns `{"promote": bool, "why": str}`. The
  gate exists because *installed* and *usable* are different claims, and this is
  the picker's chance to say which.

**The gate is never run to fill in a subtitle.** `use()` with `gate=True`
measures perplexity, which is slow; the picker answers immediately or not at
all, and the action's own handler runs the gate properly.
"""

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

logger = logging.getLogger(__name__)

#: The leading figure on a row, by what that row is.
_CHECK = "object-select-symbolic"
_DOWNLOAD = "document-open-recent-symbolic"


def rows_for(app) -> "list":
    """`(key, title, subtitle, usable, active)` for every tier, ordered.

    **A row is never dropped for being unusable.** Hiding an uninstalled tier
    leaves a picker whose choices look like the whole story, and the next thing
    that happens is somebody wonders where the big model went.
    """
    try:
        from shani_chronoa import local_llm

        tiers = local_llm.TIERS
        installed = set(local_llm.installed())
        active = local_llm.active()
    except Exception as exc:  # noqa: BLE001 - a picker with no list says nothing
        logger.debug("model picker: the tier list is unreadable: %s", exc)
        return []

    out = []
    for spec, label, order in tiers:
        # The tier list is `(spec, label, order)`; `label` is the shelf's own
        # sentence ("Small (0.6 GB) - quick on any computer..."), and the short
        # name is what belongs in a narrow popover.
        try:
            short = str(label).split(" - ", 1)[0]
        except Exception:  # noqa: BLE001
            short = str(getattr(spec, "key", ""))
        if spec.key not in installed:
            # **Pressing it does something.** The row used to say "this is a
            # download, not a mode" and be **inert**, which is the worst of both:
            # an instruction with no way to follow it, on the one screen a person
            # with nothing installed reaches for. On a fresh machine every row
            # read that and none could be pressed, so the picker - the control
            # that exists to answer "which model answers" - was a dead end on
            # exactly the question it was built for.
            #
            # Selecting it opens the setup wizard, which is the only thing in the
            # app that installs a model, by the same `common.open_setup` the
            # three panels with "not installed" rows already use. It does *not*
            # hand the key to `app.answer-model`: that action refuses an
            # uninstalled tier by name, correctly, and a correct refusal is
            # still a dead end here.
            subtitle = ("not installed - this is a download, not a mode. "
                        "Select it to install")
        elif spec.key == active:
            subtitle = f"answering now - {getattr(spec, 'blurb', '') or short}"
        else:
            subtitle = getattr(spec, "blurb", "") or short
        out.append((spec.key, short, subtitle, spec.key in installed,
                    spec.key == active))
    return out


class ModelPicker(Gtk.Popover):
    """One popover, behind the picker row under the composer.

    **One widget, so there cannot be two.** A picker that listed the tiers one
    way on the mode strip and another way in the palette would be the
    second-source-of-truth failure this module docstring is written against.

    `chip_for()` used to sit here as the strip's own button, with a docstring
    claiming it was "called on every refresh". **It had no caller at all** -
    `_ModeStrip._model_chip()` builds the strip's dropdown from `_model_rows`
    instead, which is a different widget with a different purpose - so it was a
    second model button that nothing pressed. Deleted rather than wired: the
    strip's control is answered, and a button with no caller is this
    repository's most-recorded defect wearing a docstring as a disguise.
    """

    def __init__(self, app) -> None:
        super().__init__()
        self.add_css_class("model-picker")
        self.set_size_request(260, -1)
        self._app = app
        self.rebuild()

    def rebuild(self) -> None:
        """Refill from the tiers, in the order the shelf keeps them.

        **Rebuilt rather than diffed** - the tiers are static but what is
        installed and what is answering are not, and a picker that kept stale
        rows would say "answering now" next to a model that stopped.

        **`set_child(None)`, not `remove()`.** A `Gtk.Popover` holds exactly
        one child through a setter and is **not** a `Gtk.Box`, so it has no
        `remove` at all. Measured, `hasattr(Gtk.Popover, "remove")` is False
        and `hasattr(Gtk.Box, "remove")` is True.

        This was a **shipped defect, and a quiet one**: `rebuild()` is called
        from `__init__`, where the child is still `None`, so the loop body never
        ran and construction succeeded. Every rebuild **after** that raised
        `AttributeError: 'ModelPicker' object has no attribute 'remove'` - so
        the one thing this method exists for, a model downloaded while the
        window was open, was the one thing that did not work.
        """
        self.set_child(None)

        listing = Gtk.ListBox()
        listing.set_selection_mode(Gtk.SelectionMode.NONE)
        listing.add_css_class("boxed-list")

        rows = rows_for(self._app)
        if not rows:
            empty = Gtk.Label(label="No model tiers could be read.")
            empty.add_css_class("dim-label")
            empty.set_margin_top(12)
            empty.set_margin_bottom(12)
            empty.set_margin_start(12)
            empty.set_margin_end(12)
            self.set_child(empty)
            return

        for key, title, subtitle, usable, active in rows:
            row = Adw.ActionRow()
            row.set_title(title)
            row.set_subtitle(subtitle)
            # **Every row except the answering one can be pressed.** An
            # uninstalled row routes to the installer rather than being inert;
            # `usable` alone made the whole picker unusable on a fresh machine.
            row.set_activatable(not active)
            if active:
                row.add_prefix(Gtk.Image.new_from_icon_name(
                    _CHECK))
                row.add_css_class("model-picker-active")
            elif usable:
                row.add_prefix(Gtk.Image.new_from_icon_name(
                    "go-next-symbolic"))
            else:
                row.add_prefix(Gtk.Image.new_from_icon_name(_DOWNLOAD))
                row.add_css_class("dim-label")
            row.connect(
                "activated",
                lambda _r, k=key: self._choose(k))
            listing.append(row)
        self.set_child(listing)

    @staticmethod
    def _installed(key: str) -> bool:
        """Whether `key` names a tier on this machine.

        **Asked of `local_llm`, not read off the row.** The row was built from
        `installed()` at open time and a download can finish while the popover is
        open, so a row that was a download when it was drawn may be a mode now -
        and pressing it should do what it now says.
        """
        try:
            from shani_chronoa import local_llm

            return key in set(local_llm.installed())
        except Exception as exc:  # noqa: BLE001 - a probe must not break a click
            logger.debug("model picker: could not read installed tiers: %s", exc)
            return False

    def _choose(self, key: str) -> None:
        """Switch, or install - whichever the row is.

        **The action, not a call into `local_llm`.** `app.answer-model` restarts
        the service off the main loop and runs the quality gate - the thing that
        makes *installed* and *usable* different claims - and a picker that
        pointed `current.gguf` itself would read the new name while the old one
        was still answering.

        **An uninstalled tier goes to the wizard instead**, which is the only
        thing in the app that installs one. Measured on a fresh install: six
        rows, every one of them a download, and not one of them pressable, so
        the picker answered "no model answers" with the only control that could
        have changed it doing nothing.
        """
        if not self._installed(key):
            from shani_chronoa.gui.surfaces import common

            common.open_setup(self._app, self)
            return
        app = self._app
        action = getattr(app, "activate_action", None)
        if action is None:
            logger.debug("model picker: nothing to hand the choice to")
            return
        try:
            from gi.repository import GLib

            action("app.answer-model", GLib.Variant("s", key))
        except Exception:  # noqa: BLE001
            logger.warning("model picker: could not switch to %s", key,
                           exc_info=True)
            return
        self.popdown()
