"""Skill: what does a command do, or which command does something - offline.

From the manuals already on this machine: tealdeer's `tldr` pages (short,
example-first) when its page cache exists, else the man page's own NAME and
SYNOPSIS (man-db), and `apropos` for "which command renames files". Nothing
leaves the machine, and nothing is run except the documentation tools.
"""

import re
import shutil
import subprocess

from shani_chronoa.skills import Skill

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "explain_command",
        "description": "Explain a Linux shell command (not a general topic) from the local manuals (tldr, man), e.g. "
                       "'what does tar do', 'how do I use rsync'; or find which command does "
                       "something, e.g. 'which command shows disk usage'.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "A command name, e.g. 'tar'."},
            "task": {"type": "string", "description": "Or: what you want to do, to find a command for it."},
        }},
    },
}


def _out(argv, timeout=10) -> str:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           env={"MANWIDTH": "100", "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _tldr(cmd: str) -> str:
    if not shutil.which("tldr"):
        return ""
    text = _out(["tldr", "--quiet", "--color", "never", cmd])
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _man(cmd: str) -> str:
    text = _out(["man", "-P", "cat", cmd], timeout=15)
    if not text:
        return ""
    text = re.sub(r".\x08", "", text)  # overstrike formatting
    keep, section = [], None
    for line in text.splitlines():
        if re.match(r"^[A-Z][A-Z ]+$", line):
            section = line.strip()
            continue
        if section in ("NAME", "SYNOPSIS", "DESCRIPTION") and line.strip():
            keep.append((section, line.strip()))
    name = " ".join(l for s, l in keep if s == "NAME")
    syn = [l for s, l in keep if s == "SYNOPSIS"][:3]
    desc = " ".join(l for s, l in keep if s == "DESCRIPTION")[:500]
    return f"{name}\nUsage: {' | '.join(syn)}\n{desc}".strip()


def _run(arguments: dict) -> str:
    cmd = (arguments.get("command") or "").strip()
    task = (arguments.get("task") or "").strip()
    if cmd:
        if not re.fullmatch(r"[A-Za-z0-9._+-]{1,64}", cmd):
            return f"'{cmd}' is not a command name."
        page = _tldr(cmd) or _man(cmd)
        return page[:1500] if page else f"There is no manual for '{cmd}' on this machine."
    if task:
        words = [w for w in re.findall(r"[a-z]{3,}", task.lower()) if w not in {"how", "the", "which", "command", "can", "what", "for"}]
        if not words:
            return "What do you want to do?"
        lines = _out(["apropos", "-a", *words[:3]]).splitlines() or _out(["apropos", words[0]]).splitlines()
        lines = [l for l in lines if "(1)" in l or "(8)" in l][:8]
        return ("Commands that may do that:\n" + "\n".join(lines)) if lines else f"No command's summary mentions '{task}'."
    return "Which command, or what do you want to do?"


SKILLS = [Skill(name="explain_command", schema=_SCHEMA, run=_run)]
