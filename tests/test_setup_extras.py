"""The setup's More page: eyes, imagine, memory and languages - each step and what it feeds.

Downloads go through the real verified path (`stt_provision._install`) with a
stub HTTP transport and tiny files whose digests the test computes, so the
size and sha256 checks that guard the real 0.5-2 GB files are the ones run.
Servers are not started here (a test has no user session); what the units
would run is read back from the env files the steps write. The servers
themselves, and a real picture, description and search, are shani-testbed's
`chronoa-extras` slot test.
"""

import base64
import hashlib
import io
import json
import os
import zipfile

import httpx
import pytest

from shani_chronoa import (conversation_store, sherpa, imagegen, languages, local_embed, local_llm, local_vision,
                           model_service, setup_wizard, voices)
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.stt_provision import DigestMismatch, ModelSpec

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _spec(key, filename, data, base="https://huggingface.co/x"):
    return ModelSpec(key, filename, len(data), hashlib.sha256(data).hexdigest(), "", base)


def _serve(payloads: dict, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        for name, data in payloads.items():
            if str(request.url).endswith(name):
                return httpx.Response(200, content=data, headers={"content-length": str(len(data))})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


# --- model_service -----------------------------------------------------------

def test_env_file_round_trips_and_refuses_what_systemd_would_split():
    path = model_service.write_env("vision", "/usr/bin/llama-server", ["-m", "/a/b.gguf", "--port", "8767"])
    assert path.name == "model-vision.env"
    assert model_service.read_env("vision") == ("/usr/bin/llama-server", ["-m", "/a/b.gguf", "--port", "8767"])
    for bad in ("/home/a b/m.gguf", "$HOME/m.gguf", ""):
        with pytest.raises(ValueError):
            model_service.write_env("vision", "/usr/bin/llama-server", ["-m", bad])
    with pytest.raises(ValueError):
        model_service.write_env("nonsense", "/bin/true", [])
    assert model_service.read_env("embed") == ("", [])


def test_every_server_has_its_own_loopback_port():
    ports = list(model_service.PORTS.values()) + [local_llm.PORT, 8766]
    assert len(set(ports)) == len(ports) and model_service.HOST == "127.0.0.1"


def test_the_template_unit_reads_the_env_file_it_is_named_after():
    unit = (open(os.path.join(os.path.dirname(__file__), "..", "usr", "lib", "systemd", "user",
                              "shani-chronoa-model@.service")).read())
    assert "EnvironmentFile=%h/.config/shani-chronoa/model-%i.env" in unit
    assert "ConditionPathExists=%h/.config/shani-chronoa/model-%i.env" in unit
    assert "ExecStart=/usr/bin/env ${MODEL_BIN} $MODEL_ARGS" in unit


# --- eyes ------------------------------------------------------------------

def test_vision_pins_are_real_and_tiers_follow_ram_and_gpu():
    for m in local_vision.TIERS:
        for spec in (m.model, m.mmproj):
            assert len(spec.sha256) == 64 and spec.size_bytes > 50_000_000 and spec.base_url.startswith("https://")
    assert local_vision.recommended(4) == "smolvlm2-500m"
    assert local_vision.recommended(16) == "qwen3-vl-2b"
    assert local_vision.recommended(16, gpu=True) == "qwen3-vl-4b"
    assert local_vision.recommended(8, gpu=True) == "qwen3-vl-2b", "a GPU with little RAM does not get the large one"


def test_eyes_step_downloads_both_files_and_points_the_server_at_them(monkeypatch):
    model, proj = b"GGUF" + os.urandom(3000), b"GGUF" + os.urandom(1500)
    m = local_vision.VisionModel("smolvlm2-500m", "Small - x", _spec("smolvlm2-500m", "v.gguf", model),
                                 _spec("smolvlm2-500m-mmproj", "mmproj-v.gguf", proj), 0)
    monkeypatch.setitem(local_vision.MODELS, "smolvlm2-500m", m)
    monkeypatch.setattr(local_vision, "TIERS", (m,))
    monkeypatch.setattr(local_llm, "server_binary", lambda: "/usr/bin/llama-server")
    monkeypatch.setattr(local_llm, "gpu_devices", lambda: [])
    started = []
    monkeypatch.setattr(model_service, "start", lambda instance: started.append(instance) or "")
    out = setup_wizard.setup_eyes("smolvlm2-500m", wait_seconds=0,
                                  transport=_serve({"/v.gguf": model, "/mmproj-v.gguf": proj}))
    assert "can now see" in out and started == ["vision"]
    binary, args = model_service.read_env("vision")
    assert binary == "/usr/bin/llama-server"
    assert args[args.index("--mmproj") + 1].endswith("mmproj-v.gguf")
    assert args[args.index("--sleep-idle-seconds") + 1] == str(local_vision.IDLE_SECONDS)
    assert args[args.index("--host") + 1] == "127.0.0.1" and args[args.index("-ngl") + 1] == "0"
    assert local_vision.active() == "smolvlm2-500m" and local_vision.available() and local_vision.verify("smolvlm2-500m")


def test_an_altered_projector_is_not_installed(monkeypatch):
    model, proj = b"GGUF" + os.urandom(300), b"GGUF" + os.urandom(150)
    m = local_vision.VisionModel("k", "x - y", _spec("k", "v.gguf", model), _spec("k-mm", "p.gguf", proj), 0)
    monkeypatch.setitem(local_vision.MODELS, "k", m)
    ChronoaConfig().set("model-download-enabled", "true")
    tampered = bytearray(proj); tampered[10] ^= 1
    with pytest.raises(DigestMismatch):
        local_vision.provision("k", config=ChronoaConfig(), transport=_serve({"/v.gguf": model, "/p.gguf": bytes(tampered)}))
    assert not local_vision.available()


def test_describe_sends_the_image_as_a_data_url_and_reads_the_answer():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": " A terminal with a failed test. "}}]})
    out = local_vision.describe(PNG, "What is this?", transport=httpx.MockTransport(handler))
    assert out == "A terminal with a failed test."
    parts = seen[0]["messages"][0]["content"]
    assert parts[0]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert parts[1] == {"type": "text", "text": "What is this?"}
    with pytest.raises(local_vision.VisionError, match="HTTP 500"):
        local_vision.describe(PNG, "x", transport=httpx.MockTransport(lambda r: httpx.Response(500, text="boom")))
    with pytest.raises(local_vision.VisionError, match="no description"):
        local_vision.describe(PNG, "x", transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "  "}}]})))


def test_the_vision_sense_prefers_the_llamacpp_model_once_it_is_set_up(monkeypatch):
    from shani_chronoa import screengrab
    from shani_chronoa.senses import vision
    ChronoaConfig().set("vision-sense-enabled", "true")
    monkeypatch.setattr(local_vision, "available", lambda: True)
    monkeypatch.setattr(local_vision, "active", lambda: "qwen3-vl-2b")
    seen = {}

    def fake_child(image, model, host, prompt, timeout, backend="ollama"):
        seen.update(model=model, host=host, backend=backend)
        return "a window"
    monkeypatch.setattr(vision, "_describe_via_child", fake_child)
    monkeypatch.setattr(screengrab, "capture", lambda **kw: screengrab.Capture(
        data=PNG, source="screen", backend="test", width=1, height=1, native_width=1, native_height=1,
        captured_at=0.0)
        if hasattr(screengrab, "Capture") else None)
    if not hasattr(screengrab, "Capture"):
        pytest.skip("screengrab has no Capture type to stand in")
    percept = vision.run({})
    assert seen == {"model": "qwen3-vl-2b", "host": "http://127.0.0.1:8767", "backend": "llamacpp"}
    assert percept.metadata["backend"] == "llamacpp"
    # asking for an Ollama model by name still goes to Ollama
    vision.run({"model": "qwen3-vl:4b"})
    assert seen["backend"] == "ollama"


# --- memory ------------------------------------------------------------------

def test_embed_prefixes_the_task_and_normalises(monkeypatch):
    sent = []

    def handler(request):
        body = json.loads(request.content)
        sent.append(body["input"])
        return httpx.Response(200, json={"data": [{"index": i, "embedding": [3.0, 4.0]} for i in range(len(body["input"]))]})
    out = local_embed.embed(["router", "wifi"], local_embed.QUERY, transport=httpx.MockTransport(handler))
    assert sent == [["search_query: router", "search_query: wifi"]]
    assert out == [[0.6, 0.8], [0.6, 0.8]]
    assert local_embed.embed(["x"]) is None, "not set up: callers keep their keyword search"


#: a stand-in embedding model: each word maps onto a "concept", and texts sharing a concept point the same way
_CONCEPTS = {"router": 0, "wifi": 0, "internet": 0, "dropping": 0, "dal": 1, "dinner": 1, "recipe": 1}


def _fake_embed(texts, kind=local_embed.DOCUMENT):
    out = []
    for t in texts:
        v = [0.0, 0.0, 0.01]
        for w in t.lower().replace("?", " ").split():
            if w in _CONCEPTS:
                v[_CONCEPTS[w]] += 1.0
        out.append(local_embed.normalise(v))
    return out


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return conversation_store.session_dir()


def _say(path, *pairs):
    for role, text in pairs:
        conversation_store.append({"role": role, "content": text}, path)


def test_search_finds_by_meaning_what_no_word_matches(root):
    a = conversation_store.active_path(root)
    _say(a, ("user", "the wifi box keeps dropping every evening"), ("assistant", "Try moving it higher"))
    conversation_store.new_session(root)
    _say(conversation_store.active_path(root), ("user", "dal recipe for dinner"), ("assistant", "Soak the lentils"))
    current = conversation_store.new_session(root)
    query = "router problems"
    # the control: words alone find nothing
    assert conversation_store.search(root, query, exclude=current, embed=lambda *a, **k: None) == []
    hits = conversation_store.search(root, query, exclude=current, embed=_fake_embed)
    assert hits and hits[0]["snippet"].startswith("the wifi box") and hits[0]["match"] == "meaning"
    assert all("dal" not in h["snippet"] for h in hits), "an unrelated message is below the floor"
    # a word match and a meaning match on the same message say so
    both = conversation_store.search(root, "wifi dropping", exclude=current, embed=_fake_embed)
    assert both[0]["match"] == "words and meaning"


def test_vectors_go_with_a_deleted_conversation(root):
    import sqlite3
    a = conversation_store.active_path(root)
    _say(a, ("user", "router password"), ("assistant", "on the sticker"))
    keep = conversation_store.new_session(root)
    conversation_store.search(root, "wifi", exclude=keep, embed=_fake_embed)
    db = sqlite3.connect(root / conversation_store.SEARCH_DB)
    assert db.execute("SELECT COUNT(*) FROM vecs WHERE sid = ?", (a.stem,)).fetchone()[0] == 2
    db.close()
    ChronoaConfig().set("file-delete-enabled", "true")
    assert conversation_store.delete(root, a.stem) == a.stem
    db = sqlite3.connect(root / conversation_store.SEARCH_DB)
    assert db.execute("SELECT COUNT(*) FROM vecs").fetchone()[0] == 0
    db.close()


def test_memory_step_writes_an_embedding_server(monkeypatch):
    data = b"GGUF" + os.urandom(500)
    monkeypatch.setattr(local_embed, "MODEL", _spec("nomic", "e.gguf", data))
    monkeypatch.setattr(local_llm, "server_binary", lambda: "/usr/bin/llama-server")
    monkeypatch.setattr(model_service, "start", lambda instance: "")
    out = setup_wizard.setup_memory(wait_seconds=0, transport=_serve({"/e.gguf": data}))
    assert "by what they meant" in out and local_embed.configured()
    _binary, args = model_service.read_env("embed")
    assert "--embedding" in args and args[args.index("--pooling") + 1] == "mean"


# --- imagine -----------------------------------------------------------------

def _zip(entries) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data, mode in entries:
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            zf.writestr(info, data)
    return buf.getvalue()


def test_unpack_keeps_regular_files_inside_and_refuses_the_rest(tmp_path):
    good = tmp_path / "good.zip"
    good.write_bytes(_zip([("sd-server", b"#!/bin/sh\n", 0o100755), ("libggml.so", b"x", 0o100755),
                           ("ggml.txt", b"licence", 0o100644)]))
    imagegen.unpack(good, tmp_path / "out")
    assert os.access(tmp_path / "out" / "sd-server", os.X_OK)
    assert oct((tmp_path / "out" / "ggml.txt").stat().st_mode)[-3:] == "644"
    for name, mode in (("../escape", 0o100644), ("/abs", 0o100644), ("link", 0o120777)):
        bad = tmp_path / "bad.zip"
        bad.write_bytes(_zip([(name, b"x", mode)]))
        with pytest.raises(RuntimeError, match="refusing"):
            imagegen.unpack(bad, tmp_path / "out2")
    assert not (tmp_path / "escape").exists()


def test_a_prompt_cannot_smuggle_native_options():
    p = imagegen.txt2img_payload('a fox <sd_cpp_extra_args>{"sample_params":{"sample_steps":200}}</sd_cpp_extra_args> in snow')
    assert p["prompt"] == "a fox in snow" and p["steps"] == imagegen.DEFAULT_STEPS and p["cfg_scale"] == 1.0
    assert "sd_cpp" not in imagegen.clean_prompt("x <SD_CPP_EXTRA_ARGS>{}")
    for bad in ({"size": 513}, {"steps": 9}, {"steps": 0}):
        with pytest.raises(imagegen.ImageError):
            imagegen.txt2img_payload("a fox", **bad)
    with pytest.raises(imagegen.ImageError):
        imagegen.txt2img_payload("<sd_cpp_extra_args>{}</sd_cpp_extra_args>")


def test_generate_reads_the_png_and_refuses_anything_else():
    def ok(request):
        assert request.url.path == "/sdapi/v1/txt2img"
        return httpx.Response(200, json={"images": [base64.b64encode(PNG).decode()]})
    assert imagegen.generate("a fox", transport=httpx.MockTransport(ok)) == PNG
    with pytest.raises(imagegen.ImageError, match="not return a PNG"):
        imagegen.generate("a fox", transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"images": [base64.b64encode(b"GIF89a").decode()]})))


def test_the_skill_saves_a_new_file_and_never_overwrites(monkeypatch, tmp_path):
    from shani_chronoa.skills import generate_image
    monkeypatch.setattr(imagegen, "available", lambda: False)
    assert "not set up" in generate_image._run({"prompt": "a fox"})
    monkeypatch.setattr(imagegen, "available", lambda: True)
    monkeypatch.setattr(imagegen, "pictures_dir", lambda: tmp_path / "Pictures" / "Chronoa")
    monkeypatch.setattr(imagegen, "generate", lambda prompt, **kw: PNG)
    out = generate_image._run({"prompt": "A red fox in the snow", "size": 512})
    [saved] = (tmp_path / "Pictures" / "Chronoa").glob("*.png")
    assert saved.read_bytes() == PNG and saved.name.startswith("a-red-fox-in-the-snow-") and str(saved) in out
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / "fox.png"
    target.write_bytes(b"mine")
    assert "already exists" in generate_image._run({"prompt": "a fox", "output": str(target)})
    assert target.read_bytes() == b"mine"
    assert "whole number" in generate_image._run({"prompt": "a fox", "size": True})


def test_image_generation_gets_the_longer_time_limit():
    from shani_chronoa import tools
    assert tools._get_sandbox_config("generate_image").timeout_seconds == 300
    assert tools._get_sandbox_config("get_datetime").timeout_seconds == 30


def test_imagine_step_installs_the_engine_for_the_hardware(monkeypatch):
    engine = _zip([("sd-server", b"#!/bin/sh\n", 0o100755)])
    model = b"GGUF" + os.urandom(800)
    monkeypatch.setattr(imagegen, "ENGINE_VULKAN", _spec("v", "sd-vulkan.zip", engine, "https://github.com/x"))
    monkeypatch.setattr(imagegen, "MODEL", _spec("sd-turbo", "sd.gguf", model))
    monkeypatch.setattr(model_service, "start", lambda instance: "")
    out = setup_wizard.setup_imagine(wait_seconds=0, gpu=True,
                                     transport=_serve({"/sd-vulkan.zip": engine, "/sd.gguf": model}))
    assert "graphics card" in out and imagegen.available()
    binary, args = model_service.read_env("imagine")
    assert binary.endswith("sdcpp/sd-server") and args[args.index("--listen-ip") + 1] == "127.0.0.1"
    assert not list(imagegen.engine_dir().glob("*.zip")), "the archive is removed after unpacking"


# --- languages ---------------------------------------------------------------

def test_scripts_are_told_apart():
    assert languages.script_of("नमस्ते, आप कैसे हैं?") == "devanagari"
    assert languages.script_of("ভালো আছি") == "bengali"
    assert languages.script_of("Hello there") == ""
    assert languages.script_of("Open नमस्ते and the settings window please") == "", "a word is not a reply"


def test_language_step_installs_reading_data_and_turns_listening_on(monkeypatch):
    data = os.urandom(900)
    lang = languages.LANGUAGES["hi"]._replace(tess_size=len(data), tess_sha256=hashlib.sha256(data).hexdigest(),
                                              voice="")
    monkeypatch.setitem(languages.LANGUAGES, "hi", lang)
    monkeypatch.setattr(languages, "SYSTEM_TESSDATA", languages.tessdata_dir().parent / "system-tessdata")
    languages.SYSTEM_TESSDATA.mkdir(parents=True)
    (languages.SYSTEM_TESSDATA / "eng.traineddata").write_bytes(b"eng")
    out = setup_wizard.setup_languages(["hi", "zz"], listen=True, transport=_serve({"/hin.traineddata": data}))
    assert out.startswith("Added Hindi (reading)") and "listens" in out
    config = ChronoaConfig()
    assert languages.chosen(config) == ["hi"] and config.get("language") == "auto"
    assert languages.reads("hi") and (languages.tessdata_dir() / "eng.traineddata").is_symlink()
    from shani_chronoa.senses import ocr
    assert ocr.default_languages() == ("eng", "hin")
    assert ocr.find_tessdata_dir() == str(languages.tessdata_dir())
    assert setup_wizard.setup_languages([], listen=False) == "Choose at least one language."


def _installed_voice(key):
    voices.voice_dir().mkdir(parents=True, exist_ok=True)
    (voices.voice_dir() / f"{key}.onnx").write_bytes(b"x")
    (voices.voice_dir() / f"{key}.onnx.json").write_text("{}")


def test_a_reply_in_hindi_is_spoken_with_the_hindi_voice(monkeypatch, tmp_path):
    from shani_chronoa import tts as tts_mod
    ChronoaConfig().set(languages.SETTING, "mr,hi")
    for key in ("en_US-lessac-medium", "hi_IN-priyamvada-medium", "mr_IN-google-medium"):
        _installed_voice(key)
    piper = tmp_path / "piper"
    piper.write_text("#!/bin/sh\n"); piper.chmod(0o755)
    t = tts_mod.PiperTTS(voice="en_US-lessac-medium", piper_path=str(piper))
    path, speaker = t._piper_voice_for("Hello, how are you?")
    assert path.endswith("en_US-lessac-medium.onnx") and speaker is None
    path, speaker = t._piper_voice_for("नमस्कार, तुम्ही कसे आहात?")
    assert path.endswith("mr_IN-google-medium.onnx") and speaker == 0, "Marathi was chosen first, and it is multi-speaker"
    ChronoaConfig().set(languages.SETTING, "hi")
    path, speaker = t._piper_voice_for("नमस्ते, आप कैसे हैं?")
    assert path.endswith("hi_IN-priyamvada-medium.onnx") and speaker is None
    assert t._espeak_lang("নমস্কার, কেমন আছেন") == "bn"


def test_language_pins_are_real():
    for code, lang in languages.LANGUAGES.items():
        assert len(lang.tess_sha256) == 64 and 100_000 < lang.tess_size < 20_000_000 and lang.code == code
        assert not lang.voice or voices.VOICES[lang.voice].language == code
        assert lang.script in languages._SCRIPTS


def test_state_reports_the_extras():
    s = setup_wizard.state()
    assert set(s) >= {"eyes", "imagine", "memory", "languages"}
    assert s["eyes"]["ready"] is False and s["imagine"]["ready"] is False and s["memory"]["ready"] is False
    assert s["languages"]["chosen"] == []


# --- one list of voices, each bringing its engine -----------------------------

def _kokoro_archives(monkeypatch):
    import io
    import tarfile

    def tarball(members):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:bz2") as tar:
            for name, (body, mode) in members.items():
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(body), mode
                tar.addfile(info, io.BytesIO(body))
        return buf.getvalue()
    release = tarball({f"sherpa-onnx-{sherpa.VERSION}-linux-x64-shared/bin/sherpa-onnx-offline-tts":
                      (b"#!/bin/sh\nexit 0\n", 0o755)})
    model = tarball({f"{voices._KOKORO_MODEL_DIR}/{n}": (b"x", 0o644)
                     for n in ("model.int8.onnx", "voices.bin", "tokens.txt", "espeak-ng-data/phontab")})
    monkeypatch.setattr(sherpa, "RELEASE", _spec("sherpa", sherpa.RELEASE.filename, release, "https://github.com/x"))
    monkeypatch.setattr(voices, "_KOKORO_MODEL", _spec("kokoro", voices._KOKORO_MODEL.filename, model,
                                                       "https://github.com/x"))
    return _serve({sherpa.RELEASE.filename: release, voices._KOKORO_MODEL.filename: model})


def test_picking_a_kokoro_voice_installs_kokoro_and_speaks_with_it(monkeypatch):
    from shani_chronoa import tts as tts_mod
    out = setup_wizard.setup_voice("bf_emma", transport=_kokoro_archives(monkeypatch))
    assert out.startswith("Chronoa now speaks with Kokoro (Emma)")
    config = ChronoaConfig()
    assert config.get_bool("kokoro-tts-enabled") and config.get("kokoro-voice") == "bf_emma"
    assert voices.kokoro_installed("bf_emma") and voices.kokoro_problem("bf_emma") == ""
    assert not list(sherpa.install_dir().glob("*.tar.bz2")) and not list(voices.kokoro_dir().glob("*.tar.bz2"))
    t = tts_mod.PiperTTS(config=config)
    assert t.engine("Your timer is set.") == "kokoro"
    cmd = t._kokoro_command("-rf is not an option here", "/tmp/o.wav")
    assert cmd[0] == voices.kokoro_binary() and "--sid=7" in cmd and cmd[-1] == "rf is not an option here"
    assert f"--kokoro-model={voices.kokoro_model_dir() / 'model.int8.onnx'}" in cmd
    # a reply in another script never goes to the English-only Kokoro
    assert t.engine("नमस्ते") != "kokoro"


def test_picking_a_piper_voice_after_kokoro_switches_back(monkeypatch):
    transport = _kokoro_archives(monkeypatch)
    setup_wizard.setup_voice("af_sarah", transport=transport)
    monkeypatch.setattr(voices, "install_piper", lambda **kw: "/bin/true")
    monkeypatch.setattr(voices, "install_voice", lambda voice, **kw: None)
    out = setup_wizard.setup_voice("en_US-amy-medium")
    assert out == "Chronoa now speaks with Amy's voice."
    assert not ChronoaConfig().get_bool("kokoro-tts-enabled") and ChronoaConfig().piper_voice == "en_US-amy-medium"


def test_trying_a_voice_does_not_need_the_setting(monkeypatch):
    from shani_chronoa import tts as tts_mod
    _kokoro_archives(monkeypatch)
    voices.install_kokoro(config=type("C", (), {"get_bool": lambda self, k, d: True})(),
                          transport=_kokoro_archives(monkeypatch))
    t = tts_mod.PiperTTS(config=ChronoaConfig())
    assert t.engine("hello") != "kokoro", "the setting is off"
    t.kokoro_trial_voice = "af_sky"
    assert t.engine("hello") == "kokoro" and "--sid=4" in t._kokoro_command("hello", "/tmp/o.wav")
    ChronoaConfig().set("kokoro-tts-enabled", "true")
    p = tts_mod.PiperTTS(config=ChronoaConfig())
    p.piper_trial = True
    assert p.engine("hello") != "kokoro", "listening to a Piper voice is heard as Piper"


def test_kokoro_voice_ids_are_the_verified_female_speakers():
    assert {k: v.sid for k, v in voices.KOKORO_VOICES.items()} == {
        "af_sarah": 3, "af_bella": 1, "af_nicole": 2, "af_sky": 4, "bf_emma": 7, "bf_isabella": 8}
    assert voices.kokoro_problem("am_adam").startswith("there is no Kokoro voice")
    assert "sherpa-onnx" in voices.kokoro_problem()
