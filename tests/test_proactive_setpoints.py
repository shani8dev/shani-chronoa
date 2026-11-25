"""Setpoints: built-in proposals, off by default, armed only by the existing path."""

from shani_chronoa import setpoints


def test_every_preset_is_off_and_names_an_existing_switch():
    presets = setpoints.proposals()
    assert len(presets) == 4
    for p in presets:
        assert p["enabled"] is False
        assert p["consent_key"].endswith("-sense-enabled")
        assert p["actuator"] == "notify"  # no autonomous fix
    assert {p["name"] for p in presets} == {
        "disk-nearly-full",
        "overheating",
        "failed-unit",
        "battery-wear",
    }


def test_the_four_documented_conditions_are_covered():
    text = " ".join(
        p["name"] + " " + p["description"] + " " + p["sense"]
        if p["kind"] == "percept"
        else p["name"] + " " + p["description"] + " " + p["event_type"]
        for p in setpoints.PRESETS
    )
    for word in ("disk", "temperature", "failed", "battery"):
        assert word in text.lower()


def test_proposals_are_copies_not_the_module_tables():
    first = setpoints.proposals()[0]
    first["keywords"].append("edited")
    assert "edited" not in setpoints.PRESETS[0]["keywords"]


class Config:
    def __init__(self, **keys):
        self.keys = keys

    def get_bool(self, key, default=False):
        return self.keys.get(key, default)


def test_no_config_means_not_armable():
    assert setpoints.armable("disk-nearly-full", None) is None
    said = setpoints.describe("disk-nearly-full")
    assert "proposal" in said and "off" in said


def test_an_off_switch_keeps_a_preset_a_proposal():
    rule = setpoints.armable("disk-nearly-full", Config())
    assert rule is None


def test_an_on_switch_hands_a_rule_to_the_existing_arming_skill():
    preset = next(p for p in setpoints.PRESETS if p["name"] == "disk-nearly-full")
    rule = setpoints.armable(
        "disk-nearly-full", Config(**{preset["consent_key"]: True})
    )
    assert rule["kind"] == "percept"
    assert rule["sense"] == "storage"
    assert rule["ask_first"] is True
    assert rule["actuator"] == "notify"
    assert "enabled" not in rule  # arming is the caller's act


def test_an_event_preset_keeps_its_event_shape():
    preset = next(p for p in setpoints.PRESETS if p["name"] == "failed-unit")
    rule = setpoints.armable("failed-unit", Config(**{preset["consent_key"]: True}))
    assert rule["event_type"] == "unithealth"
    assert rule["source"] == "failed"


def test_an_unknown_name_is_a_refusal_that_names_the_presets():
    said = setpoints.describe("nonesuch")
    assert "no setpoint" in said and "overheating" in said
