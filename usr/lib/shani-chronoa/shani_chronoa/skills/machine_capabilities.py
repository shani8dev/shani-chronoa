"""Skill: what this machine can actually do, asked now rather than assumed.

Chronoa ships a capability matrix - 3,990 commands, 227 enabled units - and it
is correct for the image it was generated against. It is **not** a description
of the machine running this copy, and the difference is not academic: on the
machine this was written, the matrix's packages and the machine's actual
contents disagree about `sox`, `tesseract`, `magick`, `numpy` and `sympy`.

So the question is asked live. `which` costs microseconds, `systemctl
is-enabled` costs about 30ms and is cached per unit, and the python-module
probe catches the case that matters most for a learning layer: a module that
imports cleanly on a developer machine and is absent on a fresh image.

**A capability is reported three ways, not two.** Present-and-runnable is the
obvious one. **Broken** is the one that earns its keep: a command that exists,
runs, and returns an error is what a static inventory cannot see, and it is
the reason a recommendation can be wrong in a way nothing predicted.
"""

from __future__ import annotations

from shani_chronoa.capability import capabilities, describe
from shani_chronoa.skills import Skill

SCHEMA = {
    "type": "function",
    "function": {
        "name": "machine_capabilities",
        "description": (
            "Report what this machine can actually do: which of the commands "
            "Chronoa knows how to use are really present, which Python modules "
            "import here, and which services are enabled. Asks the live machine "
            "rather than reading the shipped inventory, which describes the image "
            "it was built from and not necessarily this one. Use it before "
            "offering to convert a file, edit an image, read a document or "
            "capture audio, to find out whether that is even possible here."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _run(arguments: dict) -> str:
    cap = capabilities(refresh=bool(arguments.get("refresh")))
    return describe(cap)


SKILLS = [Skill(name="machine_capabilities", schema=SCHEMA, run=_run)]
