"""Mutation set for "cloud models can be listed, read and switched".

Three shipped defects, none of which any test could see, and the reasons are
worth more than the tally.

**1. The palette was never in the window.** `_build_palette` built it, fed it 50
entries, set it invisible and wired Ctrl+K - and added it to *no container*. No
`append`, `set_parent`, `Overlay` or `add(` for `_palette` existed anywhere in
`window.py`. Measured on the built window before the fix: before the press
`visible=False parent=None`; after the real handler returned `True`,
`visible=True parent=None`; 400 ms later `mapped=False` and **not in the
window's widget subtree**. A `Gtk.Box` cannot be a toplevel, so there was never
a surface for it to appear on.

It survived because `tests/test_command_palette.py` builds the widget
standalone, where everything works, and `test_shortcuts_are_discoverable.py`
asserts the *text* "Ctrl+K for anything else". This is the repository's own
"a test that calls the function is not a test that the skill is reachable", in a
widget - and the mutation that restores it is the strongest kind there is.

**2. Neither surface could scroll.** `Gtk.ListBox` is not a `Gtk.Scrollable` and
cannot be made one; measured, the palette's own rows ask for **252px at 6
entries, 840px at 20 and 2100px at 50**, `min == natural` at every one. The
picker's six local tiers ask for **324px** and the provider catalogues behind it
are 458 models (OpenRouter) and 390 (Kilo) - about 21,000px of list. Neither
popover could shrink, so the tail was unreachable.

**3. The chosen model could vanish from the picker.** `offerable_models` caps at
`cloud_llm._MENU_LIMIT` (12) while OpenRouter publishes 458, so a person whose
choice sits past the cap had their row dropped and **the picker marked nothing
as answering while a cloud model answered every turn**. That is the
absence-shaped wrong answer on the one row whose whole job is to say what is
answering, and it was found by measuring the first version rather than by
thinking about it.

**Two of the three are over-fixes in waiting**, so both directions are carried:
an unreadable store reported as a clean answer is the sibling of the third
defect, and marking *any* provider row as answering would be its mirror image.

`AGENTS.md`'s rule applies to this file as much as to the others: a run that
cannot measure is reported unmeasurable rather than as caught, and every
mutation names the test that fails rather than the behaviour it hopes to break.
"""

TEST_FILE = "tests/test_cloud_models_are_chooseable.py"

_PICKER = "usr/lib/shani-chronoa/shani_chronoa/gui/model_picker.py"
_PALETTE = "usr/lib/shani-chronoa/shani_chronoa/gui/palette.py"
_WINDOW = "usr/lib/shani-chronoa/shani_chronoa/gui/window.py"
_APP = "usr/lib/shani-chronoa/shani_chronoa/app/application.py"
_CLOUD = "usr/lib/shani-chronoa/shani_chronoa/cloud_llm.py"

MUTATIONS = [
    # ── the palette was never on screen ──────────────────────────────────
    {
        "file": _WINDOW,
        # **The shipped defect, restored verbatim.** One line removed, and the
        # palette goes back to being a widget nobody can see - built, filled,
        # keyed, and parented to nothing.
        "old": "        main_box.append(self._palette)",
        "new": "        pass  # not parented",
        "label": "the palette is never added to the window",
        "expect": [
            "test_the_palette_is_parented_into_the_window",
            "test_the_palette_is_in_the_windows_widget_subtree",
        ],
    },
    {
        "file": _WINDOW,
        # **In the window, in the wrong column** - inside `_question_row`,
        # which is itself hidden. This *constructs*, which is why it replaced
        # `self.set_child(self._palette)`: that one replaces the
        # `Gtk.ApplicationWindow`'s only child and the harness aborted with no
        # summary, so it was UNMEASURABLE rather than caught.
        #
        # And it is the more instructive mutation - the palette is a descendant
        # of the window, so a test asking only "is it in the subtree" passes,
        # while the widget is laid out inside a box that is not on screen. A
        # hidden ancestor takes its children with it, so `mapped` is False and
        # `get_parent() is window._chat_column` is False.
        "old": "        main_box.append(self._palette)",
        "new": "        self._question_row.append(self._palette)",
        "label": "the palette is parented inside the hidden question row",
        "expect": ["test_the_palette_is_in_the_windows_widget_subtree"],
    },

    # ── the ceiling without the other half is a crush, not a cap ──────────
    # **These two are separate from the ceiling tests above because
    # `test_the_picker_has_a_scroller_with_a_ceiling` PASSES with the flag
    # deleted** - measured, it did. `set_max_content_height` bounds the
    # request and does nothing about the scroller not *asking* for the
    # height, so the widget came back at its own 58px minimum (one row) and
    # the 3-entry palette drew a scrollbar over content that fits.
    {
        "file": _PALETTE,
        "old": "        scroller.set_propagate_natural_height(True)\n",
        "new": "",
        "label": "the palette's scroller is capped but never asked to grow",
        "expect": ["test_both_scrollers_are_asked_to_grow_into_their_ceiling"],
    },
    {
        "file": _PICKER,
        "old": "        scroller.set_propagate_natural_height(True)\n",
        "new": "",
        "label": "the picker's scroller is capped but never asked to grow",
        "expect": ["test_both_scrollers_are_asked_to_grow_into_their_ceiling"],
    },

    # ── neither surface could scroll ─────────────────────────────────────
    {
        "file": _PALETTE,
        # **The shipped defect, restored verbatim.** The bare ListBox is back,
        # and 50 entries are 2100px of unreachable list again.
        "old": "        scroller = Gtk.ScrolledWindow()\n"
               "        # A cap rather than a demand: fewer than about six rows and the palette\n"
               "        # is exactly as tall as its rows need, which is how it read before.\n"
               "        scroller.set_max_content_height(_MAX_HEIGHT_PX)",
        "new": "        scroller = Gtk.ListBox()\n"
               "        scroller.set_max_content_height(_MAX_HEIGHT_PX)",
        "label": "the palette's list is a bare ListBox again",
        "expect": ["test_the_palette_list_is_inside_a_scroller_with_a_ceiling"],
    },
    {
        "file": _PALETTE,
        "old": "        scroller.set_max_content_height(_MAX_HEIGHT_PX)\n",
        "new": "",
        "label": "the scroller has no ceiling and grows with the entries",
        "expect": ["test_the_palette_list_is_inside_a_scroller_with_a_ceiling"],
    },
    {
        "file": _PALETTE,
        "old": "        scroller.set_child(self._list)\n"
               "        self._scroller = scroller\n"
               "        self.append(scroller)",
        "new": "        scroller.set_child(self._list)\n"
               "        self._scroller = scroller\n"
               "        scroller.set_visible(False)",
        "label": "the scroller exists but is hidden",
        "expect": ["test_the_palette_list_is_inside_a_scroller_with_a_ceiling"],
    },
    {
        "file": _PICKER,
        "old": "        scroller.set_propagate_natural_height(True)\n"
               "        scroller.set_propagate_natural_width(True)\n"
               "        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)\n"
               "        scroller.set_child(box)\n"
               "        self.set_child(scroller)",
        "new": "        self.set_child(box)",
        "label": "the picker's list is not in a scroller",
        "expect": ["test_the_picker_has_a_scroller_with_a_ceiling"],
    },
    {
        "file": _PICKER,
        # **Not the same as removing the scroller**: the popover keeps one but
        # the list inside it is the bare box, so the cap applies to nothing.
        "old": "        scroller.set_child(box)",
        "new": "        scroller.set_child(self._list)",
        "label": "the scroller wraps the old list rather than the new one",
        "expect": ["test_the_picker_scroller_wraps_the_rows_it_contains"],
    },

    # ── the chosen model could vanish ────────────────────────────────────
    {
        "file": _PICKER,
        # **The shipped defect, restored verbatim.** A chosen model the
        # provider's capped list does not carry is dropped, and the picker marks
        # nothing as answering.
        "old": "        if active and not listed:",
        "new": "        if False:",
        "label": "a chosen model the provider omits is dropped",
        "expect": [
            "test_the_chosen_model_is_shown_even_when_the_provider_omits_it",
        ],
    },
    {
        "file": _PICKER,
        "old": "                        f\"{label} - answering now - not in this provider's \"\n"
               "                        f\"published list\", True, False)",
        "new": "                        f\"{label}\", True, False)",
        "label": "a model the provider does not list passes for one it does",
        "expect": [
            "test_the_chosen_model_says_so_when_the_provider_omits_it",
        ],
    },
    {
        "file": _PICKER,
        # **The over-fix.** Marking *every* provider's default as answering is
        # the mirror of the defect: ten rows claiming to answer.
        "old": "            listed = listed or model == active",
        "new": "            listed = True",
        "label": "a listed model always counts as the chosen one",
        "expect": [
            "test_the_chosen_model_is_shown_even_when_the_provider_omits_it",
        ],
    },

    # ── a refusal is not "there are no models" ──────────────────────────
    {
        "file": _PICKER,
        "old": "            out.append((provider_id, \"\",\n"
               "                        f\"{label} - {why or 'gave no model list'}\", False, False))",
        "new": "            out.append((provider_id, \"\", f\"{label}\", False, False))",
        "label": "a refusal loses the provider's own reason",
        "expect": ["test_a_refusal_carries_the_providers_own_reason"],
    },
    {
        "file": _WINDOW,
        "old": "                    f\"{label}\", why or \"gave no model list\",",
        "new": "                    f\"{label}\", \"gave no model list\",",
        "label": "the palette's refusal loses the provider's reason",
        "expect": ["test_a_refusal_is_kept_and_says_why"],
    },
    {
        "file": _WINDOW,
        # `collect()` drops an entry with no `run`, so a refusal with nothing to
        # do disappears and a 429 reads the same as a provider never asked.
        "old": "                    \"network-transmit-receive-symbolic\",\n"
               "                    (lambda p=provider_id: self._read_cloud_models(p)),\n"
               "                    group=\"Cloud models\", weight=0))\n"
               "                continue\n"
               "            # **`collect()` drops an entry with no `run`,** so every row here",
        "new": "                    \"network-transmit-receive-symbolic\",\n"
               "                    None,\n"
               "                    group=\"Cloud models\", weight=0))\n"
               "                continue\n"
               "            # **`collect()` drops an entry with no `run`,** so every row here",
        "label": "the palette's refusal has nothing to do and is dropped",
        "expect": [
            "test_a_refusal_is_kept_and_says_why",
            "test_the_cloud_rows_survive_collect",
        ],
    },

    # ── the key and the address have to travel ───────────────────────────
    {
        "file": _PICKER,
        "old": "                cloud_llm.fetch_models(\n"
               "                    pid, api_key=keys.get(pid, \"\"), base_url=base)",
        "new": "                cloud_llm.fetch_models(pid)",
        "label": "the provider is asked with no key and no address",
        "expect": ["test_the_fetch_carries_the_key_and_the_custom_address"],
    },
    {
        "file": _PICKER,
        "old": "        base = app.config.get(\"custom-llm-base-url\", \"\")",
        "new": "        base = \"\"",
        "label": "your own server is asked at the default address",
        "expect": ["test_the_fetch_carries_the_key_and_the_custom_address"],
    },

    # ── a row nothing can do ─────────────────────────────────────────────
    {
        "file": _PICKER,
        # The pending row goes back to inert: "reading the list..." and a press
        # that does nothing, which is the dead-control class.
        "old": "        elif pending:\n"
               "            row.set_activatable(True)\n"
               "            row.connect(\"activated\", lambda _r, p=provider_id:\n"
               "                        _read_models(self._app, self, p))",
        "new": "        elif pending:\n"
               "            row.set_activatable(False)\n"
               "            row.connect(\"activated\", lambda _r, p=provider_id:\n"
               "                        _read_models(self._app, self, p))",
        "label": "a provider nobody asked cannot be asked",
        "expect": ["test_an_unasked_provider_can_be_pressed_to_read_its_list"],
    },

    # ── the action ───────────────────────────────────────────────────────
    {
        "file": _APP,
        "old": "        if self.current_llm_kind() != \"cloud\":",
        "new": "        if False:",
        "label": "a local machine is moved to the cloud by choosing a model",
        "expect": [
            "test_a_machine_answered_locally_is_not_moved_to_the_cloud",
        ],
    },
    {
        "file": _APP,
        # **The over-fix, and the one that matters.** Rebuilding unconditionally
        # means a model pick takes seconds on the main-loop thread and refuses
        # when privacy mode is on, for a machine answering locally.
        "old": "        problem = self.select_llm(\"cloud\")\n"
               "        if problem:",
        "new": "        problem = \"\"\n"
               "        if problem:",
        "label": "the chain is never rebuilt, so the old model keeps answering",
        "expect": ["test_a_cloud_engine_is_rebuilt_so_the_choice_takes_effect_now"],
    },
    {
        "file": _APP,
        "old": "        provider = cloud_llm.PROVIDERS.get(provider_id)\n"
               "        if provider is None:",
        "new": "        provider = cloud_llm.PROVIDERS.get(provider_id)\n"
               "        if False:",
        "label": "an unregistered provider is accepted",
        # **Not `test_an_unknown_provider_is_refused_by_name`, which came back a
        # SURVIVOR against this.** A name in neither `PROVIDERS` nor
        # `MODEL_SETTINGS` is refused by *both* guards, so removing either one
        # leaves that test green - the witness could not tell the two apart. The
        # test that adds the name to `MODEL_SETTINGS` alone is the one that
        # leaves the registry guard as the only thing standing.
        "expect": ["test_a_provider_the_registry_does_not_know_is_still_refused"],
    },
    {
        "file": _APP,
        # `set_cloud_model` returns early on an id it does not name, so this
        # would look like it worked and be discarded.
        "old": "        if MODEL_SETTINGS.get(provider_id) is None:",
        "new": "        if False:",
        "label": "a choice for a provider with no model setting is discarded",
        "expect": ["test_a_provider_with_no_model_setting_is_refused_by_name"],
    },
    {
        "file": _APP,
        "old": "        model = model.strip()\n        if not model:",
        "new": "        model = model\n        if not model:",
        "label": "a whitespace-only model name is written",
        "expect": ["test_a_blank_model_is_refused"],
    },
    {
        "file": _APP,
        "old": "        model = model.strip()\n        if not model:",
        "new": "        if not model.strip():",
        "label": "the model name is saved with its surrounding spaces",
        # **Was `test_a_choice_is_written`, which passes either way** - it sends
        # an already-trimmed name, so a removed `.strip()` cannot change what it
        # sees. The run reported it as a survivor and named the test that did
        # bite (`test_a_padded_model_name_is_saved_trimmed`), which is the
        # over-claiming failure `asleep_state` records: a witness list that
        # names a test passing either way reads as coverage while the mutation
        # is covered by a different one. Correct the name, do not add a test.
        "expect": ["test_a_padded_model_name_is_saved_trimmed"],
    },
    {
        "file": _APP,
        "old": "            \"answer-cloud-model\", GLib.VariantType.new(\"(ss)\"))",
        "new": "            \"answer-cloud-model\", GLib.VariantType.new(\"s\"))",
        "label": "the action takes one value instead of a provider and a model",
        "expect": ["test_it_is_registered_with_a_provider_and_a_model"],
    },
]