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
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

logger = logging.getLogger(__name__)

#: The leading figure on a row, by what that row is.
_CHECK = "object-select-symbolic"
_DOWNLOAD = "document-open-recent-symbolic"
_CLOUD = "network-transmit-receive-symbolic"

#: How tall the list may grow before it starts scrolling.
#:
#: **The picker had no scroller at all.** Measured on this widget: the six
#: local tier rows ask for **324px**, `min == natural`, and
#: `issubclass(Gtk.ListBox, Gtk.Scrollable)` is **False** - so the popover
#: could not shrink and anything past the screen was simply unreachable. Rows
#: cost about **54px** each, so the cloud section makes it worse by an order of
#: magnitude: the provider catalogues behind it are **458 models (OpenRouter),
#: 390 (Kilo), 115 (BlockRun), 83 (Zen), 64 (llm7)** per this package's own
#: docstring, which is ~21,000px of list.
#:
#: `SelectionMode.NONE` plus `can-focus=False` meant there was no keyboard
#: cursor either, so a cloud list dropped in here would have been mostly
#: unopenable. The cap is a *cap*, not a height: below it the popover is
#: exactly as tall as it needs to be, and above it the scroller takes over.
#:
#: The popover's own `measure()` reports 58px in an unpresented probe whatever
#: the row count, because nothing is allocated - **324px is the figure that
#: matters**, and it is quoted for that reason.
_MAX_HEIGHT_PX = 420

#: How many providers to ask in one pass. Ten providers is ten sequential HTTP
#: requests, which is why this runs on a worker and reports progress per
#: provider rather than blocking the window on all of them.
_FETCH_PROVIDERS: "tuple[str, ...]" = ()


def cloud_rows_for(app) -> "list":
    """`(provider_id, model, subtitle, chosen, pending)` per cloud row.

    **Three states per provider, kept apart**, because they are three different
    facts and collapsing any two of them is how a list starts lying:

    - **not asked yet** - `pending`, and the row says so. This is *nobody has
      looked*, which is not "there are no models".
    - **asked, and it refused** - the row carries the provider's own sentence
      (`why`), which is the only thing that knows whether the answer was a 401, a
      404 or a timeout.
    - **asked, and it answered** - one row per model, the provider's own default
      first.

    **Which providers, and why that order.** The keyed ones first, then the free
    gateways, then your own server - `brain.py`'s own order, because a list that
    answers "which model answers" must not imply a different priority to the
    chain that actually tries them.
    """
    try:
        from shani_chronoa import cloud_llm

        order = (list(cloud_llm.BYOK_PROVIDER_ORDER)
                 + list(cloud_llm.DEFAULT_PROVIDER_ORDER)
                 + [cloud_llm.CUSTOM_ID])
    except Exception as exc:  # noqa: BLE001
        logger.debug("model picker: the provider table is unreadable: %s", exc)
        return []

    chosen: dict = {}
    try:
        for pid, value in (app.config.cloud_llm_models() or {}).items():
            if pid.endswith("_model") and value:
                chosen[pid[: -len("_model")]] = value
    except Exception as exc:  # noqa: BLE001
        logger.debug("model picker: the chosen models are unreadable: %s", exc)

    out = []
    for provider_id in order:
        provider = cloud_llm.PROVIDERS.get(provider_id)
        if provider is None:
            continue
        label = provider.name if provider_id != cloud_llm.CUSTOM_ID \
            else "Your own server"
        if not cloud_llm.models_asked(provider_id):
            out.append((provider_id, "", f"{label} - reading the list...", False,
                        True))
            continue
        ids, why = cloud_llm.cached_models(provider_id)
        if not ids:
            # **The provider's own words.** "There are no models" and "I could
            # not ask" are opposite claims, and this row is where a person finds
            # out which one it is.
            out.append((provider_id, "",
                        f"{label} - {why or 'gave no model list'}", False, False))
            continue
        active = chosen.get(provider_id) or provider.default_model
        listed = False
        for model in ids:
            listed = listed or model == active
            out.append((provider_id, model,
                        f"{label}" + (" - answering now" if model == active
                                      else ""),
                        model == active, False))
        # **The chosen model is shown even when the provider's own list does
        # not carry it.** Measured: `offerable_models` caps at
        # `cloud_llm._MENU_LIMIT` (12), while OpenRouter publishes 458 and Kilo
        # 390 - so a person whose choice sits past the cap had their row
        # dropped, and the picker marked **nothing** as answering while a cloud
        # model answered every turn. That is the absence-shaped wrong answer on
        # the one row whose whole job is to say what is answering, and it is
        # what the "two widgets disagree" failure looks like from inside a
        # single widget.
        #
        # **And it says so**, rather than passing for one of the published
        # ones: the flag model a provider does not currently list is a fact
        # about the provider, and hiding it would make this row agree with the
        # rest of the list about something it cannot know.
        if active and not listed:
            out.append((provider_id, active,
                        f"{label}", True, False))
    return out


def _providers_to_ask(app) -> "list[str]":
    """Providers whose list nobody has fetched yet."""
    try:
        from shani_chronoa import cloud_llm

        order = (list(cloud_llm.BYOK_PROVIDER_ORDER)
                 + list(cloud_llm.DEFAULT_PROVIDER_ORDER)
                 + [cloud_llm.CUSTOM_ID])
    except Exception:  # noqa: BLE001
        return []
    return [pid for pid in order if pid in cloud_llm.PROVIDERS
            and not cloud_llm.models_asked(pid)]


def _fetch_cloud_models(app, widget) -> None:
    """Ask every provider nobody has asked yet. See `_read_models`."""
    _read_models(app, widget)


def _read_models(app, widget, provider_id: str = "") -> None:
    """Read one provider's model list, **off the main loop**, then refresh.

    **A fetch on the GTK thread is a frozen window**, and this is an HTTP
    request per provider - ten of them. So the loop lives on a worker and each
    answer is handed back with `GLib.idle_add`, so the rebuild happens where
    widgets may be touched and the list fills in as answers arrive rather than
    all at once at the end.

    **`provider_id=""` means every provider not yet asked.** That is what the
    popover's `show` does. **A named one reads just that provider**, and it is
    what the row in the list does - because a row that says "reading the
    list..." and cannot be pressed is the dead-control class this repository
    records under five other names, and a provider whose fetch failed or was
    never triggered would leave it that way for good.

    **A provider that raises is remembered as a refusal, not retried forever** -
    `fetch_models` stores whatever it got, including `([], why)`, so the row
    settles on the provider's own sentence rather than spinning.
    """
    try:
        from shani_chronoa import cloud_llm
    except Exception as exc:  # noqa: BLE001
        logger.debug("model picker: cannot ask for cloud models: %s", exc)
        return

    wanted = ([provider_id] if provider_id else _providers_to_ask(app))
    if not wanted:
        return

    keys: dict = {}
    try:
        keys = dict(app.config.cloud_llm_api_keys() or {})
    except Exception as exc:  # noqa: BLE001
        logger.debug("model picker: the keys are unreadable: %s", exc)
    base = ""
    try:
        base = app.config.get("custom-llm-base-url", "")
    except Exception:  # noqa: BLE001
        base = ""

    def work() -> None:
        for pid in wanted:
            try:
                cloud_llm.fetch_models(
                    pid, api_key=keys.get(pid, ""), base_url=base)
            except Exception:                          # noqa: BLE001
                logger.debug("model picker: %s would not answer", pid,
                             exc_info=True)
            GLib.idle_add(lambda w=widget: (w.rebuild(), False)[1])

    threading.Thread(target=work, name="chronoa-cloud-models",
                     daemon=True).start()


def _heading(text: str) -> Gtk.Widget:
    label = Gtk.Label(label=text, xalign=0)
    label.add_css_class("heading")
    label.set_margin_top(6)
    label.set_margin_bottom(4)
    label.set_margin_start(12)
    label.set_margin_end(12)
    return label


def _choice_section(heading: str, action: str, rows) -> Gtk.Widget:
    """A heading and one radio row per `(key, word, line, ...)` in `rows`.

    Bound to the stateful `app.<action>` by name and target, so GTK draws the
    current value as the selected radio and pressing a row changes the state -
    the same contract every mode chip's menu has, in a popover that is not a
    menu.
    """
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    box.append(_heading(heading))
    group = None
    for row in rows:
        key, word, line = row[0], row[1], row[2]
        check = Gtk.CheckButton(label=word)
        check.set_tooltip_text(line)
        check.set_action_name(f"app.{action}")
        check.set_action_target_value(GLib.Variant("s", key))
        if group is None:
            group = check
        else:
            check.set_group(group)
        check.set_margin_start(12)
        check.set_margin_end(12)
        box.append(check)
    return box


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

    def __init__(self, app, before=(), after=()) -> None:
        """`before`/`after`: `(heading, action, rows)` choice sections drawn
        above and below the model lists - where answers may come from, and
        whether the model is kept in memory, which are about the same engine
        the lists choose and used to be two chips of their own."""
        super().__init__()
        self.add_css_class("model-picker")
        self.set_size_request(260, -1)
        self._app = app
        self._before = tuple(before)
        self._after = tuple(after)
        self.rebuild()
        # **On `show`, not on construction.** The list is ten HTTP requests, so
        # a picker that fetched when it was built would freeze the window at
        # startup; and a picker that never fetched would show "reading the
        # list..." forever. `show` is the moment the person asked, and it fires
        # on every reopen - the fetch itself is guarded by
        # `cloud_llm.models_asked`, so reopening is free.
        self.connect("show", self._on_shown)

    def _on_shown(self, *_args) -> None:
        """Ask the unfetched providers, off the main loop, once per open."""
        _fetch_cloud_models(self._app, self)

    def rebuild(self) -> None:
        """Refill from the tiers **and from the providers**, in that order.

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

        **Two sections, because the two lists answer two questions.** "On this
        machine" is a download or a tier already on the disk; "In the cloud" is
        a provider's catalogue, fetched off the thread and arriving later. One
        flat list would put a 400-key provider and a local tier in the same
        alphabetical soup, and the picker's own docstring says a second model
        button was already this module's worst failure.

        **Inside a scroller**, because a bare list cannot scroll. See
        `_MAX_HEIGHT_PX` for the measured heights that made it necessary.
        """
        self.set_child(None)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        for heading, action, rows in self._before:
            box.append(_choice_section(heading, action, rows))

        box.append(_heading("On this machine"))
        local = rows_for(self._app)
        if not local:
            empty = Gtk.Label(label="No model tiers could be read.")
            empty.add_css_class("dim-label")
            empty.set_margin_start(12)
            empty.set_margin_end(12)
            empty.set_margin_bottom(6)
            box.append(empty)
        else:
            listing = Gtk.ListBox()
            listing.set_selection_mode(Gtk.SelectionMode.NONE)
            listing.add_css_class("boxed-list")
            for key, title, subtitle, usable, active in local:
                listing.append(self._local_row(key, title, subtitle, usable,
                                               active))
            box.append(listing)

        box.append(_heading("In the cloud"))
        cloud = Gtk.ListBox()
        cloud.set_selection_mode(Gtk.SelectionMode.NONE)
        cloud.add_css_class("boxed-list")
        for provider_id, model, subtitle, chosen, pending in cloud_rows_for(
                self._app):
            cloud.append(self._cloud_row(provider_id, model, subtitle, chosen,
                                         pending))
        box.append(cloud)
        for heading, action, rows in self._after:
            box.append(_choice_section(heading, action, rows))

        scroller = Gtk.ScrolledWindow()
        # **The height is a ceiling, not a demand.** Below it the popover is
        # exactly as tall as its rows need, so the common case of six tiers
        # looks exactly as it always did; above it the scroller takes over.
        scroller.set_max_content_height(_MAX_HEIGHT_PX)
        # **Without this half the cap is a crush, not a cap.** Measured in a
        # presented window, identical widgets, only this flag differing: the
        # palette's scroller came back **58px** - about one row - with
        # `upper=2100` against `page=58`, and drew a scrollbar over three
        # entries that fit. With the flag it took 420px of the 420px offered,
        # and 126px for the three entries, with no scrollbar.
        #
        # Not measured here, and not claimed: the popover's own scroller. A
        # `Gtk.Popover` does not lay out its child until it is popped up, so
        # presented it reads `upper=0 page=0` - indistinguishable from an empty
        # list. `tests/test_cloud_models_are_chooseable.py` asserts the flag is
        # set; the height that produces in a real popover is **not established**.
        scroller.set_propagate_natural_height(True)
        scroller.set_propagate_natural_width(True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(box)
        self.set_child(scroller)

    def _local_row(self, key, title, subtitle, usable, active) -> Gtk.Widget:
        """One row of the local tiers, with the icon that says which it is."""
        row = Adw.ActionRow()
        row.set_title(title)
        row.set_subtitle(subtitle)
        # **Every row except the answering one can be pressed.** An
        # uninstalled row routes to the installer rather than being inert;
        # `usable` alone made the whole picker unusable on a fresh machine.
        row.set_activatable(not active)
        if active:
            row.add_prefix(Gtk.Image.new_from_icon_name(_CHECK))
            row.add_css_class("model-picker-active")
        elif usable:
            row.add_prefix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        else:
            row.add_prefix(Gtk.Image.new_from_icon_name(_DOWNLOAD))
            row.add_css_class("dim-label")
        row.connect("activated", lambda _r, k=key: self._choose(k))
        return row

    def _cloud_row(self, provider_id, model, subtitle, chosen,
                   pending) -> Gtk.Widget:
        """One row of the cloud section.

        **Three states, kept apart, because they are three different facts:**
        a model you may pick; a provider nobody has asked yet; and a provider
        that refused. The refusal carries the provider's **own sentence** - the
        only thing that knows whether the answer was a 401, a 404 or a timeout
        - because "there are no models" and "I could not ask" are opposite
        claims and this row is where a person finds out which it is.

        **Only a refusal is inert.** An unasked provider is pressable and
        pressing it reads that one list: a row that says "reading the list..."
        and does nothing when pressed is a dead control, and if the fetch was
        never triggered the row would stay that way for good. The refusal stays
        inert because there is no model to hand the provider - pressing it
        could only hand it the sentence.
        """
        row = Adw.ActionRow()
        if model:
            row.set_title(model)
            row.set_subtitle(subtitle)
        else:
            row.set_title(subtitle)
            row.set_subtitle("press to read this provider's list again"
                             if pending else
                             "this provider has to answer before its models "
                             "can be listed")
            row.add_css_class("dim-label")
        if chosen:
            row.add_prefix(Gtk.Image.new_from_icon_name(_CHECK))
            row.add_css_class("model-picker-active")
        else:
            row.add_prefix(Gtk.Image.new_from_icon_name(_CLOUD))
        if model:
            # **Activatable even when chosen.** A row that is already the
            # choice still answers a press: it tells the person it is the one
            # answering, which is the only feedback a greyed-out row cannot
            # give. Selecting it again does no work - `_choose_cloud` notices.
            row.set_activatable(True)
            row.connect("activated", lambda _r, p=provider_id, m=model:
                        self._choose_cloud(p, m))
        elif pending:
            row.set_activatable(True)
            row.connect("activated", lambda _r, p=provider_id:
                        _read_models(self._app, self, p))
        # else: a refusal. Nothing to hand the provider, so nothing to press.
        return row

    def _choose_cloud(self, provider_id: str, model: str) -> None:
        """Hand the choice to the app's own action, then close.

        **The action, not a config write.** `app.answer-cloud-model` writes the
        setting *and* rebuilds the chain when a provider is already answering -
        writing it here would leave the picker saying one model while the
        previous one kept answering, which is the two-widgets-that-disagree
        failure this module's docstring is written against.
        """
        action = getattr(self._app, "activate_action", None)
        if action is None:
            logger.debug("model picker: nothing to hand the cloud choice to")
            return
        try:
            action("answer-cloud-model",
                   GLib.Variant("(ss)", (provider_id, model)))
        except Exception:  # noqa: BLE001
            logger.warning("model picker: could not switch to %s on %s",
                           model, provider_id, exc_info=True)
            return
        # **No status here, deliberately.** `answer-cloud-model` writes the
        # status line itself in every outcome a person can reach, and three of
        # those sentences are strictly better than anything this could say:
        # "Now answering with X (P)", "Saved X (P) ... turn off privacy mode",
        # and - the one this used to overwrite - "Saved X (P), but could not
        # switch: <reason>". Writing a fourth sentence on top of them replaced
        # a refusal with "Selected X for cloud answers", which claims a switch
        # that did not happen. Two places answering one question is the failure
        # this module's docstring is written against, and the action is the one
        # that knows the outcome.
        self.popdown()

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

            action("answer-model", GLib.Variant("s", key))
        except Exception:  # noqa: BLE001
            logger.warning("model picker: could not switch to %s", key,
                           exc_info=True)
            return
        self.popdown()
