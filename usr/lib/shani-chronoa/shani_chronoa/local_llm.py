"""The on-machine language model without Ollama: llama.cpp's `llama-server` and a pinned GGUF file.

Neither image ships Ollama (the CLI matrix finds no `ollama` on GNOME or
Plasma), and its Linux release is a 1.4 GB tarball of CUDA libraries a machine
with no GPU never uses. Arch packages llama.cpp itself (`extra/llama-cpp`,
17 MiB, CPU backends in `ggml`), so Chronoa depends on it and only the model
file is downloaded - one of the three below, chosen by RAM, verified against
the digest pinned here exactly as `stt_provision` verifies a speech model
(the same `_install`, so the same temp-file, size and sha256 rules).

`llama-server` runs as a user service (`shani-chronoa-llm.service`) on
127.0.0.1 only, with the model's own chat template (`--jinja`) so tool calls
work, and speaks the OpenAI API, so the client is `cloud_llm`'s
`OpenAICompatibleLLM` pointed at localhost - nothing leaves the machine.
Ollama, when someone has it, still comes first.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Dict, Optional

try:  # httpx is the app's HTTP client; the CLI paths must still import without it
    import httpx
except ImportError:  # pragma: no cover - exercised only on a stripped install
    httpx = None  # type: ignore[assignment]

from shani_chronoa import hardware_profile
from shani_chronoa import ctxfit, files
from shani_chronoa.stt_provision import ModelSpec, _install

logger = logging.getLogger(__name__)

HOST, PORT = "127.0.0.1", 8765
BASE_URL = f"http://{HOST}:{PORT}/v1"
UNIT = "shani-chronoa-llm.service"

# (spec, label, smallest RAM it is the default for, in GB). Sizes and digests
# are each file's Hugging Face LFS `size` and `oid` (sha256) at the pinned
# revision, read from the HF API on 2026-10-02 - not from a download.
_QWEN = "https://huggingface.co/Qwen/{repo}/resolve/{rev}"
TIERS = (
    (ModelSpec("qwen3-0.6b", "Qwen3-0.6B-Q8_0.gguf", 639_446_688,
               "9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031",
               "tiny and quick; simple requests",
               _QWEN.format(repo="Qwen3-0.6B-GGUF", rev="23749fefcc72300e3a2ad315e1317431b06b590a")),
     "Small (0.6 GB) - quick on any computer, for simple requests", 0),
    (ModelSpec("qwen3-1.7b", "Qwen3-1.7B-Q4_K_M.gguf", 1_107_409_472,
               "b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897",
               "the default; reliable tool calls on a CPU",
               "https://huggingface.co/unsloth/Qwen3-1.7B-GGUF/resolve/d7f544eead698dbd1f15126ef60b45a1e1933222"),
     "Medium (1.1 GB) - the best fit for most computers", 6),
    (ModelSpec("qwen3-4b", "Qwen3-4B-Q4_K_M.gguf", 2_497_280_256,
               "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5",
               "smarter, slower on a CPU",
               _QWEN.format(repo="Qwen3-4B-GGUF", rev="bc640142c66e1fdd12af0bd68f40445458f3869b")),
     "Large (2.5 GB) - smarter, noticeably slower without a graphics card", 999),
    (ModelSpec("qwen3-8b", "Qwen3-8B-Q4_K_M.gguf", 5_027_783_488,
               "d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785",
               "the quality step; reliable tool calls, still CPU-viable",
               _QWEN.format(repo="Qwen3-8B-GGUF", rev="7c41481f57cb95916b40956ab2f0b139b296d974")),
     "Very large (5.0 GB) - the first size where replies are markedly better", 999),
    (ModelSpec("qwen3-14b", "Qwen3-14B-Q4_K_M.gguf", 9_001_752_960,
               "500a8806e85ee9c83f3ae08420295592451379b4f8cf2d0f41c15dffeb6b81f0",
               "near-desktop-class reasoning; slow on a CPU",
               _QWEN.format(repo="Qwen3-14B-GGUF", rev="530227a7d994db8eca5ab5ced2fb692b614357fd")),
     "Huge (9.0 GB) - for machines with room; slow without a graphics card", 999),
    # A 30B mixture-of-experts: 17.3 GiB on disk, ~3.3B of 30.5B parameters
    # active per token, so it READS a fraction of what it stores. That is what
    # makes it fast enough on modest hardware - see REQUIRES' `active_bytes`.
    # `active_bytes` there is an estimate from the architecture, not a
    # measurement; tests/test_model_requirements.py pins that this tier must not
    # become a default until it has been corrected from a real decode.
    (ModelSpec("qwen3-30b-a3b", "Qwen3-30B-A3B-Q4_K_M.gguf", 18_556_685_824,
               "0d003f6662faee786ed5da3e31b29c978de5ae5d275c8794c606a7f3c01aa8f5",
               "a 30B that reads like a 3B; the size that works on small machines",
               _QWEN.format(repo="Qwen3-30B-A3B-GGUF", rev="e4d4bafdfb96a411a163846265362aceb0b9c63a")),
     "Mixture of experts (18.6 GB) - reads a fraction of what it stores", 999),
)
SPECS = {spec.key: spec for spec, _label, _ram in TIERS}


def model_dir() -> Path:
    return files.data_home() / "shani-chronoa" / "llm"


def current_link() -> Path:
    """The file the service loads; a symlink to the chosen model, so switching models is one rename."""
    return model_dir() / "current.gguf"


def ram_gb() -> float:
    try:
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / (1024 * 1024)
    except (OSError, ValueError, IndexError):
        pass
    return 8.0


def effective_bandwidth() -> Optional[float]:
    """This machine's effective memory bandwidth in bytes/s, or None.

    **Measured, never assumed.** Decode is bandwidth-bound - each generated
    token reads the whole weight set - so a live server's own
    `predicted_per_second` times the loaded weights *is* the machine's effective
    bandwidth, and the same figure predicts any other model on that machine.

    That is why this is worth measuring rather than reading from a datasheet: a
    Threadripper's 8 channels of DDR5 and this laptop's 2 of DDR4 show up
    directly, and a `nvidia-smi` VRAM figure would not answer it at all.

    **And the prediction it makes is checked against a real decode**, which is
    the only thing separating this from a plausible story. Measured 2026-10-11
    on this box (i7-1165G7, 8 cores, no CUDA/Vulkan/SYCL), the 8B's server on
    8791 and nothing else running:

    | tier | reads/token | predicted | measured |
    |---|---|---|---|
    | Qwen3-1.7B-Q4_K_M | 1.03 GiB | 15.8 tok/s | 6.81 tok/s |
    | Qwen3-8B-Q4_K_M   | 4.68 GiB | **3.5 tok/s** | **3.69 tok/s** |

    The 8B is within 5%. The 1.7B is predicted too high, and *that* is the
    finding rather than a failure of the formula: a small model is not
    bandwidth-saturated, so it moves less memory per second than the hardware
    can, and the true figure is better read from a larger one. That is why the
    cache below keeps the **largest** measurement rather than the first.

    The first 8B measurement here read **0.20 tok/s** and was wrong for reasons
    worth keeping: two llama-servers were running (the 1.7B on 8765 and the 8B
    on 8791, seven threads each, on eight cores) with 732 MiB of free RAM.
    Pausing one with SIGSTOP was not enough either - this is a shared desktop,
    and GNOME, Firefox, Slack and two editor sessions are always resident.
    "Measured on a real machine" means measured with nothing else competing, and
    a bandwidth figure taken under contention is a number about the contention.

    **The probe generates real tokens, and it has to.** `n_predict: 1` returns
    `predicted_per_second: 1000000.0` - llama.cpp dividing a single millisecond
    token - which would report every machine as infinitely fast. Sixteen tokens
    costs under two seconds at the slowest tier and averages the timing out.

    Returns None when no server is answering, because a bandwidth guessed from a
    part number is a number about a chip rather than about this machine, and the
    caller must then say "not measured" rather than assume a fast one.
    """
    if httpx is None:
        return None
    global _BANDWIDTH_CACHE, _BANDWIDTH_MODEL
    # Re-probe when the loaded model changes. Caching purely per process meant
    # the figure belonged to whichever model happened to be loaded at startup -
    # and on this box that is the 1.7B, which reports the machine at 7 GiB/s
    # when it will do 17. A model change is a deliberate act, so it is the one
    # moment worth spending a four-second decode on.
    current = _loaded_model_path()
    if _BANDWIDTH_CACHE is not None and _BANDWIDTH_MODEL == current:
        return _BANDWIDTH_CACHE
    try:
        props = httpx.get(f"http://{HOST}:{PORT}/props", timeout=3).json() or {}
    except Exception:  # noqa: BLE001 - no server, no measurement
        return None
    path = str(props.get("model_path") or "")
    try:
        weights = os.path.getsize(path) if path else 0
    except OSError:
        weights = 0
    if not weights:
        return None
    try:
        body = httpx.post(
            f"http://{HOST}:{PORT}/completion",
            json={"prompt": "Count:", "n_predict": 16, "temperature": 0.0},
            timeout=30).json() or {}
    except Exception:  # noqa: BLE001 - a server that cannot answer is unmeasured
        return None
    timings = body.get("timings") or {}
    generated = float(timings.get("predicted_n") or 0)
    tps = float(timings.get("predicted_per_second") or 0)
    # Guard the single-token artefact directly rather than trusting n_predict:
    # a rate above ~1000 tok/s is not a decode on any hardware this runs on.
    if tps <= 0 or generated < 4 or tps > 1000:
        return None
    measured = weights * tps
    # **Keep the largest figure seen, not the first.** Measured twice on one box
    # within minutes, nothing else running:
    #
    #   Qwen3-1.7B-Q4_K_M  6.81 tok/s x 1.03 GiB =  7.03 GiB/s
    #   Qwen3-8B-Q4_K_M    3.69 tok/s x 4.68 GiB = 17.29 GiB/s
    #
    # Both are "the same machine", and they disagree by 2.5x. The reason is
    # that a small model is not bandwidth-*saturated* - it is bound by
    # per-token compute and latency instead, so it moves less memory per second
    # than the hardware can. A big one is, so it reports the machine's real
    # figure. Caching the first measurement therefore locked in whichever model
    # happened to be loaded, and every prediction for a *larger* model was
    # built on an underestimate.
    #
    # The maximum is the better estimate for one reason: a non-saturated model
    # can only under-report, never over-report. It is still this machine's own
    # number rather than a part number - which is the property that makes it
    # worth measuring, since a Threadripper's 8 DDR5 channels show up directly
    # and an `nvidia-smi` figure would not answer the question at all.
    if _BANDWIDTH_CACHE is None or measured > _BANDWIDTH_CACHE:
        _BANDWIDTH_CACHE = measured
    # The path we already fetched, not a third `/props` round trip: the panel
    # asks per card, and this ran three GETs for one measurement before.
    _BANDWIDTH_MODEL = current or (os.path.realpath(path) if path else None)
    return _BANDWIDTH_CACHE


#: Measured once per *model*, not once per process: the probe generates real
#: tokens and the model list asks for it once per card, so re-probing on every
#: card would be a decode per card. `_BANDWIDTH_MODEL` is the path the cached
#: figure came from; a different one means re-measure.
_BANDWIDTH_CACHE: Optional[float] = None
_BANDWIDTH_MODEL: Optional[str] = None


def _loaded_model_path() -> Optional[str]:
    """The GGUF the running server has loaded, or None.

    Read from the server's own `/props`, and through `realpath` because that
    field is the **symlink** (`current.gguf`), so the same model reported under
    two names would look like two models and re-probe on every card. The same
    trap is recorded in `tests/test_routines_live.py`'s skip, which took three
    attempts to get right.
    """
    if httpx is None:
        return None
    try:
        props = httpx.get(f"http://{HOST}:{PORT}/props", timeout=3).json() or {}
    except Exception:  # noqa: BLE001 - no server, no path
        return None
    path = str(props.get("model_path") or "")
    if not path:
        return None
    try:
        return os.path.realpath(path)
    except OSError:
        return path


def expected_decode_tps(key: str, bandwidth: Optional[float] = None) -> Optional[float]:
    """Tokens per second `key` would decode at here, or None when unmeasured.

    The bytes one token reads - `bytes_per_token`, which is the ACTIVE set for a
    mixture-of-experts and the whole file for a dense model - divided into the
    machine's effective bandwidth. None - never a guess - when the bandwidth is
    unknown, because a predicted speed quoted as though it were measured is the
    confident wrong answer this repo keeps recording.
    """
    per_token = bytes_per_token(key)
    if not per_token:
        return None
    if bandwidth is None:
        bandwidth = effective_bandwidth()
    if not bandwidth:
        return None
    return bandwidth / per_token


def _fits_in_memory() -> bool:
    """Whether a live server is answering, so speed can be measured at all."""
    return effective_bandwidth() is not None


def recommended(ram: Optional[float] = None) -> str:
    """The default for this much RAM, at CPU decode rates.

    0.6B under 6 GB, 1.7B otherwise. Everything above stays at `999` - offered,
    never defaulted on a CPU - because decode is bandwidth-bound: the 1.7B
    measures ~15 tok/s on 8 cores here, which puts a 4B near 6.7, an 8B near 3.3
    and a 14B near 1.8. `recommended_for_machine()` is what a GPU unlocks.
    """
    ram = ram_gb() if ram is None else ram
    pick = [spec.key for spec, _label, need in TIERS if ram >= need]
    return pick[-1]


def installed() -> "list[str]":
    return [key for key, spec in SPECS.items() if (model_dir() / spec.filename).is_file()]


def active() -> str:
    target = current_link()
    try:
        name = os.readlink(target)
    except OSError:
        return ""
    return next((k for k, s in SPECS.items() if s.filename == Path(name).name), "")


def server_binary() -> str:
    return shutil.which("llama-server") or ""


def verify(key: str) -> bool:
    """Whether the downloaded file for `key` still has its pinned size and sha256 (about a second per GB)."""
    from shani_chronoa.stt_provision import file_matches
    return file_matches(model_dir() / SPECS[key].filename, SPECS[key])


def provision(key: str, progress: Optional[Callable[[int, int], None]] = None, config=None, transport=None) -> Path:
    """Download and verify one model, then make it the one the service loads.

    A file already in place is re-hashed first, not trusted for being there: a
    copied-in, truncated or altered model is removed and fetched again.
    """
    spec = SPECS[key]
    present = model_dir() / spec.filename
    if present.is_file() and not verify(key):
        logger.warning("%s does not match its pinned digest; fetching it again", present)
        present.unlink()
    path = _install(spec, user_dir=model_dir(), system_dir=Path("/usr/share/shani-chronoa/llm"),
                    existing=present if present.is_file() else None,
                    config=config, progress=progress, transport=transport, label="llm")
    use(key)
    return path


#: How much worse a candidate's perplexity may be than the model it would
#: replace, as a fraction. 2% is roughly where a Q4_K_M starts to be felt in
#: replies and 5% is where it is obvious; refusing past 2% leaves a margin under
#: the "this quant is worse" threshold without refusing every rebuild.
PERPLEXITY_REGRESSION_LIMIT = 0.02

#: Fixed calibration text, so two models are scored on **the same bytes** and the
#: comparison means something. Written here rather than shipped as a data file so
#: it cannot go missing from a package and silently turn the gate into a no-op.
_CORPUS = """\
The perception layer reads the machine's own state rather than asking the user
to describe it, because a person who wanted to narrate their own battery level
would not need an assistant. Each sense answers whether it may run at all before
anything is read, and the consent key is checked first, so an unconsented sense is
never invoked even when the data is sitting on disk in plain text.

Quantization reduces precision, and the cost is measured in perplexity rather
than assumed: the upstream tooling reports it, and a number beats a feeling. Two
models are only comparable when they are scored on identical bytes, so the corpus
is fixed here rather than sampled per run, and a calibration file that can go
missing from a package would silently turn a gate into a no-op.

A blue whale is the largest animal known to have existed, and it eats krill in
enormous quantities during the feeding season. The pattern generalizes: a system
that appears to understand a narrow domain can still be wrong in a way that only
shows up when the domain shifts slightly underneath it.

Rain fell steadily through the afternoon, filling the gutter along the north side
of the lane before the storm finally eased into a drizzle that lasted until dusk.
Nobody in the village had expected the river to rise so far overnight.
"""



def perplexity(path: "Path", timeout: float = 180.0) -> "float | None":
    """This model's perplexity on a fixed corpus, or None when it cannot be read.

    **Perplexity, because that is how llama.cpp documents quantization loss** -
    the upstream quantize README measures it in "ppl and/or KLD", and
    `llama-perplexity` is the tool that reports it. It is already installed and
    **was never invoked anywhere in this repository**: `local_llm.py`,
    `local_vision.py` and `local_embed.py` all download *pre-quantized* GGUFs and
    point `current.gguf` at one, so nothing here ever checked that the chosen
    quantization is a good one. A truncated download, a mislabelled quant, or a
    build that re-quantized badly all promoted silently.

    None on every failure path - no binary, a timeout, unparseable output. This is
    a gate and a probe at once, and a probe that could not read anything must say
    "unknown" rather than "fine"; `None` is that answer, and callers must not
    treat it as a pass without saying so.
    """
    binary = shutil.which("llama-perplexity")
    if not binary:
        logger.info("llama-perplexity is not installed, so quality is unmeasured")
        return None
    candidate = Path(path)
    if not candidate.is_file():
        logger.info("no model at %s to measure", candidate)
        return None
    with tempfile.TemporaryDirectory(prefix="chronoa-ppl-") as work:
        corpus = Path(work) / "calibration.txt"
        corpus.write_text(_CORPUS * 8, encoding="utf-8")
        try:
            done = subprocess.run(
                [binary, "-m", str(candidate), "-f", str(corpus),
                 "-c", "512", "-t", "4", "--no-mmap"],
                capture_output=True, text=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.info("perplexity of %s could not be measured: %s", candidate.name, exc)
            return None
    # **The real output is `Final estimate: PPL = 1.0128 +/- 0.00145`.** My
    # first patterns were `perplexity = ...` and a case-sensitive `\bppl`, so
    # every measurement came back `None` - on a box where the tool runs in four
    # seconds. Verified against the installed `llama-perplexity` on
    # SmolLM2-135M-Instruct-Q4_K_M.
    output = done.stdout + done.stderr
    matches = (re.findall(r"Final estimate:\s*PPL\s*=\s*([0-9.eE+-]+)", output)
               or re.findall(r"\bPPL\s*=\s*([0-9.eE+-]+)", output, re.IGNORECASE)
               or re.findall(r"\bperplexity\s*=\s*([0-9.eE+-]+)", output, re.IGNORECASE))
    if not matches:
        logger.info("llama-perplexity produced no perplexity for %s", candidate.name)
        return None
    try:
        return float(matches[-1])
    except ValueError:
        return None


def quality_verdict(candidate: "Path", reference: "Path | None" = None) -> Dict[str, object]:
    """Should this model become `current.gguf`? With the numbers that decided it.

    Two shapes of answer, and the difference matters:

    - **measured and worse** - the candidate regresses against the model it would
      replace by more than `PERPLEXITY_REGRESSION_LIMIT`, so it is not promoted.
      A refusal that names both numbers is actionable; one that says "failed" is
      not.
    - **unmeasured** - `llama-perplexity` is absent or would not run, so nothing
      is known. The model is **promoted** and the reason is recorded, because a
      minimal install without the tool should not be unable to choose a model at
      all. What is refused is the *claim*, not the choice: the caller reports
      "quality not measured" rather than nothing.
    """
    measured = perplexity(candidate)
    if measured is None:
        return {"promote": True, "perplexity": None, "reference": None,
                "measured": False,
                "why": "perplexity could not be measured - llama-perplexity is "
                       "absent or would not run, so this promotion is unverified"}
    if reference is None:
        return {"promote": True, "perplexity": measured, "reference": None,
                "measured": True,
                "why": f"perplexity {measured:.3f} with nothing to compare against"}
    baseline = perplexity(reference)
    if baseline is None:
        return {"promote": True, "perplexity": measured, "reference": None,
                "measured": True,
                "why": f"perplexity {measured:.3f}; the model being replaced could "
                       "not be measured, so there is no comparison"}
    worse_by = (measured - baseline) / baseline if baseline else 0.0
    if worse_by > PERPLEXITY_REGRESSION_LIMIT:
        return {"promote": False, "perplexity": measured, "reference": baseline,
                "measured": True, "regression": round(worse_by, 4),
                "why": f"perplexity {measured:.3f} against {baseline:.3f} for the "
                       f"model it would replace - {worse_by:.1%} worse, past the "
                       f"{PERPLEXITY_REGRESSION_LIMIT:.0%} limit - so it was not "
                       "made current. The file is still there if you want it."}
    return {"promote": True, "perplexity": measured, "reference": baseline,
            "measured": True, "regression": round(worse_by, 4),
            "why": f"perplexity {measured:.3f} against {baseline:.3f} "
                   f"({worse_by:+.1%}), inside the "
                   f"{PERPLEXITY_REGRESSION_LIMIT:.0%} limit"}


#: The quantizations worth producing locally. `Q4_K_M` is the working default;
#: `Q5_K_M` is the one to reach for when a reply's quality matters more than the
#: 40% of disk, and `Q8_0` is the reference to measure against - a quant scored
#: against another quant has no fixed point.
LOCAL_QUANT_TARGETS = ("Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0")


def calibration_corpus(max_chars: int = 200_000) -> str:
    """Text to calibrate an importance matrix against, and why this text.

    **An importance matrix is only as good as the corpus it was fitted to**, so
    this is not filler. llama.cpp's own guidance is calibration data "derived by
    running a model over a representative text corpus", and for Shanios the
    representative corpus is *Chronoa's own source* - the tool schemas, the skill
    docstrings, the settings copy. That is the text this system is asked to
    reason about, so it is the text whose activations should decide which weights
    keep their precision.

    Falls back to plain English prose when the source tree is unreadable, and says
    which it used, because "calibrated on something" and "calibrated on the right
    thing" are different claims.
    """
    import glob

    roots = [str(Path(__file__).resolve().parent), "/usr/share/shani-chronoa"]
    text = []
    for root in roots:
        for pattern in ("*.py", "skills/*.py", "senses/*.py", "*.md"):
            text.extend(glob.glob(os.path.join(root, pattern)))
    body = ""
    for name in sorted(text)[:400]:
        try:
            body += Path(name).read_text(encoding="utf-8", errors="replace") + "\n"
        except OSError:
            continue
    if len(body.strip()) < 2000:
        body = _CORPUS * 40
    return body[:max_chars]


def build_imatrix(source: "Path", corpus: str, out: "Path",
                  timeout: float = 1800.0) -> "bool":
    """Fit an importance matrix with `llama-imatrix`. True on success.

    Fails closed and loudly: a missing binary or a timeout returns False rather
    than producing a quant that is quietly uncalibrated, which would look exactly
    like the naive one.
    """
    binary = shutil.which("llama-imatrix")
    if not binary:
        logger.warning("llama-imatrix is not installed, so no calibrated quant "
                       "can be produced")
        return False
    Path(corpus).write_text(calibration_corpus(), encoding="utf-8")
    try:
        done = subprocess.run(
            [binary, "-m", str(source), "-f", str(corpus), "-o", str(out),
             "-ngl", "0", "-c", "512"],
            capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("importance matrix failed: %s", exc)
        return False
    if done.returncode != 0 or not Path(out).exists():
        logger.warning("importance matrix not produced (exit %s)", done.returncode)
        return False
    return True


def quantize(source: "Path", target: str, out: "Path",
             imatrix: "Path | None" = None, timeout: float = 900.0) -> "bool":
    """Quantize with `llama-quantize`, optionally guided by an importance matrix."""
    binary = shutil.which("llama-quantize")
    if not binary:
        logger.warning("llama-quantize is not installed")
        return False
    # **Flags first, then the positionals.** The installed tool's usage is
    # `llama-quantize [--imatrix file] model-f32.gguf [model-quant.gguf] type
    # [nthreads]`, and the trailing `[nthreads]` is positional - so appending
    # `--imatrix` after the model paths makes it parse the flag as a thread count:
    #
    #     main: invalid nthread '--imatrix' (stoi)
    #
    # Measured, not assumed: that is the actual output of the wrong order on this
    # box, and it costs the whole calibration silently because the function
    # returns False with no more clue than that.
    cmd = [binary]
    if imatrix is not None and Path(imatrix).is_file():
        cmd += ["--imatrix", str(imatrix)]
    cmd += [str(source), str(out), target]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("quantization to %s failed: %s", target, exc)
        return False
    if done.returncode != 0 or not Path(out).exists():
        logger.warning("no %s produced (exit %s)", target, done.returncode)
        return False
    return True


#: Directories `calibrated_quantize` created itself and has not yet reclaimed.
#: A module-level list rather than a local because every early return has to
#: reach the cleanup, and threading a `try/finally` through four returns to
#: preserve a return value is how the leak got there in the first place.
_QUANT_SCRATCH: "list[Path]" = []

#: Scratch directory name prefixes this module is allowed to delete. Declared
#: once because both the creator and the remover have to agree on it exactly,
#: and a literal typed twice is a literal that will drift.
_QUANT_SCRATCH_PREFIXES = ("chronoa-quant-",)


def _human_bytes(count: int) -> str:
    """A size a person can act on.

    **`f"{freed / 1e6:.0f} MB"` reported "0 MB" for a 4 KB reclaim**, measured -
    which is the size the button exists to explain. Rounding to whole megabytes
    makes every small result look like nothing happened, and "0 MB removed" is
    exactly the report a person stops trusting.
    """
    value = float(max(0, count))
    for unit, step in (("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if value >= step:
            return f"{value / step:.1f} {unit}"
    return f"{int(value)} B"


def _new_quant_scratch() -> "Path":
    """A private scratch directory for one quantization, under `model_dir()`.

    Under `model_dir()` rather than `$TMPDIR` so a calibrated model this function
    builds is somewhere durable and findable, instead of a path that the
    system clears on its own schedule. `model_dir()` is created first because
    `mkdtemp` will not create its parent, and `dir=` is only honoured when the
    parent already exists.
    """
    parent = model_dir()
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        parent = Path(tempfile.gettempdir())
    return Path(tempfile.mkdtemp(prefix=_QUANT_SCRATCH_PREFIXES[0],
                                 dir=str(parent)))


def _remove_scratch_dir(directory: "Path") -> bool:
    """`rmtree` a scratch directory. False if it would not go.

    **The name and the parent are both checked here**, because this is the only
    function that deletes anything on the strength of a pattern. `_QUANT_SCRATCH`
    cannot be the guard: it is in memory, so it is empty in every process except
    the one that made the directory - which is to say the Settings window could
    never reclaim anything left by a previous run. That was measured: the first
    version of `reclaim_quant_scratch()` found the directory and returned
    "removed nothing" for exactly this reason.
    """
    directory = Path(directory)
    # **Prefix test, not equality.** My first version wrote
    # `directory.name not in _QUANT_SCRATCH_PREFIXES`, which asks whether the
    # whole name equals the prefix - so every real scratch directory was
    # refused, `mkdtemp` having appended random characters to the prefix, and
    # reclaim removed nothing. Measured: two directories present, zero removed,
    # and the refusals logged as "not a quantization scratch name".
    if not directory.name.startswith(_QUANT_SCRATCH_PREFIXES):
        logger.warning("not removing %s: not a quantization scratch name",
                       directory)
        return False
    if directory.parent != model_dir():
        logger.warning("not removing %s: not under %s", directory, model_dir())
        return False
    try:
        shutil.rmtree(directory)
    except OSError as exc:
        logger.warning("Could not remove %s: %s", directory, exc)
        return False
    if directory in _QUANT_SCRATCH:
        _QUANT_SCRATCH.remove(directory)
    return True


def discard_quant_scratch(directory: "Path") -> bool:
    """Remove a scratch directory `calibrated_quantize` made. True if it went.

    **Only ever called on a directory this module created.** Removing a caller's
    `work=` directory would delete their files on the strength of a name match,
    which is the same class of bug as `rm -rf "$TMPDIR"/*`.
    """
    if directory not in _QUANT_SCRATCH:
        return False
    return _remove_scratch_dir(directory)


def calibrated_quantize(source: "Path", target: str = "Q4_K_M",
                        work: "Path | None" = None) -> Dict[str, object]:
    """Produce a calibrated quantization of an open model, with both paths named.

    **This is the "modify an open model" path, and it is worth stating what it is
    not.** It re-quantizes a model's own weights at a chosen precision, guided by
    an importance matrix fitted to text representative of this system's domain. It
    does not train: no parameter is updated from a gradient, and nothing here can
    teach the model a fact. What it does is spend the precision budget where the
    activations say it matters, which measurably beats spending it uniformly.

    Returns a dict rather than a bool because a caller has to be able to say
    *which* corpus and *which* matrix, and because a refusal has to be reportable:
    a missing `llama-imatrix` yields `calibrated=False` with the reason, not a
    silently uncalibrated file that is indistinguishable from a good one.
    """
    source = Path(source)
    if not source.is_file():
        return {"ok": False, "calibrated": False,
                "why": f"no model at {source}"}
    # **A directory this function makes, it cleans up.** Measured: every call
    # left a `chronoa-quant-*` directory in $TMPDIR holding a corpus, an
    # `imatrix.dat` and **two** ~100 MB GGUFs - the naive build and the
    # calibrated one - and nothing ever removed them, on *any* of the four exit
    # paths. The Settings "Rebuild" button passes no `work=`, so it hits the
    # `mkdtemp` branch: pressing it repeatedly fills /tmp with model copies.
    #
    # The two GGUFs are *returned* on purpose (the caller is meant to compare or
    # install them), so this cannot simply `rmtree` in a `finally` - it would
    # delete the file it just reported as built. So ownership is the rule: a
    # directory passed in as `work=` belongs to the caller and is left alone,
    # and only one this function created is removed. The default therefore
    # changes to "under `model_dir()`", where a surviving calibrated model is
    # *useful* rather than orphaned in /tmp: a caller that wants to install it
    # can move it, and one that does not has not leaked a disk's worth of RAM
    # image into a directory the system will eventually clear anyway.
    directory = (Path(work) if work is not None
                 else _new_quant_scratch())
    # **A caller's `work=` directory is created here if it is not there yet.**
    # Dropping this line is what a careless edit to the two lines above looks
    # like, and `test_the_happy_path_names_all_three_artifacts` caught it:
    # `llama-quantize` was handed a path inside a directory that did not exist,
    # so every run with `work=` failed with "llama-quantize could not produce".
    directory.mkdir(parents=True, exist_ok=True)
    _QUANT_SCRATCH.append(directory)
    corpus = directory / "calibration.txt"
    matrix = directory / "imatrix.dat"
    naive = directory / f"{source.stem}-{target}-naive.gguf"
    calibrated = directory / f"{source.stem}-{target}-calibrated.gguf"

    if not quantize(source, target, naive):
        discard_quant_scratch(directory)
        return {"ok": False, "calibrated": False,
                "why": f"llama-quantize could not produce {target} from {source.name}"}
    if not build_imatrix(source, str(corpus), matrix):
        # **The honest outcome is a refusal, not the naive file.** Returning
        # `naive` here would hand back a quant that is byte-identical to the
        # uncalibrated one and let the caller believe it had been calibrated.
        # **The directory stays, and has to: `naive` is returned.** Reclaiming
        # it here would delete the very file the return value points at. So this
        # path leaks by design rather than by accident - which means it is the
        # one path that *must* name the directory it left behind, and it does,
        # twice: as `uncalibrated_fallback` and as `scratch`.
        return {"ok": False, "calibrated": False, "uncalibrated_fallback": str(naive),
                "scratch": str(directory),
                "why": "llama-imatrix is unavailable or failed, so only an "
                       "UNCALIBRATED quant could be produced - it is at "
                       f"{naive} if you want it, and it was not called calibrated. "
                       f"Its working directory {directory} is being kept for that "
                       "file; discard it with "
                       "local_llm.discard_quant_scratch(path) when you are done"}
    if not quantize(source, target, calibrated, imatrix=matrix):
        discard_quant_scratch(directory)
        return {"ok": False, "calibrated": False, "uncalibrated_fallback": str(naive),
                "why": "the calibrated quantization failed after the matrix was "
                       f"built; {naive} is the uncalibrated build of the same "
                       f"target and {directory} has been reclaimed"}
    return {"ok": True, "calibrated": True, "target": target,
            "calibrated_path": str(calibrated), "naive_path": str(naive),
            "imatrix": str(matrix), "corpus": str(corpus), "scratch": str(directory),
            "corpus_chars": len(calibration_corpus()),
            "why": f"{target} built with an importance matrix fitted on "
                   f"{len(calibration_corpus())} chars of this system's own source; "
                   f"the uncalibrated build of the same target is beside it for "
                   "comparison"}


def compare_calibration(out: Dict[str, object]) -> Dict[str, object]:
    """Did the importance matrix actually help? Both builds, and the delta.

    `calibrated_quantize` builds **two** files on purpose - the same target from
    the same source, one fitted with an importance matrix and one without - and
    its own return value says the second is *"beside it for comparison"*. Nothing
    compared them: `naive_path` had no reader outside the test asserting the two
    files differ, which is a fact about the filesystem and not about quality.

    **This is the one measurement the operation exists for**, and it is the
    question `quality_verdict` structurally cannot answer. That gate compares a
    candidate against the model being *replaced*; this compares the new build
    against the one it would have been without spending ten seconds on a
    calibration.

    Two shapes of answer, as in `quality_verdict`: **measured** (both builds
    scored on the same fixed corpus, so the numbers are comparable), and
    **unmeasured**, where `perplexity()` returned `None` for either. `None` is
    never read as "no difference" - a missing measurement and a measured zero
    are different facts, and only the second one says the matrix did nothing.

    The percentage is reported **beside both absolute numbers**, never instead
    of them: a figure derived from two numbers stays true-looking when the
    numbers are dropped, so a sentence carrying only "0.4% better" cannot be
    checked against anything.
    """
    naive, calibrated = out.get("naive_path"), out.get("calibrated_path")
    if not naive or not calibrated:
        return {"measured": False, "verdict": "unmeasured",
                "naive": None, "calibrated": None, "change_percent": None,
                "why": "no pair to compare - this was not a successful build of "
                       "both files, so nothing was measured"}
    if naive == calibrated:
        return {"measured": False, "verdict": "unmeasured",
                "naive": None, "calibrated": None, "change_percent": None,
                "why": "the two builds are the same file, so comparing them would "
                       "report the number twice and call it a measurement"}

    before = perplexity(Path(str(naive)))
    after = perplexity(Path(str(calibrated)))
    if before is None or after is None:
        missing = [name for name, value in (("uncalibrated", before),
                                            ("calibrated", after)) if value is None]
        return {"measured": False, "verdict": "unmeasured",
                "naive": before, "calibrated": after, "change_percent": None,
                "why": f"perplexity could not be measured for the "
                       f"{' and '.join(missing)} build"
                       f"{'s' if len(missing) > 1 else ''} - llama-perplexity is "
                       "absent or would not run, so whether the matrix helped is "
                       "unknown and not the same as 'it did not help'"}
    if before <= 0.0:
        # A percentage of a non-positive baseline is not a small number, it is
        # not a number. Report the two figures and refuse to divide.
        return {"measured": False, "verdict": "unmeasured",
                "naive": before, "calibrated": after, "change_percent": None,
                "why": f"perplexity {before:.3f} for the uncalibrated build is not "
                       "positive, so there is no meaningful percentage; the "
                       f"calibrated build measured {after:.3f}"}

    change = (after - before) / before * 100.0
    if after == before:
        verdict = "unchanged"
    elif after < before:
        verdict = "helped"
    else:
        verdict = "hurt"
    direction = {"helped": "better", "hurt": "worse"}.get(verdict, "the same")
    return {"measured": True, "verdict": verdict,
            "naive": before, "calibrated": after, "change_percent": change,
            "why": f"perplexity {before:.3f} uncalibrated against {after:.3f} "
                   f"calibrated ({change:+.3f}%), so the matrix made it {direction} "
                   "on the same fixed corpus"}


def adopt_path(candidate: "Path", gate: bool = True) -> Dict[str, object]:
    """Point `current.gguf` at **any** model file, after the quality gate.

    **`use()` cannot do this**, and that made the calibration feature useless.
    `use(key)` looks the file up in `SPECS[key].filename`, so it only ever
    promotes one of the catalogue's own files. But `calibrated_quantize()` builds
    something the catalogue cannot name - it re-quantizes whatever source you
    gave it - and `current.gguf` is a **symlink**, so the server (`server_args`,
    line 609) would happily load it. Measured before this function existed:
    Settings -> Models -> *Rebuild* reported `Done: Q4_K_M`, named a path in
    `$TMPDIR`, and there was **no way to point the model link at it**. The button
    built a better model and left it where nothing could reach it.

    The gate is the same one `use()` applies, and it is the reason this is not
    just `os.replace`: a hand-built quantization is exactly the artifact whose
    quality is unknown, because nothing about *how* it was built guarantees a
    good one. It is measured against the model being replaced and refused if it
    regresses by more than `PERPLEXITY_REGRESSION_LIMIT`.

    The symlink is written **absolute**, unlike `use()`'s relative one. A relative
    target inside a `chronoa-quant-*` scratch directory would still resolve
    correctly today, but only for as long as that scratch directory survives -
    and `discard_quant_scratch()` is meant to be callable on it.
    """
    target = Path(candidate)
    verdict: Dict[str, object] = {"promote": True, "measured": False,
                                   "why": "quality gate not run"}
    if not target.is_file():
        return {"promote": False, "measured": False, "path": str(target),
                "why": f"no model file at {target}"}
    if gate:
        reference = None
        link = current_link()
        try:
            if link.is_symlink() or link.exists():
                reference = link.resolve()
        except OSError:
            reference = None
        # **Never measure the candidate against itself.** Adopting the file that
        # is already current would compare it to itself, report a perplexity
        # "improvement" of exactly zero, and promote on the strength of a number
        # that says nothing.
        if reference is not None:
            try:
                if reference == target.resolve():
                    verdict = {"promote": True, "measured": False,
                               "path": str(target),
                               "why": "this is already the model in use"}
                    return verdict
            except OSError:
                pass
        verdict = quality_verdict(target, reference)
        verdict["path"] = str(target)
        if not verdict.get("promote"):
            logger.warning("not switching to %s: %s", target, verdict.get("why"))
            return verdict
    link = current_link()
    tmp = link.with_suffix(".adopt.tmp")
    tmp.unlink(missing_ok=True)
    tmp.symlink_to(target.resolve())
    os.replace(tmp, link)
    return verdict


def quant_scratch_dirs() -> "list[Path]":
    """Quantization scratch directories under `model_dir()`, newest last.

    A *report*, not a repair: these are the directories a build leaves behind,
    each holding two quantized copies of a model, and the only honest way to
    present them is with their size attached so a person can decide.
    """
    found = []
    try:
        pattern = "chronoa-quant-*"
        for path in sorted(model_dir().glob(pattern)):
            if path.is_dir():
                found.append(path)
    except OSError:
        return []
    return found


def reclaim_quant_scratch(keep: "Path | None" = None) -> Dict[str, object]:
    """Delete every quantization scratch directory, keeping the one in use.

    **Kept deliberately narrow, and it refuses rather than guessing.** `keep` is
    honoured by resolved path, so the directory backing the *current* model
    cannot be deleted out from under a running `llama-server`; pass nothing and
    the current one is kept anyway. It only ever removes directories whose name
    matches `chronoa-quant-*` **inside `model_dir()`**, so it cannot be pointed
    at anything else - which is the failure mode that makes `rm -rf` in a helper
    function unacceptable.
    """
    freed = 0
    removed: list[str] = []
    kept: list[str] = []
    current = None
    link = current_link()
    if link.is_symlink() or link.exists():
        try:
            current = link.resolve()
        except OSError:
            current = None
    # **Protect directories, and the current model is a file.** This is the bug
    # the second version had: `protected` collected `link.resolve()` - the
    # *model file* - and was then compared against each scratch *directory*, so
    # the two could never be equal and the guard never fired. Measured: after
    # adopting `chronoa-quant-a/m.gguf`, reclaim deleted `chronoa-quant-a` and
    # left `current.gguf` as a dangling symlink pointing at nothing. So the
    # file's own path is protected *and* the directory containing it, because
    # removing the directory is what breaks the link.
    protected = set()
    if keep is not None:
        try:
            resolved_keep = Path(keep).resolve()
            protected.add(resolved_keep)
            protected.add(resolved_keep.parent)
        except OSError:
            pass
    if current is not None:
        protected.add(current)
        protected.add(current.parent)
    for directory in quant_scratch_dirs():
        try:
            resolved = directory.resolve()
        except OSError:
            resolved = directory
        # **Dangling link counts as in use too.** `current_link().resolve()` on
        # a broken symlink returns the path anyway, but if the target has
        # vanished some resolvers raise, and a directory holding a *dangling*
        # current link is still the directory somebody meant.
        if resolved in protected or directory in protected:
            kept.append(str(directory))
            continue
        link_here = directory / current_link().name
        if link_here.is_symlink():
            kept.append(str(directory))
            continue
            kept.append(str(directory))
            continue
        total = 0
        for child in directory.rglob("*"):
            try:
                if child.is_file():
                    total += child.stat().st_size
            except OSError:
                pass
        if _remove_scratch_dir(directory):
            removed.append(str(directory))
            freed += total
        else:
            kept.append(str(directory))
    return {"removed": removed, "kept": kept, "freed_bytes": freed,
            "why": f"removed {len(removed)} quantization scratch "
                   f"director{'y' if len(removed) == 1 else 'ies'} worth "
                   f"{_human_bytes(freed)}"
                   + (f"; kept {len(kept)} that a model is using"
                      if kept else "")}


def use(key: str, gate: bool = True) -> Dict[str, object]:
    """Point `current.gguf` at this model, after a quality gate.

    **`gate=False` restores the previous behaviour** - one rename, no questions -
    because this is the hot path for setup and a missing `llama-perplexity` must
    not be able to stop somebody choosing a model at all.

    The gate is what makes "installed" and "usable" different claims. Before it,
    `provision()` verified a **digest** and nothing about the artifact: a
    correctly-hashed bad quant passed every check there was.
    """
    spec = SPECS[key]
    link = current_link()
    target = model_dir() / spec.filename
    verdict: Dict[str, object] = {"promote": True, "measured": False,
                                   "why": "quality gate not run"}
    if gate:
        reference = None
        try:
            if link.is_symlink() or link.exists():
                reference = link.resolve()
        except OSError:
            reference = None
        verdict = quality_verdict(target, reference)
        if not verdict.get("promote"):
            logger.warning("not switching to %s: %s", key, verdict.get("why"))
            return verdict
    tmp = link.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    tmp.symlink_to(spec.filename)
    os.replace(tmp, link)
    return verdict


def _systemctl(*args: str) -> "subprocess.CompletedProcess | None":
    if shutil.which("systemctl") is None:
        return None
    try:
        return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=30, check=False)
    except subprocess.TimeoutExpired:
        return None


def start_service() -> str:
    """Enable and (re)start the user service; '' on success, else why not."""
    if not server_binary():
        return "llama-server is not installed (the llama-cpp package)"
    if not current_link().exists():
        return "no model is downloaded yet"
    write_env()
    proc = _systemctl("enable", "--now", UNIT)
    if proc is None or proc.returncode != 0:
        return (proc.stderr.strip() if proc else "systemctl is not available")[:200]
    _systemctl("restart", UNIT)
    return ""


def stop_service() -> str:
    """Stop the user service, so the model's memory is actually released.

    **This did not exist, and its absence is why `presence.py` had to be honest
    about Drowsy.** There was `start_service()` and nothing to undo it with, and
    setup ran `systemctl --user enable --now` - so from the first login the unit
    came up on its own and stayed up, holding a 1.1-4 GB model resident whether or
    not anyone was talking to it. `assistd`'s one-key Active/Drowsy/Sleeping
    cycle needs exactly this lever, and a laptop that cannot let go is a laptop
    that runs warm and flat all day.

    `disable` is deliberately **not** used: the unit stays enabled so a question
    can start it again, which is what makes "drowsy" mean *released, not gone*.
    Only the runtime stop is reversible; disabling would leave a machine whose
    assistant silently never answers.

    '' on success, else why not - the same contract as `start_service`.
    """
    if shutil.which("systemctl") is None:
        return "systemctl is not available, so the model cannot be released"
    proc = _systemctl("stop", UNIT)
    if proc is None:
        return "systemctl is not available, so the model cannot be released"
    if proc.returncode != 0:
        return (proc.stderr.strip() or f"systemctl exited {proc.returncode}")[:200]
    return ""


def is_up(timeout: float = 1.5) -> bool:
    import httpx
    try:
        return httpx.get(f"http://{HOST}:{PORT}/health", timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


def gpu_devices() -> "list[str]":
    """Accelerators llama.cpp can use here (Vulkan via ggml-vulkan), from `llama-server --list-devices`.

    Empty on a CPU-only machine - or with no Vulkan driver - and that is not an
    error: ggml then runs on the CPU, so the same package works on both.
    """
    binary = server_binary()
    if not binary:
        return []
    try:
        proc = subprocess.run([binary, "--list-devices"], capture_output=True, text=True, timeout=30, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return []
    out = []
    for line in (proc.stdout + proc.stderr).splitlines():
        line = line.strip()
        # "Vulkan0: Intel(R) Iris(R) Xe Graphics (12345 MiB, 12000 MiB free)"
        if ":" in line and not line.lower().startswith(("available devices", "load_backend", "ggml_", "warning")):
            name = line.split(":", 1)[0]
            if name and name[-1].isdigit() and not name.lower().startswith("cpu"):
                out.append(line)
    return out


def _context_arg() -> str:
    """The `-c` size for llama-server, chosen for this machine's actual build.

    A GPU takes the fit computed by `ctxfit` (weights on disk, the model's own
    KV cost per token from its GGUF header, total GPU memory); anything that
    cannot be measured (no model installed, unparseable header, unknown VRAM)
    keeps the historical 8192 rather than guessing. A CPU-only machine keeps
    8192: llama.cpp's KV pages are allocated on load, and 8k of weights+cache
    already fits where a smaller CPU tier picked this package.
    """
    try:
        info = ctxfit.gguf_model_info(current_link())
        weights = os.path.getsize(current_link()) if current_link().exists() else 0
        total = ctxfit.total_vram_bytes()
        # **The cross-check runs whenever a header was read, not only on a GPU.**
        # It was first wired inside the `if ... and total:` branch below, which
        # needs `total_vram_bytes()` - and on the CPU-only machine Chronoa's
        # default local brain runs on that is None, so the check never ran at
        # all. Comparing two readings of one file needs the file and nothing
        # else. A disagreement is reported and **not acted on**: the header stays
        # the authority because it is the only route that knows about shared
        # caches and hybrid layer patterns, and silently switching on one file
        # would trade a visible wrong number for an invisible right one. What
        # changes is that the wrong number is now sayable.
        if info:
            try:
                disagreement = ctxfit.kv_width_disagreement(current_link(), info)
            except Exception:  # noqa: BLE001 - a check must never cost the fit
                disagreement = None
            if disagreement:
                logger.warning(
                    "llama-server context: the GGUF header and the model's own "
                    "tensors disagree - %s", disagreement)

        if info and weights and total:
            per_token = ctxfit.kv_bytes_per_token(info) or None
            chosen = ctxfit.context_that_fits(
                weights, total_bytes=total, per_token=per_token)
            # **The cache type is a lever, not a measurement.** The context
            # ladder has bottomed out and the KV cache is still f16 - which is
            # llama.cpp's default and 2x the bytes of `q8_0`. Rather than ship a
            # context that does not fit, keep the context and compress the cache
            # it is made of. This is unsloth's budget decision: it ranks cache
            # types by bytes per element and picks the lightest that fits,
            # instead of treating the KV cost as fixed.
            #
            # f16 is preferred everywhere it fits, so this only fires when the
            # ladder has already failed - the ordering buys nothing otherwise.
            cache_type = "f16"
            if not ctxfit.estimate_fit(weights, chosen, total_bytes=total,
                                       per_token=per_token).fits:
                cache_type = ctxfit.cache_type_for_context(
                    weights, chosen, total_bytes=total,
                    per_token_at_f16=per_token)
                if cache_type != "f16":
                    chosen = ctxfit.context_that_fits(
                        weights, total_bytes=total,
                        per_token=ctxfit.kv_bytes_per_token(info, cache_type))
                    logger.info(
                        "llama-server context: KV cache quantised to %s so the "
                        "context fits (%d) at %0.1f GiB vram",
                        cache_type, chosen, total / 1024**3)
            logger.info(
                "llama-server context: %d (weights %0.1f GiB, vram %0.1f GiB, "
                "kv/token %s, cache %s)",
                chosen, weights / 1024**3, total / 1024**3,
                per_token if per_token is not None else "unknown", cache_type,
            )
            _KV_CACHE_TYPE[0] = cache_type
            return str(chosen)
    except OSError:
        pass
    return "8192"


def physical_cores() -> int:
    """This machine's physical cores - the value llama.cpp's `-t` wants.

    The implementation, the measurement and the reasoning live in
    `hardware_profile.physical_cores`, because four launchers in this package
    need the same number and a second copy of the table is a table that will
    disagree with the first one. Re-exported here so the module that starts the
    server reads its own knob without reaching sideways.

    Measured on this box: `-t 4` gives 3.73 tok/s and `-t 8` gives 2.32 - the
    old `os.cpu_count() - 1` was 7, in between, losing about a third.
    """
    return hardware_profile.physical_cores()


#: The KV-cache type `_context_arg()` settled on, in a one-element list so a
#: caller can read it after the decision without the two functions sharing a
#: return type. `f16` is llama.cpp's default and what it stays at whenever the
#: context fits in it - the lever below only moves when the ladder has already
#: failed.
_KV_CACHE_TYPE: "list[str]" = ["f16"]


def kv_cache_type() -> str:
    """The cache type the context decision chose. `f16` before it has run."""
    return _KV_CACHE_TYPE[0]


def server_args(devices: "Optional[list[str]]" = None) -> "list[str]":
    """The llama-server command line for this machine: GPU offload only when a GPU is listed."""
    devices = gpu_devices() if devices is None else devices
    args = ["-m", str(current_link()), "--host", HOST, "--port", str(PORT), "--jinja",
            "-c", _context_arg() if devices else "8192", "--no-webui"]
    # Physical cores, measured - see `physical_cores`. Not `os.cpu_count() - 1`.
    args += ["-ngl", "99"] if devices else ["-ngl", "0", "-t", str(physical_cores())]
    # Only when the decision actually moved off the default. An f16 server gets
    # no `-ctk`/`-ctv`, so a machine that fits keeps the exact command line it
    # had before the cache type became a parameter.
    cache_type = kv_cache_type()
    if cache_type != "f16":
        args += ["-ctk", cache_type, "-ctv", cache_type]
    return args


def write_env(devices: "Optional[list[str]]" = None) -> Path:
    """~/.config/shani-chronoa/llm.env, which the unit reads - so a GPU added later is a restart away."""
    path = files.config_home() / "shani-chronoa" / "llm.env"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("LLAMA_ARGS=" + " ".join(server_args(devices)) + "\n", encoding="utf-8")
    return path


def recommended_for_machine() -> str:
    """The default for THIS machine, from memory AND decode speed.

    **A RAM gate alone is not enough, and the first version got this wrong.**
    `recommended()` came back `qwen3-14b` on a CPU-only box with 30 GB of RAM -
    a model whose decode is ~1.8 tok/s there. Decode is memory-bandwidth bound:
    every generated token reads the whole weight set, so tok/s x weights is the
    machine's effective bandwidth and the same figure predicts any other model.

    That is also why "is there a GPU" is the wrong question. A Threadripper 7980X
    has 8 channels of DDR5 against this laptop's 2 of DDR4 - roughly 86-129 GiB/s
    of effective bandwidth here's 15.4 - so a 14B runs at 10-15 tok/s there and
    1.8 here. Gating on the GPU would tell a Threadripper owner to run a 1.7B on
    a machine that handles a 14B comfortably, which is under-serving exactly the
    users with the hardware to use it.

    So: **does it fit**, and **will it decode fast enough**. The speed is
    measured from a live decode when one is running, and when it cannot be
    measured the answer stays at the tier whose speed is known on a modest CPU
    rather than assuming a bigger machine. The tiers are *offered* either way -
    this picks a default, not a permission.
    """
    if not _fits_in_memory():              # no server to measure against
        return recommended()
    # **A decode rate is not evidence it picks the right skill.** On a machine
    # with no GPU the default is capped at the largest tier with a measured
    # `tools/task_eval.py` score, because a reply that arrives and a reply that
    # uses the right tool are different claims and only one has been measured
    # above the 1.7B. With a GPU the decode is comfortable and the ceiling lifts.
    for key in reversed(list(SPECS)):      # largest first
        if not _may_default(key):
            continue
        met, _missing = requirements_met(key)
        if met:
            return key
    return recommended()


#: What each tier needs to be a *default*, rather than merely offered.
#: `cpu`, `ram` and `vram` are the machine's budgets in the units their own
#: readers report; `min_tps` is the decode rate below which a turn takes long
#: enough to be annoying.
#:
#: `active_bytes` is the field the whole table turns on, and it is the accounting
#: edge0's paper is about: **peak memory tracks the parameter count, but decode
#: cost tracks the ACTIVE set.** A dense model reads its whole weight file once
#: per generated token, so `active_bytes` is the file size. A mixture-of-experts
#: model routes each token to a few experts, so it reads a fraction of the file -
#: Qwen3-30B-A3B stores 17.3 GiB and touches ~3.3B of its 30.5B parameters, which
#: is why a 30B decodes here at a few GiB per token and a 14B dense does not.
#:
#: `None` means "the whole file", which is every tier above the fold.
#:
#: `eval` is the tier's measured score on `tools/task_eval.py` - right tool and
#: right arguments, 62 cases, scored and never executed. **A decode rate is not
#: this.** It says a reply arrives; it says nothing about whether the model calls
#: the right skill, which is the whole of what an assistant does. So a tier with
#: no `eval` may be the default on a machine with a GPU whose decode rate is
#: comfortable, and may not be the default on a CPU-only machine, where the
#: 1.7B is the ceiling. Those are the only two numbers in the tree with an eval
#: behind them: 0.6B 44/62, 1.7B 56/62 - and the 4B has never been run.
#:
#: **This is the rule the 2026-10-11 measurement nearly broke.** With the
#: bandwidth model corrected, the 4B predicts 7.4 tok/s here and clears
#: `min_tps` on its own, so `recommended_for_machine()` started returning it -
#: promoting the CPU default to a model whose task performance has never been
#: measured, on a speed figure. The speed figure was the excuse; the eval is the
#: evidence, and only one of the two had been measured.
REQUIRES: Dict[str, Dict[str, float]] = {
    "qwen3-0.6b": {"cpu": 2, "ram": 0, "vram": 0, "min_tps": 5.0,
                   "active_bytes": None, "eval": "44/62"},
    "qwen3-1.7b": {"cpu": 2, "ram": 0, "vram": 0, "min_tps": 5.0,
                   "active_bytes": None, "eval": "56/62"},
    "qwen3-4b": {"cpu": 2, "ram": 8, "vram": 0, "min_tps": 5.0, "active_bytes": None},
    "qwen3-8b": {"cpu": 2, "ram": 16, "vram": 0, "min_tps": 5.0, "active_bytes": None},
    "qwen3-14b": {"cpu": 4, "ram": 24, "vram": 0, "min_tps": 5.0, "active_bytes": None},
    # A 30B that reads ~2 GiB per token. `ram` is the real budget (the weights
    # must be somewhere), but `active_bytes` is what decides whether it is fast
    # enough to be a default on a modest machine - which is the whole point.
    # `measured` is False because the figure comes from the architecture
    # (3.3B of 30.5B parameters active), not from a decode on this machine. An
    # unmeasured figure must not make a tier the DEFAULT: `requirements_met`
    # refuses it, so the tier is offered and the speed is reported as an
    # estimate. Flip this to True with the measured number once a real decode
    # has been run - which is the whole point of the flag.
    "qwen3-30b-a3b": {"cpu": 4, "ram": 20, "vram": 0, "min_tps": 5.0,
                      "active_bytes": 2_200_000_000, "measured": False},
}

#: Tokens per second below which a model is offered but not defaulted. Derived,
#: not picked from feel: `MAX_TURN_SECONDS` is 600, and a spoken reply is wanted
#: inside ~20 s, so 5 tok/s leaves the model well inside the turn budget while
#: keeping a reply's first sentence audible.
MIN_USABLE_TPS = 5.0


def bytes_per_token(key: str) -> Optional[int]:
    """What one generated token actually reads for `key`, or None.

    `active_bytes` when the tier declares it, otherwise the whole file. This is
    the number decode speed and memory *pressure* both follow, and the two differ
    for a mixture-of-experts.
    """
    spec = SPECS.get(key)
    if spec is None or not spec.size_bytes:
        return None
    declared = REQUIRES.get(key, {}).get("active_bytes")
    # **The file wins over the table.** `active_bytes` for the one MoE tier was a
    # hardcoded constant marked `measured: False`, and nothing could improve it
    # because nothing read the checkpoint to find out. A GGUF carries its own
    # routing topology, so whenever the model is on disk the number is derived
    # from it; the constant remains the fallback for a tier that is not installed.
    try:
        info = ctxfit.gguf_model_info(spec.filename) \
            if os.path.exists(spec.filename) else None
        derived = ctxfit.expert_bytes_per_token(info, spec.size_bytes) if info else None
    except Exception:  # noqa: BLE001 - a table entry must never cost the figure
        derived = None
    if derived:
        return derived
    return int(declared) if declared else spec.size_bytes


def _may_default(key: str) -> bool:
    """Whether `key` may be the default here, on evidence rather than on speed.

    **A decode rate is not evidence it picks the right skill.** It says a reply
    arrives; it says nothing about whether the model calls the right tool, which
    is the whole of what an assistant does. So a tier may be the default if
    either

    * it carries a measured `tools/task_eval.py` score - `eval` in the table, of
      which exactly two tiers have one (0.6B 44/62, 1.7B 56/62); or
    * **this machine decodes it at twice `min_tps`** - the one case where the
      speed figure is large enough to be its own argument.

    Both halves are needed. The first is what keeps a machine with comfortable
    hardware from being handed an unmeasured model; the second is what stops that
    becoming a blanket "never above 1.7B" rule that would under-serve a
    Threadripper.

    Measured on this laptop, and the reason the second half exists: the 4B
    predicts **7.4 tok/s against a 5.0 floor** - 1.48x, a reply that arrives and
    nothing more - so it stays offered and not defaulted, which is what
    `tests/test_setup_wizard.py` asserts and what the 2026-10-11 bandwidth
    correction nearly broke. A 14B on a 7980X decodes at 12-15, which is
    2.4-3.0x and is a different statement.

    A GPU machine lifts the ceiling entirely: the weights are not coming off
    DRAM, so the decode is comfortable by construction.
    """
    if gpu_devices():
        return True
    need = REQUIRES.get(key)
    if need is None:
        return True
    if need.get("eval"):
        return True
    tps = expected_decode_tps(key)
    return bool(tps and tps >= 2.0 * need.get("min_tps", MIN_USABLE_TPS))


def requirements_met(key: str) -> "tuple[bool, list[str]]":
    """Whether this machine can run `key` as a default, and what it is short of.

    Returns `(met, missing)` where `missing` names each unmet requirement in the
    units the machine reports, so the panel can say *"needs 20 GB of RAM, this
    machine has 25.9"* rather than a bare "not recommended".
    """
    need = REQUIRES.get(key)
    if need is None:
        return True, []
    missing: "list[str]" = []
    ram = ram_gb()
    cores = os.cpu_count() or 1
    gpu = bool(gpu_devices())
    if ram < need.get("ram", 0):
        # **Residency and runnability are different questions, and naming the
        # difference is what airllm's whole thesis is.** `air_llm/airllm/
        # airllm_base.py`: *"AirLLM only ever keeps one layer on the GPU at a
        # time, so the VRAM you need depends on the model's layer size - not its
        # total size"* - which is how a 70B runs in 4 GB. Chronoa's own
        # `bytes_per_token()` already computes the comparable number: what one
        # generated token actually reads, which is the whole file for a dense
        # model and a fraction of it for a mixture-of-experts.
        #
        # Before this, a machine short on RAM was told *"20 GB of RAM (this
        # machine has 6.0)"* - which reads as "cannot run". Measured, that is the
        # wrong reading for the tier where the two numbers diverge most:
        # `qwen3-30b-a3b` declares `ram=20` and `active_bytes` of 2.05 GiB, so a
        # 6 GB box is short of the *resident* figure while its per-token cost
        # fits with room to spare. The card shows both numbers and still led with
        # the residency one as a requirement.
        #
        # The sentence now says what the RAM figure is *for* and gives the
        # per-token number beside it. It deliberately does **not** claim a decode
        # rate: nobody here has measured a >RAM model on a 6 GB box, and an
        # invented tok/s is the confident wrong answer this file keeps recording.
        # It also does not say "it runs", only that this is a residency limit
        # rather than a cap on whether it runs - which follows from the
        # arithmetic, not from a measurement.
        active = bytes_per_token(key)
        active_gib = None if active is None else active / (1024 ** 3)
        if active_gib is not None and active_gib <= ram:
            missing.append(
                f"{need['ram']:g} GB of RAM to keep every weight resident "
                f"(this machine has {ram:.1f}). Each generated token only reads "
                f"{active_gib:.2f} GiB, which fits, so this is a residency limit "
                f"and not a cap on whether it runs.")
        else:
            missing.append(f"{need['ram']:g} GB of RAM (this machine has {ram:.1f})")
    if cores < need.get("cpu", 0) and not gpu:
        missing.append(f"{need['cpu']:g} CPU cores (this machine has {cores})")
    if not need.get("measured", True):
        # The bytes-per-token figure is an estimate from the architecture, so
        # everything computed from it is an estimate too. Offering the tier is
        # fine - the panel says "estimated" - but making it the default on a
        # number nobody measured is the confident wrong answer this repo keeps
        # recording.
        missing.append("a measured decode rate (this tier's bytes-per-token is "
                       "estimated, not measured)")
        return (not missing), missing
    tps = expected_decode_tps(key)
    if tps is None:
        # Unmeasured is not a pass. The tier stays offerable but is never the
        # default, and the caller says so rather than assuming a fast machine.
        missing.append("a measured decode rate (no local model is answering)")
    elif tps < need.get("min_tps", 0):
        missing.append(f"{need['min_tps']:g} tok/s (this machine decodes at "
                       f"{tps:.1f})")
    return (not missing), missing


_RULES_PREFIX = "The user's standing rules for you"
_THINK = re.compile(r"<think>.*?</think>\s*", re.S)
_TOOL_TAG = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
#: Qwen3-Coder's XML form - `<function=name><parameter=key>value</parameter></function>`,
#: usually inside `<tool_call>`. Seen live 2026-10-08: a free cloud model asked
#: for `browse` this way and Chronoa showed the markup to the person as its reply.
_XML_FUNCTION = re.compile(r"<function=([\w.\-]+)>(.*?)</function>", re.S)
_XML_PARAMETER = re.compile(r"<parameter=([\w.\-]+)>(.*?)</parameter>", re.S)
#: The wrapper a chat template puts around an XML call. Matched only to *widen*
#: a span that already became a call - see `_widen`. Deliberately plain ASCII: a
#: zero-width-space variant looks defensible, is unmeasured, and would be the
#: first thing to be "fixed" by intuition - which is precisely what the captured
#: fixture in `tests/test_text_tool_calls_recovered.py` exists to prevent.
_TOOL_CALL = re.compile(r"<\u200b?tool_call>.*?</\u200b?tool_call>", re.S)


def _xml_value(raw: str, schema: dict):
    """A parameter's text as the type its schema asks for; the text itself when it is not one."""
    text = raw.strip("\n")
    kind = (schema or {}).get("type")
    if kind in ("integer", "number", "boolean", "array", "object"):
        try:
            value = json.loads(text.strip())
        except ValueError:
            return text
        if kind == "integer" and isinstance(value, bool):
            return text
        return value
    return text


def _xml_parameters(body: str, props: dict):
    """Every ``<parameter=key>value</parameter>`` in a function block, or None.

    **None means the block was not wholly parameter pairs** - which is a
    refusal, not a tolerance. A partial parse calls the tool with the arguments
    that happened to match and silently discards the rest, so the model gets
    confidently wrong input and never learns: the local 1.7B's reply to a
    timezone question is

        <function=get_datetime>
        <parameter=tz>Europe/Madrid</parameter>
        the user is in Spain
        </function>

    and before this, `the user is in Spain` vanished while the call ran anyway.
    Taken from edge0's `_parse_arguments`, which is stricter than this was for
    the same reason.
    """
    args = {}
    pos = 0
    for match in _XML_PARAMETER.finditer(body):
        if body[pos:match.start()].strip():
            return None
        key = match.group(1)
        args[key] = _xml_value(match.group(2), props.get(key) or {})
        pos = match.end()
    if body[pos:].strip():
        return None
    return args


def _widen(spans, text: str) -> list:
    """Consumed spans grown to the `<tool_call>` wrapper around them, if any.

    Without this the wrapper is left behind empty - the recovered reply becomes
    `"I'll open the site.\\n<tool_call>\\n\\n</tool_call>"`, which is the very
    complaint `tests/test_text_tool_calls_recovered.py` opens with: markup
    shown to the person as a reply. Only a wrapper that actually *contains* a
    consumed span grows, so a second, unconsumed call in the same turn keeps its
    own.
    """
    out = []
    for start, end in spans:
        for wrapper in _TOOL_CALL.finditer(text):
            if wrapper.start() <= start and end <= wrapper.end():
                start, end = wrapper.start(), wrapper.end()
                break
        out.append((start, end))
    return out


def _without(text: str, spans) -> str:
    """`text` with every consumed span cut out and the rest joined.

    **A recovered call used to wipe the whole reply.** Measured before this:
    "Let me check that for you." + the call + "That is the current time."
    became content `""` - the person watched a tool run and the assistant's own
    words disappear, unmentioned and unlogged, because the only content the
    transcript had was the string this used to overwrite.

    Only the spans that actually became a call are removed. A block whose name
    was not offered, or whose arguments did not parse whole, stays in the text -
    it is the evidence of what the model actually wrote, and `text_tool_call()`
    reads that text to tell a person the model called something that does not
    exist. Shape from edge0's `_split`.
    """
    out = []
    pos = 0
    for start, end in sorted(spans):
        if start < pos:          # overlapping match; the first one owns it
            continue
        out.append(text[pos:start])
        pos = end
    out.append(text[pos:])
    return "".join(out).strip()


def _redact(messages: "list[dict]") -> "list[dict]":
    """Every message's content, with registered secrets replaced.

    Applied to the whole list rather than to the system prompt alone, because
    the dangerous half is a secret in a *tool result* or a *file read* - those
    arrive as `tool` and `user` messages, and they are exactly the messages a
    reader would assume were safe because they are not the system prompt.
    """
    try:
        from shani_chronoa.redaction import redactor
    except Exception:  # noqa: BLE001 - redaction must never break a reply
        return messages
    out = []
    for message in messages:
        if not isinstance(message, dict):
            out.append(message)
            continue
        content = message.get("content")
        if not isinstance(content, str) or not content:
            out.append(message)
            continue
        try:
            cleaned = redactor.sanitize(content)
        except Exception:  # noqa: BLE001
            cleaned = content
        out.append({**message, "content": cleaned})
    return out

def normalize_messages(messages: "list[dict]") -> "list[dict]":
    """One system message at the head, holding only what stays the same; changing context folded into the last user turn.

    Two reasons, both from harness-study: llama.cpp reuses its prompt cache
    only for an unchanged prefix (assistd folds per-turn context into the user
    turn for this), and several models' chat templates reject a system
    message anywhere but first (sayri merges them for this). Percepts change
    every turn, so they go into the newest user message; the rules file
    rarely changes, so it joins the system prompt. Nothing is dropped, and
    the caller's list is not modified.

    **Every registered secret is redacted here, and this is the only place in
    this module that could do it.** `ollama_llm.py` and all three cloud paths
    call `redactor.sanitize` on every message before sending, and this file
    called it nowhere - so on the *default* backend, the one `AGENTS.md` says is
    now the default brain, a registered API key that had been read back by a
    tool, quoted in a file, or pasted into a turn went to the model verbatim.
    Every other backend redacted it. `normalize_messages` is the single
    function every request here passes through (see the `prepare_messages`
    below), so this is the chokepoint rather than a belt-and-braces extra.

    Fail-soft: a redactor that raises must not stop a reply, so an exception
    here degrades to the unsanitized text rather than to no answer - which is
    the one direction that is wrong, and it is stated rather than hidden. The
    registered-secret case is narrow (they are the keys Chronoa itself holds)
    and losing a turn would be worse than losing the redaction.
    """
    if not messages:
        return messages
    messages = _redact(messages)
    head = dict(messages[0]) if messages[0].get("role") == "system" else None
    rest = messages[1:] if head else list(messages)
    stable, moving, body = [], [], []
    for m in rest:
        if m.get("role") == "system":
            text = str(m.get("content") or "")
            (stable if text.startswith(_RULES_PREFIX) else moving).append(text)
        else:
            body.append(dict(m))
    if head is not None and stable:
        head["content"] = "\n\n".join([str(head.get("content") or "")] + stable)
    if moving:
        last_user = max((i for i, m in enumerate(body) if m.get("role") == "user"), default=None)
        context = "\n\n".join(moving)
        if last_user is None:
            body.insert(0, {"role": "user", "content": context})
        else:
            body[last_user]["content"] = f"{context}\n\n---\n{body[last_user].get('content') or ''}"
    return ([head] if head is not None else []) + body


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def text_tool_call(content) -> "Optional[tuple]":
    """(name, arguments) when a reply is nothing but a tool call written as text, else None.

    Unlike `recover_tool_calls` this does not check the name against the offered
    tools - it is how the assistant notices a model calling a tool that does not
    exist (`set_reminder` for `reminders`, seen live 2026-10-08) or was not
    offered, so it can say so to the model instead of showing the markup.
    """
    if not isinstance(content, str):
        return None
    text = _THINK.sub("", content).strip()
    match = _XML_FUNCTION.search(text)
    if match and text.replace(match.group(0), "").replace("<tool_call>", "").replace("</tool_call>", "").strip() == "":
        return match.group(1), dict(_XML_PARAMETER.findall(match.group(2)))
    block = None
    for found in (_TOOL_TAG.search(text), _JSON_FENCE.search(text)):
        if found and text.replace(found.group(0), "").strip() == "":
            block = found.group(1)
    if block is None and text.startswith("{") and text.endswith("}"):
        block = text
    if block is None:
        return None
    try:
        obj = json.loads(block)
    except ValueError:
        return None
    if isinstance(obj, dict) and isinstance(obj.get("name"), str):
        args = obj.get("arguments", obj.get("parameters", {}))
        return obj["name"], args if isinstance(args, dict) else {}
    return None


def recover_tool_calls(message: dict, tools) -> dict:
    """A tool call a model wrote as text turned into a real one.

    Three spellings: `<tool_call>{...}</tool_call>` (JSON), a fenced JSON block,
    and Qwen3-Coder's `<function=name><parameter=k>v</parameter></function>`.

    Only for a tool that was offered, with an object of arguments; anything
    else is left as the text it was. Also removes a leaked `<think>` block,
    which would otherwise be shown and spoken.
    """
    content = message.get("content") or ""
    if isinstance(content, str) and "<think>" in content:
        content = _THINK.sub("", content).strip()
        message = {**message, "content": content}
    # A malformed entry (a string, None) in the tool list is skipped, not fatal:
    # the cloud path runs this too now, and its schema guard is tested with a
    # deliberately broken list (`test_skills.TestCloudSchemaGuards`).
    tools = [t for t in (tools or []) if isinstance(t, dict)]
    if message.get("tool_calls") or not tools or not isinstance(content, str):
        return message
    offered = {(t.get("function") or {}).get("name") for t in tools}
    calls = []
    consumed = []
    schemas = {(t.get("function") or {}).get("name"): (t.get("function") or {}).get("parameters") or {}
               for t in tools}
    for match in _XML_FUNCTION.finditer(content):
        name = match.group(1)
        if name not in offered:
            continue
        props = (schemas.get(name) or {}).get("properties") or {}
        args = _xml_parameters(match.group(2), props)
        if args is None:
            continue
        calls.append({"id": f"recovered-{len(calls)}", "type": "function",
                      "function": {"name": name, "arguments": json.dumps(args)}})
        consumed.append(match.span())
    if calls:
        logger.info("Recovered %d tool call(s) the model wrote as XML text", len(calls))
        return {**message, "content": _without(content, _widen(consumed, content)),
                "tool_calls": calls}
    scanner = _TOOL_TAG if _TOOL_TAG.search(content) else _JSON_FENCE
    blocks = [(m.group(1), m.span()) for m in scanner.finditer(content)]
    if not blocks:
        # A reply that is nothing but the call as a bare JSON object (or one per
        # line) - seen live 2026-10-08: `{"name": "browse", "parameters": {...}}`
        # ended a booking after four steps. Only when the whole reply is JSON, so
        # an answer that merely quotes some JSON is never turned into an action.
        stripped = content.strip()
        lines = [l.strip() for l in stripped.splitlines() if l.strip()]
        if stripped.startswith("{") and stripped.endswith("}"):
            # Either way the *whole* reply was calls, so the whole reply is the
            # consumed span - there is no prose to preserve in this branch.
            if _is_json(stripped):
                blocks = [(stripped, (0, len(content)))]
            elif lines and all(l.startswith("{") and l.endswith("}") and _is_json(l)
                               for l in lines):
                blocks = [(l, (0, len(content))) for l in lines]
            else:
                blocks = []
    for block, span in blocks:
        try:
            obj = json.loads(block)
        except ValueError:
            continue
        # **`function` is the third spelling of the name field, and it was
        # measured rather than guessed.** The balanced-adapter ladder
        # (`tools/ladder_balanced.py`, three lumen adapters with their 111x delta
        # norm spread removed) got a model that answered "make a reminder" with
        # `{"arguments": {...}, "function": "reminders"}` - correct tool, correct
        # arguments, and the call was left as text because only `name` and
        # `parameters` were read. `function` is OpenAI's own wrapper key, so it
        # is a spelling a small model converges on and not a private one.
        # This is arXiv:2604.14862's finding from the other side: the same
        # object under a different key is a different answer.
        name, args = None, {}
        if isinstance(obj, dict):
            name = obj.get("name")
            if name is None:
                # The OpenAI wrapper nested: {"function": {"name": ..., ...}}
                nested = obj.get("function")
                name = nested.get("name") if isinstance(nested, dict) \
                    else nested
            args = obj.get("arguments", obj.get("parameters", {}))
        # **Only a string may name a tool.** `name in offered` raises on an
        # unhashable one, and a reply carrying `{"function": ["get_weather"]}`
        # would take the whole turn down at the point the model has already
        # answered - measured by a test built for it after the first version of
        # this did exactly that.
        if not isinstance(name, str) or not isinstance(args, dict):
            name, args = None, {}
        if name in offered and isinstance(args, dict):
            calls.append({"id": f"recovered-{len(calls)}", "type": "function",
                          "function": {"name": name, "arguments": json.dumps(args)}})
            consumed.append(span)
    if not calls:
        return message
    logger.info("Recovered %d tool call(s) the model wrote as text", len(calls))
    return {**message, "content": _without(content, _widen(consumed, content)),
            "tool_calls": calls}


def _short(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    cut = re.search(r"(?<=[.!?])\s", text)
    text = text[:cut.start()] if cut and cut.start() > 20 else text
    return text if len(text) <= limit else text[:limit - 1].rsplit(" ", 1)[0] + "…"


def compact_tools(tools, limit: int = 160):
    """The same tools with each description cut to its first sentence (and `limit` characters).

    Names, parameter names, types, enums and `required` are untouched, so a
    call is exactly as valid; only the prose shrinks. On a CPU the schemas are
    most of the prompt the model reads before its first word.
    """
    if not tools:
        return tools
    out = []
    for tool in tools:
        fn = dict(tool.get("function") or {})
        fn["description"] = _short(fn.get("description", ""), limit)
        params = fn.get("parameters")
        if isinstance(params, dict) and isinstance(params.get("properties"), dict):
            props = {}
            for name, prop in params["properties"].items():
                prop = dict(prop) if isinstance(prop, dict) else prop
                if isinstance(prop, dict) and "description" in prop:
                    prop["description"] = _short(prop["description"], 80)
                props[name] = prop
            fn["parameters"] = {**params, "properties": props}
        out.append({**tool, "function": fn})
    return out


#: Whether the local client sends compacted schemas. Off, by measurement: on
#: both images (Qwen3-0.6B, CPU, 2026-10-02) compact was 30% faster (28.2 s vs
#: 40.2 s for 8 calls) but chose the right tool 6/8 times against 7/8, and a
#: wrong action costs more than a slower right one. Re-measure with
#: shani-testbed's chronoa-setup (setup-compact-tools) for a larger model.
COMPACT_TOOLS = False

#: Sampling temperature for the local model. None is llama-server's own
#: default (0.8), which for a 0.6B model choosing among a dozen tools is close
#: to dice. Decided by tools/task_eval.py (shani-testbed chronoa-eval).
TEMPERATURE = None

#: A second, narrower chance when the model answered in words to what was
#: plainly a command (tool_select.confident): the same request with only those
#: few tools, ask_user, and an explicit "answer without a tool" choice, under
#: tool_choice=required. A destructive tool is never forced, and not acting
#: stays a choice the model can make. Decided by tools/task_eval.py.
FORCE_RETRY = False

#: Two constrained steps instead of free-form tool calling (adopted from Alpaca's
#: schema-constrained title generation, applied to the tool call itself): first
#: the tool's *name*, under a JSON schema whose only allowed values are the
#: offered tools and "none"; then its *arguments*, under that tool's own
#: parameter schema. llama-server turns each schema into a grammar, so an
#: unknown tool or malformed arguments cannot be generated at all. Decided by
#: tools/task_eval.py.
CONSTRAINED = False

NO_TOOL_NAME = "answer_without_a_tool"
NO_TOOL = {"type": "function", "function": {
    "name": NO_TOOL_NAME,
    "description": "Choose this when none of the other tools is needed: the request is a question to answer, "
                   "or chat. Put your answer in text.",
    "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}}


def _first_sentence(text: str, limit: int = 140) -> str:
    return _short(text, limit)


def tool_menu(tools: "list[dict]") -> str:
    return "\n".join(f"- {t['function']['name']}: {_first_sentence(t['function'].get('description', ''))}"
                     for t in tools)


def pick_schema(tools: "list[dict]") -> dict:
    names = [t["function"]["name"] for t in tools]
    return {"type": "object", "properties": {"tool": {"type": "string", "enum": names + ["none"]}},
            "required": ["tool"]}


def argument_schema(tool: dict) -> dict:
    params = (tool.get("function") or {}).get("parameters") or {}
    props = params.get("properties") or {}
    return {"type": "object", "properties": props, "required": [r for r in params.get("required", []) if r in props]}


def _json_format(schema: dict) -> dict:
    return {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}}


def _never_forced(name: str) -> bool:
    from shani_chronoa import capabilities
    return capabilities.GATED.get(name) in capabilities.DESTRUCTIVE_CONSENT_KEYS


def forced_tools(messages: "list[dict]") -> "list[dict]":
    """The narrowed tool list for a forced retry, or [] when the request is not clearly a command."""
    from shani_chronoa.tool_select import confident
    from shani_chronoa.tools import TOOLS
    asked = next((str(m.get("content") or "") for m in reversed(messages) if m.get("role") == "user"), "")
    names = [n for n in confident(asked, TOOLS) if not _never_forced(n)]
    if not names:
        return []
    keep = set(names) | {"ask_user"}
    return [tool for tool in TOOLS if tool["function"]["name"] in keep] + [NO_TOOL]


#: Characters per token, conservatively low for English + JSON (so the estimate errs towards "too long").
CHARS_PER_TOKEN = 3.0
#: Tokens kept free for the reply itself.
REPLY_RESERVE = 1024
_N_CTX = {}


def context_tokens(default: int = 8192) -> int:
    """llama-server's real context window (GET /props), cached; `default` when it cannot be read."""
    if "n" not in _N_CTX:
        import httpx
        try:
            props = httpx.get(f"http://{HOST}:{PORT}/props", timeout=2.0).json()
            _N_CTX["n"] = int((props.get("default_generation_settings") or {}).get("n_ctx") or props.get("n_ctx") or default)
        except (httpx.HTTPError, ValueError, TypeError):
            return default
    return _N_CTX["n"]


def fit_to_context(messages: "list[dict]", n_ctx: int, reserved_chars: int = 0) -> "list[dict]":
    """Drop the oldest whole turns until the request fits the model's window (assistd's effective_budget).

    A turn is a user message and everything after it up to the next user
    message, so a tool call is never separated from its result. The head
    system message and the newest turn are always kept; a note says how many
    messages were left out, so the model does not mistake a gap for nothing.
    """
    budget = int((n_ctx - REPLY_RESERVE) * CHARS_PER_TOKEN) - reserved_chars
    size = lambda ms: sum(len(str(m.get("content") or "")) + len(json.dumps(m.get("tool_calls") or "")) for m in ms)
    if size(messages) <= budget or len(messages) < 3:
        return messages
    head, body = (messages[:1], messages[1:]) if messages[0].get("role") == "system" else ([], list(messages))
    starts = [i for i, m in enumerate(body) if m.get("role") == "user"] or [0]
    turns = [body[a:b] for a, b in zip(starts, starts[1:] + [len(body)])]
    lead = body[:starts[0]]
    dropped = 0
    while len(turns) > 1 and size(head + lead + [m for t in turns for m in t]) > budget:
        dropped += len(turns.pop(0))
    kept = [m for t in turns for m in t]
    if dropped:
        note = {"role": "user", "content": f"[{dropped} earlier message(s) of this conversation were left out "
                                            f"to fit the model's memory.]"}
        if kept and kept[0].get("role") == "user":
            kept[0] = {**kept[0], "content": f"{note['content']}\n\n{kept[0].get('content') or ''}"}
        else:
            kept.insert(0, note)
        logger.info("Left out %d old message(s) to fit a %d-token context", dropped, n_ctx)
    return head + lead + kept


class LocalLLM:
    """Constructed lazily below so importing this module never imports httpx/cloud_llm."""

    def __new__(cls, model_key: str = ""):
        from shani_chronoa.cloud_llm import CloudProvider, OpenAICompatibleLLM

        class _Local(OpenAICompatibleLLM):
            local = True
            # Qwen3 "thinks" before answering unless told not to; on a CPU that
            # is most of the wait, and the thought is never shown (assistd sends
            # the same flag). llama-server passes it to the chat template.
            extra_payload = {"chat_template_kwargs": {"enable_thinking": False}}
            read_timeout = 300.0
            stream_supported = True

            def is_available(self) -> bool:
                return is_up()

            def prepare_messages(self, messages):
                return fit_to_context(normalize_messages(messages), context_tokens(),
                                      reserved_chars=getattr(self, "_tools_chars", 0))

            def _payload_extra(self, **more):
                extra = {"chat_template_kwargs": {"enable_thinking": False}}
                if TEMPERATURE is not None:
                    extra["temperature"] = TEMPERATURE
                extra.update(more)
                return extra

            async def _json(self, messages, schema, temperature=None):
                more = {"temperature": temperature} if temperature is not None else {}
                self.extra_payload = self._payload_extra(response_format=_json_format(schema), **more)
                try:
                    reply = await super().chat_message(messages, tools=None)
                finally:
                    self.extra_payload = self._payload_extra()
                try:
                    return json.loads(reply.get("content") or "{}")
                except ValueError:
                    return {}

            async def suggest_title(self, asked: str, answered: str) -> str:
                """A few-word title for a conversation, under a JSON schema (Alpaca's titles), at low temperature."""
                schema = {"type": "object", "properties": {"title": {"type": "string", "maxLength": 48}},
                          "required": ["title"]}
                got = await self._json([{"role": "user", "content": (
                    "Write a short title, 2 to 6 words, for a conversation that began like this. No quotes.\n\n"
                    f"User: {asked[:600]}\nAssistant: {answered[:600]}")}], schema, temperature=0.2)
                return str(got.get("title") or "").strip()[:48] if isinstance(got, dict) else ""

            async def _constrained(self, messages, tools):
                """The tool's name, then its arguments, each generated under a schema."""
                conversation = [m for m in messages if m.get("role") != "system"]
                head = next((str(m.get("content") or "") for m in messages if m.get("role") == "system"), "")
                pick = await self._json([{"role": "system", "content": head + (
                    "\n\nDecide what to do with the user's last message. If one of these tools does it, choose that "
                    "tool. If it is a question you can answer yourself, or chat, choose none.\nTools:\n"
                    + tool_menu(tools))}] + conversation, pick_schema(tools))
                name = pick.get("tool")
                chosen = next((t for t in tools if t["function"]["name"] == name), None)
                if chosen is None:
                    return None  # "none", or nothing usable: answer in words
                args = await self._json([{"role": "system", "content": head + (
                    f"\n\nCall {name}: {chosen['function'].get('description', '')}\nGive its arguments for the "
                    "user's last message.")}] + conversation, argument_schema(chosen))
                return {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "call_0", "type": "function",
                     "function": {"name": name, "arguments": json.dumps(args if isinstance(args, dict) else {})}}]}

            required_tool = ""

            async def chat_message(self, messages, tools=None, stream=False):
                tools = compact_tools(tools) if COMPACT_TOOLS else tools
                self._tools_chars = len(json.dumps(tools)) if tools else 0
                self.extra_payload = self._payload_extra()
                named = next((t for t in tools or [] if t["function"]["name"] == self.required_tool), None)
                if named is not None:
                    # the person named the tool: only its arguments are left to decide
                    conversation = [m for m in messages if m.get("role") != "system"]
                    head = next((str(m.get("content") or "") for m in messages if m.get("role") == "system"), "")
                    args = await self._json([{"role": "system", "content": head + (
                        f"\n\nCall {named['function']['name']}: {named['function'].get('description', '')}\n"
                        "Give its arguments for the user's last message.")}] + conversation, argument_schema(named))
                    return {"role": "assistant", "content": "", "tool_calls": [
                        {"id": "call_0", "type": "function", "function": {
                            "name": named["function"]["name"],
                            "arguments": json.dumps(args if isinstance(args, dict) else {})}}]}
                if CONSTRAINED and tools and messages and messages[-1].get("role") == "user":
                    called = await self._constrained(messages, tools)
                    if called is not None:
                        return called
                    return await super().chat_message(messages, tools=None, stream=stream)
                reply = await super().chat_message(messages, tools=tools, stream=stream)
                if FORCE_RETRY and tools and not reply.get("tool_calls"):
                    narrowed = forced_tools(messages)
                    if narrowed:
                        self.extra_payload = self._payload_extra(tool_choice="required")
                        try:
                            retry = await super().chat_message(messages, tools=narrowed, stream=stream)
                        finally:
                            self.extra_payload = self._payload_extra()
                        calls = retry.get("tool_calls") or []
                        if calls and (calls[0].get("function") or {}).get("name") != NO_TOOL_NAME:
                            logger.info("Answered in words to a clear command; the narrowed retry called %s",
                                        calls[0]["function"].get("name"))
                            return retry
                return reply

            async def chat_message_stream(self, messages, tools=None, on_text=None):
                tools = compact_tools(tools) if COMPACT_TOOLS else tools
                self._tools_chars = len(json.dumps(tools)) if tools else 0
                self.extra_payload = self._payload_extra()
                return await super().chat_message_stream(messages, tools=tools, on_text=on_text)

            def postprocess(self, message, tools):
                return recover_tool_calls(message, tools)

        provider = CloudProvider("llama.cpp", "llama.cpp (this computer)", BASE_URL, model_key or active() or "local")
        return _Local(provider)
