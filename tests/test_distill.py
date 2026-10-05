"""Distillation, end to end: any teacher -> agreements -> a student -> the decision path.

Three bugs this file exists to hold shut, all of which were found by running the
code rather than by reading it, and all of which read as something else:

- **`.chat()` does not exist on most backends.** The first teacher called
  `.chat(messages)`, which only `OllamaLLM` defines. llama.cpp and the whole
  cloud chain raised `AttributeError`, the loop moved on, and a machine with a
  configured Anthropic key reported "no language model is configured" while the
  provider sat right there. The uniform interface is `chat_message()`.
- **A substring search is not a skill name.** Asking for the first
  `[a-z][a-z0-9_]*` in a reply returns "he" out of "The skill is edit_image",
  which then enters the report as a teacher's opinion. The test that matters is
  the one that feeds a chatty reply in and asserts it is refused, not guessed.
- **Bernoulli's absent term over the whole hash width is not a small
  correction.** Summing `log(1 - p)` across all 16,384 columns handed the most
  used skill a ~450-point head before a word was read, and every request was
  answered with it. Only the fitted vocabulary may be counted.

And one test of the chain rather than of a part: a written student must be
visible to `tool_select`, because the outcome-model path spent hours
misdiagnosed as unwired when its two ends simply pointed at different files.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import distill, tool_select, tools  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def models(tmp_path, monkeypatch):
    """A private model directory, and a router cache that starts empty."""
    directory = tmp_path / "models"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(distill, "router_path",
                        lambda space=None: directory / f"router-{distill.space_id()}.json")
    monkeypatch.setattr(tool_select, "_ROUTER", None)
    monkeypatch.setattr(tool_select, "_ROUTER_TRIED", False)
    return directory


#: Several phrasings per skill, which is what ordinary use produces and what a
#: one-request-per-skill case file cannot be.
CORPUS = {
    "get_datetime": ["what time is it", "what's the time", "current time please",
                     "tell me the time", "what is the time now", "time please"],
    "list_services": ["what services are running", "show me the services",
                      "which services are enabled", "list the services",
                      "the services please", "services list"],
    "get_battery_status": ["what is my battery level", "how much battery is left",
                           "battery status please", "check my battery",
                           "is my battery low", "battery level"],
}


def interleaved():
    rows, pending = [], {k: list(v) for k, v in CORPUS.items()}
    while any(pending.values()):
        for key in list(pending):
            if pending[key]:
                rows.append((pending[key].pop(0), key))
    return rows


class FakeConfig:
    """A config object with only the three things the teacher gate reads."""

    def __init__(self, privacy: bool = False, cloud: bool = True, keys=None):
        self.privacy_mode = privacy
        self.cloud_fallback_enabled = cloud
        self.ollama_host = "http://localhost:11434"
        self.model = ""
        self._keys = keys or {}

    def cloud_llm_api_keys(self):
        return dict(self._keys)


# ---------------------------------------------------------------------------
# the teacher: any model, and only the ones that are allowed
# ---------------------------------------------------------------------------


def test_every_teacher_answers_through_chat_message(monkeypatch):
    """The uniform interface is `chat_message`; `.chat` exists on one backend only."""
    asked = {}

    class OnlyChatMessage:
        model = "a-model"
        provider = type("P", (), {"name": "a provider", "id": "p"})()

        async def chat_message(self, messages, tools=None, stream=False):
            asked["messages"] = messages
            asked["tools"] = tools
            return {"role": "assistant", "content": "get_datetime"}

    import asyncio

    teacher = distill.Teacher("p", "p", True,
                              distill._ask_chat_message(OnlyChatMessage()))
    assert asyncio.run(teacher.ask("what time is it")) == "get_datetime"
    assert asked["tools"] is None
    assert asked["messages"][0]["role"] == "system"


def test_a_provider_reply_that_is_blocks_is_still_readable_text():
    """A backend that returns content blocks must not stringify them into a name."""
    import asyncio

    class Blocks:
        async def chat_message(self, messages, tools=None, stream=False):
            return {"content": [{"type": "text", "text": "get_"}]}

    teacher = distill.Teacher("p", "p", True, distill._ask_chat_message(Blocks()))
    assert asyncio.run(teacher.ask("x")) == "get_"


def test_privacy_mode_alone_stops_every_off_machine_teacher(monkeypatch):
    monkeypatch.setattr(distill, "_local_teachers", lambda config: [])
    assert distill._cloud_teachers(FakeConfig(privacy=True)) == (
        [], "privacy mode is on, so nothing leaves this computer")


def test_cloud_fallback_alone_is_not_enough_either(monkeypatch):
    monkeypatch.setattr(distill, "_local_teachers", lambda config: [])
    teachers, why = distill._cloud_teachers(FakeConfig(privacy=False, cloud=False))
    assert teachers == []
    assert "cloud fallback is off" in why


def test_a_configured_key_reaches_a_teacher(monkeypatch):
    """With both gates open, a keyed provider becomes a usable teacher."""
    monkeypatch.setattr(distill, "_local_teachers", lambda config: [])
    teachers, _why = distill._cloud_teachers(
        FakeConfig(keys={"anthropic": "sk-test", "openai": "sk-test-2"}))
    ids = [t.id for t in teachers]
    # Keyed providers first, in the order `app/brain.py` prefers them, then the
    # keyless gateways - which are teachers too, and are *not* a reason to refuse.
    assert ids[:2] == ["anthropic", "openai"]
    assert ids[2:] == list(__import__("shani_chronoa.cloud_llm",
                                      fromlist=["x"]).DEFAULT_PROVIDER_ORDER)
    assert all(not t.on_this_machine for t in teachers)
    assert "Anthropic" in teachers[0].label
    assert "Claude" in teachers[0].label or "claude" in teachers[0].label.lower()


def test_an_unavailable_named_teacher_is_an_error_not_a_substitution(monkeypatch):
    """Asking for a provider that is not here must never quietly use another."""
    monkeypatch.setattr(distill, "available_teachers", lambda config=None: [
        distill.Teacher("local", "this computer", True, lambda p: "")])
    with pytest.raises(RuntimeError, match="no teacher called 'anthropic'"):
        distill.resolve("anthropic")


def test_no_teacher_at_all_explains_itself(monkeypatch):
    monkeypatch.setattr(distill, "available_teachers", lambda config=None: [])
    monkeypatch.setattr(distill, "teacher_notes",
                        lambda config=None: ["no local model is answering"])
    with pytest.raises(RuntimeError) as caught:
        distill.resolve()
    assert "no local model is answering" in str(caught.value)
    assert "would be guessing" in str(caught.value)


# ---------------------------------------------------------------------------
# reading a reply
# ---------------------------------------------------------------------------


def test_a_chatty_reply_is_refused_rather_than_guessed():
    """The bug this guards: "The skill is x" used to parse as the skill "he"."""
    menu = ["get_datetime", "edit_image"]
    assert distill.parse_choice("The skill is edit_image", menu) == "edit_image"
    assert distill.parse_choice("Sure, let me think about that.", menu) is None
    assert distill.parse_choice("", menu) is None


def test_a_reply_naming_something_that_is_not_a_skill_is_not_a_name():
    assert distill.parse_choice("I would use he", ["get_datetime"]) is None


def test_a_skill_named_inside_a_longer_sentence_is_still_found():
    assert distill.parse_choice("`web_search`. Because you asked about a website.",
                                ["web_search", "get_datetime"]) == "web_search"


# ---------------------------------------------------------------------------
# the student
# ---------------------------------------------------------------------------


def test_one_request_per_skill_cannot_train_a_student(models):
    """The shape of `tools/eval_cases.json`: a smoke test, not a training set.

    57 requests across 53 skills, so a held-out request asks for a skill the
    training half never saw and no student can score above the simplest guess.
    The refusal has to say *that*, because the fix is more data and not a
    different model.
    """
    rows = [(say, tool) for tool, says in CORPUS.items() for say in says[:1]]
    rows += [("a request nothing else looks like", skill)
             for skill in ("web_search", "take_screenshot", "open_application")]
    out = distill.train_router(rows)
    assert out["saved"] is False
    assert "under two each" in out["reason"]
    assert "one request per skill" in out["reason"]


def test_a_corpus_that_can_train_one_does(models):
    out = distill.train_router(interleaved(), teacher="fixture")
    assert out["saved"] is True
    assert out["provenance"]["honest"] is True
    assert out["provenance"]["accuracy"] > out["provenance"]["baseline"]


def test_a_student_that_loses_to_the_baseline_is_not_written(models):
    """A corpus whose words carry no signal: every class shares a vocabulary.

    Each skill's six requests are built from the same six word pools, so every
    word appears once under every label. There is nothing to learn, the router
    falls back to the class prior, its accuracy equals the majority baseline -
    and the gate has to refuse it rather than ship a file.
    """
    pools = ["alpha bravo", "charlie delta", "echo foxtrot",
             "golf hotel", "india juliet", "kilo lima"]
    rows = [(pool, tool) for tool in ("get_datetime", "list_services",
                                      "get_battery_status") for pool in pools]
    out = distill.train_router(rows)
    assert out["saved"] is False
    assert "baseline" in out["reason"]
    assert out["provenance"]["accuracy"] == out["provenance"]["baseline"]


def test_a_corpus_with_one_wording_per_skill_cannot_be_measured(models):
    """Nothing to hold out is its own refusal, not a silently untested student."""
    rows = [("what time is it", "get_datetime")] * 8 + [
        ("completely unrelated wording about nothing in particular", "list_services")]
    out = distill.train_router(rows)
    assert out["saved"] is False
    assert "hold anything out" in out["reason"]


def test_the_split_straddles_skills_and_never_a_request(models):
    """The two properties that make the split a measurement at all: every skill
    appears on both sides, and no request does."""
    rows = interleaved()
    train, held, _unreachable = distill._split(rows, 0.25)
    assert train and held
    assert {t for _s, t in train} == {t for _s, t in held}
    assert not ({s for s, _t in train} & {s for s, _t in held})


def test_a_repeated_request_stays_on_one_side_of_the_split(models):
    """A request seen three times cannot be partly training and partly test."""
    rows = interleaved() + [("what time is it", "get_datetime")] * 3
    train, held, _ = distill._split(rows, 0.25)
    assert sum(1 for s, _t in rows if s == "what time is it") == 4
    in_train = sum(1 for s, _t in train if s == "what time is it")
    in_held = sum(1 for s, _t in held if s == "what time is it")
    assert (in_train == 0 or in_held == 0), "a repeated request straddled the split"


def test_a_written_student_is_visible_to_the_decision_path(models):
    """The chain, not a part of it: written here, asked for there."""
    out = distill.train_router(interleaved(), teacher="fixture")
    assert out["saved"] is True
    monkey = tool_select.distilled_router()
    assert monkey is not None, "a saved student the selector cannot see"
    assert "get_datetime" in tool_select.distilled("what time is it", tools.TOOLS)


def test_the_selector_sees_nothing_when_nothing_is_saved(models):
    assert tool_select.distilled_router() is None
    assert tool_select.distilled("what time is it", tools.TOOLS) == []


@pytest.mark.parametrize("mutation,expected", [
    ("unsigned", "no digest"),
    ("tampered", "digest does not match"),
    ("dishonest", "never beat the majority baseline"),
    ("foreign_space", "feature space"),
])
def test_a_student_that_cannot_account_for_itself_is_refused(models, mutation, expected,
                                                            caplog):
    out = distill.train_router(interleaved(), teacher="fixture")
    assert out["saved"] is True
    path = distill.router_path()
    payload = json.loads(path.read_text())
    if mutation == "unsigned":
        payload = {k: v for k, v in payload.items()
                   if k not in ("digest", "signature", "signature_scheme", "signed_by")}
    elif mutation == "tampered":
        payload = {**payload, "log_prior": [0.0] * len(payload["log_prior"])}
    elif mutation == "dishonest":
        # Re-signed, so the honesty gate is what refuses it rather than the
        # digest: a file whose own report says it learned nothing must be
        # refused on its own account, not merely because it was edited.
        from shani_chronoa import learning
        payload = learning.sign_model(
            {**payload, "provenance": {**payload["provenance"], "honest": False}})
    else:
        payload = {**payload, "feature_space": "deadbeefdeadbeef"}
    path.write_text(json.dumps(payload))
    with caplog.at_level("WARNING"):
        assert distill.load_router() is None
    assert expected in caplog.text


def test_a_honest_student_still_loads(models):
    """Guarding must not become refusing."""
    assert distill.train_router(interleaved(), teacher="fixture")["saved"] is True
    router = distill.load_router()
    assert router is not None
    assert router.classes


def test_the_student_generalises_to_a_wording_it_never_saw(models):
    distill.train_router(interleaved(), teacher="fixture")
    router = distill.load_router()
    assert router.top("what is the time", 1)[0][0] == "get_datetime"
    assert router.top("is my battery low", 1)[0][0] == "get_battery_status"


def test_the_student_cannot_name_a_skill_that_was_never_trained(models):
    distill.train_router(interleaved(), teacher="fixture")
    router = distill.load_router()
    assert "web_search" not in router.classes


# ---------------------------------------------------------------------------
# the fallback when no model answers
# ---------------------------------------------------------------------------


def test_the_fallback_offers_a_safe_skill(models):
    distill.train_router(interleaved(), teacher="fixture")
    proposal = distill.fallback("what time is it")
    assert proposal and proposal["tool"] == "get_datetime"
    assert proposal["runner_up"] != proposal["tool"]


def test_the_fallback_says_nothing_when_it_cannot_separate_anything(models):
    distill.train_router(interleaved(), teacher="fixture")
    assert distill.fallback("make me a sandwich") is None


@pytest.mark.parametrize("skill", ["delete_file", "trash_file", "web_search"])
def test_a_destructive_or_outward_facing_skill_is_never_auto_run(skill):
    """`delete_file` declares no required parameter, so the argument gate alone
    waves it through - and a classifier must never choose to delete something."""
    assert distill.safe_to_run_unattended(skill) is False


def test_a_skill_with_no_arguments_and_nothing_destructive_is_allowed():
    assert distill.safe_to_run_unattended("get_datetime") is True


def test_a_skill_needing_arguments_is_not_auto_run(models):
    distill.train_router(interleaved(), teacher="fixture")
    router = distill.load_router()
    assert "web_search" not in router.classes
    assert distill.safe_to_run_unattended("web_search") is False


# ---------------------------------------------------------------------------
# harvesting the machine's own decisions
# ---------------------------------------------------------------------------


def test_a_tool_call_with_no_request_before_it_is_left_out(tmp_path):
    """An unattended trigger, or the tail of a session cut mid-file, is not a
    request, and a router trained on one is learning noise."""
    session = tmp_path / "20260101-000000-test.jsonl"
    session.write_text("\n".join(json.dumps(m) for m in [
        {"role": "assistant", "tool_calls": [
            {"function": {"name": "get_datetime", "arguments": {}}}]},
        {"role": "user", "content": "what time is it"},
        {"role": "assistant", "tool_calls": [
            {"function": {"name": "get_datetime", "arguments": {}}},
            {"function": {"name": "list_services", "arguments": {}}}]},
    ]))
    rows = distill.harvest_rows(tmp_path)
    assert rows == [("what time is it", "get_datetime")]


def test_harvesting_reads_the_verdicts_rather_than_predicting_them(tmp_path, monkeypatch):
    """A skill the post-condition found did not work must not become a lesson."""
    session = tmp_path / "s.jsonl"
    session.write_text("\n".join(json.dumps(m) for m in [
        {"role": "user", "content": "what time is it"},
        {"role": "assistant", "tool_calls": [
            {"function": {"name": "get_datetime", "arguments": {}}}]},
    ]))
    assert distill.harvest_rows(tmp_path, verdicts={"get_datetime": "failed"}) == []
    assert distill.harvest_rows(tmp_path, verdicts={"get_datetime": "verified"}) == [
        ("what time is it", "get_datetime")]


def test_a_stemmed_word_is_the_word_it_came_from():
    """`running` stemmed to `runn`, so "running", "run" and "runner" were three
    different words to everything downstream - the router and the schema matcher
    alike."""
    assert tool_select._stem("running") == "run"
    assert tool_select._stem("stopping") == "stop"
    assert tool_select._stem("sing") == "sing"
    assert tool_select._stem("bed") == "bed"


def test_the_report_names_the_teacher_and_the_refusals():
    report = {"teacher": "Claude", "cases": 4, "agreed": 3, "agreement": 0.75,
              "disagreements": [{"say": "x", "reason": "said 'nope'"}],
              "student": {"saved": False, "reason": "not enough agreements"}}
    text = distill.render_report(report)
    assert "Claude" in text
    assert "not enough agreements" in text
    assert "cannot be trusted" in text