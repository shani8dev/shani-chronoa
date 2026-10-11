"""The Models panel could not switch model (2026-10-11)

`_pick`'s own docstring said "Writes the setting and stops there", and the button
that called it was labelled from `installed`: **"Ready"**, with the tooltip
**"In use"** - for every model that happened to be on disk, while the server held
whichever one it had loaded. So a panel with five installed models showed five
"In use"s: five claims, at most one of them true, and no control anywhere that
answered "use this one".

`local_llm.use(key)` points `current.gguf` at the model after a quality gate, and
`start_service()` restarts the unit with it. Both already existed; neither was
reachable from the panel.
"""

import gi

gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

Adw.init()
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402


def _kids(widget):
    out, child = [], widget.get_first_child()
    while child is not None:
        out.append(child)
        child = child.get_next_sibling()
    return out


def _button(box):
    return next(c for c in _kids(box) if isinstance(c, Gtk.Button))


def _status(box):
    labels = [c for c in _kids(box) if isinstance(c, Gtk.Label)]
    return labels[0] if labels else None


class _App:
    """A stub the panel can build against: a config, and a locally-routed engine.

    `llm` matters - **"In use" is a claim about the app being routed to this
    model, not only about the server holding its file** - so a stub without one
    makes the honest answer "Loaded", which is not what this test is about.
    """

    class config:
        get = staticmethod(lambda *a, **k: "")

    llm = "a local engine"


def _card(card):
    from shani_chronoa.gui.surfaces import models

    return models._card(_App(), card, lambda: None)


def _llm(key, installed, chosen=False):
    return {"key": key, "label": key, "size": 4e9, "kind": "llm",
            "installed": installed, "chosen": chosen}


def test_an_installed_model_offers_to_be_used_not_merely_remarked():
    """The button said "Ready" for every model on disk, which reads as "this is
    the one answering". It now offers the action it can perform."""
    button = _button(_card(_llm("qwen3-1.7b", True)))

    assert button.get_label() == "Use it", (
        "an installed model that is not running offered %r; the panel's job is "
        "to let a person switch, not to remark that it is on disk"
        % button.get_label())
    assert button.get_sensitive() is True


def test_the_running_model_says_it_is_the_one_and_offers_nothing(monkeypatch):
    """`installed` and `in use` were the same word. **Five installed models showed
    five "In use"s**, which is five claims and at most one true one."""
    from shani_chronoa import local_llm
    monkeypatch.setattr(local_llm, "active", lambda: "qwen3-1.7b")

    in_use = _button(_card(_llm("qwen3-1.7b", True)))
    other = _button(_card(_llm("qwen3-8b", True)))

    assert in_use.get_label() == "In use", (
        "the model that is answering did not say so: %r" % in_use.get_label())
    assert in_use.get_sensitive() is False, (
        "pressing 'switch' on the model already running is a no-op that reports "
        "success")
    assert other.get_label() == "Use it", (
        "another installed model claimed to be in use as well: %r"
        % other.get_label())


def test_a_model_that_is_not_here_still_leads_to_setup():
    button = _button(_card(_llm("gpt-big", False)))

    assert button.get_label() == "Set up"
    assert button.get_sensitive() is False, (
        "the 'Set up' path delegates to the wizard, which the button cannot open "
        "without an application - so it must say so rather than doing nothing")


def test_a_switch_loads_the_model_and_says_which_one_ran(monkeypatch):
    """The whole point: `use()` + `start_service()`, both of which the panel never
    called."""
    from shani_chronoa.gui.surfaces import models
    from shani_chronoa import local_llm

    calls = []
    monkeypatch.setattr(local_llm, "use",
                        lambda key, gate=True: calls.append(("use", key))
                        or {"promote": True, "measured": False})
    monkeypatch.setattr(local_llm, "start_service",
                        lambda: calls.append(("service",)) or "")

    card = _llm("qwen3-8b", True)
    box = _card(card)
    models._switch(_App(), card, box, lambda: None)

    # The reply reaches the main loop from a worker thread; give it a turn.
    import time

    for _ in range(200):
        if calls:
            break
        time.sleep(0.01)
    from gi.repository import GLib as _G

    for _ in range(50):
        if _status(box) and _status(box).get_text() == "On this machine":
            break
        _G.MainContext.default().iteration(False)

    assert ("use", "qwen3-8b") in calls, (
        "the panel wrote a setting and never pointed current.gguf at the model: "
        "%r" % (calls,))
    assert ("service",) in calls, (
        "the model was pointed at but the service was never restarted, so the "
        "new model is not the one answering")


def test_a_switch_the_quality_gate_refuses_is_reported_not_swallowed(monkeypatch):
    """A panel that ignored the verdict would claim to have switched when it had
    not - which is the false-success shape this repo keeps recording."""
    from shani_chronoa.gui.surfaces import models
    from shani_chronoa import local_llm

    monkeypatch.setattr(local_llm, "use",
                        lambda key, gate=True: {"promote": False,
                                                "measured": True,
                                                "why": "31.5% worse, past the 2% limit"})
    monkeypatch.setattr(local_llm, "start_service",
                        lambda: (_ for _ in ()).throw(
                            AssertionError("must not start a refused model")))

    card = _llm("qwen3-8b", True)
    box = _card(card)
    models._switch(_App(), card, box, lambda: None)

    import time

    for _ in range(300):
        status = _status(box)
        if status and status.get_text().startswith("Switch failed"):
            return
        time.sleep(0.01)
    from gi.repository import GLib as _G

    for _ in range(100):
        status = _status(box)
        if status and status.get_text().startswith("Switch failed"):
            return
        _G.MainContext.default().iteration(False)
    raise AssertionError("the gate's refusal was never shown on the card")


def test_the_children_walk_uses_the_gtk4_api():
    """`Gtk.Widget.get_children()` does not exist in GTK4 - it is the GTK3 API, and
    a `getattr` guard turns that into 'no children'. The first version of this
    change had exactly that: the switch worked, the button was never found, and
    nothing failed."""
    from shani_chronoa.gui.surfaces import models

    box = _card(_llm("a", True))
    assert models._card_button(box) is not None, (
        "the walker found no button in a card that has one")
    assert models._first_button_status(box) is not None


def test_a_model_the_server_holds_is_not_claimed_as_the_one_answering(monkeypatch):
    """**Loaded is not answering, and this is the second half of the claim.**

    `local_llm.active()` says which file `current.gguf` points at. Whether that
    model is the one *answering* is a different question: the LLM is chosen once,
    at startup, and `_maybe_enable_cloud_fallback` returns early on
    `CloudLLMChain` - so a machine that fell back to the cloud never reconsiders,
    and starting llama-server afterwards adds an available engine without changing
    the selected one.

    `model.py`'s `_selected_engine` exists for exactly this, and its own note says
    to read the object rather than re-deriving the condition, because re-deriving
    it is how two rows came to disagree about which engine was answering.
    """
    from shani_chronoa.cloud_llm import CloudLLMChain
    from shani_chronoa import local_llm

    # Pinned, not ambient: which model is loaded is a fact about this machine, and
    # a test that reads it passes or fails with what happens to be in the
    # developer's model directory.
    monkeypatch.setattr(local_llm, "active", lambda: "qwen3-1.7b")

    app = type("A", (), {})()
    app.config = type("C", (), {"get": staticmethod(lambda *a, **k: "")})()
    # A real `CloudLLMChain`, with no providers configured: it is the *type* that
    # decides the answer, so a chain that would fall through to nothing is a
    # valid stand-in and builds nothing on the network.
    app.llm = CloudLLMChain(provider_ids=())

    card = {"key": "qwen3-1.7b", "label": "Active", "size": 1e9,
            "kind": "llm", "installed": True, "chosen": False}
    box = _card_with(app, card)

    button = _button(box)
    assert button.get_label() != "In use", (
        "the server holds this model's file, but the assistant is answering "
        "through a cloud provider - saying 'In use' is the same false claim this "
        "panel made before, one level down")
    assert button.get_label() in ("Loaded", "Use it"), button.get_label()


def test_a_locally_routed_app_does_say_the_model_is_answering(monkeypatch):
    from shani_chronoa import local_llm

    monkeypatch.setattr(local_llm, "active", lambda: "qwen3-1.7b")
    app = type("A", (), {})()
    app.config = type("C", (), {"get": staticmethod(lambda *a, **k: "")})()
    app.llm = local_llm          # not a CloudLLMChain: the local route

    card = {"key": "qwen3-1.7b", "label": "Active", "size": 1e9,
            "kind": "llm", "installed": True, "chosen": False}

    assert _button(_card_with(app, card)).get_label() == "In use"


def _card_with(app, card):
    from shani_chronoa.gui.surfaces import models

    return models._card(app, card, lambda: None)
