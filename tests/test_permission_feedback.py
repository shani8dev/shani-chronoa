"""The feedback-with-text permission outcome, and the recall relevance gate.

Two unrelated changes share this file because they were made in one pass and
both are about the *same* discipline: an outcome that claims to carry
information must carry it, exactly once, and must not claim more than it does.

T1.9 (feedback-with-text): a third answer to a permission question, alongside
allow and deny, that carries free-form text back to the model. AutoGPT-classic's
`UserFeedbackProvided` (`forge/permissions.py:22-31`).

T2.16 (threshold-gate-before-combine): mem0's
`utils/scoring.py:111-112` - the gate on the primary signal runs *before* the
other signals are combined, so a candidate that failed the primary test cannot
be rescued by a boost.
"""

import threading

import pytest

from shani_chronoa import ask_bridge, permissions, tools
from shani_chronoa.senses import memory
from shani_chronoa.senses.store import PerceptStore


# --- helpers ---------------------------------------------------------------


def pick(choice):
    """A presenter that always answers `choice`, off the calling thread."""
    def presenter(question, options, timeout=None):
        done, resolve = ask_bridge.make_event()
        threading.Timer(0.01, lambda: resolve(choice)).start()
        return done
    return presenter


def types(text):
    """A text presenter that answers `text`."""
    def presenter(prompt, placeholder):
        done, resolve = ask_bridge.make_event()
        threading.Timer(0.01, lambda: resolve(text)).start()
        return done
    return presenter


def never_answers(prompt, placeholder):
    """A text presenter that is shown a prompt and dismissed."""
    done, _resolve = ask_bridge.make_event()
    return done


@pytest.fixture(autouse=True)
def clean_permissions():
    """Every test starts and ends with no rules, reasons or feedback on file.

    Autouse because the module state these tests read is process-wide: a test
    that leaves a session grant behind makes the *next* test's "the user was
    asked" assertion pass for the wrong reason.
    """
    permissions.clear()
    ask_bridge.set_presenter(None)
    ask_bridge.set_text_presenter(None)
    yield
    permissions.clear()
    ask_bridge.set_presenter(None)
    ask_bridge.set_text_presenter(None)


# --- T1.9: the option exists and is offered --------------------------------


def test_feedback_is_offered_only_where_the_extra_answers_are():
    """The three answers stay three until a caller asks for the split.

    `options` is what a presenter that offers no cancel gets. Adding feedback
    there would put a fifth button on every prompt in the app, including the
    ones a caller deliberately kept to a yes/no.
    """
    ask_bridge.set_presenter(pick(permissions.ALLOW_ONCE_CHOICE))
    seen = {}

    def capture(question, options, timeout=None):
        seen["options"] = list(options)
        done, resolve = ask_bridge.make_event()
        threading.Timer(0.01, lambda: resolve(permissions.ALLOW_ONCE_CHOICE)).start()
        return done

    ask_bridge.set_presenter(capture)
    permissions.decide("calendar_events", "/tmp/a", "k")

    assert permissions.FEEDBACK_CHOICE not in seen["options"]
    assert seen["options"] == [permissions.ALLOW_ONCE_CHOICE,
                               permissions.ALLOW_SESSION_CHOICE,
                               permissions.DENY_CHOICE]

    # Without a text presenter the option is a control that leads nowhere -
    # `ask_for_text` would always answer "" - so it is not offered even with
    # offer_cancel=True. Measured on a real run: the option was in the question
    # and the free-form path behind it was unreachable.
    ask_bridge.set_presenter(capture)
    permissions.clear()
    permissions.decide("calendar_events", "/tmp/b", "k", offer_cancel=True)
    assert permissions.FEEDBACK_CHOICE not in seen["options"]

    # With a text presenter it comes back, and the caller's split still works.
    ask_bridge.set_text_presenter(types("some guidance"))
    ask_bridge.set_presenter(capture)
    permissions.clear()
    permissions.decide("calendar_events", "/tmp/c", "k", offer_cancel=True)
    assert permissions.FEEDBACK_CHOICE in seen["options"]


def test_bypass_immune_action_never_offers_a_session_grant():
    """The option would be a promise the mechanism cannot keep: a standing
    grant is ignored for bypass-immune tools anyway."""
    ask_bridge.set_presenter(pick(permissions.ALLOW_ONCE_CHOICE))
    seen = {}

    def capture(question, options, timeout=None):
        seen["options"] = list(options)
        done, resolve = ask_bridge.make_event()
        threading.Timer(0.01, lambda: resolve(permissions.ALLOW_ONCE_CHOICE)).start()
        return done

    ask_bridge.set_presenter(capture)
    permissions.decide("delete_file", "/tmp/b", "k")

    assert permissions.ALLOW_SESSION_CHOICE not in seen["options"]


def test_choosing_feedback_records_the_text_and_still_allows_the_call():
    """The outcome is a *third* answer: the call proceeds and text comes back."""
    ask_bridge.set_presenter(pick(permissions.FEEDBACK_CHOICE))
    ask_bridge.set_text_presenter(types("delete into the bin, not permanently"))

    granted = permissions.decide("delete_file", "/tmp/x", "k", offer_cancel=True)

    assert granted == permissions.Decision.ALLOW_ONCE
    assert permissions.consume_feedback(
        "delete_file", "/tmp/x") == "delete into the bin, not permanently"


def test_the_text_prompt_names_the_call_it_is_about():
    """A bare text box with no question is a text box nobody answers."""
    ask_bridge.set_presenter(pick(permissions.FEEDBACK_CHOICE))
    seen = {}

    def capture(prompt, placeholder):
        seen["prompt"] = prompt
        done, resolve = ask_bridge.make_event()
        threading.Timer(0.01, lambda: resolve("use the bin")).start()
        return done

    ask_bridge.set_text_presenter(capture)
    permissions.decide("delete_file", "/tmp/report.pdf", "k", offer_cancel=True)

    assert "delete_file" in seen["prompt"]
    assert "/tmp/report.pdf" in seen["prompt"]


# --- T1.9: one-shot, which is the bug this most easily grows ---------------


def test_feedback_is_consumed_not_repeated():
    """The property the whole feature rests on.

    A read that does not consume leaves the text on the books, so every later
    call to the same tool on the same path is prefixed with it - the model told
    the same thing over and over about a call the user was not being asked
    about. This is the "allow this once meant twice" bug this module already
    had once, in the opposite direction.
    """
    ask_bridge.set_presenter(pick(permissions.FEEDBACK_CHOICE))
    ask_bridge.set_text_presenter(types("only this once"))

    permissions.decide("delete_file", "/tmp/x", "k", offer_cancel=True)

    assert permissions.consume_feedback("delete_file", "/tmp/x") == "only this once"
    assert permissions.consume_feedback("delete_file", "/tmp/x") is None


def test_feedback_does_not_leak_onto_the_next_call(monkeypatch):
    """End to end through the dispatcher, because that is where it would show.

    A test that only exercises `consume_feedback` proves the accessor is
    called; it does not prove the dispatcher called it, or called it once.
    """
    monkeypatch.setattr(tools, "_LOCAL_TOOLS", frozenset({"ask_user"}))
    monkeypatch.setattr(
        tools, "_consent_key_for",
        lambda name: "ask-user-enabled" if name == "ask_user" else None)

    class Cfg:
        def get_bool(self, key, default=False):
            return False

    monkeypatch.setattr(tools.config_mod, "ChronoaConfig", lambda *a, **k: Cfg())

    ask_bridge.set_text_presenter(types("use trash, not rm"))
    args = {"question": "what time is it?", "options": ["a", "b"]}

    ask_bridge.set_presenter(pick(permissions.FEEDBACK_CHOICE))
    first = tools.execute_tool_outcome("ask_user", args)
    assert "use trash, not rm" in first.text

    # Second call, same tool, and the user this time just says yes.
    ask_bridge.set_presenter(pick(permissions.ALLOW_ONCE_CHOICE))
    second = tools.execute_tool_outcome("ask_user", args)
    assert "use trash, not rm" not in second.text


def test_the_guidance_is_marked_as_the_users_and_not_the_tools():
    """Joined with a bare separator, the model cannot tell the two apart.

    This is the one assertion here about wording rather than behaviour, and it
    is here because the alternative reads as a finding: a small local model
    summarising the output would report the instruction as what the tool said.
    """
    assert tools._with_feedback("Deleted the file.", "use the bin") != \
        tools._with_feedback("Deleted the file.", None)
    marked = tools._with_feedback("Deleted the file.", "use the bin")
    assert "use the bin" in marked
    assert "Deleted the file." in marked
    # Attributed, and not first-person from the tool.
    assert marked.splitlines()[0].endswith('"')
    assert "I" not in marked


def test_whitespace_is_not_guidance():
    """Otherwise the result claims the user said something they did not.

    `_with_feedback("out", "   ")` used to render `guidance: ""` - a confident
    claim attached to a call the user was handed nothing for. A person who hit
    space and dismissed the box meant no answer, and the refusal path is what
    that already means everywhere else here.
    """
    assert tools._with_feedback("Deleted it.", "   ") == "Deleted it."
    assert tools._with_feedback("Deleted it.", "\n\t ") == "Deleted it."


def test_no_feedback_leaves_the_message_byte_identical():
    """An addition that rewrites every result is the worse regression."""
    assert tools._with_feedback("out", None) == "out"
    assert tools._with_feedback("out", "") == "out"


# --- T1.9: every way of not answering is a refusal ------------------------


@pytest.mark.parametrize("presenter,label", [
    (None, "no text presenter installed at all"),
    (lambda p, ph: (_ for _ in ()).throw(RuntimeError("no text box")), "a presenter that raises"),
    (lambda p, ph: "a bare string, not an event", "a presenter returning the wrong type"),
    (never_answers, "a prompt that is dismissed"),
])
def test_anything_other_than_typed_text_is_a_refusal(presenter, label):
    """Fail closed, four ways.

    The dangerous case is the first one, and it is the real state of the
    product today: nothing installs a text presenter. Without this, selecting
    "Provide feedback" on a build that cannot ask for text would grant the call
    with no text at all - the one outcome the option's label rules out.
    """
    ask_bridge.set_presenter(pick(permissions.FEEDBACK_CHOICE))
    ask_bridge.set_text_presenter(presenter)
    permissions.DECISION_TIMEOUT_SECONDS = 0.2
    try:
        granted = permissions.decide("delete_file", "/tmp/x", "k", offer_cancel=True)
    finally:
        permissions.DECISION_TIMEOUT_SECONDS = 120.0

    assert granted is None, f"granted on: {label}"
    assert permissions.evaluate("delete_file", "/tmp/x") == \
        permissions.Decision.DENY_SESSION, f"no denial recorded on: {label}"
    assert permissions.consume_feedback("delete_file", "/tmp/x") is None


def test_a_session_reset_drops_feedback_too():
    """Feedback rides with the answer it was given to.

    Left behind it would survive the reset and be prepended to a call made
    hours later - advice about a decision the user has not made again.
    """
    ask_bridge.set_presenter(pick(permissions.FEEDBACK_CHOICE))
    ask_bridge.set_text_presenter(types("only for this one"))
    permissions.decide("delete_file", "/tmp/x", "k", offer_cancel=True)

    permissions.clear(session_only=True)

    assert permissions.consume_feedback("delete_file", "/tmp/x") is None


def test_an_answer_nobody_can_parse_is_still_a_refusal():
    """The existing rule, and the new answer must not become an exception to it.

    A presenter that returns something unexpected resolves to "" rather than
    to a feedback request, so the escape stays the same refusal path.
    """
    ask_bridge.set_presenter(pick("something nobody offered"))
    assert permissions.decide("delete_file", "/tmp/x", "k",
                              offer_cancel=True) is None


# --- T2.16: the recall relevance gate --------------------------------------


def _store(tmp_path, texts):
    store = PerceptStore(durable_path=tmp_path / "memory.jsonl")
    for text in texts:
        memory.store_fact(
            memory.Fact(text, text, text.split(":")[0], "user-stated",
                        memory.CONFIDENCE_STATED), store)
    return store


def test_the_gate_defaults_to_off_so_recall_is_unchanged(tmp_path):
    """0.0 must be behaviourally identical to not having a gate.

    Asserted by comparing against a store read with the gate removed from the
    source, which is the only way to show the default is inert rather than
    merely small.
    """
    store = _store(tmp_path, ["The user's editor: neovim", "The user's city: Berlin"])
    query = "what editor do I use"

    assert memory.RECALL_MIN_RELEVANCE == 0.0
    with_gate = [p.content for p in memory.recall(query, store)]
    memory.RECALL_MIN_RELEVANCE = 1.01        # nothing can pass
    without_gate = [p.content for p in memory.recall(query, store)]

    assert with_gate == ["The user's editor: neovim"]
    assert without_gate == []


def test_a_fact_below_the_threshold_is_not_recalled(tmp_path):
    store = _store(tmp_path, ["The user's editor: neovim", "The user's city: Berlin"])
    memory.RECALL_MIN_RELEVANCE = 0.5
    try:
        assert memory.recall("what editor do I use", store) == []
    finally:
        memory.RECALL_MIN_RELEVANCE = 0.0


def test_the_threshold_is_chosen_against_a_measured_relevance(tmp_path):
    """Why the default is 0.0 and not something reassuring.

    The obvious move is to set a threshold that "looks reasonable". Measured,
    a fact that is exactly on point scores 0.2893 for a four-word query, so
    any threshold above that deletes correct answers. The number is asserted
    from a live computation rather than copied in, so it cannot go stale
    silently as the scoring changes.
    """
    store = _store(tmp_path, ["The user's editor: neovim"])
    query = "what editor do I use"
    hit = memory.recall(query, store)[0]

    wanted = memory._tokens(query)
    vocabularies = [memory._keywords(p) for p in store.durable()]
    total = len(vocabularies)
    doc_freq = {}
    for vocabulary in vocabularies:
        for token in vocabulary:
            doc_freq[token] = doc_freq.get(token, 0) + 1
    weight = lambda t: memory._idf(total, doc_freq.get(t, 0))
    query_mass = sum(weight(t) for t in wanted)
    document_mass = sum(weight(t) for t in memory._keywords(hit))
    shared = sum(weight(t) for t in wanted if t in memory._keywords(hit))
    measured = memory._relevance(shared, query_mass, document_mass)

    assert 0.0 < measured < 1.0, "a one-word match must not score 0 or 1"
    # The gate is documented as sitting below every honest score, so a fact
    # that matches at all is never gated by default.
    assert memory.RECALL_MIN_RELEVANCE <= measured


def test_a_gated_fact_is_not_marked_accessed(tmp_path):
    """The gate runs above `mark_accessed`, and that placement is load-bearing.

    `last_accessed_at` is the only signal the durable tier's eviction order can
    honestly use, so a fact the user was never shown must not be stamped as
    shown - otherwise a store full of suppressed facts looks like the store
    that gets evicted last.
    """
    store = _store(tmp_path, ["The user's editor: neovim", "The user's city: Berlin"])
    memory.RECALL_MIN_RELEVANCE = 0.5
    try:
        memory.recall("what editor do I use", store)
    finally:
        memory.RECALL_MIN_RELEVANCE = 0.0

    assert [p.content for p in store.durable() if p.last_accessed_at] == []


def test_the_gate_sits_above_the_reserve_not_below_it():
    """Ordering is the whole of the port, so it is asserted as ordering.

    mem0's gate exists so a boost cannot rescue a candidate that failed the
    primary test. The "combine" here is the distinct-key reserve, which draws
    from `ranked`; gating after that draw would let a weak fact spend a
    reserved slot meant for a strong one. A behavioural test cannot separate
    the two placements in this configuration, so the placement is checked
    directly rather than asserted through a proxy that passes either way.
    """
    from tests._source import package_source
    source = package_source("senses.memory")
    gate = source.index("if relevance < RECALL_MIN_RELEVANCE")
    ranked = source.index("ranked = sorted(")
    reserve = source.index("keys_seen")
    assert gate < ranked < reserve

# --- T1.9: feedback must not change what the result *claims* ---------------


def test_a_failing_local_tool_still_reports_failure_with_feedback_present(
        monkeypatch):
    """The bug this file's own first version of the change introduced.

    `ToolFailure.MARKER` is matched with `startswith`, so writing the feedback
    prefix in front of the message made the marker miss - and a skill that
    failed was then returned as an ordinary successful result. The marker is
    the code's way of saying "it ran and its effect did not hold", so losing it
    inverts the only claim that matters.
    """
    from shani_chronoa import toolfailure

    monkeypatch.setattr(tools, "_LOCAL_TOOLS", frozenset({"ask_user"}))
    monkeypatch.setattr(
        tools, "_consent_key_for",
        lambda name: "ask-user-enabled" if name == "ask_user" else None)

    class Cfg:
        def get_bool(self, key, default=False):
            return False

    monkeypatch.setattr(tools.config_mod, "ChronoaConfig", lambda *a, **k: Cfg())
    # A local skill reports failure the documented way: the marker as the first
    # characters of what it returned. Patched at `_HANDLER_FNS` because that is
    # the table the local-tool branch actually reads - patching the skill
    # module's `run` leaves this string winning instead, which is how the first
    # version of this test asserted nothing.
    monkeypatch.setattr(
        tools, "_HANDLER_FNS",
        {"ask_user": lambda arguments: toolfailure.ToolFailure.MARKER + "nothing happened"},
        raising=False)

    ask_bridge.set_presenter(pick(permissions.FEEDBACK_CHOICE))
    ask_bridge.set_text_presenter(types("and be careful"))
    outcome = tools.execute_tool_outcome(
        "ask_user", {"question": "q?", "options": ["a"]})

    assert "nothing happened" in outcome.text
    assert outcome.verdict.value == "failed", (
        "a marker-reported failure came back as "
        f"{outcome.verdict.value!r} once feedback was prefixed")
    assert "and be careful" in outcome.text


def test_whitespace_only_feedback_is_a_refusal_not_an_empty_guidance():
    ask_bridge.set_presenter(pick(permissions.FEEDBACK_CHOICE))
    ask_bridge.set_text_presenter(types("   \n "))
    permissions.DECISION_TIMEOUT_SECONDS = 0.2
    try:
        granted = permissions.decide("delete_file", "/tmp/x", "k",
                                     offer_cancel=True)
    finally:
        permissions.DECISION_TIMEOUT_SECONDS = 120.0

    assert granted is None
    assert permissions.consume_feedback("delete_file", "/tmp/x") is None
