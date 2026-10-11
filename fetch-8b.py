#!/usr/bin/env python3
"""Download the qwen3-8b tier into the REAL model dir and verify it.

Deliberately leaves HOME alone: `local_llm.model_dir()` resolves through
`files.data_home()`, which follows `Path.home()`. An earlier attempt redirected
HOME to a temp dir and the whole 4.7 GB landed there, was verified, and was then
deleted - a correct download thrown away by the harness. Only the config and
state dirs are redirected here, for the keyfile-schema isolation.
"""
import sys
from shani_chronoa import local_llm
from shani_chronoa.config import ChronoaConfig

print("model_dir:", local_llm.model_dir(), flush=True)
config = ChronoaConfig()
config.set("model-download-enabled", "true")
config.set("privacy-mode", "false")

last = [0]


def progress(done, total):
    mib = int(done / 1048576)
    if mib >= last[0] + 500:
        last[0] = mib
        print(f"  {mib} / {int(total / 1048576)} MiB", flush=True)


path = local_llm.provision("qwen3-8b", progress=progress)
print("installed:", path, flush=True)
print("verify():", local_llm.verify("qwen3-8b"), flush=True)
print("active():", local_llm.active(), flush=True)
