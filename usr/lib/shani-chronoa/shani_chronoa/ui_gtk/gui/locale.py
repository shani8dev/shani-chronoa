"""UI translations for the GTK4 surfaces: gettext, system locale, no silent English.

The rule this implements is in `AI-DESKTOP.md`: the UI works before the model
does, and never silently falls back to English. Three consequences shape this
module:

- **The locale is the system's**, read from the same environment variables
  gettext uses (`LANGUAGE`, `LC_ALL`, `LC_MESSAGES`, `LANG`). This is not the
  `language` setting (whisper's speech language) and not the `set_locale` skill
  (which changes the *system* locale): neither is repurposed here.
- **A locale is translated only when a compiled catalog covers most strings.**
  An empty, missing or stub `.po` never makes a language appear in
  `translated_locales()`; see `MIN_TRANSLATION_COVERAGE`. Normal mode may show
  the English msgid, but it logs that fact and never claims the locale is
  translated.
- **Dev mode shows what is missing.** With `SHANI_CHRONOA_DEV_LOCALE=1`, or the
  `debug-mode` setting, a msgid with no translation is returned with a visible
  `[untranslated:<lang>]` marker instead of looking like a finished locale.

Strings are marked with `_()` at their site in `gui/` and `gui/surfaces/`, so
the usual gettext tools can extract them. The catalogs live beside this module
in `locale/`; messages.pot is the template other languages start from.
"""

from __future__ import annotations

import ast
import gettext as _gettext
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger("shani_chronoa.gui.locale")

DOMAIN = "shani-chronoa"
#: A stub with one filled msgstr is a catalog to finish, not a translated UI.
#: A locale is advertised only at this share of the template's strings.
MIN_TRANSLATION_COVERAGE = 0.9
#: Catalogs ship with the GUI, not in ~/.local: they are code-sized and
#: read-only, unlike the downloaded OCR/voice data `languages.py` manages.
LOCALE_ROOT = Path(__file__).parent / "locale"
TEMPLATE = LOCALE_ROOT / "messages.pot"
DEV_ENV = "SHANI_CHRONOA_DEV_LOCALE"

_PO_LINE = re.compile(r'^(msgid|msgstr)\s+"(.*)"\s*$')

_tr: _gettext.NullTranslations | None = None
_lang = "en"
_dev = False


def system_locale(environ: "dict[str, str] | None" = None) -> str:
    """The UI language from gettext's own environment variables, default 'en'.

    `LANGUAGE` may be a preference list ("hi:en"); only its first entry is a
    translation choice, so later entries cannot hide a missing catalog.
    Territories and encodings ("hi_IN.UTF-8", "hi-IN") reduce to "hi", the
    directory name gettext's `translation()` looks for.
    """
    env = os.environ if environ is None else environ
    for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        raw = (env.get(name) or "").strip()
        if not raw or raw in ("C", "POSIX"):
            continue
        first = raw.split(":", 1)[0].split(",", 1)[0]
        code = first.split(".", 1)[0].split("@", 1)[0].replace("-", "_")
        code = code.split("_", 1)[0].lower()
        if re.fullmatch(r"[a-z]{2,3}", code):
            return code
    return "en"


def _catalog_path(lang: str, suffix: str = ".po") -> Path:
    """`<locale>/<lang>/LC_MESSAGES/shani-chronoa<suffix>`."""
    return LOCALE_ROOT / lang / "LC_MESSAGES" / f"{DOMAIN}{suffix}"


def _entries(path: Path) -> "dict[str, str]":
    """Single-line PO entries, the shape every shipped catalog uses.

    The header (`msgid ""`) is metadata, not a message, so it is dropped.
    Multi-line entries are left to `msgfmt`/gettext at runtime; the POT and
    the `.po` files here are generated with one quoted line per entry.
    """
    out: "dict[str, str]" = {}
    if not path.is_file():
        return out
    msgid: "str | None" = None
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _PO_LINE.match(line)
        if not match:
            continue
        key, raw = match.groups()
        try:
            value = ast.literal_eval(f'"{raw}"')
        except (SyntaxError, ValueError):
            continue
        if key == "msgid":
            msgid = value if value else None
        elif msgid is not None:
            out[msgid] = value
            msgid = None
    return out


def catalog_ids() -> "tuple[str, ...]":
    """Every extracted UI string, in the template's order."""
    return tuple(_entries(TEMPLATE))


def untranslated(lang: str) -> "list[str]":
    """Extracted strings this locale has not translated (all of them if absent)."""
    have = _entries(_catalog_path(lang))
    return [msgid for msgid in catalog_ids() if not have.get(msgid)]


def translation_coverage(lang: str) -> float:
    """Share of extracted strings this locale actually translates (0.0..1.0)."""
    ids = catalog_ids()
    if not ids:
        return 0.0
    have = _entries(_catalog_path(lang))
    return sum(1 for msgid in ids if have.get(msgid)) / len(ids)


def is_translated(lang: str) -> bool:
    """True only for a compiled catalog that covers most extracted strings.

    A `.po` skeleton with empty `msgstr`s, a stub with a single translation, or
    a `.po` nobody compiled is a catalog *to fill in*, not a translated locale.
    One `msgstr` used to be enough to advertise a language and leave the rest
    of its UI in English; the coverage floor keeps that claim honest.
    """
    if lang in ("", "en"):
        return False  # English is the source language, not a translation
    if not _catalog_path(lang, ".mo").is_file():
        return False
    return translation_coverage(lang) >= MIN_TRANSLATION_COVERAGE


def translated_locales() -> "list[str]":
    """Non-English locales that actually have translations (English is the source)."""
    if not LOCALE_ROOT.is_dir():
        return []
    return sorted(
        {
            p.parent.parent.name
            for p in LOCALE_ROOT.glob("*/LC_MESSAGES/*.po")
            if is_translated(p.parent.parent.name)
        }
    )


def _dev_mode() -> bool:
    flag = os.environ.get(DEV_ENV)
    if flag is not None:
        return flag.strip().lower() not in ("", "0", "false", "no")
    try:
        from shani_chronoa.config import ChronoaConfig

        return ChronoaConfig().debug_mode
    except Exception:  # noqa: BLE001 - a config read must not take the UI down
        return False


def _current() -> _gettext.NullTranslations:
    """Load the catalog once, on first use; surfaces build titles at import."""
    global _tr, _lang, _dev
    if _tr is None:
        configure()
    return _tr


def configure(lang: "str | None" = None) -> None:
    """Choose the UI locale and load its gettext catalog.

    `lang=None` means the system locale. Passing a code is for callers that
    know better (tests, or a later settings UI); it does not change the system
    locale or the `language` speech setting.
    """
    global _tr, _lang, _dev
    _lang = (
        system_locale()
        if lang is None
        else lang.replace("-", "_").split("_", 1)[0].lower()
    )
    _dev = _dev_mode()
    _gettext.bindtextdomain(DOMAIN, str(LOCALE_ROOT))
    _gettext.textdomain(DOMAIN)
    if _lang != "en" and not is_translated(_lang):
        logger.warning(
            "no %s UI catalog for %r; showing English source strings", DOMAIN, _lang
        )
    try:
        _tr = _gettext.translation(
            DOMAIN, localedir=str(LOCALE_ROOT), languages=[_lang], fallback=True
        )
    except OSError:  # a corrupt .mo is not a reason to refuse to open the UI
        _tr = _gettext.NullTranslations()
        logger.warning(
            "could not load the %s UI catalog for %r", DOMAIN, _lang, exc_info=True
        )


def translate(msgid: str) -> str:
    """Translate one UI string, making dev-mode gaps visible.

    Normal mode returns the English msgid when a translation is missing - the
    UI still works - but `is_translated`/`translated_locales` do not pretend
    that locale is done. Dev mode adds a marker so the gap cannot hide.
    """
    result = _current().gettext(msgid)
    if result == msgid and _lang != "en" and _dev and msgid in untranslated(_lang):
        return f"[untranslated:{_lang}] {msgid}"
    return result


#: The conventional gettext spelling used at every string site in `gui/`.
_ = translate
