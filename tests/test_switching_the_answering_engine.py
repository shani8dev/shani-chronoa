"""Choosing which engine answers, at all (2026-10-11)

`_maybe_enable_cloud_fallback` runs **once, at startup**, and returns early when
`isinstance(self.llm, CloudLLMChain)` - so a machine switched to the cloud by the
Settings toggle had no way back but a restart, and a machine that started on the
cloud never reconsidered at all. `_toggle_cloud_fallback` flips the fallback on
and off; it cannot answer "use this one instead".

`self.assistant.llm` is the reason this needs care: the assistant holds its own
reference and reads that one on every request, so setting only `self.llm` would
look like a switch and not be one.
"""

from shani_chronoa.app.brain import BrainMixin


class _Privacy:
    def __init__(self, local_only=False):
        self.is_local_only = local_only


class _Config:
    def __init__(self, cloud=True):
        self.cloud_fallback_enabled = cloud
        self.set_calls = []

    def get(self, key, default=""):
        return ""

    def cloud_llm_api_keys(self):
        return {}


class _Assistant:
    llm = None


def _brain(local_only=False, cloud=True, llm="a local engine"):
    brain = BrainMixin.__new__(BrainMixin)
    brain.privacy = _Privacy(local_only)
    brain.config = _Config(cloud)
    brain.assistant = _Assistant()
    brain.llm = llm
    return brain


def test_the_engine_is_named_from_the_object_not_from_a_setting():
    """A setting cannot answer this. `cloud-fallback-enabled` being true says
    nothing about what is answering now, because the selection is re-decided at
    startup and by the fallback."""
    assert _brain(llm="a local engine").current_llm_kind() == "local"
    assert _brain(llm=None).current_llm_kind() == "none"


def test_both_directions_are_choices_not_a_restart():
    brain = _brain()

    assert brain.select_llm("local") == ""
    assert brain.current_llm_kind() == "local"
    assert brain.select_llm("cloud") == ""
    assert brain.current_llm_kind() == "cloud"


def test_the_assistant_is_moved_too_not_only_the_app():
    """The whole switch: the assistant holds its own reference and is what
    answers. Setting one and not the other is a switch that looks like it
    worked."""
    brain = _brain()
    brain.assistant.llm = None

    brain.select_llm("local")

    assert brain.assistant.llm is not None, (
        "only app.llm was moved; the assistant still holds the object it was "
        "given at startup and keeps answering from that")


def test_privacy_mode_shuts_the_cloud_and_names_itself():
    brain = _brain(local_only=True)

    why = brain.select_llm("cloud")

    assert "privacy mode" in why, why
    assert brain.current_llm_kind() != "cloud", (
        "privacy mode was on and the switch happened anyway; the local route "
        "stays available, which is the whole point of the asymmetry")


def test_the_cloud_fallback_switch_shuts_the_cloud_and_names_itself():
    brain = _brain(cloud=False)

    why = brain.select_llm("cloud")

    assert "cloud fallback" in why, why
    assert brain.current_llm_kind() != "cloud"


def test_a_shut_cloud_gate_still_leaves_local_working():
    """The two are not one switch. A person who has privacy mode on must still be
    able to choose *between local models*, and a cloud refusal must not take that
    away with it."""
    brain = _brain(local_only=True, cloud=True)

    assert brain.select_llm("cloud") != ""
    assert brain.select_llm("local") == "", (
        "the local route was collateral damage from a cloud refusal")


def test_a_choice_that_is_not_one_names_the_two_that_are():
    brain = _brain()

    why = brain.select_llm("sideways")

    assert "local" in why and "cloud" in why, why


def test_the_two_paths_read_one_answer_about_the_gate():
    """`_maybe_enable_cloud_fallback` had this check inline, interleaved with the
    log line it fed - so a *choice* could not ask the same question, and
    re-deriving it is how two rows come to disagree about what is answering."""
    from shani_chronoa.app import brain as brain_mod

    src = open(brain_mod.__file__).read()
    assert src.count("def _cloud_gate_reason") == 1, (
        "the gate has more than one definition, so the startup path and the "
        "choice path can disagree about whether the cloud is open")
    # and the startup fallback consults the same one
    assert "_cloud_gate_reason" in src.split("def select_llm")[0], (
        "the startup fallback still has its own copy of the gate check")
