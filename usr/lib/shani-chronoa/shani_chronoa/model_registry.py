"""A catalog of the model files on this machine, read from the files themselves.

**Why this module exists.** `local_llm.SPECS` answers "what does Chronoa ship
and vouch for". It could not answer "what is on this machine", and the two came
apart in a way that was measured rather than argued: put a GGUF on disk that is
not one of the six pinned filenames and `installed()` returned `[]`, `active()`
returned `""`, and the mode strip said **"No model"** - while `current.gguf`
pointed at a file `llama-server` would have loaded. `installed()` was checking
`SPECS`, so a shared model was not *unavailable*; it was *unmentionable*.

**Structure comes from `modelfmt`, and only structure does.** `modelfmt` owns the
sidecar / adapter / vocab / shard tables, read from the GGUF naming convention,
and owns format identification by magic. This module adds the two things only it
can know, because it has read the header: whether the file **declares an
architecture**, and what **identity and provenance** it carries.

**One definition per question.** An earlier version of this file carried its own
copy of the sidecar table while `modelfmt` grew the same one, and the two
disagreed in a run rather than in review - `modelfmt`'s `*.gguf -> llm` glob
called `mmproj-SmolVLM2.gguf` a language model while this file excluded it as a
sidecar. Then the reverse mistake: delegating `classify` wholesale to
`modelfmt.kind_of` lost the architecture check, because a filename cannot tell
you what a file implements, and a GGUF with no `general.architecture` was offered
as a model again. Both were caught by running the probes, not by reading. So the
line is: **structure from `modelfmt`, header facts from here, and each question
answered in exactly one place.**

**A registry is not a tier.** `SPECS` entries carry a pinned size and sha256, and
`use()`'s quality gate compares against them. A file from anywhere else has
neither, so `register()` computes a digest and flags `vouched=False` - a visible
difference rather than an implied one. Nothing has checked that file, and the
catalog says so rather than implying otherwise.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from shani_chronoa import ctxfit, modelfmt


def _shard_of(name: str) -> Tuple[int, int]:
    """`(number, total)` for a split model, or `(0, 0)` for a whole one."""
    hit = modelfmt._SHARD_RE.search(name)
    if not hit:
        return 0, 0
    return int(hit.group("num")), int(hit.group("total"))


def _text(value) -> str:
    """A GGUF string, which arrives as a byte array (see `ctxfit`)."""
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    return str(value)


def describe(path: Path, info: Optional[dict] = None) -> Dict[str, object]:
    """What this file says about itself, with the header winning over its name.

    `info` is `ctxfit.gguf_model_info(path)`'s dict, passed in so a caller
    scanning many files reads each header once.
    """
    path = Path(path)
    info = info if info is not None else ctxfit.gguf_model_info(path)

    def meta(key: str) -> str:
        return _text((info or {}).get(key))

    parts = modelfmt._from_filename(path.name)
    return {
        "path": path,
        "filename": path.name,
        # `general.architecture` is the spec's one required key: "describes what
        # architecture this model implements". Without it nothing can load it, so
        # `classify` treats it as disqualifying rather than as a missing nicety.
        "architecture": meta("general.architecture"),
        # The spec says `general.size_label` is the size class "useful for leader
        # boards", which is exactly what a picker should print: the file names
        # itself, and a key somebody typed does not get to override it.
        "word": meta("general.size_label") or parts.get("SizeLabel", ""),
        "basename": meta("general.basename") or parts.get("BaseName", ""),
        "finetune": meta("general.finetune") or parts.get("FineTune", ""),
        "version": meta("general.version") or parts.get("Version", ""),
        "license": meta("general.license"),
        "uuid": meta("general.uuid"),
        "source_url": meta("general.source.url") or meta("general.repo_url"),
        "quantization_version": (info or {}).get("general.quantization_version"),
        "had_header": bool(info),
    }


def classify(entry: Dict[str, object]) -> Tuple[str, str]:
    """`(kind, reason)` for one described file.

    **Two questions, kept apart.** Structure - sidecar, adapter, vocab, shard -
    is `modelfmt.kind_of`'s answer and this module does not restate it. Whether
    the header declares an architecture is answered here, because only this
    module has read the header.
    """
    kind, reason = modelfmt.kind_of(str(entry["filename"]))
    if kind != "model":
        return kind, reason
    if not entry.get("architecture"):
        return ("not-a-model",
                "no `general.architecture` in the header, so nothing knows what "
                "it implements")
    return "model", ""


def _digest(path: Path) -> str:
    """sha256 of a file, streamed. The same primitive `SPECS` digests are checked
    with, so a registered file and a shipped tier are verified identically."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _tier_of(filename: str, specs: dict) -> Optional[str]:
    return next((k for k, s in specs.items() if s.filename == filename), None)


def scan(model_dir: Optional[Path] = None,
         specs: Optional[dict] = None) -> Dict[str, object]:
    """Everything in the model directory, classified.

    Returns `models` (loadable), `excluded` (each with a stated reason) and
    `unreadable`. The last is separate on purpose: **a file that could not be
    opened is a permissions problem, and reporting it as a format problem is a
    confident wrong answer.** An earlier version answered "no
    `general.architecture` in the header" about a root-owned mode-0600 file whose
    header it had never read.
    """
    from shani_chronoa import local_llm        # imported here: local_llm uses us

    if model_dir is None:
        model_dir = local_llm.model_dir()
    specs = specs if specs is not None else getattr(local_llm, "SPECS", {})

    models: List[Dict[str, object]] = []
    excluded: List[Dict[str, object]] = []
    unreadable: List[Dict[str, object]] = []

    try:
        files = sorted(p for p in Path(model_dir).glob("*.gguf")
                       if p.name != "current.gguf" and not p.is_symlink())
    except OSError:
        files = []

    for path in files:
        try:
            info = ctxfit.gguf_model_info(path)
        except Exception:                                   # noqa: BLE001
            info = None

        entry = describe(path, info)
        if not info:
            # Filename structure still says something true when the header cannot
            # be read, so a LoRA is still called a LoRA - but nothing is claimed
            # about whether it *is* a model.
            kind, reason = classify(entry)
            if kind in ("model", "not-a-model"):
                kind = "unreadable"
                reason = ("the GGUF header could not be read, so whether this is "
                          "a model is unknown - a truncated download and a "
                          "tokenizer file are not the same problem")
            excluded.append({"entry": entry, "kind": kind, "reason": reason})
            continue

        kind, reason = classify(entry)
        if kind != "model":
            excluded.append({"entry": entry, "kind": kind, "reason": reason})
            continue

        tier = _tier_of(path.name, specs)
        entry["kind"] = kind
        entry["key"] = tier or ""
        entry["tier"] = tier
        entry["vouched"] = tier is not None
        if tier:
            entry["sha256"] = specs[tier].sha256
        _, total = _shard_of(path.name)
        if total:
            entry["incomplete"] = f"needs shards 2-{total} beside it"
        models.append(entry)

    return {"models": models, "excluded": excluded,
            "unreadable": unreadable, "dir": Path(model_dir)}


def active_entry(model_dir: Optional[Path] = None,
                 specs: Optional[dict] = None) -> Optional[Dict[str, object]]:
    """What `current.gguf` points at, described - **whatever it is**.

    This is the fix to the measured defect. `local_llm.active()` returns a
    `SPECS` key or `""`, so a shared model resolved to nothing and every reader
    rendered that as absence. Here the file is resolved through `realpath` and
    described from its own header, so an imported model is *named* rather than
    denied, and the caller decides what `vouched=False` means.
    """
    from shani_chronoa import local_llm

    if model_dir is None:
        model_dir = local_llm.model_dir()
    specs = specs if specs is not None else getattr(local_llm, "SPECS", {})
    try:
        target = Path(os.path.realpath(Path(model_dir) / "current.gguf"))
    except OSError:
        return None
    if not target.is_file():
        return None

    entry = describe(target, ctxfit.gguf_model_info(target))
    kind, reason = classify(entry)
    entry["kind"] = kind
    entry["reason"] = reason
    tier = _tier_of(target.name, specs)
    entry["key"] = tier or ""
    entry["tier"] = tier
    entry["vouched"] = tier is not None
    if tier:
        entry["sha256"] = specs[tier].sha256
    return entry


def register(entry: Dict[str, object]) -> Dict[str, object]:
    """Compute a digest for an imported file so it can be verified like a tier.

    **Returns the verdict; writes nothing.** A registry file is new on-disk state
    that something else would then trust, and the honest first version is the one
    that reports what it found. `verify_digest` is the check a caller performs on
    every later run, and it is the same one shipped tiers already get.
    """
    path = Path(entry["path"])
    return {
        "filename": path.name,
        "sha256": _digest(path),
        "size_bytes": path.stat().st_size,
        "architecture": entry.get("architecture", ""),
        "license": entry.get("license", ""),
        "source_url": entry.get("source_url", ""),
        "uuid": entry.get("uuid", ""),
        "vouched": False,
    }


def verify_digest(path: Path, expected: str) -> bool:
    """Whether `path` still hashes to `expected` - the question `verify()` asks of
    a shipped tier, asked of an imported one."""
    try:
        return _digest(Path(path)) == expected
    except OSError:
        return False
