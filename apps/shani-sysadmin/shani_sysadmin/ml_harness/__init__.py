"""Legacy ML harnesses kept out of Chronoa's assistant surface.

These modules were written for model work (routing distillation, GGUF
quantisation, LoRA cultivation and model merging). None is a skill: Chronoa
never offered them to the model, and the two that still have callers there
(``distill`` and ``model_registry``) reach them through import shims in
``shani_chronoa``. Keeping them here means the code survives the split with
one owner; the shims are names, not implementations.
"""

from __future__ import annotations
