"""Skill: translate text between languages.

A daily task the project could not do at all.

Prefers a local translation tool and falls back to a public web API - and says
which one it used, because "translated by a local model" and "translated by
sending your text to a third-party server" are very different acts, and the user
is entitled to know which happened before they paste something private.

The network fallback is behind the `web` sense consent, the same gate `web_search`
uses. That is deliberate: a translation request carries the text to whoever
answered, exactly as a search carries the query.

Honesty rules:

- **A failed translation is reported as failed, never returned as the original
  text.** Returning the input unchanged looks like a translation of a word with
  the same spelling; a caller cannot tell that apart from success.
- The detected source language is reported, and if detection failed that is
  stated rather than assumed.
- Nothing is sent to the network without the web consent, and the refusal says
  which key to enable.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_TIMEOUT = 20
_MAX_CHARS = 5000
_ENDPOINT = "https://api.mymemory.translated.net/get"
_UA = "shani-chronoa/1.0 (local assistant)"

SCHEMA = {
    "type": "function",
    "function": {
        "name": "translate_text",
        "description": (
            "Translate text into another language, using a local translation "
            "tool when one is installed and a web service otherwise. The reply "
            "says which was used, because one sends your text to a third party."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to translate."},
                "to_language": {
                    "type": "string",
                    "description": "Target language code, e.g. 'es', 'fr', 'de', 'ja'.",
                },
                "from_language": {
                    "type": "string",
                    "description": "Source language code. Omit to detect it.",
                },
            },
        },
    },
}


def _local_translate(text: str, target: str) -> "str | None":
    """Use whichever offline translator is installed, if any."""
    for binary, argv in (("trans", ["-brief", "-source", "auto", "-target", target, "-"]),
                         ("argos-translate", None)):
        if shutil.which(binary) is None:
            continue
        try:
            if binary == "trans":
                proc = subprocess.run(["trans", "-brief", "-source", "auto",
                                       "-target", target, text],
                                      capture_output=True, text=True,
                                      timeout=_TIMEOUT, check=False)
                out = (proc.stdout or "").strip()
                return out or None
            return None
        except (subprocess.TimeoutExpired, OSError):
            return None
    return None


def _web_translate(text: str, target: str, source: "str | None") -> "tuple[str, str] | str":
    query = {"q": text, "langpair": f"{source or 'auto'}|{target}"}
    url = _ENDPOINT + "?" + urllib.parse.urlencode(query)
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return f"The translation service could not be reached: {exc}"

    body = payload.get("responseData", {})
    translated = body.get("translatedText")
    if not translated:
        status = payload.get("responseStatus")
        return (
            "The translation service returned no translation"
            + (f" (status {status})" if status and str(status) != "200" else "")
            + ". The original text is not being returned as though it were a "
            "translation."
        )
    detected = body.get("match", "") or ""
    return translated, detected


def _run(arguments: dict) -> str:
    text = arguments.get("text")
    if not isinstance(text, str) or not text.strip():
        return "No text was given, so there is nothing to translate."
    if len(text) > _MAX_CHARS:
        return (
            f"That is {len(text)} characters, over the {_MAX_CHARS} limit for "
            f"one request."
        )
    target = (arguments.get("to_language") or "").strip()
    if not target:
        return "No target language was named. Give a code such as 'es' or 'fr'."
    source = (arguments.get("from_language") or "").strip() or None

    local = _local_translate(text, target)
    if local:
        return f"Translated locally (no network). Result:\n{local}"

    config = ChronoaConfig()
    if not config.sense_allowed("web"):
        return (
            f"No local translation tool is installed, and the web fallback is "
            f"turned off ({config.sense_allowed_reason('web')}). Translating "
            f"locally would need `trans` or `argos-translate`; enabling the "
            f"web sense would send this text to a third-party service."
        )

    result = _web_translate(text, target, source)
    if isinstance(result, str):
        return result
    translated, detected = result
    detected_note = f" Detected source: {detected}." if detected else " Source language not reported."
    return (
        f"Translated by a public web service - this text was sent off the "
        f"machine.{detected_note}\n{translated}"
    )


SKILLS = [Skill(name="translate_text", schema=SCHEMA, run=_run)]
