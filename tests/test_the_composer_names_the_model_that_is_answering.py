"""The composer names the model that is answering, not the one on disk.

**The defect (measured, 2026-10-15).** The window's model row was drawn from
`local_llm.active()`, which reads the `current.gguf` **symlink in this process's
own data home**. The model that is answering is the one the *server* has loaded,
and those are different files in different places whenever the data home and the
server disagree - which is what the demo recording that prompted this did: a
throwaway profile with no model in it, pointed at a server another session had
already started.

    installed()        -> []
    active()           -> ''
    is_up()            -> True
    _loaded_model_path -> .../Qwen3-1.7B-Q4_K_M.gguf

and the composer read **"No model"** on a window that was visibly answering every
question. A row that says the assistant cannot answer, beside replies arriving, is
the two-widgets-disagree failure this repository records more than any other -
reached here by asking the filesystem a question about the network.

**Why the fixture drives a real window.** `_model_rows()` and `_model_word()` are
readable on a `SimpleNamespace`, and a test that does that cannot see whether the
*label on screen* changed - which is the whole complaint. The label is read back
off the real `Gtk.Label` the composer builds.
"""
import pytest

pytest.importorskip("gi")
gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
Adw = gi.require_version("Adw", "1")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa import local_llm  # noqa: E402
from shani_chronoa.gui import ChronoaWindow  # noqa: E402


class _Closed:
    """A config that grants nothing, so nothing here reaches a real machine.

    **Every attribute the strip actually reads, with an inert value.** The
    first version carried only `sense_allowed`, and the strip's six readers each
    caught their own `AttributeError` and logged a traceback - so the refresh
    under test ran against a fixture that answered *nothing*, which is not the
    same as running it against a machine with nothing switched on. Worse, nine
    tracebacks per refresh bury any real one: this repository's own recorded
    failure for a fixture that cannot be interrogated.

    So the values are the off state of each switch, and `get_bool` answers a
    default rather than raising. The consent switches stay **off** - that is the
    point of the stub - but the reads succeed, so a red line in the log means
    something.
    """

    privacy_mode = False
    wake_word_enabled = False
    wake_phrase = ""
    barge_in_vad_enabled = False
    reply_style = "ordinary"

    def sense_allowed(self, key):
        return False

    def get_bool(self, key, default=False):
        return bool(default)


class _App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="test.chronoa.composerword")

    def activate_action(self, name, parameter=None):
        return None

    def current_llm_kind(self):
        return "local"


@pytest.fixture
def window(tmp_path, monkeypatch):
    """The real window, on a data home that holds no model at all."""
    monkeypatch.setattr(local_llm, "_LOADED_TTL", 0.0)

    def _no_server():
        return None

    monkeypatch.setattr(local_llm, "_loaded_model_path", _no_server)
    app = _App()
    win = ChronoaWindow(app, config=_Closed())
    yield win
    win.destroy()


def _composer_says(window):
    """The text the composer's model row actually draws."""
    return window._model_picker_word.get_label()


def _chip_says(window):
    return window._mode_strip._model_label.get_label()


def _refresh_both(window):
    """Redraw both widgets, **the way the application does it**.

    `app._refresh_strip` is two calls - `strip.refresh()` and then
    `window._refresh_model_word()` - and the first version of the test below
    made only the second. That is not a thinner test, it is a different one: the
    chip is drawn inside `_ModeStrip.refresh()`, so refreshing the composer's
    row alone leaves the chip reading whatever it read at construction. The test
    then failed with the chip saying `No model` and the row saying `8B` - which
    is *this file's own defect*, produced by driving half of the refresh, and a
    reader would have taken it for the bug rather than the fixture.

    So the fixture drives the pair, and that is also what makes the assertion
    meaningful: a window whose chip and row disagree is broken however the
    disagreement was reached.
    """
    window._mode_strip.refresh()
    window._refresh_model_word()


# --- the defect --------------------------------------------------------------

def test_a_server_answering_is_named_even_with_nothing_on_disk(window, monkeypatch, tmp_path):
    """The recorded state: empty data home, a server serving the 1.7B.

    Reproduced through the real reader, not through a stub of the answer - the
    stub is a `/props` reply, and the path it names is the whole point.
    """
    weights = tmp_path / "elsewhere" / "Qwen3-1.7B-Q4_K_M.gguf"
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"not really a gguf")
    monkeypatch.setattr(
        local_llm, "_loaded_model_path",
        lambda: str(weights))

    assert local_llm.installed() == [], "the disk must be empty for this to be the defect"
    assert local_llm.active() == "", "and so must the symlink"

    window._refresh_model_word()
    assert _composer_says(window) == "1.7B"


def test_the_chip_and_the_composer_agree_on_that_model(window, monkeypatch, tmp_path):
    """Two answers for one question is the failure; both must move together.

    **The larger tier, deliberately.** `1.7B` is what this machine answers with
    and `8B` is not on disk, so asserting the 1.7B here would pass whether or
    not the refresh moved at all - the fixture's own state already agrees with
    the expected value. `8B` can only appear if a redraw happened, which is what
    this test is for.
    """
    weights = tmp_path / "elsewhere" / "Qwen3-8B-Q4_K_M.gguf"
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"not really a gguf")
    monkeypatch.setattr(local_llm, "_loaded_model_path", lambda: str(weights))

    _refresh_both(window)
    assert _chip_says(window) == _composer_says(window) == "8B"


# --- the controls ------------------------------------------------------------

def test_a_machine_with_nothing_answering_still_says_no_model(window):
    """**The over-fix.** Without this, "always name something" passes.

    Measured on the real window: no server, nothing on disk, and the row says
    "No model" - which is true, and the only true answer here.
    """
    assert _composer_says(window) == "No model"


def test_the_word_is_the_same_one_an_installed_tier_uses(window, monkeypatch, tmp_path):
    """`1.7B`, not `qwen3-1.7b`.

    **A control against the fix rather than for it.** The answering row was
    written with the bare key while the installed row beside it carries the size,
    so the chip held two names for one model - the defect this file records
    most often, introduced by the row rather than left by it.
    """
    spec = local_llm.SPECS["qwen3-1.7b"]
    weights = tmp_path / "elsewhere" / spec.filename
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"not really a gguf")
    monkeypatch.setattr(local_llm, "_loaded_model_path", lambda: str(weights))

    monkeypatch.setattr(local_llm, "installed", lambda: ["qwen3-1.7b"])
    rows = window._mode_strip._model_rows()
    installed_row = next(r for r in rows if r[0] == "qwen3-1.7b")

    # The same key, reached through the *other* branch of the same table.
    monkeypatch.setattr(local_llm, "installed", lambda: [])
    answering_row = next(r for r in window._mode_strip._model_rows()
                         if r[0] == "qwen3-1.7b")

    assert installed_row[1] == answering_row[1] == "1.7B"


def test_an_installed_machine_is_unchanged(monkeypatch, tmp_path):
    """No regression for the ordinary case: the symlink still answers.

    `answering_key()` falls back to `active()`, so a machine with the model
    downloaded and no server running reads exactly as it did before.
    """
    monkeypatch.setattr(local_llm, "_loaded_model_path", lambda: None)
    monkeypatch.setattr(local_llm, "_LOADED_TTL", 0.0)
    monkeypatch.setattr(local_llm, "active", lambda: "qwen3-8b")
    assert local_llm.answering_key() == "qwen3-8b"


def test_the_server_is_asked_before_the_symlink(monkeypatch, tmp_path):
    """**Precedence is the fix.** Symlink first would keep the defect.

    The disk says `8b`; the server has the 1.7B loaded. The server is what
    receives the prompt, so the server is what the row is about.
    """
    monkeypatch.setattr(local_llm, "_loaded_model_path",
                        lambda: str(tmp_path / "Qwen3-1.7B-Q4_K_M.gguf"))
    monkeypatch.setattr(local_llm, "_LOADED_TTL", 0.0)
    monkeypatch.setattr(local_llm, "active", lambda: "qwen3-8b")
    assert local_llm.answering_key() == "qwen3-1.7b"


def test_a_loaded_file_no_tier_describes_is_not_invented(monkeypatch, tmp_path):
    """An unrecognised GGUF names nothing rather than naming the nearest tier.

    The honest answer to "which tier is answering" when the file is somebody
    else's model is **no tier** - and the fallback then asks the symlink, which
    is the next best answer rather than a guess.
    """
    monkeypatch.setattr(local_llm, "_loaded_model_path",
                        lambda: str(tmp_path / "somebody-elses-finetune.gguf"))
    monkeypatch.setattr(local_llm, "_LOADED_TTL", 0.0)
    monkeypatch.setattr(local_llm, "active", lambda: "")
    assert local_llm.answering_key() == ""


def test_three_reads_cost_one_request(monkeypatch, tmp_path):
    """One strip refresh asks three times; `/props` is a network call.

    `effective_bandwidth`'s own comment records having run three `/props` round
    trips for one measurement before - the same trap, reached from the composer.

    **The cache is module-global, so it has to be emptied before it is counted.**
    `_LOADED_CACHE` is process-wide state that an earlier test in this file
    left warm, and with a five-second TTL its entry was still valid: the three
    reads below were all served from it, `/props` was never asked, and the
    counter saw zero calls. That is the "a control that cannot fail" trap in a
    new costume - the assertion was `len(calls) == 1`, the truth was `0`, and a
    reader looking only at "the count was not one" would have gone looking for a
    second cache bug.

    `(0.0, None)` is the module's own cold sentinel rather than a fabricated
    timestamp: any monotonic clock on a machine that has been up for more than
    five seconds reads as expired, so this cannot pass by being fresh.
    """
    calls = []
    monkeypatch.setattr(local_llm, "_LOADED_TTL", 5.0)
    monkeypatch.setattr(local_llm, "_LOADED_CACHE", (0.0, None))
    monkeypatch.setattr(local_llm, "active", lambda: "")

    def _counting():
        calls.append(1)
        return str(tmp_path / "Qwen3-1.7B-Q4_K_M.gguf")

    monkeypatch.setattr(local_llm, "_loaded_model_path", _counting)

    # **The answer is asserted on every read, not just the call count.** One
    # request is only worth having if the three readers got the same thing from
    # it; a cache that answered "" twice and the right thing once would satisfy
    # a count-only assertion perfectly.
    for _ in range(3):
        assert local_llm.answering_key() == "qwen3-1.7b"
    assert len(calls) == 1


def test_the_symlink_is_still_read_when_the_server_answer_is_cached(
        monkeypatch, tmp_path):
    """**The cache holds the server's answer, not the key - and this is that.**

    The first version cached the *key*, so a warm cache also froze the **local
    symlink** read for five seconds. Nothing caught it: every other test here
    zeroes the TTL, and a switch test with the TTL open is a test about a sleep.

    A model the server has loaded that `SPECS` does not describe is the case that
    shows it - a shared or imported GGUF, which `model_registry` exists for and
    which `_key_for_file` deliberately declines to name. There the answer comes
    from the symlink, so a cached key would hold the **previous** model's name
    for five seconds while the server was answering a different one: the row
    saying one thing and the server doing another, which is this file's defect
    in a new place.

    The TTL is left at its **real five seconds** and the cache is left **warm**,
    so a cache that held the key rather than the path fails this and a cache
    that caches nothing also fails it on the count.
    """
    asked = []
    shared = tmp_path / "somebody-elses-finetune.gguf"
    shared.write_bytes(b"not really a gguf")

    def _serving():
        asked.append(1)
        return str(shared)

    monkeypatch.setattr(local_llm, "_loaded_model_path", _serving)
    monkeypatch.setattr(local_llm, "_LOADED_TTL", 300.0)
    monkeypatch.setattr(local_llm, "_LOADED_CACHE", (0.0, None))
    monkeypatch.setattr(local_llm, "active", lambda: "qwen3-8b")
    assert local_llm.answering_key() == "qwen3-8b"     # warms the cache
    assert len(asked) == 1

    # **The symlink moves and the server keeps serving the same file.** Only a
    # reader that asks the symlink again can see this.
    monkeypatch.setattr(local_llm, "active", lambda: "qwen3-1.7b")
    assert local_llm.answering_key() == "qwen3-1.7b"
    assert len(asked) == 1, "the server was asked again for a local change"


def test_a_model_switch_is_picked_up(monkeypatch, tmp_path):
    """A short cache is only safe if a switch shows up.

    A model change is a deliberate act and the server restart takes seconds, so
    a reader that hid a switch for longer than this would be a second way for
    the row and the server to disagree.
    """
    which = {"path": tmp_path / "Qwen3-1.7B-Q4_K_M.gguf"}

    monkeypatch.setattr(local_llm, "_LOADED_TTL", 0.0)
    monkeypatch.setattr(local_llm, "active", lambda: "")
    monkeypatch.setattr(local_llm, "_loaded_model_path", lambda: str(which["path"]))
    assert local_llm.answering_key() == "qwen3-1.7b"

    which["path"] = tmp_path / "Qwen3-8B-Q4_K_M.gguf"
    assert local_llm.answering_key() == "qwen3-8b"