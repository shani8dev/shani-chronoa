"""Skill: download a file from a web address, and say what happened.

Nothing could fetch a URL. `web_search` finds pages and `browse` drives a real
browser, and neither saves a file to disk - so "download that installer" had no
answer on a machine whose only tool for it is the browser.

`curl` is in `shani-tools-network`, so it is on both images by design, and
`curl -w` reports the transfer itself rather than just whether it worked.

**Four things measured on this machine, each a way to report a failure as a
success:**

- **A 404 exits 0.** Measured without a pipe (which is how `readelf`'s and
  `pdffonts`' real contracts were both got wrong before):
  `curl -s -o /dev/null -w '%{http_code}' https://example.com/nosuchpage`
  prints `404` and **exits 0**. Only `%{http_code}` distinguishes it from a
  file. Curl's exit status answers "did I reach a server and get a reply", not
  "did I get the file I asked for".
- **A name that does not resolve exits 6**, which *is* a failure - but 6 is
  `COULDNT_RESOLVE_HOST`, and the message must say the name did not resolve
  rather than that the download failed.
- **`file://` prints `000` and exits 0.** A scheme curl was not asked to
  handle produces a perfectly successful-looking zero-width answer, so the
  saved file would be reported as downloaded when nothing was fetched.
- **`--write-out` is the only thing that reports the transfer.** Without it
  curl prints nothing on success and there is no size or speed to quote.

**The URL is validated before curl ever sees it.** Not for tidiness: the
sandbox's own policy rejects `file://`, and a skill that passes one through
gets an empty file and reports success, which is worse than a refusal.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from shani_chronoa import files
from shani_chronoa.skills import Skill

_TIMEOUT = 300

#: curl's exit statuses that mean something specific. `run()` prints the
#: number for any other failure, but these three are worth naming because each
#: is a different problem wearing the same "download failed".
_MEANING = {
    6: "the site name could not be resolved - check the address, or whether "
       "this machine has a working internet connection at all",
    7: "could not connect to the site at all (it may be down, or this "
       "network may be blocking it)",
    28: "the transfer timed out before it finished",
}

#: `%{http_code}` when curl fetched nothing at all - an unhandled scheme, or a
#: failure so early there was no reply. 000 is *not* a success code.
_NOTHING_FETCHED = "000"

_SAFE_SCHEMES = ("http", "https")


def _run(arguments: dict) -> str:
    url = str(arguments.get("url") or "").strip()
    if not url:
        return ("I need an address to download from. Give me a full web "
                "address, starting with https://")

    parsed = urlparse(url)
    # **Checked before curl runs.** `file://` is rejected by the sandbox's own
    # policy and produces `000` with an empty file if it reaches curl, so the
    # two halves agree rather than one quietly succeeding.
    if parsed.scheme.lower() not in _SAFE_SCHEMES:
        return (f"I will not fetch a {parsed.scheme or 'scheme-less'}:// "
                f"address - I only download over http or https. (A `file://` "
                "URL would quietly produce an empty file and report success.)")

    destination = arguments.get("destination")
    if destination:
        target = Path(files.expand(str(destination)))
        if target.is_dir():
            name = os.path.basename(parsed.path) or "download"
            target = target / name
    else:
        name = os.path.basename(parsed.path) or "download"
        target = Path(os.path.expanduser("~")) / "Downloads" / name
    target = target.expanduser()

    if shutil.which("curl") is None:
        return files.tool_missing("curl", "download that file")

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"I could not make {target.parent}: {exc}. Nothing was saved."

    command = [
        "curl", "--silent", "--show-error", "--location",
        "--max-time", str(_TIMEOUT),
        "--output", str(target),
        "--write-out",
        "%{http_code} %{size_download} %{time_total} %{speed_download}",
        url,
    ]
    try:
        proc = subprocess.run(command, capture_output=True, text=True,
                              timeout=_TIMEOUT + 15, check=False)
    except subprocess.TimeoutExpired:
        return (f"The download did not finish within {_TIMEOUT}s, so "
                f"{target} may be incomplete. Nothing was reported as saved.")

    status = (proc.stdout or "").split()
    code = status[0] if status else _NOTHING_FETCHED
    size = status[1] if len(status) > 1 else "0"
    seconds = status[2] if len(status) > 2 else "?"
    speed = status[3] if len(status) > 3 else "?"

    # **The exit code first, then the HTTP code.** Measured: a 404 exits 0 and
    # an unresolvable host exits 6, so checking either one alone accepts a
    # failure. Both are checked, and they are different failures.
    if proc.returncode != 0:
        reason = _MEANING.get(
            proc.returncode,
            (proc.stderr or "").strip().splitlines()[-1:] or ["curl failed"])[0] \
            if proc.returncode not in _MEANING else _MEANING[proc.returncode]
        try:
            target.unlink()
        except OSError:
            pass
        return (f"The download did not happen: {reason} (curl said "
                f"{proc.returncode}). Nothing was saved to {target}.")

    if code == _NOTHING_FETCHED:
        try:
            target.unlink()
        except OSError:
            pass
        return (f"The server sent no reply at all, so there is no file to "
                f"save. Nothing was written to {target}.")

    if not code.startswith("2"):
        # **A 404 exits 0.** Only this catches it.
        try:
            target.unlink()
        except OSError:
            pass
        return (f"The site answered, but with HTTP {code} - that is not a "
                f"file. Nothing was saved to {target}. The address may have a "
                "typo, or the file may have moved.")

    if size == "0":
        return (f"The download succeeded but the file is empty (0 bytes), "
                f"saved to {target}. That is what the site sent.")

    speed_text = f"{int(speed) / 1e6:.1f} MB/s" if speed.isdigit() else "?"
    return (f"Downloaded {size} bytes to {target} in {seconds}s ({speed_text}, "
            f"HTTP {code}).")


SCHEMA = {
    "type": "function",
    "function": {
        "name": "download_file",
        "description": (
            "Download a file from a web address and save it to disk, "
            "reporting the size and how long it took. Use for 'download that "
            "file', 'get this installer', 'save that page'. Only http and "
            "https; a site that answers with an error page reports that "
            "rather than saving the error as if it were the file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string",
                        "description": "The full http:// or https:// address."},
                "destination": {
                    "type": "string",
                    "description": ("Where to save it. Defaults to "
                                    "~/Downloads with the filename from the "
                                    "address.")},
            },
            "required": ["url"],
        },
    },
}

SKILLS = [Skill(name="download_file", schema=SCHEMA, run=_run)]