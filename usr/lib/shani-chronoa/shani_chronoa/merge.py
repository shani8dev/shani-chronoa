"""Merge open-weight models into one, and prove the result is worth serving.

**What this can and cannot do, stated first because the limit is the design.**
Two models can only be merged when they are the *same model*: same architecture,
same tensor names, same shapes, same vocabulary. `Qwen3-1.7B` and `Llama-3-8B`
have nothing to add - their tensors are not the same tensors, and averaging them
produces a file, not a model. So the chimera this module builds is a chimera of
**siblings**: `Qwen3-1.7B` base, its instruct tune, its DPO tune, and any
community fine-tune of that same base, which is where the open-weight ecosystem
actually keeps its variety.

**The operation is task arithmetic, not an average.** With `base` as the
reference, each sibling contributes the *difference* it represents:

    merged = base + sum(lambda_i * (parent_i - base))

This is what makes the coefficients mean something. `lambda=0` returns the base
exactly; `lambda=1` on one parent returns that parent exactly; and the weights
interpolate between *behaviours* rather than between arbitrary numbers. Averaging
raw weights (`sum(p_i)/n`) is the thing people reach for, and it has no such
guarantee - with `n=2` it silently collapses to the midpoint regardless of what
you asked for.

**Everything stays in high precision and is quantised once, afterwards.** Merging
quantised tensors and requantising is what `--allow-requantize` warns "can
severely reduce quality", so parents are dequantised, blended in f32, written as
F16, and `llama-quantize` does the single lossy step. `llama-gguf-split --merge`
is *not* this: it concatenates the shards of one split model and cannot blend two
different models at all.

**Refusing to produce a merge is a first-class outcome.** `gate()` measures the
merged model with `llama-perplexity` against **both** parents and returns a
verdict rather than a model. A merge that beats neither parent has produced
nothing worth loading, and the honest output is that - not a 1.1 GB file that
sounds like an improvement. This is `distill.py`'s rule applied to weights:
refusing is allowed, and the report is the product.

**Provenance is a record, not a filename.** A merged model carries who its
parents were, their digests, the coefficients, and what it was quantised from -
because the whole reason to merge rather than download is provenance, and a
merged file that cannot say what went into it has thrown that away.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Tensors whose names must match exactly for two models to be the same model.
#: Anything else means a different architecture, and blending is meaningless.
_STRUCTURAL = ("general.architecture", "tokenizer.ggml.tokens",
               "tokenizer.ggml.model")


class MergeRefusal(Exception):
    """Raised with the reason. Never a bare ``False``: a refusal nobody can act
    on is a bug report, not a design."""


def _reader(path: Path):
    from gguf import GGUFReader
    return GGUFReader(str(path))


def _scalar(value):
    """Unwrap gguf-py's byte-array / one-element-list shapes into a plain value."""
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, list) and value:
        if all(isinstance(b, int) and 32 <= b < 127 for b in value):
            return bytes(value).decode("ascii", "replace")
        if len(value) == 1:
            return value[0]
    return value


def _as_declared(value, declared):
    """The value in the shape its *declared* type expects.

    GGUF strings come back from the reader as `memmap([113, 119, ...])` - the
    bytes of the word, not the word. Handing that to a STRING writer does not
    pack, and the pack is deferred, so the failure appears as a mystery later.
    """
    from gguf import GGUFValueType
    if declared == GGUFValueType.STRING and hasattr(value, "tolist"):
        raw = value.tolist()
        if isinstance(raw, list) and raw and all(
                isinstance(b, int) and 0 <= b < 256 for b in raw):
            return bytes(raw).decode("utf-8", "replace")
    if hasattr(value, "tolist"):
        return value.tolist()
    return value


def _probe_writer(arch: str):
    """A throwaway writer used to check that one key can be packed.

    gguf-py defers packing to write time, so a key that cannot be expressed
    surfaces only when the whole header is written. Validating each key as it is
    added turns "one unnamed key, after the whole blend" into "this key, now".
    """
    import tempfile
    from gguf import GGUFWriter
    # **A real file, not `None`.** The first version of this probe passed
    # `None`, and `write_kv_data_to_file()` then failed for every key - so the
    # check rejected 28 perfectly good keys including `general.architecture`, and
    # the merged file came out unloadable. A validation that cannot pass is worse
    # than no validation, because its output looks like a finding.
    handle = tempfile.NamedTemporaryFile(suffix=".gguf", delete=False)
    handle.close()
    return GGUFWriter(handle.name, arch)


def metadata(path: Path) -> Dict[str, object]:
    """Every header key as a plain Python value."""
    reader = _reader(path)
    out: Dict[str, object] = {}
    for name, field in reader.fields.items():
        try:
            out[name] = _scalar(field.parts[-1])
        except (AttributeError, IndexError, TypeError):
            continue
    return out


def tensor_entries(path: Path) -> Dict[str, Tuple[Tuple[int, ...], object, object]]:
    """`name -> (shape, data, quant_type)` for every tensor in the file.

    **The quant type is not optional bookkeeping.** `gguf.quants.dequantize`
    takes it as a second argument, and getting that wrong is not a crash - it is
    a silent no-op path, which is why `to_float32` refuses rather than guessing.
    Data is left as gguf-py hands it back so this stays a cheap index operation.
    """
    reader = _reader(path)
    out: Dict[str, Tuple[Tuple[int, ...], object, object]] = {}
    for tensor in reader.tensors:
        out[tensor.name] = (tuple(tensor.shape), tensor.data,
                            getattr(tensor, "tensor_type", None))
    return out


def tensor_map(path: Path) -> Dict[str, Tuple[Tuple[int, ...], object]]:
    """`name -> (shape, data)`, for callers that only compare structure."""
    return {name: (shape, data)
            for name, (shape, data, _q) in tensor_entries(path).items()}


def to_float32(data, qtype=None):
    """A tensor as f32, dequantising when the file is quantised.

    Returns `(array, how)` so a caller can record which path a merge took. **A
    quantised parent must be dequantised before blending and re-quantised once
    after**, because blending quantised blocks and then requantising is the
    lossy-twice step that `llama-quantize --allow-requantize` warns "can severely
    reduce quality".

    Measured on real weights: `gguf.quants.dequantize(data, qtype)` is the call,
    and **the type argument is required** - calling it with one argument silently
    does nothing useful, which is why this raises instead of falling through.
    """
    import numpy as np

    if hasattr(data, "dtype") and data.dtype.kind == "f":
        return np.asarray(data, dtype=np.float32), "stored"
    try:
        from gguf import quants
        if qtype is not None:
            return (np.asarray(quants.dequantize(np.asarray(data), qtype),
                               dtype=np.float32), "dequantized")
    except Exception:                                       # noqa: BLE001
        pass
    raise MergeRefusal(
        f"a tensor could not be read as float32 (quant type {qtype!r}) - this "
        "gguf-py build exposed no working dequantiser for it, so merging would "
        "have to guess at the weights")


def check_compatible(paths: Sequence[Path]) -> Dict[str, object]:
    """Whether these files are the same model. Returns a verdict, not a bool.

    The checks are ordered cheapest-first and each one names itself, because
    "merge failed" over two gigabytes of work is not an error message.
    """
    if len(paths) < 2:
        raise MergeRefusal("a merge needs at least two parents")
    metas = [metadata(p) for p in paths]
    base = metas[0]
    for path, meta in zip(paths[1:], metas[1:]):
        for key in _STRUCTURAL:
            mine, theirs = base.get(key), meta.get(key)
            if mine != theirs:
                raise MergeRefusal(
                    f"{path.name} and {paths[0].name} differ in `{key}`"
                    + ("" if theirs in (None, "") else
                       f" ({str(theirs)[:40]!r} vs {str(mine)[:40]!r})")
                    + " - these are different models, and averaging weights "
                      "across architectures produces a file, not a model")
    shapes = {p.name: tensor_map(p) for p in paths}
    names = set(shapes[paths[0].name])
    for path in paths[1:]:
        theirs = set(shapes[path.name])
        if theirs != names:
            only_them = sorted(theirs - names)[:3]
            only_us = sorted(names - theirs)[:3]
            raise MergeRefusal(
                f"{path.name} and {paths[0].name} do not have the same tensors"
                + (f" (only in {path.name}: {only_them})" if only_them else "")
                + (f" (only in {paths[0].name}: {only_us})" if only_us else "")
                + " - they are not the same architecture")
        for name in names:
            if shapes[path.name][name][0] != shapes[paths[0].name][name][0]:
                raise MergeRefusal(
                    f"`{name}` is {shapes[path.name][name][0]} in {path.name} and "
                    f"{shapes[paths[0].name][name][0]} in {paths[0].name}")
    return {"ok": True, "tensors": len(names),
            "architecture": base.get("general.architecture"),
            "basename": base.get("general.basename")}


def _combine(base_arr, task_vectors, method, density, lam):
    """Combine one tensor's task vectors. Pure arithmetic, no files.

    Split out of `blend` so the arithmetic is testable without a 600 MB file,
    which is the only reason these two algorithms can both be compared at all.

    `task_vectors` is a list of `(array, coefficient)`; `base_arr` is the
    reference the vectors are differences *from*.

    **The two methods, and why there are two.** Naive task arithmetic sums every
    difference from the base; when the parents disagree, contributions cancel
    signal and compound noise. TIES adds the two steps every method in
    mergekit's family converges on: *sparsify* each vector so only its largest
    changes survive, then take a *sign consensus* so a coordinate is decided by
    whoever agrees on its direction rather than by whoever is loudest.

    Measured on the one sibling pair this box has
    (`Qwen3-0.6B-Base` 48.770 against `Qwen3-0.6B` 76.469, same corpus, a 57%
    gap), a midpoint of the two models lands between them and beats neither -
    which is the interference this exists for, observed rather than argued.
    """
    import numpy as np

    reference = np.asarray(base_arr, dtype=np.float32)
    if lam == 0.0 or not task_vectors:
        return reference

    if method == "task_arithmetic":
        total = np.zeros_like(reference)
        for array, coefficient in task_vectors:
            total += np.float32(coefficient) * (np.asarray(array, np.float32)
                                                - reference)
        return reference + np.float32(lam) * total

    if method != "ties":
        raise MergeRefusal(
            f"unknown merge method {method!r} - this is task_arithmetic or "
            "ties, and a name that is neither is a typo rather than a method")

    if not 0.0 < density <= 1.0:
        raise MergeRefusal(
            f"TIES density is {density} - it is the fraction of each task "
            "vector that survives, so 0 drops every change and makes a silent "
            "copy of the base, and anything above 1 is not a fraction")

    # Trim, per tensor. mergekit's TIES trims by magnitude within each tensor;
    # DELLA is the row-wise variant and is not this.
    trimmed = []
    for array, coefficient in task_vectors:
        vector = np.float32(coefficient) * (np.asarray(array, np.float32)
                                            - reference)
        flat = vector.reshape(-1)
        keep = max(1, int(round(float(density) * flat.size)))
        if keep >= flat.size:
            trimmed.append(vector)
            continue
        # The largest `keep` by magnitude; ties broken by index so the result
        # is deterministic rather than dependent on the partition's ordering.
        cut = np.argpartition(np.abs(flat), flat.size - keep)[flat.size - keep:]
        sparse = np.zeros_like(flat)
        sparse[cut] = flat[cut]
        trimmed.append(sparse.reshape(vector.shape))

    # **Elect Sign: majority by count, not by magnitude.** mergekit sums each
    # task vector's *sign* and takes the sign of that count, so a coordinate
    # where three parents agree and one dissents is decided by the three
    # whatever their magnitudes. Summing magnitudes instead would let one large
    # vector outvote three small ones, which is the failure the step exists to
    # avoid. An exact count tie has no majority at all, so it falls back to
    # summed magnitude; that fallback is a stated choice, not mergekit's rule.
    votes = np.zeros_like(reference)
    positive = np.zeros_like(reference)
    negative = np.zeros_like(reference)
    for vector in trimmed:
        votes += np.sign(vector)
        positive += np.where(vector > 0, vector, np.float32(0.0))
        negative += np.where(vector < 0, -vector, np.float32(0.0))
    elected = np.where(votes > 0, np.float32(1.0),
                       np.where(votes < 0, np.float32(-1.0), np.float32(0.0)))
    undecided = elected == 0.0
    elected = np.where(undecided,
                       np.where(positive >= negative, np.float32(1.0),
                                np.float32(-1.0)),
                       elected)

    # Disjoint mean: only the contributions that agree with the elected sign
    # count, and only when at least one does. A coordinate with no agreement
    # keeps the base, because there is no change worth importing.
    agree = np.zeros_like(reference)
    counts = np.zeros(reference.shape, dtype=np.int32)
    for vector in trimmed:
        mask = np.sign(vector) == elected
        agree += np.where(mask, vector, np.float32(0.0))
        counts += mask
    total = np.divide(agree, np.maximum(counts, 1),
                      out=np.zeros_like(reference), where=counts > 0)
    return reference + np.float32(lam) * total


def blend(parents: Sequence[Path], weights: Sequence[float],
          base: Optional[Path] = None,
          out_path: Optional[Path] = None,
          method: str = "task_arithmetic",
          density: float = 0.5,
          lam: float = 1.0) -> Path:
    """`base + sum(lambda_i * (parent_i - base))`, written as a single F16 GGUF.

    `base` defaults to `parents[0]` with its coefficient taken as the reference,
    so `blend([a, b], [0.0, 1.0])` returns `b` in value - the property that makes
    the coefficients legible rather than folklore.

    **The metadata section is copied, not rebuilt.** Every earlier version
    reconstructed it through `gguf-py`'s typed writer and lost 15 of 31 keys on a
    real Qwen3 file, which `llama-quantize` then refused. The weights are the only
    thing that changed, so `gguf_write.kv_section_bytes` hands back the parent's
    own bytes and only the tensor infos and the tensor data are written here.
    """
    import numpy as np

    if len(weights) != len(parents):
        raise MergeRefusal(
            f"{len(weights)} coefficients for {len(parents)} parents - a weight "
            "with no parent is silently ignored, which is how a merge ends up "
            "not containing the model you thought it did")
    check_compatible(parents)

    from shani_chronoa import gguf_write

    reference = Path(base) if base else Path(parents[0])
    ref_map = tensor_entries(reference)
    metadata_bytes, tensor_count, alignment = gguf_write.kv_section_bytes(reference)

    if len(ref_map) != tensor_count:
        raise MergeRefusal(
            f"the parent's own preamble declares {tensor_count} tensors but "
            f"{len(ref_map)} were read - writing that would produce a file whose "
            "header disagrees with its own body")

    # The reference is only loaded twice when it is also a contributor, and the
    # second read is skipped when its coefficient is zero - which is what makes
    # `blend([a, b], [0.0, 1.0])` read `a` once and `b` once.
    paths = [Path(p) for p in parents]
    loaded = {i: tensor_entries(p) for i, p in enumerate(paths) if weights[i]}
    ref_float = {name: to_float32(data, qtype)[0]
                 for name, (_shape, data, qtype) in ref_map.items()}

    target = Path(out_path) if out_path else Path(
        tempfile.mkdtemp(prefix="chronoa-merge-")) / "merged-f16.gguf"
    target.parent.mkdir(parents=True, exist_ok=True)

    # A tensor whose parent stores it in a float type keeps that type; a
    # quantised one is dequantised and written F16, so the whole merge still has
    # exactly one lossy step.
    from gguf.quants import GGMLQuantizationType as _T
    _keep = {_T.F32: gguf_write.GGML_TYPE_F32, _T.BF16: gguf_write.GGML_TYPE_BF16}

    hows = set()
    blended = []
    for name, (_shape, _ref_data, ref_type) in ref_map.items():
        vectors = []
        for index, coefficient in enumerate(weights):
            if not coefficient:
                continue
            _shape, data, qtype = loaded[index][name]
            array, how = to_float32(data, qtype)
            hows.add(how)
            vectors.append((array, coefficient))
        acc = _combine(ref_float[name], vectors, method, density, lam)
        blended.append((name, acc, _keep.get(ref_type,
                                             gguf_write.GGML_TYPE_F16)))

    gguf_write.write_gguf(target, metadata_bytes, tensor_count, alignment,
                          blended, ggml_type=gguf_write.GGML_TYPE_F16)
    logger.info("merge: wrote %s from %d parents (%s)", target.name, len(parents),
                ", ".join(sorted(hows)) or "stored")
    return target


def digest(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def quantize(source: Path, target: Path, quant: str = "Q4_K_M") -> Optional[Path]:
    """Quantise once, with `llama-quantize`. Returns None if it is not installed.

    **Once, not twice.** The merge is already the lossy step; requantising a
    blended quant would be a second one, which is what
    `--allow-requantize` warns "can severely reduce quality".
    """
    binary = shutil.which("llama-quantize")
    if not binary:
        logger.warning("merge: llama-quantize is not installed; leaving %s f16",
                       source.name)
        return None
    done = subprocess.run([binary, str(source), str(target), quant],
                          capture_output=True, text=True, timeout=None)
    if done.returncode != 0 or not Path(target).is_file():
        logger.error("merge: quantize failed: %s",
                     (done.stderr or done.stdout)[-400:])
        return None
    return Path(target)


def perplexity(path: Path, corpus: Optional[Path] = None) -> Optional[float]:
    """The measured perplexity, or None if it could not be measured.

    **A missing `llama-perplexity` is a `None`, never a pass.** `distill.use()`
    takes the same stance: "installed" and "usable" are different claims, and a
    gate that cannot run must not be a gate that says yes.
    """
    # **A path that does not exist is not measurable, and saying so is the whole
    # contract.** The first version logged `path.name` in its own warning, so
    # passing the `None` that `quantize()` returns for a failed or absent
    # `llama-quantize` raised `AttributeError` *inside the probe* - which reads
    # as the merge being broken rather than as the measurement never happening.
    # `quantize` returns None for three different reasons and all three mean
    # "unmeasured", so the None is checked before anything else touches it.
    if path is None or not Path(path).is_file():
        logger.warning("merge: no model file to measure (%s)", path)
        return None
    binary = shutil.which("llama-perplexity")
    if not binary:
        return None
    args = [binary, "-m", str(path)]
    if corpus and Path(corpus).is_file():
        args += ["-f", str(corpus)]
    else:
        try:
            from shani_chronoa import local_llm
            text = getattr(local_llm, "_CORPUS", "") * 8
            if not text:
                return None
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                             encoding="utf-8") as handle:
                handle.write(text)
                corpus = Path(handle.name)
            args += ["-f", str(corpus)]
        except Exception:                                   # noqa: BLE001
            return None
    args += ["-c", "256", "-t", "4", "--no-mmap"]
    done = subprocess.run(args, capture_output=True, text=True, timeout=None)
    # The estimate is printed on **stderr**, which cost me a run to discover.
    match = re.search(r"Final estimate:\s*PPL\s*=\s*([0-9.]+)",
                      (done.stderr or "") + (done.stdout or ""))
    if not match:
        logger.warning("merge: no perplexity in %s's output", path.name)
        return None
    return float(match.group(1))


def gate(merged: Path, parents: Sequence[Path],
         corpus: Optional[Path] = None,
         quant: Optional[str] = None) -> Dict[str, object]:
    """Whether the merge is worth serving, measured against **both** parents.

    A merge that beats neither parent has produced nothing worth loading. Being
    *worse than the base* is the clearer failure and is reported separately,
    because "the merge did not help" and "the merge damaged the base" call for
    different responses.

    **`quant` compares like with like, and without it the gate is wrong.**

    A merge is written F16 and then quantised **once** - that is the whole point
    of `blend` - so the file being judged is Q4_K_M while its parents are sitting
    on disk in whatever they shipped as, which here is Q8_0. The first version
    compared those two directly and therefore charged the entire requantization
    cost to the merge. Measured on Qwen3-0.6B, an identity merge - lambda = [1,
    0], which *is* the base, bit for bit - came back 55.374 against the base's
    48.770 and was **refused for being 13.6% worse than the thing it is**. The
    requantization alone costs 10-14% on this checkpoint depending on the
    parent, and it is the same number for every merge.

    Pass the quantisation the merge was written at and each parent is requantised
    to match before anything is compared. Both figures are recorded, so the
    cost is visible rather than silently distributed - `scores` holds each
    parent's own number under its own name and the matched one beside it.

    **Three verdicts, not two.** A merge that exactly *equals* its better parent
    has added nothing and is not a failure either, and "no worse" is not "better":
    the first version's success sentence claimed a merge tied for first had
    "beats every measured parent", which is the kind of sentence that makes a
    number read as a result.
    """
    scratch = None
    try:
        rivals = []
        scores: Dict[str, object] = {}
        for parent in parents:
            own = perplexity(parent, corpus)
            scores[parent.name] = own
            measured = own
            if quant:
                try:
                    if scratch is None:
                        import tempfile
                        scratch = Path(tempfile.mkdtemp(prefix="merge-gate-"))
                    same = quantize(parent, scratch / (parent.stem + f".{quant}.gguf"),
                                    quant)
                    measured = perplexity(same, corpus) if same else None
                except Exception as problem:                 # noqa: BLE001
                    # Falling back to the parent's own number keeps the gate
                    # answering rather than crashing, which is right - and a
                    # refusal that cannot say why is the thing this module is
                    # for, so the reason is recorded rather than swallowed.
                    measured = None
                    scores[parent.name + " (same quant)"] = (
                        "not measured: %s" % problem)
                if measured is not None:
                    scores[parent.name + " (same quant)"] = measured
            if measured is not None:
                rivals.append(measured)
        merged_score = perplexity(merged, corpus)
        scores["merged"] = merged_score
        if merged_score is None:
            return {"ok": False, "ship": False, "verdict": "unmeasured",
                    "why": "the merged model's quality could not be measured, "
                    "and an unmeasured merge is not a good merge",
                    "scores": scores}
        if not rivals:
            return {"ok": False, "ship": False, "verdict": "unmeasured",
                    "why": "no parent's quality could be measured, so there is "
                    "nothing to compare against", "scores": scores}
        best_parent = min(rivals)
        if merged_score > best_parent:
            return {"ok": False, "ship": False, "verdict": "worse",
                    "why": f"the merge is worse than its best parent "
                           f"({merged_score:.3f} vs {best_parent:.3f}), so it "
                           "has cost quality rather than adding any",
                    "scores": scores}
        if merged_score == best_parent:
            return {"ok": False, "ship": False, "verdict": "no-better",
                    "why": f"the merge is no better than its best parent "
                           f"({merged_score:.3f}), so it has cost disk and added "
                           "nothing", "scores": scores}
        return {"ok": True, "ship": True, "verdict": "better",
                "why": f"beats every measured parent ({merged_score:.3f} vs "
                       f"{best_parent:.3f})", "scores": scores}
    finally:
        if scratch is not None:
            for leftover in scratch.glob("*"):
                try:
                    leftover.unlink()
                except OSError:
                    pass
            try:
                scratch.rmdir()
            except OSError:
                pass


def provenance(parents: Sequence[Path], weights: Sequence[float],
               verdict: Dict[str, object], quantized_to: Optional[Path] = None,
               corpus: Optional[Path] = None) -> Dict[str, object]:
    """The record: who the parents were, what they hashed to, and the verdict.

    **A merge's whole reason for existing is provenance**, so a merged file that
    cannot say what went into it has thrown away the only thing it was better
    than a download for.
    """
    return {
        "kind": "merge",
        "parents": [{"filename": p.name, "sha256": digest(p)} for p in parents],
        "weights": list(weights),
        "quantized_to": quantized_to.name if quantized_to else None,
        "perplexity": verdict.get("scores"),
        "ship": verdict.get("ship"),
        "why": verdict.get("why"),
        "corpus_sha256": digest(corpus) if corpus and Path(corpus).is_file() else None,
    }
