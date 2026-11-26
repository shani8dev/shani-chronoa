"""/status names the model that answers, not the empty override setting."""

from shani_chronoa import inventory
from shani_chronoa.engine_ops import models


def test_status_uses_the_live_choice(monkeypatch):
    monkeypatch.setattr(models, "answering_model", lambda config=None: "qwen3:8b")
    status = inventory.describe("status")["items"]
    assert status["llm_model"] == "qwen3:8b"


def test_answering_model_uses_profile_when_override_is_empty(monkeypatch):
    from shani_chronoa import local_llm
    from shani_chronoa.hardware_profile import HardwareProfile

    class Config:
        def get(self, key, default=""):
            return default

    monkeypatch.setattr(local_llm, "active", lambda: "")
    monkeypatch.setattr(models, "bounded", lambda read, **kw: (None, "local"))
    monkeypatch.setattr(HardwareProfile, "get_model", lambda self: "qwen3:8b")
    assert models.answering_model(Config()) == "qwen3:8b"


def test_answering_model_is_honest_when_probes_time_out(monkeypatch):
    monkeypatch.setattr(models, "bounded", lambda read, **kw: models.NOT_CHECKED)
    assert "not checked" in models.answering_model()
