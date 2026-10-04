"""First-run setup: model tiers, CPU/GPU server arguments, verified steps (mock transport), and gating.

The real end-to-end - downloads, llama-server answering a tool call, Piper
heard back by whisper.cpp - is shani-testbed's slot-tests/chronoa-setup.sh on
a booted image. These pin the logic that run depends on.
"""

import hashlib
import io
import os
import tarfile
import threading
from pathlib import Path

import httpx
import pytest

from shani_chronoa import local_llm, setup_wizard, stt_provision, voices
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.stt_provision import ModelSpec


def test_default_model_follows_ram_and_gpu(monkeypatch):
    assert local_llm.recommended(4) == "qwen3-0.6b"
    assert local_llm.recommended(8) == "qwen3-1.7b"
    assert local_llm.recommended(64) == "qwen3-1.7b", "4B is offered, never the CPU default"
    monkeypatch.setattr(local_llm, "ram_gb", lambda: 16.0)
    monkeypatch.setattr(local_llm, "gpu_devices", lambda: ["Vulkan0: Some GPU (8192 MiB)"])
    assert local_llm.recommended_for_machine() == "qwen3-4b"
    monkeypatch.setattr(local_llm, "gpu_devices", lambda: [])
    assert local_llm.recommended_for_machine() == "qwen3-1.7b"


def test_server_args_offload_only_with_a_gpu():
    cpu = local_llm.server_args([])
    gpu = local_llm.server_args(["Vulkan0: GPU"])
    assert cpu[cpu.index("-ngl") + 1] == "0" and "-t" in cpu
    assert gpu[gpu.index("-ngl") + 1] == "99" and "-t" not in gpu
    for args in (cpu, gpu):
        assert args[args.index("--host") + 1] == "127.0.0.1" and "--jinja" in args


def test_gpu_list_parsing(monkeypatch, tmp_path):
    fake = tmp_path / "llama-server"
    fake.write_text("#!/bin/sh\necho 'load_backend: loaded CPU backend'\necho 'Available devices:'\n"
                    "echo '  Vulkan0: Intel(R) Iris(R) Xe Graphics (15000 MiB, 14000 MiB free)'\n")
    fake.chmod(0o755)
    monkeypatch.setattr(local_llm, "server_binary", lambda: str(fake))
    assert local_llm.gpu_devices() == ["Vulkan0: Intel(R) Iris(R) Xe Graphics (15000 MiB, 14000 MiB free)"]
    fake.write_text("#!/bin/sh\necho 'Available devices:'\n")
    assert local_llm.gpu_devices() == [], "a CPU-only machine lists none, and that is fine"


def _serve(payloads: dict):
    def handler(request):
        for name, data in payloads.items():
            if str(request.url).endswith(name):
                return httpx.Response(200, content=data, headers={"content-length": str(len(data))})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_brain_step_downloads_verifies_and_links(monkeypatch):
    data = b"GGUF" + os.urandom(2000)
    spec = ModelSpec("qwen3-0.6b", "tiny.gguf", len(data), hashlib.sha256(data).hexdigest(), "", "https://huggingface.co/x")
    monkeypatch.setitem(local_llm.SPECS, "qwen3-0.6b", spec)
    monkeypatch.setattr(local_llm, "server_binary", lambda: "/usr/bin/llama-server")
    monkeypatch.setattr(local_llm, "start_service", lambda: "")
    monkeypatch.setattr(local_llm, "is_up", lambda timeout=1.5: True)
    monkeypatch.setattr(local_llm, "gpu_devices", lambda: [])
    steps = []
    out = setup_wizard.setup_brain("qwen3-0.6b", report=lambda f, t: steps.append(f), transport=_serve({"tiny.gguf": data}))
    assert out.startswith("Chronoa now thinks on this computer (qwen3-0.6b, on the processor)")
    assert local_llm.active() == "qwen3-0.6b" and local_llm.verify("qwen3-0.6b")
    assert ChronoaConfig().get_bool("model-download-enabled", False), "pressing Download is the consent"
    assert steps and steps[-1] == 1.0
    # an altered file in place is not trusted: provision re-fetches it
    (local_llm.model_dir() / "tiny.gguf").write_bytes(b"GGUF" + b"\0" * 2000)
    assert not local_llm.verify("qwen3-0.6b")
    local_llm.provision("qwen3-0.6b", transport=_serve({"tiny.gguf": data}), config=ChronoaConfig())
    assert local_llm.verify("qwen3-0.6b")


def test_a_tampered_download_is_refused_and_leaves_nothing(monkeypatch):
    data = os.urandom(500)
    spec = ModelSpec("qwen3-0.6b", "t.gguf", len(data), "0" * 64, "", "https://huggingface.co/x")
    monkeypatch.setitem(local_llm.SPECS, "qwen3-0.6b", spec)
    monkeypatch.setattr(local_llm, "server_binary", lambda: "/usr/bin/llama-server")
    with pytest.raises(stt_provision.DigestMismatch):
        setup_wizard.setup_brain("qwen3-0.6b", transport=_serve({"t.gguf": data}))
    assert not (local_llm.model_dir() / "t.gguf").exists()


def test_cancel_stops_a_download(monkeypatch):
    data = os.urandom(5000)
    spec = ModelSpec("qwen3-0.6b", "c.gguf", len(data), hashlib.sha256(data).hexdigest(), "", "https://huggingface.co/x")
    monkeypatch.setitem(local_llm.SPECS, "qwen3-0.6b", spec)
    monkeypatch.setattr(local_llm, "server_binary", lambda: "/usr/bin/llama-server")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(setup_wizard.Cancelled):
        setup_wizard.setup_brain("qwen3-0.6b", cancel=cancel, transport=_serve({"c.gguf": data}))
    assert not list(local_llm.model_dir().glob("*.gguf"))


def test_without_llama_cpp_the_brain_step_says_so(monkeypatch):
    monkeypatch.setattr(local_llm, "server_binary", lambda: "")
    assert setup_wizard.setup_brain("qwen3-1.7b").startswith("llama.cpp is not installed")


def test_voice_step_installs_piper_and_a_voice(monkeypatch):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        exe = b"#!/bin/sh\necho piper\n"
        info = tarfile.TarInfo("piper/piper"); info.size = len(exe); info.mode = 0o755
        tar.addfile(info, io.BytesIO(exe))
    archive = buf.getvalue()
    onnx, cfg = os.urandom(3000), b'{"audio": {}}'
    monkeypatch.setattr(voices, "_PIPER", ModelSpec("p", "piper.tgz", len(archive), hashlib.sha256(archive).hexdigest(),
                                                    "", "https://github.com/x"))
    monkeypatch.setitem(voices.VOICES, "en_GB-jenny_dioco-medium",
                        voices.Voice("Jenny - soft, British", "en/en_GB/jenny", hashlib.sha256(onnx).hexdigest(),
                                     hashlib.sha256(cfg).hexdigest(), len(cfg), len(onnx)))
    monkeypatch.setattr(voices.shutil, "which", lambda name: None)
    out = setup_wizard.setup_voice("en_GB-jenny_dioco-medium", transport=_serve(
        {"piper.tgz": archive, "jenny_dioco-medium.onnx": onnx, "jenny_dioco-medium.onnx.json": cfg}))
    assert out == "Chronoa now speaks with Jenny's voice."
    assert voices.piper_binary().endswith("piper/piper") and voices.voice_installed("en_GB-jenny_dioco-medium")
    assert ChronoaConfig().piper_voice == "en_GB-jenny_dioco-medium"
    assert not list(voices.piper_dir().glob("*.tgz")), "the archive is removed after unpacking"


def test_needs_setup_and_its_off_switch(monkeypatch):
    monkeypatch.setattr(setup_wizard, "state", lambda config=None: {
        "brain": {"ready": False}, "ears": {"ready": True}, "voice": {"ready": True}})
    assert setup_wizard.needs_setup()
    ChronoaConfig().set("setup-complete", "true")
    assert not setup_wizard.needs_setup()


def test_every_pin_is_a_real_sha256():
    for spec in list(local_llm.SPECS.values()) + [voices._PIPER]:
        assert len(spec.sha256) == 64 and int(spec.sha256, 16) >= 0 and spec.size_bytes > 1_000_000
        assert spec.base_url.startswith("https://")
    for v in voices.VOICES.values():
        assert len(v.onnx_sha256) == len(v.json_sha256) == 64 and 4000 < v.json_size < 6000
        assert 50_000_000 < v.onnx_size < 90_000_000 and v.folder.startswith(v.language + "/")


def test_closing_setup_means_not_now(monkeypatch):
    monkeypatch.setattr(setup_wizard, "state", lambda config=None: {
        "brain": {"ready": False}, "ears": {"ready": False}, "voice": {"ready": False}})
    assert setup_wizard.needs_setup()
    ChronoaConfig().set("setup-dismissed", "true")
    assert not setup_wizard.needs_setup(), "a closed setup does not reopen by itself"
