"""llm.env follows the build: a file an older build wrote is rewritten.

Measured 2026-10-15: the context size became RAM-aware (32768 on a 30 GB
machine) and the rail still read 8,192, because the unit started at login from
an llm.env written the day before, and only `start_service` ever rewrote it.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import files, local_llm  # noqa: E402


def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "config_home", lambda: tmp_path)
    monkeypatch.setattr(local_llm, "server_args",
                        lambda devices=None: ["-m", "m.gguf", "-c", "32768"])
    return tmp_path / "shani-chronoa" / "llm.env"


def test_a_stale_file_is_rewritten(tmp_path, monkeypatch):
    path = _env(tmp_path, monkeypatch)
    path.parent.mkdir(parents=True)
    path.write_text("LLAMA_ARGS=-m m.gguf -c 8192\n")
    assert local_llm.refresh_env() is True
    assert "-c 32768" in path.read_text()
    assert local_llm.refresh_env() is False, "an up-to-date file was rewritten again"


def test_no_file_means_no_service_and_none_is_made(tmp_path, monkeypatch):
    path = _env(tmp_path, monkeypatch)
    assert local_llm.refresh_env() is False
    assert not path.exists(), "a machine that never set up the service got one"


def test_the_app_checks_it_at_startup():
    source = (pathlib.Path(local_llm.__file__).parent / "app" / "application.py").read_text()
    assert "local_llm.refresh_env()" in source and "local_llm.restart_service()" in source
