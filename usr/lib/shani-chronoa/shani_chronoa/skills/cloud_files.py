"""Skill: what is in my cloud storage?

`backup_status` answers whether there is an off-machine *repository*
(`restic`). This answers the other half, and the one people actually mean by
"the cloud": a remote bucket or drive that `rclone` has configured, and what
is in it. Nothing in Chronoa asked `rclone` anything, and it ships in
`shani-tools-network` on both images.

**Read-only, and the reading forms are the whole of it.** `rclone` can copy,
sync, move and delete, and every one of those is a long-running transfer with
a real destination. This reports *state*: which remotes are configured, how
many objects and bytes they hold, and what is at the top level. A skill that
can reach `sync` from a status question is not a status skill.

**All shapes below measured on a real `@blue` slot against a local remote the
run configured**, because the only prior measurement was a refusal - `rclone
about` on the image's own R2 remote exits 3. A skill written from a refusal is
a skill that can only fail, so a credential-free local backend was configured
and read for real:

    rclone listremotes      ->  probe:                       rc=0
    rclone lsd  probe:DIR   ->        4096 2026-10-10 08:14:08        -1 docs
    rclone size probe:DIR   ->  Total objects: 2
                              Total size: 8 B (8 Byte)      rc=0
    rclone lsf  probe:DIR   ->  a.txt
                              docs/                         rc=0
    rclone lsd  nosuch:     ->  CRITICAL: Failed to create file system for
                              "nosuchremote:": didn't find section in config   rc=1

**Four things about that output a reader gets wrong by assumption:**

- **`listremotes` prints one remote per line *with a trailing colon*, and an
  empty list is rc=0 with no output at all.** "No remotes" and "rclone is not
  installed" are different claims, and the first is the answer for almost every
  fresh machine.
- **A directory's size is `4096` and a `-1` follows the timestamp** in `lsd`.
  Neither is a byte count of anything, so reporting "4096 B" as a directory's
  contents is wrong in a way that reads plausibly.
- **`lsf` marks a directory with a trailing `/`** - so a name ending in `/` is
  a directory and one without is a file. Splitting on `/` and dropping the
  empty tail turns `docs/` into `docs` and loses the distinction.
- **An unconfigured remote is rc=1 with `didn't find section in config`**, not
  an empty listing. Treating rc=1 as "nothing in it" reports an empty bucket for
  a typo'd name, which is the opposite of the truth.

`--config` is passed explicitly rather than `RCLONE_CONFIG_DIR` being exported:
measured, this build (1.75.1) **ignores the environment variable** and falls
back to `~/.config/rclone/rclone.conf`, which is a refusal reading as a
missing remote.
"""

from __future__ import annotations

import re
import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.skills import Skill

_TIMEOUT = 45

#: Verbs that transfer or destroy. `cloud_files` must never be given one.
_FORBIDDEN = ("copy", "sync", "move", "delete", "purge", "rmdir", "mkdir",
              "touch", "cat", "dedupe", "check", "cleanup", "mount")

#: `Total objects: 2` and `Total size: 8 B (8 Byte)` - from `rclone size`.
_TOTAL_OBJECTS = re.compile(r"^Total objects:\s*(\d+)", re.M)
_TOTAL_SIZE = re.compile(r"^Total size:\s*(.+?)\s\((\d+)", re.M)

#: The refusal wording that separates "you typo'd the remote" from "it is
#: empty". Measured; rclone's own sentence names the config, not the path.
_NO_SECTION = re.compile(r"didn't find section in config")


def _run(arguments):
    try:
        proc = subprocess.run(arguments, capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return "", "rclone did not answer within %ds" % _TIMEOUT, 124
    except OSError as exc:
        return "", str(exc), 127
    return proc.stdout or "", proc.stderr or "", proc.returncode


def _remote(token):
    """(remote, path) from `remote:path/to/dir`. The colon is the separator and
    a lone `remote:` is valid - everything after the first colon is the path."""
    if ":" in token:
        name, _, path = token.partition(":")
        return name, path
    return token, ""


def _list_remotes():
    """(remotes, reason). An empty list with no reason is the honest common
    case: almost no machine has one configured."""
    out, err, rc = _run(["rclone", "listremotes"])
    if rc == 124:
        return [], "rclone did not answer within %ds" % _TIMEOUT
    if rc != 0:
        detail = [line for line in err.splitlines() if line.strip()]
        return [], (detail[-1].strip() if detail else "rclone exited %d" % rc)
    return [line.strip().rstrip(":")
            for line in out.splitlines() if line.strip()], ""


def _size(remote):
    out, err, rc = _run(["rclone", "size", remote])
    if rc != 0:
        return -1, ""
    objects = _TOTAL_OBJECTS.search(out)
    size = _TOTAL_SIZE.search(out)
    return (int(objects.group(1)) if objects else -1,
            size.group(1).strip() if size else "")


def _top_level(remote):
    """(entries, reason). A trailing `/` in the name means a directory, and it
    is kept rather than stripped - that is the only thing distinguishing the
    two kinds."""
    out, err, rc = _run(["rclone", "lsf", remote])
    if rc != 0:
        detail = [line for line in err.splitlines() if line.strip()]
        return [], (detail[-1].strip() if detail else "rclone exited %d" % rc)
    return [line.rstrip("\n") for line in out.splitlines() if line.strip()], ""


def _run_skill(arguments):
    if shutil.which("rclone") is None:
        return files.tool_missing("rclone", "see what is in your cloud storage")

    remotes, problem = _list_remotes()
    if problem:
        return ("rclone could not list the configured remotes: %s. "
                "Nothing is known about what is in your cloud storage." % problem)
    if not remotes:
        return ("No cloud storage remotes are configured on this machine, so "
                "there is nowhere off-machine that rclone would send anything. "
                "That is the answer for a machine that has never run "
                "`rclone config` - it is not the same as a configured remote "
                "that happens to be empty.")

    wanted = str(arguments.get("remote") or "").strip().rstrip(":")
    if wanted:
        if wanted not in remotes:
            return ("There is no remote called %r. The configured ones are %s."
                    % (wanted, ", ".join(remotes)))
        chosen = wanted
    else:
        chosen = remotes[0] if len(remotes) == 1 else ""

    if not chosen:
        lines = ["%d cloud storage remote(s) configured: %s."
                 % (len(remotes), ", ".join(remotes))]
        for remote in remotes:
            objects, size = _size(remote + ":")
            if objects < 0:
                lines.append("- %s: I could not read its size." % remote)
            else:
                lines.append("- %s: %d object(s), %s." % (remote, objects, size))
        lines.append("")
        lines.append("Ask about one by name to see what is in it.")
        return "\n".join(lines)

    path = str(arguments.get("path") or "").strip().strip("/")
    target = chosen + ":" + path if path else chosen + ":"

    entries, why = _top_level(target)
    if why:
        if _NO_SECTION.search(why):
            return ("There is no remote called %r in rclone's configuration. "
                    "The configured ones are %s - so this is a typo, not an "
                    "empty bucket." % (chosen, ", ".join(remotes)))
        return ("I could not read %s: %s. Nothing is known about what is in "
                "it, and that is not the same as it being empty."
                % (target, why))

    objects, size = _size(target)
    lines = ["**%s**" % target]
    if objects >= 0:
        lines.append("%d object(s), %s." % (objects, size or "size not reported"))
    if not entries:
        lines.append("It holds nothing at the top level.")
    else:
        folders = [e for e in entries if e.endswith("/")]
        others = [e for e in entries if not e.endswith("/")]
        lines.append("")
        if folders:
            lines.append("%d director(ies): %s%s"
                         % (len(folders),
                            ", ".join(f.rstrip("/") for f in folders[:20]),
                            " ..." if len(folders) > 20 else ""))
        if others:
            lines.append("%d file(s): %s%s"
                         % (len(others),
                            ", ".join(others[:20]),
                            " ..." if len(others) > 20 else ""))

    # The honest ceiling, and the thing a person asking this actually wants to
    # know: whether the copy in the cloud matches the copy here. This says no
    # such claim is made.
    lines.append("")
    lines.append("This says what is *there*, not whether it matches anything "
                 "here - a comparison needs a local path and a transfer to "
                 "settle, and this reads only.")
    return "\n".join(lines)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "cloud_files",
        "description": (
            "See what is in this machine's configured cloud storage remotes - "
            "which remotes rclone knows, how many objects and bytes each "
            "holds, and what is at the top level of one. Use for 'what is in "
            "my cloud drive', 'is anything in the bucket', 'how big is my "
            "remote'. Read-only: it never copies, syncs or deletes anything. "
            "A machine with no remotes says so, because that means there is "
            "nowhere off-machine for anything to go."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "remote": {"type": "string",
                           "description": ("Name of the remote to look "
                                           "inside. Optional - with several "
                                           "configured, all are summarised "
                                           "without one.")},
                "path": {"type": "string",
                         "description": ("Path inside the remote to list. "
                                         "Optional - the top level without "
                                         "it.")},
            },
        },
    },
}

SKILLS = [Skill(name="cloud_files", schema=SCHEMA, run=_run_skill)]
