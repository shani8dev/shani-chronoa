"""Text normalisation for the speech path, before any engine sees a word.

**Why this exists, in one measured fact.** `espeak-ng -X` on the sentence a
language model actually writes:

    It cost $5.00 at 42km/h, i.e. 3 items.
    -> Unpronouncable? 'km'
    -> It k'0st d'0l3 f'aIv pOInt z'i@roU z'i@roU at f'o@ti t'u: k,eI'Em sl'aS 'eItS

Read back in English: "It cost dollar five **point zero zero** at forty two
**K E M slash H**". espeak itself reports `km` as unpronounceable and falls back
to spelling it letter by letter. The same text through Piper or Kokoro is no
better: every engine here is a phoneme front-end and none carries a vocabulary of
units.

Nothing in this app normalised text for speech until now. That is a gap rather
than a decision - the LLM writes `42km/h`, `$5.00`, `8GB`, `100ms`, and a listener
hears arithmetic and letter names. The idea, and the split between what belongs
here and what belongs to an engine, comes from VoxCPM's
`utils/text_normalize.py`; the implementation does not, because that one needs
`inflect` and `wetext` and is aimed at Chinese normalisation. This is the English
subset against the standard library, since a hard dependency for a voice line is
not a trade this app makes (`sox` is an optdepend for the same reason).

**What this deliberately does not do.** It does not spell out numbers in general.
`3.14` reads acceptably through every engine here ("three point one four"), and
`2026`, `1st`, `53,312` were all measured working before this module was written.
Rewriting them would be change for its own sake, and a normaliser that touches
more than it fixes is one nobody can review. It handles only what is *provably*
wrong: units, currency, and the abbreviations a listener expects in words.

**Every substitution is reported, not assumed.** `normalize_for_speech` returns
the text and a list of what changed, because a voice line a user cannot see has to
be auditable: if a normaliser ever rewrote something it should not have, the
report is how anyone would find out.
"""

from __future__ import annotations

import re
from typing import Callable, Final

def _mk_unit_repl(spoken: str) -> Callable[[re.Match], str]:
    def repl(match: re.Match) -> str:
        number = match.group("num") or ""
        # A number directly against the unit needs the space the author did not
        # type; one already followed by a space does not need a second.
        if number and not number.endswith((" ", "\t")):
            number += " "
        return number + spoken
    return repl


#: Units, spelled the way a listener expects to hear them.
#:
#: **The anchor is `(?<![A-Za-z])`, not `\b`, and that is a measured fix.** `\bkm`
#: cannot match in `42km` - `2` and `k` are both word characters, so there is no
#: boundary between them - which is exactly the case that matters most, because
#: `42km/h` is the sentence that started this module. A negative look-behind on a
#: letter still refuses `km` inside a word like `kmod` while allowing it after a
#: digit, a space or an opening bracket.
#:
#: The optional leading `(?P<num>\d+\s*)?` is the second measured fix. Replacing
#: `km` in `5km` yields `5kilometres` - two words run together - so the number is
#: captured and re-emitted with a separating space.
_UNITS: Final[tuple[tuple[str, str, Callable[[re.Match], str]], ...]] = tuple(
    (r"(?<![A-Za-z])(?P<num>\d+\s*)?" + re.escape(abbr) + r"(?![A-Za-z])",
     spoken,
     _mk_unit_repl(spoken))
    for abbr, spoken in (
        ("km/h", "kilometres per hour"),
        ("km", "kilometres"),
        ("cm", "centimetres"),
        ("mm", "millimetres"),
        ("kg", "kilograms"),
        ("mg", "milligrams"),
        ("ml", "millilitres"),
        ("GB/s", "gigabytes per second"),
        ("MB/s", "megabytes per second"),
        ("kb/s", "kilobytes per second"),
        ("GB", "gigabytes"),
        ("MB", "megabytes"),
        ("TB", "terabytes"),
        ("KB", "kilobytes"),
        ("GHz", "gigahertz"),
        ("MHz", "megahertz"),
        ("kHz", "kilohertz"),
        ("Hz", "hertz"),
        ("kWh", "kilowatt hours"),
        ("FPS", "frames per second"),
        ("RPM", "revolutions per minute"),
        ("MP", "megapixels"),
        ("ms", "milliseconds"),
        ("DPI", "dots per inch"),
    )
)

#: Case-insensitive throughout: `gb`, `Gb` and `GB` all mean gigabytes, and a
#: normaliser that handled only the documented casing would be right twice in
#: three tries.
_UNIT_FLAGS: Final = re.IGNORECASE


#: Currency. `$5.00` is the one that breaks: espeak reads it "dollar five point
#: zero zero", so the cents arrive as a decimal rather than an amount.
_MONEY: Final[tuple[tuple[str, str, str], ...]] = (
    (r"\$(\d+(?:\.\d{1,2})?)", "dollars", "cents"),
    (r"€(\d+(?:\.\d{1,2})?)", "euros", "cents"),
    (r"£(\d+(?:\.\d{1,2})?)", "pounds", "pence"),
)


def _mk_money_repl(symbol_word: str, cents_word: str) -> Callable[[re.Match], str]:
    def repl(match: re.Match) -> str:
        amount = match.group(1)
        if "." not in amount:
            return f"{amount} {symbol_word}"
        whole, _, frac = amount.partition(".")
        if frac in ("00", ""):
            return f"{whole or '0'} {symbol_word}"
        # A whole part of zero is cents and nothing else: "0 dollars and 99
        # cents" names two amounts where the price is one.
        if whole in ("", "0"):
            return f"{frac} {cents_word}"
        return f"{whole} {symbol_word} and {frac} {cents_word}"
    return repl


#: Abbreviations a listener expects spoken in full. Only ones that are
#: unambiguous in prose, and only with the punctuation the author typed, so
#: `e.g.` still matches at the end of a sentence while a filename containing it
#: does not.
_ABBREVIATIONS: Final[tuple[tuple[str, str], ...]] = (
    (r"\be\.g\.", "for example"),
    (r"\bi\.e\.", "that is"),
    (r"\betc\.", "et cetera"),
    (r"\bvs\.", "versus"),
    (r"\bDr\.", "Doctor"),
    (r"\bMr\.", "Mister"),
    (r"\bMrs\.", "Missus"),
    (r"\bMs\.", "Miss"),
    (r"\bProf\.", "Professor"),
    (r"\bSt\.", "Saint"),
    (r"\bapprox\.", "approximately"),
    (r"\bmin\.", "minimum"),
    (r"\bmax\.", "maximum"),
    (r"\bfig\.", "figure"),
    (r"\bdept\.", "department"),
    # Initialisms are spelled, because an engine reads `API` as a word ("apee")
    # or as letters inconsistently, and a listener deserves the same reading
    # every time.
    (r"\bAPI\b", "A P I"),
    (r"\bAPIs\b", "A P Is"),
    (r"\bURL\b", "U R L"),
    (r"\bURLs\b", "U R Ls"),
    (r"\bHTTP\b", "H T T P"),
    (r"\bHTTPS\b", "H T T P S"),
    (r"\bSSH\b", "S S H"),
    (r"\bCPU\b", "C P U"),
    (r"\bGPU\b", "G P U"),
    (r"\bRAM\b", "R A M"),
    (r"\bUSB\b", "U S B"),
    (r"\bPDF\b", "P D F"),
    (r"\bHTML\b", "H T M L"),
    (r"\bCLI\b", "C L I"),
    (r"\bGUI\b", "G U I"),
    (r"\bOS\b", "operating system"),
)

#: Words an engine already reads correctly, listed so the *next* person does not
#: "fix" them into something worse. `bytes`, `docs` and `info` are ordinary
#: English words; expanding them is a style choice, not a correction, and this
#: module only corrects.
_LEFT_ALONE: Final = ("bytes", "docs", "info", "GB", "3.14", "2026", "1st")


def normalize_for_speech(text: str) -> tuple[str, list[str]]:
    """Rewrite `text` so a speech engine says what the author meant.

    Returns the rewritten text and a list of the substitutions made, each as
    ``"from -> to"``. The report is the caller's to log: a transformation applied
    to a voice line nobody can see has to be observable after the fact, and a
    normaliser that silently rewrote something it should not have is exactly the
    defect nobody could find.
    """
    if not text or not text.strip():
        return text, []

    changes: list[str] = []
    out = text

    def sub(pattern: str, repl: str | Callable[[re.Match], str]) -> None:
        """Apply one substitution, recording it as ``from -> to``.

        The report says what the text actually became, not which pattern fired: a
        log line reading ``"/\\bkm\\b/ -> kilometres per hour"`` cannot be checked
        against the sentence that was spoken.
        """
        nonlocal out
        compiled = re.compile(pattern, _UNIT_FLAGS)
        if not compiled.search(out):
            return
        found = compiled.search(out).group(0)
        new = compiled.sub(repl, out)
        if new != out:
            changes.append(f"{found!r} -> {_as_text(repl, found)!r}")
            out = new

    # Units first, so `$5.00/km` is priced per kilometre rather than per K-E-M.
    for pattern, _spoken, repl in _UNITS:
        sub(pattern, repl)
    for pattern, symbol_word, cents_word in _MONEY:
        sub(pattern, _mk_money_repl(symbol_word, cents_word))
    for pattern, spoken in _ABBREVIATIONS:
        sub(pattern, spoken)

    return out, changes


def _as_text(repl, matched: str) -> str:
    """What `repl` produced for `matched`, for the change report.

    A callable is the interesting case, and there are two of them: the unit
    replacement and the money one. Reporting `str(repl)` - which is what the
    first version did - prints ``<function _mk_money_repl.<locals>.repl at
    0x...>``, a log line that cannot be checked against anything. Each callable
    is matched against the pattern that produced it, so the report quotes the
    words the listener will hear.
    """
    if callable(repl):
        for pattern in _REPORT_PATTERNS:
            m = re.match(pattern, matched)
            if m:
                return repl(m)
    return str(repl)


#: The shapes `_as_text` recognises, in the order the substitutions run: a number
#: then a unit, then a currency amount.
_REPORT_PATTERNS: Final = (
    r"(?<![A-Za-z])(?P<num>\d+\s*)?[A-Za-z/]+",
    r"\$(\d+(?:\.\d{1,2})?)",
)
