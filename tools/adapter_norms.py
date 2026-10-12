"""Per-adapter delta norms on the three lumen adapters, measured not assumed.

**This is the number the chimera path turns on.** The three adapters carry
identical `alpha/rank` at 2.0, so a reader expects comparable strength. They are
**134x** apart: delta norms 0.0732 / 6.5299 / 9.7797. The difference lives in
`lora_b`, which is not what `alpha/rank` declares.

*Crowded in B-Space* (arXiv:2604.16826) is titled for exactly this spread, and
the recipe is arithmetic: rescale every adapter to a common delta norm before
combining, so three capabilities contribute equally instead of one being a
rounding error beside two enormous ones. The caveat measured earlier on this
model stands - a *spectral* rescale lands at x1.4230 for 50% truncation.

**The first version of this probe measured nothing** because it assumed the
layers: it filtered on `q/k/v/o_proj` and the adapters target
`ffn_down`/`ffn_gate`/`ffn_up`. An empty list reads as "no adapters found", so
this reads every pair and prints the families it found.
"""

import argparse
import collections
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "cache" / "cultivate-libs"))

MODELS = REPO / "cache" / "data-home" / "shani-chronoa" / "llm"

ADAPTERS = ["lumen-mouth-lora.gguf", "lumen-cortex-lora.gguf",
            "lumen-executor-lora.gguf"]


def _values(block, np):
    if block.tensor_type.name.upper() == "F32":
        return np.array(block.data, dtype=np.float32).astype(np.float64)
    from gguf.quants import dequantize
    return np.asarray(
        dequantize(block.data, block.tensor_type.name), dtype=np.float64)


def read_adapter(path):
    """({target: Frobenius norm of B@A}, total, target families)."""
    np = __import__("numpy")
    from gguf import GGUFReader

    reader = GGUFReader(str(path))
    slots = collections.defaultdict(dict)
    for t in reader.tensors:
        if t.name.endswith(".lora_a"):
            slots[t.name[: -len(".lora_a")]]["a"] = t
        elif t.name.endswith(".lora_b"):
            slots[t.name[: -len(".lora_b")]]["b"] = t

    per, total, fams = {}, 0.0, collections.Counter()
    for key, pair in sorted(slots.items()):
        if "a" not in pair or "b" not in pair:
            continue
        a_t, b_t = pair["a"], pair["b"]
        A = _values(a_t, np).reshape(a_t.shape)
        B = _values(b_t, np).reshape(b_t.shape)
        delta = B @ A if A.shape[-1] == B.shape[-1] else A @ B
        n = float(np.linalg.norm(delta))
        per[key] = n
        total += n * n
        fams[key.rsplit(".", 1)[-1]] += 1
    return per, total ** 0.5, fams


def main():
    ap = argparse.ArgumentParser()
    ap.parse_args()

    rows, fams = [], None
    for name in ADAPTERS:
        p = MODELS / name
        if not p.exists():
            print("absent:", name)
            continue
        per, total, f = read_adapter(p)
        fams = f
        rows.append((name, total, per))
        print(f"{name:<26} total {total:9.4f}   per-tensor "
              f"min {min(per.values()):9.5f}  max {max(per.values()):9.5f}  "
              f"n={len(per)}")

    if not rows:
        print("no adapters found; nothing measured")
        return 1

    if fams:
        print("\ntargets:", dict(fams))
    vals = [r[1] for r in rows]
    print(f"spread {max(vals) / min(vals):.1f}x between weakest and strongest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
