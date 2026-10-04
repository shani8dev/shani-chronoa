"""The general cut-out's own half: the pinned u2net model, and what is missing when it cannot run.

Four things can be missing and they are four different sentences - onnxruntime,
the model file, a model that does not match its pin, and an address-space
ceiling below what the model needs. Each is asserted on its own, with the other
three arranged so that only that one is wrong, because a single "it did not
work" covers none of them and a message that names the wrong one sends a person
installing something they already have.

The last test in here is the control this feature most needs: if the object path
ever quietly answered with PP-HumanSeg, asking for a cut-out of a dog would
return a cut-out of whatever person-shaped region the people model found, under
a sentence saying the dog's background was removed. Both nets are checked from
the two sides - the runtime sentinel (a fallback would be seen being called) and
the source (a fallback placed after the refusal would be seen in the text).
"""

import ast
import hashlib
import inspect
import sys
import types

import httpx
import pytest

from shani_chronoa import matting_cutout, segmentation_u2net
from shani_chronoa.stt_provision import DigestMismatch, ModelSpec

PAYLOAD = b"not a real onnx graph, only bytes that hash"


class _Consenting:
    """The one setting the downloader asks about."""

    def get_bool(self, key, default=False):
        return key == "model-download-enabled"


def _spec(data=PAYLOAD, base="https://github.com/x"):
    return ModelSpec("u2net", "u2net.onnx", len(data), hashlib.sha256(data).hexdigest(),
                     "u2net (Apache-2.0)", base)


def _serve(payload, digest=None):
    def handler(request):
        return httpx.Response(200, content=payload if digest is None else digest,
                              headers={"content-length": str(len(payload))})
    return httpx.MockTransport(handler)


@pytest.fixture
def u2net_dir(tmp_path, monkeypatch):
    """The model directory, with the pin pointed at bytes a test can serve."""
    where = tmp_path / "u2net"
    monkeypatch.setattr(segmentation_u2net, "model_dir", lambda: where)
    return where


@pytest.fixture
def no_runtime(monkeypatch):
    """`import onnxruntime` raises, the way it does on a machine without the package."""
    monkeypatch.setitem(sys.modules, "onnxruntime", None)


@pytest.fixture
def runtime_here(monkeypatch):
    monkeypatch.setitem(sys.modules, "onnxruntime", types.ModuleType("onnxruntime"))


# --- the pin -----------------------------------------------------------------

def test_the_pin_is_a_whole_pin():
    spec = segmentation_u2net.MODEL
    assert spec.filename == "u2net.onnx"
    assert spec.size_bytes == 175_997_641, "the size the release served, read 2026-10-02"
    assert len(spec.sha256) == 64 and int(spec.sha256, 16) >= 0
    assert spec.sha256 == "8d10d2f3bb75ae3b6d527c77944fc5e7dcd94b29809d47a739a7a728a912b491"
    assert spec.base_url == "https://github.com/danielgatis/rembg/releases/download/v0.0.0"
    assert "Apache-2.0" in spec.note


def test_the_model_is_fetched_through_the_shared_verified_downloader(u2net_dir, runtime_here,
                                                                     monkeypatch):
    """`stt_provision.install_verified`, not a second downloader with looser rules."""
    monkeypatch.setattr(segmentation_u2net, "MODEL", _spec())
    written = segmentation_u2net.provision(config=_Consenting(), transport=_serve(PAYLOAD))
    assert written == u2net_dir / "u2net.onnx"
    assert written.read_bytes() == PAYLOAD
    assert segmentation_u2net.installed() and segmentation_u2net.available()


def test_wrong_bytes_install_nothing_at_all(u2net_dir, monkeypatch):
    """The negative control: a tampered download must leave no file to be loaded."""
    monkeypatch.setattr(segmentation_u2net, "MODEL", _spec())
    tampered = bytearray(PAYLOAD)
    tampered[10] ^= 0x01
    with pytest.raises(DigestMismatch):
        segmentation_u2net.provision(config=_Consenting(), transport=_serve(bytes(tampered)))
    assert not segmentation_u2net.installed()
    assert list(u2net_dir.iterdir()) == [], "a partial file is still a file the reader will find"


# --- each missing thing says its own name ------------------------------------

def test_onnxruntime_missing_is_named_as_onnxruntime_and_nothing_else(no_runtime, u2net_dir,
                                                                       monkeypatch):
    monkeypatch.setattr(segmentation_u2net, "MODEL", _spec())
    u2net_dir.mkdir(parents=True)
    (u2net_dir / "u2net.onnx").write_bytes(PAYLOAD)
    said = segmentation_u2net.problem()
    assert "onnxruntime" in said and "python-onnxruntime-cpu" in said
    assert "u2net.onnx" not in said, "the model is present and must not be blamed"


def test_a_missing_model_is_named_as_the_model_and_nothing_else(runtime_here, u2net_dir):
    said = segmentation_u2net.problem()
    assert "u2net.onnx" in said and "More -> Photos and videos" in said
    assert "onnxruntime" not in said, "the runtime is importable and must not be blamed"


def test_both_missing_names_both(no_runtime, u2net_dir):
    said = segmentation_u2net.problem()
    assert "onnxruntime" in said and "u2net.onnx" in said


def test_a_model_that_does_not_match_its_pin_is_refused_not_loaded(runtime_here, u2net_dir,
                                                                  monkeypatch):
    monkeypatch.setattr(segmentation_u2net, "MODEL", _spec())
    u2net_dir.mkdir(parents=True)
    (u2net_dir / "u2net.onnx").write_bytes(b"x" * len(PAYLOAD))
    said = segmentation_u2net.problem()
    assert "pinned digest" in said and hashlib.sha256(b"x" * len(PAYLOAD)).hexdigest()[:16] in said
    assert "onnxruntime" not in said
    assert not segmentation_u2net.available()


def test_a_partial_model_is_reported_as_partial_not_as_a_digest_problem(runtime_here, u2net_dir,
                                                                       monkeypatch):
    monkeypatch.setattr(segmentation_u2net, "MODEL", _spec())
    u2net_dir.mkdir(parents=True)
    (u2net_dir / "u2net.onnx").write_bytes(PAYLOAD[:20])
    said = segmentation_u2net.problem()
    assert "partial download" in said and str(len(PAYLOAD)) in said


def test_a_ceiling_below_what_the_model_needs_is_a_named_condition(monkeypatch, runtime_here,
                                                                   u2net_dir):
    """A skill call runs under the profile's 512 MiB RLIMIT_AS; u2net needs about 1 GiB.

    Asserted by patching the limit, which is where it is read - the same rule the
    repo applies after the `sandbox` package split moved the code and left two
    tests patching a copy nobody reads.
    """
    monkeypatch.setattr(segmentation_u2net, "_soft_address_space", lambda: 512 << 20)
    u2net_dir.mkdir(parents=True)
    (u2net_dir / "u2net.onnx").write_bytes(PAYLOAD)
    monkeypatch.setattr(segmentation_u2net, "MODEL", _spec())
    assert segmentation_u2net.address_space_limited()
    said = segmentation_u2net.problem()
    assert "1024 MiB of address space" in said and "capped at 512 MiB" in said
    assert not segmentation_u2net.available()
    monkeypatch.setattr(segmentation_u2net, "_soft_address_space",
                        lambda: segmentation_u2net.ADDRESS_SPACE_BYTES)
    assert not segmentation_u2net.address_space_limited() and segmentation_u2net.available()


def test_the_control_whoever_it_is_the_object_path_never_falls_back_to_the_people_model(
        monkeypatch, no_runtime):
    """FAILS if the general path ever answers with PP-HumanSeg.

    Two nets, because a fallback can be written in either place: the sentinel
    catches one that runs, and the source check catches one that is only reached
    after the refusal. Each was confirmed by mutating `matting_cutout.cut_out`
    and watching this fail.
    """
    from shani_chronoa.opencv import detect, effects

    people = []
    monkeypatch.setattr(effects, "remove_background",
                        lambda image: people.append("remove_background") or image)
    monkeypatch.setattr(detect, "people_mask", lambda image: people.append("people_mask") or image)
    monkeypatch.setattr(segmentation_u2net, "problem", lambda: "onnxruntime for Python is not installed")

    with pytest.raises(matting_cutout.CutoutUnavailable) as raised:
        matting_cutout.cut_out(object(), "object")
    assert "onnxruntime" in str(raised.value)
    assert people == [], "the people model was asked for a non-person subject"

    source = inspect.getsource(matting_cutout.cut_out)
    tail = source.split("raise CutoutUnavailable", 1)[-1]
    assert "remove_background" not in tail and "people_mask" not in tail, \
        "the object path answers with the people model after being told it cannot"


def test_the_person_path_is_still_pphumanseg_and_needs_no_extra(monkeypatch, no_runtime):
    """The people path must not start failing because the optional extra is absent."""
    from shani_chronoa.opencv import effects

    seen = []
    monkeypatch.setattr(effects, "remove_background", lambda image: seen.append(image) or "cut")
    assert matting_cutout.cut_out("image", "person") == "cut"
    assert seen == ["image"]
    assert matting_cutout.model_of("person") == "PP-HumanSeg"
    assert matting_cutout.model_of("object") == "u2net"
    with pytest.raises(ValueError, match="subject must be"):
        matting_cutout.cut_out("image", "dog")


def test_the_model_is_never_imported_by_the_people_path():
    """Structural: this module names nothing from the people path in code, only in prose.

    Checked against the parsed code rather than the file text, because the module
    docstring has to be able to *say* `people_mask` to explain what it replaced.
    """
    tree = ast.parse(inspect.getsource(segmentation_u2net))
    named = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    named |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert not named & {"people_mask", "remove_background", "detect", "effects"}, \
        "the general path must not reach into the people path, even once"


# --- the shape of the module's own contract ----------------------------------

def test_the_runtime_error_names_the_package_for_both_packaged_distsributions(no_runtime):
    with pytest.raises(ImportError) as raised:
        segmentation_u2net.runtime()
    assert "python-onnxruntime-cpu" in str(raised.value)
    assert "python3-onnxruntime" in str(raised.value)


def test_a_session_is_built_once_and_can_be_dropped(u2net_dir, monkeypatch):
    """176 MB of weights are loaded once per process, not once per call."""
    built = []

    class _Session:
        def get_inputs(self):
            return [types.SimpleNamespace(name="input.1")]

    fake = types.ModuleType("onnxruntime")
    fake.InferenceSession = lambda *a, **k: (built.append(a[0]), _Session())[1]
    fake.SessionOptions = lambda: types.SimpleNamespace(intra_op_num_threads=0,
                                                        inter_op_num_threads=0,
                                                        execution_mode=None,
                                                        enable_cpu_mem_arena=True)
    fake.ExecutionMode = types.SimpleNamespace(ORT_SEQUENTIAL="sequential")
    monkeypatch.setitem(sys.modules, "onnxruntime", fake)
    monkeypatch.setattr(segmentation_u2net, "MODEL", _spec())
    u2net_dir.mkdir(parents=True)
    (u2net_dir / "u2net.onnx").write_bytes(PAYLOAD)

    segmentation_u2net.reset()
    first = segmentation_u2net._session()
    assert segmentation_u2net._session() is first
    assert built == [str(u2net_dir / "u2net.onnx")]
    segmentation_u2net.reset()
    assert segmentation_u2net._session() is not first and len(built) == 2
    segmentation_u2net.reset()


def test_the_model_is_fed_at_its_own_input_size_with_imagenet_normalisation():
    """320x320 is the graph's input, and the normalisation is u2net's training recipe.

    Not decoration: a 640x640 feed is refused by the session, and ImageNet
    statistics on an ImageNet-trained saliency model are not interchangeable with
    any other pair of numbers.
    """
    assert segmentation_u2net.INPUT_SIZE == 320
    assert segmentation_u2net._MEAN == (0.485, 0.456, 0.406)
    assert segmentation_u2net._STD == (0.229, 0.224, 0.225)
    assert segmentation_u2net.ADDRESS_SPACE_BYTES >= 1 << 30, "the measured ceiling, not a guess"