"""Skill: send a file to a printer.

The `printing` *sense* reports what the machine can print to and scan from; this
is the act. They are separate because knowing a queue exists is passive and
sending a document to it is not - a misheard file name puts a page of somebody's
mistaken output into a shared tray.

That is also why this refuses directories and refuses anything that is not
obviously a document, rather than trusting the extension: the failure this
guards against is printing a directory listing or a private file by accident,
and the caller is frequently an LLM working from a guess.

Honesty rules: a job accepted by the queue is reported as *queued*, not printed.
Paper is a separate system this cannot see, and saying "printed" would be a
claim it has no way to support.
"""

from __future__ import annotations

import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.skills import Skill

_TIMEOUT = 30

SCHEMA = {
    "type": "function",
    "function": {
        "name": "print_file",
        "description": (
            "Print a document file on the default printer, or on a named one. "
            "Refuses directories and non-document files. Reports the job as "
            "queued, since whether it actually printed is not observable from here."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The document to print."},
                "printer": {
                    "type": "string",
                    "description": "Printer name. Defaults to the system default.",
                },
            },
        },
    },
}

#: Extensions a print job plausibly accepts. Deliberately a whitelist: refusing
#: an unknown extension is a sentence the user can act on, while accepting one is
#: a sheet of something else in the tray.
_PRINTABLE = {
    ".pdf", ".txt", ".ps", ".eps", ".png", ".jpg", ".jpeg", ".gif",
    ".tif", ".tiff", ".bmp", ".svg", ".html", ".htm", ".rtf", ".doc", ".docx",
    ".odt", ".xls", ".xlsx", ".ods", ".ppt", ".pptx", ".odp",
}


def _run(arguments: dict) -> str:
    try:
        target = files.resolve(arguments.get("path") or "")
    except files.PathProblem as exc:
        return str(exc)
    if target.is_dir():
        return (
            f"Refusing to print {target}: it is a directory. Print a specific "
            f"document instead."
        )
    if not target.exists():
        return files.describe(FileNotFoundError(2, "No such file or directory"),
                              target, "print")
    if target.suffix.lower() not in _PRINTABLE:
        return (
            f"Refusing to print {target.name}: {target.suffix or 'a file with no extension'} "
            f"is not a document type this will send to a printer. A misdirected "
            f"job lands in the tray, so name the file explicitly if you mean it."
        )
    if shutil.which("lp") is None:
        return files.tool_missing("lp", "print")

    printer = (arguments.get("printer") or "").strip()
    args = ["lp"]
    if printer:
        args += ["-d", printer]
    args.append(str(target))
    try:
        proc = subprocess.run(args, capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return f"Asked the print queue to take {target.name} but it did not answer in {_TIMEOUT}s; whether it was queued is unknown."
    except OSError as exc:
        return f"Could not print {target.name}: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return f"The print queue refused {target.name} (exit {proc.returncode})" + (
            f": {detail[-1]}" if detail else ". Is a printer configured?"
        )
    return (
        f"Queued {target.name}"
        + (f" on {printer}" if printer else " on the default printer")
        + f". Job: {(proc.stdout or '').strip() or 'id not reported'}. This is "
        f"queued, not confirmed printed."
    )


SKILLS = [Skill(name="print_file", schema=SCHEMA, run=_run)]
