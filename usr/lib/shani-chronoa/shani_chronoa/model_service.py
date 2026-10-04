"""The model servers beside the brain: one systemd template unit, one env file per server.

The brain (`local_llm`, `shani-chronoa-llm.service`) came first and keeps its
own unit. Everything added after it - eyes (a llama.cpp vision model), memory
(a llama.cpp embedding model) and imagine (stable-diffusion.cpp's
`sd-server`) - runs from `shani-chronoa-model@.service`, whose instance name
picks `~/.config/shani-chronoa/model-<instance>.env`:

    MODEL_BIN=/usr/bin/llama-server
    MODEL_ARGS=-m ... --host 127.0.0.1 --port 8767 ...

Every server listens on 127.0.0.1 only, on its own port, and is reached over
HTTP. That is the reason they are services rather than programs a skill runs:
a skill's child is capped at 512 MB of address space by the sandbox profile
(`sandbox/profiles.py`), and a 2 GB image model cannot load under it - nor
should a skill's limits be loosened to let it. A server the user started from
setup is the user's own process, like the brain.

Ports: brain 8765, whisper-server 8766, then the ones here.
"""

from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from shani_chronoa import files

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
TEMPLATE = "shani-chronoa-model@{}.service"

#: instance -> port. A new server takes the next free port here, never one in use.
PORTS = {"vision": 8767, "embed": 8768, "imagine": 8769}


def unit(instance: str) -> str:
    return TEMPLATE.format(instance)


def base_url(instance: str) -> str:
    return f"http://{HOST}:{PORTS[instance]}"


def env_file(instance: str) -> Path:
    return files.config_home() / "shani-chronoa" / f"model-{instance}.env"


def write_env(instance: str, binary: str, args: Sequence[str]) -> Path:
    """The file the unit reads. `args` are joined with spaces, so no argument may contain one."""
    if instance not in PORTS:
        raise ValueError(f"unknown model server {instance!r}")
    for arg in args:
        if not arg or any(c.isspace() for c in arg) or any(c in arg for c in "$\\'\"`"):
            # systemd splits an unbraced $VAR on whitespace and would expand a $
            # inside it; a model path under a home with a space in it would be
            # split into two arguments and fail to load, so say so here instead
            raise ValueError(f"model server argument {arg!r} cannot be passed through an env file")
    path = env_file(instance)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"MODEL_BIN={binary}\nMODEL_ARGS={' '.join(args)}\n", encoding="utf-8")
    return path


def read_env(instance: str) -> "tuple[str, list[str]]":
    """(binary, args) as the unit would run them; ('', []) when the server was never set up."""
    try:
        text = env_file(instance).read_text(encoding="utf-8")
    except OSError:
        return "", []
    values = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    return values.get("MODEL_BIN", ""), shlex.split(values.get("MODEL_ARGS", ""))


def _systemctl(*args: str) -> "subprocess.CompletedProcess | None":
    if shutil.which("systemctl") is None:
        return None
    try:
        return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True,
                              timeout=30, check=False)
    except subprocess.TimeoutExpired:
        return None


def start(instance: str) -> str:
    """Enable and (re)start one server; '' on success, else why not."""
    if not env_file(instance).is_file():
        return "it has not been set up yet"
    proc = _systemctl("enable", unit(instance))
    if proc is None:
        return "systemctl is not available"
    proc = _systemctl("restart", unit(instance))
    if proc is None or proc.returncode != 0:
        return ((proc.stderr if proc else "") or "systemctl failed").strip()[:200]
    return ""


def stop(instance: str) -> None:
    _systemctl("disable", "--now", unit(instance))


def is_up(instance: str, path: str = "/health", timeout: float = 1.5) -> bool:
    import httpx
    try:
        return httpx.get(base_url(instance) + path, timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


def wait_up(instance: str, seconds: float, path: str = "/health", cancel=None) -> bool:
    """Poll until the server answers or `seconds` pass; `cancel` (a threading.Event) stops early."""
    import time
    deadline = time.monotonic() + seconds
    while True:
        if is_up(instance, path):
            return True
        if time.monotonic() >= deadline or (cancel is not None and cancel.is_set()):
            return False
        time.sleep(1.0)
