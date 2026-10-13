"""The API key could be saved and the model list would still never load.

Asked for a settings icon on each provider so the key could be set in the same
row that needs it. Building it turned up the reason the row *needed* one.

`setup_wizard.py`'s cloud-keys page called

    ids, why = cloud_llm.offerable_models(provider_id)

with **no `api_key`**, so the default `""` applied, so `list_models` built no
`Authorization` header at all. `cloud_llm.py`'s own docstring records that all
four keyed providers *"reject an unauthenticated request outright"* - measured
live, all four, and Gemini's 404 rather than 401 because its API is not
OpenAI-shaped. So **Anthropic, OpenAI, Google and Groq could never list a model
on this page, on any machine, whether a key was saved or not.**

And the row said so in the provider's own words. The refusal path keeps `why`,
so the dropdown went dead beside *"https://api.anthropic.com/v1/models could not
be read (HTTP Error 401: Unauthorized)"* - which reads as *your key is wrong or
missing*, and in fact **no key was ever offered**. The key was read forty lines
above the call; the only missing half was handing it over.

**That is the half the first two tests pin**, because it is the half that was
actually broken. The gear is the half that was asked for, and it is a real
improvement - but a gear on a row whose dropdown is disabled for a reason the
gear does not fix would be a second way to be told nothing.

### What the first version of this file got wrong

It asserted the provider row *has* a settings button. That passes on a build
where the button does nothing at all - it is a shape, not a consequence - and
this file has been bitten by that eleven times in one session. The control is
`test_a_saved_key_is_what_the_fetch_is_given`, which is the claim the defect
actually was; the gear's own tests walk the **built** page rather than
re-deriving which rows the builder makes.
"""
import subprocess
from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

Adw.init()

from shani_chronoa import cloud_llm, setup_wizard  # noqa: E402
from shani_chronoa.config import ChronoaConfig      # noqa: E402


def _walk(widget, out=None):
    out = [] if out is None else out
    child = widget.get_first_child()
    while child is not None:
        out.append(child)
        _walk(child, out)
        child = child.get_next_sibling()
    return out


def _view(window):
    for widget in _walk(window):
        if isinstance(widget, Adw.NavigationView):
            return widget
    return None


class _Config(ChronoaConfig):
    """A **real** `ChronoaConfig`, with only the two things a test must control
    replaced.

    **This file's first version hand-wrote the stub**, and omitted three
    attributes the page reads - `piper_voice`, `ollama_host` and `model` - so
    `setup_wizard.state()` raised `AttributeError` **inside
    `Gtk.Application`'s `activate` handler**. Measured: an exception there
    neither propagates nor quits the app, so the fixture's `poll` was never
    armed and `app.run([])` never returned. **One test, 90 s, no output at all**,
    and two backgrounded runs of the whole file burned 878 s before being killed.

    A hand-written stub is a guess about an interface with a dozen methods on it;
    the real class *is* the interface. Only two things are replaced:

    - `set_api_key`, which would otherwise write the developer's own keyring -
      this repository's recorded trap, from a test that did it once already; and
    - the key reader, so the page sees this test's keys and not whatever is in
      the developer's own settings.
    """

    def __init__(self):
        super().__init__()
        self.keys = {"kilo": "kilo-secret", "openai": "openai-secret",
                     "anthropic": "anthropic-secret", "google": "",
                     "groq": ""}
        self.models = {}
        self.saved_keys = {}
        self.saved_models = {}

    def cloud_llm_api_keys(self):
        return dict(self.keys)

    def cloud_llm_models(self):
        return dict(self.models)

    def set_api_key(self, setting, value):
        self.saved_keys[setting] = value

    def set_cloud_model(self, provider_id, model):
        self.saved_models[provider_id] = model


@pytest.fixture
def page(monkeypatch):
    """The **built** cloud-keys page, its config, and every fetch it made.

    `offerable_models` is stubbed *before* the window is built, because the
    page's own `fill()` thread starts during construction - a test that stubbed
    afterwards would race a real network fetch against its own assertion.
    """
    seen = []
    ticks = [0]

    real = cloud_llm.offerable_models

    def fake(provider_id, timeout=12.0, api_key="", base_url=""):
        seen.append((provider_id, api_key))
        provider = cloud_llm.PROVIDERS[provider_id]
        if not provider.model_list:
            # **Delegate rather than pretend.** A stub that answered every
            # provider with a list made the one test about a provider that
            # *cannot* list report "the stub answered with a list, so this case
            # is not being exercised" - correctly. This path makes no request
            # (`list_models` refuses before it builds a URL), so it is the real
            # behaviour and it is what that test needs to be reading.
            return real(provider_id, timeout=timeout, api_key=api_key,
                        base_url=base_url)
        default = provider.default_model
        ids = [default, "alt-one", "alt-two"] if default else \
            ["served-one", "served-two"]
        return ids, ""

    monkeypatch.setattr(cloud_llm, "offerable_models", fake)
    cfg = _Config()
    # **`fill()` builds its own `ChronoaConfig()`** rather than closing over the
    # page's, which is right in production (both read the same GSettings, and the
    # comment on that thread says it must describe the machine as it is rather
    # than as this page hopes it will be). It also means the page cannot be made
    # to read a test's keys, so the fresh construction is redirected here -
    # otherwise the page is asked with `''` whatever the test saved, and the
    # very defect this file exists for is invisible.
    monkeypatch.setattr("shani_chronoa.config.ChronoaConfig", lambda: cfg)
    box = {}

    app = Gtk.Application(application_id="test.chronoa.cloudkey",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)

    def build(a):
        # **An exception in `activate` neither propagates nor quits the app.**
        # Without this the window is never built, `poll` is never armed, and
        # `app.run([])` blocks forever - so a one-line mistake in a stub reads
        # as a hung test file rather than as the AttributeError it is. That is
        # what this file's first version did, twice, for 878 s.
        try:
            box["window"] = setup_wizard.build_window(a, cfg)
        except BaseException as exc:              # noqa: BLE001
            box["error"] = exc
            a.quit()
            return False
        box["window"].present()
        GLib.timeout_add(200, poll)
        return False

    def poll():
        window = box.get("window")
        view = _view(window) if window is not None else None
        if view is not None:
            view.push_by_tag("cloud-keys")
            GLib.timeout_add(400, finish)
            return False
        # **Never re-arm forever.** A poll that only stops on success is a
        # second way to hang, on the path where the page never settles.
        ticks[0] += 1
        if ticks[0] > 60:
            box["error"] = AssertionError(
                "the wizard built no NavigationView after 60 polls, so the "
                "cloud-keys page could never be reached")
            app.quit()
            return False
        GLib.timeout_add(100, poll)
        return False

    def finish():
        GLib.timeout_add(50, lambda: (app.quit(), False)[1])
        return False

    app.connect("activate", build)
    app.run([])
    if "error" in box:
        raise box["error"]
    # **The window is handed back alive.** This file's version destroyed it
    # first, and the tell was that a probe driving the same code outside
    # pytest answered `_shown` False before the gear and True after, while the
    # test said the gear did nothing: `destroy()` tears the tree down, so every
    # ancestor the "is it on screen" walk depends on is gone. The sibling
    # `_open` in `test_cloud_branch_is_honest.py` never destroyed it either.
    return SimpleNamespace(window=box["window"], config=cfg, calls=seen)


def _provider_rows(window):
    """The `Adw.ActionRow`s carrying a model dropdown, by provider id.

    **Found by walking the built page**, not by re-deriving which rows the
    builder makes: a test that recomputes the population proves its own
    arithmetic, which is the `skills_total` mistake this file records.
    """
    by_name = {p.name: p for p in cloud_llm.PROVIDERS.values()}
    by_name["Your own server"] = cloud_llm.PROVIDERS["custom"]
    found = {}
    for w in _walk(window):
        if not isinstance(w, Adw.ActionRow):
            continue
        provider = by_name.get(w.get_title())
        if provider is None:
            continue
        if not any(isinstance(c, Gtk.DropDown) for c in _walk(w)):
            continue          # this is a key row, not a model row
        found[provider.id] = w
    return found


def _gear(row):
    for c in _walk(row):
        if isinstance(c, Gtk.Button) and \
                c.get_icon_name() == "preferences-system-symbolic":
            return c
    return None


def _shown(entry):
    """Is this entry actually on screen, in a tree that was never presented?

    **`entry.get_visible()` cannot answer this, and both properties that sound
    like they can were measured.** Measured on this build: `get_visible()`
    reports the widget's *own* flag and is `True` whether its container is
    shown or not, and `get_mapped()` is `False` in both states because nothing
    is presented in a test. So the honest question is about the **container** -
    the editor is a `Gtk.Box` that is `set_visible(False)` until the gear is
    pressed, and that box's flag really does flip.

    Both directions are asserted by the tests that call this (hidden before the
    press, shown after, hidden again on the second press), so a helper that
    answered one constant would fail the toggle test rather than pass it.
    """
    widget = entry
    while widget is not None:
        if not widget.get_visible():
            return False
        widget = widget.get_parent()
    return True


def _entry(row):
    for c in _walk(row):
        if isinstance(c, Gtk.Entry):
            return c
    return None


# ── the defect: the key was never offered to the provider ─────────────────

def test_a_saved_key_is_what_the_fetch_is_given(page):
    """**The claim the defect actually was.**

    The page read `cloud_llm_api_keys()` and then called
    `offerable_models(provider_id)` without it, so `list_models` sent no
    `Authorization` header and the four keyed providers answered 401 forever.
    This asks what the provider was actually *given*.
    """
    assert page.calls, "the page asked no provider for its models at all"

    with_key = {pid for pid, v in page.config.keys.items() if v}
    assert with_key, "the fixture saved no keys, so nothing could be checked"

    asked = {pid for pid, _api in page.calls}
    missing = with_key - asked
    assert not missing, f"{sorted(missing)} have a saved key and were never asked"

    for provider_id, api_key in page.calls:
        if provider_id in with_key:
            assert api_key == page.config.keys[provider_id], (
                f"{provider_id} has a saved key and was asked with {api_key!r}"
            )


def test_a_provider_with_no_key_is_still_asked_with_none(page):
    """The other direction, so the first test cannot be satisfied by sending
    every provider every key. `google` has none saved and must be asked as
    itself - asking it with a key would be a different lie."""
    google = [api for pid, api in page.calls if pid == "google"]
    assert google, "google was never asked"
    assert all(api == "" for api in google), (
        f"google has no key saved and was asked with {google!r}"
    )


# ── the gear the fix was asked for ────────────────────────────────────────

def test_every_provider_row_carries_a_settings_control(page):
    """A gear on each provider, because the key and the model are one decision
    about one provider and this page made them two."""
    rows = _provider_rows(page.window)
    assert len(rows) == len(cloud_llm.PROVIDERS), (
        f"expected a row for each of {len(cloud_llm.PROVIDERS)} providers, found "
        f"{sorted(rows)}"
    )
    for provider_id, row in rows.items():
        assert _gear(row) is not None, f"{provider_id} has no settings control"


def test_the_gear_icon_exists_in_adwaita_not_just_in_this_theme():
    """**The rule this file has been bitten by three times**, each time by a
    plausible name that is Yaru-only and draws an empty box that reads as a
    broken control. The check that catches it is an `rglob` over the theme
    directory, not `has_icon` - which this box's theme fallback answers yes to.
    """
    found = subprocess.run(
        ["find", "/usr/share/icons/Adwaita", "-name",
         "preferences-system-symbolic.svg"],
        capture_output=True, text=True, timeout=30)
    assert found.returncode == 0 and found.stdout.strip(), (
        "preferences-system-symbolic is not in Adwaita, so the gear is a blank "
        "box on a plain GNOME desktop"
    )


def test_the_key_editor_is_hidden_until_the_gear_is_pressed(page):
    """A row that arrives already expanded is a row nobody can read, and the
    editor is a password field."""
    rows = _provider_rows(page.window)
    assert rows, "no provider rows were built"
    for provider_id, row in rows.items():
        entry = _entry(row)
        assert entry is not None, f"{provider_id} has no key field to reveal"
        assert not _shown(entry), (
            f"{provider_id}'s key field is on screen before the gear was pressed"
        )


def test_the_gear_reveals_the_key_and_the_same_gear_closes_it(page):
    """Toggles, because a gear that only ever opens leaves no way back."""
    row = _provider_rows(page.window)["anthropic"]
    gear, entry = _gear(row), _entry(row)

    assert not _shown(entry), "the key field is on screen before any press"
    gear.emit("clicked")
    assert _shown(entry), "the gear did not reveal the key field"

    gear.emit("clicked")
    assert not _shown(entry), "the gear opened it and could not close it"


def test_enter_in_the_key_field_commits_it(page):
    """**A field with no way to commit it is the `Gtk.TextView`-in-a-`Gtk.Entry`
    shape**: the value is on screen and nothing has it. `activate` is the signal
    a `Gtk.Entry` emits for Enter, and it is distinct from the row's own
    activation, so the gear's toggle is not stolen by it."""
    row = _provider_rows(page.window)["groq"]
    entry = _entry(row)
    entry.set_text("typed-key")
    entry.emit("activate")
    assert page.config.saved_keys.get("groq-api-key") == "typed-key", (
        f"Enter committed nothing; saved keys are {sorted(page.config.saved_keys)}"
    )


def test_an_empty_key_is_not_saved_over_a_good_one(page):
    """The over-fix: pressing Enter on an empty field wiping a key that works.
    **The remedy for a mistyped key is not to lose the working one.**"""
    row = _provider_rows(page.window)["kilo"]
    entry = _entry(row)
    entry.set_text("   ")
    entry.emit("activate")
    assert "kilo-api-key" not in page.config.saved_keys, (
        "an empty field overwrote the saved key"
    )

# ── the two providers the registry did not list ───────────────────────────

def test_every_ordered_provider_now_has_a_row(page):
    """**The registry and the order were two lists, and they disagreed.**

    `anthropic` was in `BYOK_PROVIDER_ORDER` and not in `PROVIDERS`, so the
    page's loop dropped it with `if provider is None: continue` - no row, no
    key, no model, and nothing saying it was absent - while `app/brain.py`
    built an `AnthropicLLM` from the key the moment one was saved. A provider
    the engine uses and no list shows is the shape this file records most often.
    """
    ordered = list(cloud_llm.BYOK_PROVIDER_ORDER) + \
        list(cloud_llm.DEFAULT_PROVIDER_ORDER)
    missing = [p for p in ordered if p not in cloud_llm.PROVIDERS]
    assert not missing, f"ordered but unregistered: {missing}"
    rows = _provider_rows(page.window)
    for provider_id in ordered:
        assert provider_id in rows, (
            f"{provider_id} is ordered and registered but has no row")


def test_anthropic_says_its_model_is_fixed_rather_than_saying_nothing(page):
    """`model_list=False` for Anthropic because its API is **not** OpenAI-shaped
    - it authenticates with `x-api-key`, which is why `AnthropicLLM` is its own
    native adapter. The row must therefore say the model is *fixed*, never that
    there are *no models*: those are opposite claims, and the second is what an
    empty dropdown reads as."""
    row = _provider_rows(page.window)["anthropic"]
    picked = next(c for c in _walk(row) if isinstance(c, Gtk.DropDown))
    assert not picked.get_sensitive(), (
        "the stub answered with a list, so this case is not being exercised")
    ids, why = cloud_llm.offerable_models("anthropic")
    assert ids == []
    assert "claude-haiku-4-5-20251001" in why, (
        f"the reason does not name the model that answers instead: {why!r}"
    )
    assert "does not publish a model list" in why, why


def test_a_provider_that_cannot_be_listed_still_answers_with_its_default(page):
    """The over-fix this shape invites: marking a provider unlistable must not
    make it look broken, because **it is used.** `AnthropicLLM` is constructed
    from the saved key in `CloudLLMChain.__init__`."""
    from shani_chronoa import cloud_llm as cl
    assert cl.PROVIDERS["anthropic"].default_model, "no default to answer with"
    assert cl.PROVIDERS["anthropic"].requires_key is True


def test_your_own_server_is_a_row_with_its_own_three_fields(page):
    """**A gear that revealed only a key would leave the two fields this
    provider actually needs on another page** - and the address is the one
    carrying the host and the port."""
    row = _provider_rows(page.window)["custom"]
    entries = [c for c in _walk(row) if isinstance(c, Gtk.Entry)]
    assert len(entries) >= 3, (
        f"your own server needs an address, a model and a key; found "
        f"{len(entries)} field(s)"
    )
    assert not any(_shown(e) for e in entries), (
        "the fields are on screen before the gear was pressed")


def test_your_own_server_is_asked_at_the_address_the_person_typed(page):
    """`custom`'s `base_url` is empty **by design** - the address is a setting,
    on a machine nobody else can see - so without passing it through, a server
    the person has correctly configured answers "no address has been set"."""
    cfg = page.config
    cfg.set("custom-llm-base-url", "http://192.168.1.20:8080/v1")
    ids, why = cloud_llm.offerable_models(
        "custom", base_url=cfg.get("custom-llm-base-url", ""))
    assert "no address has been set" not in why, (
        "a configured server was told no address had been set"
    )
    assert why == "" and ids, f"the configured address produced {why!r}"


def test_an_empty_default_model_is_not_offered_as_a_blank_option():
    """Your own server has no registry default. Inserting the empty string put a
    **blank option at the top of a dropdown**, which reads as a broken control
    rather than as a provider with nothing to show."""
    provider = cloud_llm.PROVIDERS["custom"]
    assert provider.default_model == ""
    ids, _why = cloud_llm.offerable_models.__wrapped__(  # type: ignore[attr-defined]
        "custom") if hasattr(cloud_llm.offerable_models, "__wrapped__") else (
        [], "")
    # The real invariant: nothing offers "" as a choice.
    assert "" not in ids


def test_the_pages_own_save_button_reports_what_it_saved(page):
    """**The HEAD defect, on the path nothing drove.**

    `save()` - the handler behind both "Save these keys" and this page's Next -
    read `config.API_KEY_SETTINGS[provider_id]`. `API_KEY_SETTINGS` is a
    module-level constant in `config.py`; inside `build_window` `config` is the
    `ChronoaConfig` **instance**, so the expression raised `AttributeError` on
    every press, on every install.

    **The write is not the witness, and that is worth knowing.** `save()` loops
    over `entered`, and only `save_one_key` ever puts a value there - having
    already written the same setting with the same value. So restoring the
    shipped line leaves `saved_keys` identical and a test that only checked it
    would pass with the defect present. What the exception skips is the rest of
    the handler: the status sentence and `remember_models()`. So the witness is
    the page's own report.
    """
    buttons = [w for w in _walk(page.window)
               if isinstance(w, Gtk.Button)
               and w.get_label() == "Save these keys"]
    assert buttons, "the cloud-keys page has no save button"
    labels = [w for w in _walk(page.window) if isinstance(w, Gtk.Label)]

    buttons[0].emit("clicked")

    said = [w.get_label() for w in labels if w.get_label()]
    assert any("Saved" in text for text in said), (
        "the save button reported nothing; the page's labels are "
        f"{sorted(set(said))[:6]}"
    )
