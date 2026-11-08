"""A packaged model is a model the machine has, in every backend.

The whisper model established the pattern: `model_path` prefers the user's
copy but answers with the distro's, so shipping a model in a package means
setup never re-downloads it. These backends now answer the same way; each
test plants the file only in the system directory and asks the question the
setup wizard asks.
"""

from pathlib import Path

from shani_chronoa import imagegen, local_embed, local_llm, local_vision, sherpa, voices


def _plant(root: Path, name: str, executable: bool = False):
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_bytes(b"model")
    if executable:
        path.chmod(0o755)
    return path


def test_llm_installed_with_only_a_packaged_model(tmp_path, monkeypatch):
    monkeypatch.setattr(local_llm, "SYSTEM_MODEL_DIR", tmp_path / "sys")
    _plant(tmp_path / "sys", local_llm.SPECS["qwen3-0.6b"].filename)
    assert "qwen3-0.6b" in local_llm.installed()


def test_llm_use_links_at_a_packaged_model(tmp_path, monkeypatch):
    monkeypatch.setattr(local_llm, "SYSTEM_MODEL_DIR", tmp_path / "sys")
    target = _plant(tmp_path / "sys", local_llm.SPECS["qwen3-0.6b"].filename)
    local_llm.model_dir().mkdir(parents=True, exist_ok=True)
    local_llm.use("qwen3-0.6b", gate=False)
    assert local_llm.current_link().resolve() == target


def test_vision_installed_with_only_packaged_files(tmp_path, monkeypatch):
    monkeypatch.setattr(local_vision, "SYSTEM_MODEL_DIR", tmp_path / "sys")
    tier = local_vision.TIERS[0]
    _plant(tmp_path / "sys", tier.model.filename)
    _plant(tmp_path / "sys", tier.mmproj.filename)
    assert tier.key in local_vision.installed()


def test_embed_installed_with_only_a_packaged_model(tmp_path, monkeypatch):
    monkeypatch.setattr(local_embed, "SYSTEM_MODEL_DIR", tmp_path / "sys")
    _plant(tmp_path / "sys", local_embed.MODEL.filename)
    assert local_embed.installed() is True
    assert str(tmp_path / "sys") in local_embed.server_args()[1]


def test_imagine_server_binary_and_install_skip_a_packaged_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(imagegen, "SYSTEM_ENGINE_DIR", tmp_path / "sys-engine")
    monkeypatch.setattr(imagegen, "SYSTEM_MODEL_DIR", tmp_path / "sys-model")
    _plant(tmp_path / "sys-engine", "sd-server", executable=True)
    _plant(tmp_path / "sys-model", imagegen.MODEL.filename)
    assert imagegen.server_binary().endswith("sd-server")
    assert imagegen.install_engine(gpu=False) == imagegen.server_binary()


def test_imagine_gpu_still_downloads_when_no_marker_says_which_build(tmp_path, monkeypatch):
    monkeypatch.setattr(imagegen, "SYSTEM_ENGINE_DIR", tmp_path / "sys")
    _plant(tmp_path / "sys", "sd-server", executable=True)
    imagegen.engine_dir().mkdir(parents=True, exist_ok=True)
    calls = []
    monkeypatch.setattr(imagegen, "_install",
                        lambda spec, **kw: calls.append(spec.filename) or (tmp_path / "dl.zip"))
    monkeypatch.setattr(imagegen, "unpack", lambda archive, dest: calls.append("unpack"))
    imagegen.install_engine(gpu=True)
    assert calls, "a GPU build request must not be answered by an unmarked system binary"


def test_sherpa_binary_falls_back_to_the_packaged_one(tmp_path, monkeypatch):
    monkeypatch.setattr(sherpa, "SYSTEM_BIN_DIR", tmp_path / "sys-bin")
    _plant(tmp_path / "sys-bin", "sherpa-onnx-offline-tts", executable=True)
    assert sherpa.binary("sherpa-onnx-offline-tts").endswith("sherpa-onnx-offline-tts")


def test_kokoro_files_resolve_to_the_packaged_model(tmp_path, monkeypatch):
    monkeypatch.setattr(voices, "SYSTEM_KOKORO_DIR", tmp_path / "sys-kokoro")
    _plant(tmp_path / "sys-kokoro", "model.int8.onnx")
    files = voices.kokoro_files()
    assert files["--kokoro-model"] == tmp_path / "sys-kokoro" / "model.int8.onnx"
