"""Skill: checksum a file, to check a download or prove two files match.

One of the most basic verification tasks there is, and the project could not do
it. Uses `hashlib` from the standard library, so there is no `sha256sum` to be
missing and no dependency to declare.

Reports the *expected* checksum alongside the computed one when the caller has
one, because a hash on its own answers no question - the question is always
"does this match?".

Honesty rules: a missing file is reported as missing, never as a hash of
nothing. A partially-read file is not silently hashed, since a checksum of half
a file is worse than none - it looks authoritative and is not.
"""

from __future__ import annotations

import hashlib

from shani_chronoa import files
from shani_chronoa.skills import Skill

_ALGORITHMS = ("sha256", "sha512", "sha1", "md5")
_CHUNK = 1024 * 1024
#: Above this the read is refused rather than attempted. md5 on a 40 GiB file
#: is a long wait with no security value, and the caller can still ask for the
#: right algorithm.
_MAX_BYTES = 8 * 1024 * 1024 * 1024

SCHEMA = {
    "type": "function",
    "function": {
        "name": "compute_hash",
        "description": (
            "Compute a checksum of a file, and optionally compare it with an "
            "expected value. Defaults to SHA-256, which is what package "
            "managers and download sites publish."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file to checksum."},
                "algorithm": {
                    "type": "string",
                    "description": f"One of: {', '.join(_ALGORITHMS)}. Defaults to sha256.",
                },
                "expected": {
                    "type": "string",
                    "description": (
                        "The checksum to compare against. When given, the reply "
                        "says whether they match rather than only printing a "
                        "value."
                    ),
                },
            },
        },
    },
}


def _run(arguments: dict) -> str:
    try:
        target = files.resolve(arguments.get("path") or "")
    except files.PathProblem as exc:
        return str(exc)
    if target.is_dir():
        return f"Refusing to checksum {target}: it is a directory."
    if not target.exists():
        return files.describe(FileNotFoundError(2, "No such file or directory"),
                              target, "checksum")

    algo = (arguments.get("algorithm") or "sha256").strip().lower().replace("-", "")
    if algo not in _ALGORITHMS:
        return f"Algorithm must be one of {', '.join(_ALGORITHMS)}, not {algo!r}."

    try:
        size = target.stat().st_size
    except OSError as exc:
        return files.describe(exc, target, "checksum")
    if size > _MAX_BYTES:
        return (
            f"Refusing to checksum {target}: it is {files.human_size(size)}, "
            f"over the {files.human_size(_MAX_BYTES)} limit. That is a long wait "
            f"with little value; checksum the archive or a checksum file instead."
        )

    try:
        digest = hashlib.new(algo)
        read = 0
        with open(target, "rb") as handle:
            while True:
                chunk = handle.read(_CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
                read += len(chunk)
    except OSError as exc:
        return files.describe(exc, target, "checksum")

    if read != size:
        return (
            f"Stopped: read {files.human_size(read)} of a "
            f"{files.human_size(size)} file, so no checksum is reported. A hash "
            f"of part of a file looks authoritative and is not."
        )

    got = digest.hexdigest()
    line = f"{algo.upper()}({target.name}) = {got}"

    expected = (arguments.get("expected") or "").strip().lower()
    if not expected:
        return line + f"  [{files.human_size(size)}]"

    if len(expected) != len(got):
        return (
            f"{line}\nDoes NOT match: the expected value is {len(expected)} "
            f"characters and {algo.upper()} produces {len(got)}, so the two "
            f"cannot be compared - check the algorithm."
        )
    if expected == got:
        return f"{line}\nMatches the expected value."
    differing = sum(1 for a, b in zip(expected, got) if a != b)
    return (
        f"{line}\nDoes NOT match the expected value ({differing} of "
        f"{len(got)} characters differ). The file is not the one that was "
        f"expected."
    )


SKILLS = [Skill(name="compute_hash", schema=SCHEMA, run=_run)]
