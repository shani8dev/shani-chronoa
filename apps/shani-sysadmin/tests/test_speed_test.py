"""Speed-test diagnostics, now run from shani-sysadmin rather than asked of Chronoa.

The consent key still lives in Chronoa's schema so an existing grant keeps its
meaning; the skill and these checks moved with the rest of the network
diagnostics. Only the transport is faked, so the refusal and the accounting
are the code that would run on a machine with a network.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "usr/lib/shani-chronoa"))

from shani_sysadmin.skills import speed_test as st  # noqa: E402


def grant(key: str, value: bool = True) -> None:
    """Write a consent key the way Settings would (keyfile backend from conftest)."""
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    from shani_chronoa import config as config_mod

    import os

    path = os.path.join(
        os.environ["XDG_CONFIG_HOME"], "glib-2.0", "settings", "keyfile"
    )
    backend = Gio.keyfile_settings_backend_new(
        path, config_mod.SCHEMA_PATH, config_mod.SCHEMA_ID
    )
    settings = Gio.Settings.new_with_backend(config_mod.SCHEMA_ID, backend)
    settings.set_boolean(key, value)
    Gio.Settings.sync()


def _mock_transport(hits):
    import httpx

    def handler(request):
        hits.append((request.method, str(request.url)))
        if request.url.path == "/__down":
            n = int(request.url.params.get("bytes", "0"))
            return httpx.Response(200, content=b"\0" * n)
        return httpx.Response(200, json={})

    return httpx.MockTransport(handler)


def test_speed_test_is_refused_until_granted_and_never_touches_the_wire(monkeypatch):
    grant("speed-test-enabled", False)
    hits = []
    monkeypatch.setattr(st, "_TRANSPORT", _mock_transport(hits))
    text = st._run({})
    assert "Refusing" in text and "speed-test-enabled" in text and hits == []


def test_speed_test_privacy_mode_wins_over_the_grant(monkeypatch):
    grant("speed-test-enabled")
    hits = []
    monkeypatch.setattr(st, "_TRANSPORT", _mock_transport(hits))
    monkeypatch.setattr(st.egress, "privacy_mode_enabled", lambda: True)
    text = st._run({})
    assert "privacy mode is on" in text and hits == []


def test_speed_test_measures_and_logs(monkeypatch):
    grant("speed-test-enabled")
    hits, logged = [], []
    monkeypatch.setattr(st, "_TRANSPORT", _mock_transport(hits))
    monkeypatch.setattr(st.egress, "privacy_mode_enabled", lambda: False)
    monkeypatch.setattr(st.egress, "record", lambda *a, **k: logged.append((a, k)))
    text = st._run({"download_mb": 1, "upload_mb": 1})
    assert "Latency:" in text and "Download:" in text and "Upload:" in text
    assert "1.0 MB" in text and "One measurement" in text
    assert any(k.get("bytes_out") == 1_000_000 for _, k in logged), (
        "the upload's bytes must be logged"
    )
    assert sum(1 for m, _ in hits if m == "POST") == 1


def test_the_skill_is_not_in_chronoa_anymore():
    from shani_chronoa import tools

    assert "speed_test" not in tools.TOOLS
