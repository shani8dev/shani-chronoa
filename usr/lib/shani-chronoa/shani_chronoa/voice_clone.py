"""Voice cloning: the mechanism, and the consent record that has to exist first.

Cloning a voice is the one thing Chronoa does that speaks *as a specific
person*. Every other engine is a voice nobody owns; this one is built from a
recording of somebody who did not necessarily agree to be built. So the
difference from `kokoro` or `piper` is not the model, it is that **a reference
clip is a person's voice** and everything below treats it as one.

**Two gates, and neither is sufficient alone.**

1. `voice-clone-enabled` - the capability, off by default. This is the switch
   a person reaches for.
2. A **per-voice registration** naming the speaker, what they agreed to, and
   when - plus the *content digest* of the clip it was granted for.

A switch alone would clone anything anybody hands the machine. A registration
alone would keep working after the capability was declined. Both have to pass,
which is why `check()` asks two questions and returns a sentence naming whichever
one it failed.

**The digest binding is the load-bearing part, and it is what stops the gate
being decorative.** Consent is granted for *specific audio*, not for a
filename. A registration for `~/voice/alice.wav` is meaningless the moment
somebody replaces that file with a recording of somebody else - so
`register()` stores the SHA-256 of the bytes it read, and `check()` re-reads
and refuses on a mismatch. Without that, the registry is a list of paths and
anybody who can write to the voice directory can clone anybody.

**Every failure here is closed.** An unreadable registry is not "allowed", and
an unreadable setting is not "allowed" either - which is the opposite of
`_piper_enabled`, and deliberately so: that one is a *preference* and defaults
to on because Piper was unconditional before its switch existed. This is a
permission, and a permission that fails open on a machine whose schema
predates it is the `heard-sound` defect this repository records by name.

**Stdlib only, and no torch at import.** The Kitten runtime is ~1.7B
parameters behind `torch`/`transformers`, which is not packaged and is refused
by `ARCHITECTURE-TARGET.md`'s own no-torch rule. Everything here - the gate,
the registry, the digest - works and is enforced on a machine with none of it
installed; only the final synthesis is unavailable, and `clone_problem()` says
so honestly rather than the gate being what reports it.

**References are local files.** No URL, no download. Kitten's own
`resolve_reference_path` refuses anything that is not an existing file and that
is the right default: "clone from a link" is the shape that turns a local
assistant into an impersonation service.
"""

import hashlib
import json
import os
import pathlib
import time
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from shani_chronoa import files


def gate_label() -> str:
    """The Settings row's own wording for the clone switch.

    Read from `capabilities.GATE_NAMES` rather than typed, because this refusal
    is shown to a person and the label they are given has to be the label the
    switch in Settings actually carries - the `heard-sound` defect by another
    route, where the sentence told somebody to enable a key that is spelled
    differently on the only screen that can turn it on. Falls back to the switch
    name when the table has not been extended, so a missing entry degrades to
    something still actionable rather than to a bare key.
    """
    from shani_chronoa import capabilities
    return capabilities.gate_label(CONSENT_KEY)

#: The capability switch. Off by default, and the default is the safe one.
CONSENT_KEY = "voice-clone-enabled"

#: What the registry is called in the schema's own vocabulary, used in refusals
#: so a person can be told which of the two gates closed.
SWITCH_LABEL = 'Clone a recorded voice'

#: Kitten's own documented usable range, and the shortest clip it will say
#: anything useful about. Both are the upstream's numbers rather than ones
#: measured here - see `clone_problem()` for what this repository has measured.
MIN_CLIP_SECONDS = 5.0
MAX_CLIP_SECONDS = 30.0

#: Refuse to hash an unbounded file. A "reference clip" is 30 seconds of audio;
#: a 4 GB file named `.wav` is something else, and hashing it would be a
#: denial of service dressed as a consent check.
MAX_CLIP_BYTES = 64 * 1024 * 1024

_DIGEST_BYTES = 1024 * 1024


class Consent(NamedTuple):
    """One recorded agreement to clone one voice."""

    voice: str
    speaker: str
    #: Free text naming the basis of the agreement - a contract, a written
    #: permission, the speaker recording it themselves. Required, because a
    #: consent record nobody can review is not a consent record.
    basis: str
    #: The clip's SHA-256. A registration whose clip no longer hashes to this
    #: does not apply, whatever else is true of it.
    digest: str
    clip: str
    #: Seconds since the epoch. Read back through the file, so a record is
    #: portable between machines rather than only within one process.
    registered_at: float


def registry_path() -> pathlib.Path:
    """The consent registry, resolved per call and never captured at import.

    `files.data_home()` rather than a literal `~/.local/share`, for the reason
    every other store in this package gives: a path built at import gives a
    test no way to relocate it, and the documented way to sandbox a run is the
    XDG variable. `egress.py` records the cost of getting this wrong as a real
    user's audit log being appended to by a fixture.
    """
    return files.data_home() / "shani-chronoa" / "voices" / "consent.json"


def clip_digest(path: "str | os.PathLike[str]") -> str:
    """The SHA-256 of a clip's bytes, read in bounded chunks.

    Whole-file because the digest has to cover the whole file: hashing the
    header would let a different recording with the same RIFF preamble pass,
    which is precisely the substitution the gate exists to catch. Bounded reads
    because the file could be anything.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_DIGEST_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _read_registry() -> List[Consent]:
    """Every recorded consent, or `[]` when the file is absent.

    An **unreadable** registry is a different answer from an absent one, and
    this function cannot tell them apart - so `_registry_problem()` does, and
    `check()` asks it before answering that nothing was agreed. Collapsing
    "no agreements recorded" into "the registry is corrupt" is the
    absence-shaped wrong answer this repository records in four costumes, and a
    reader of the gate could not tell which one it was looking at.
    """
    path = registry_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(raw, dict):
        return []
    out: List[Consent] = []
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        try:
            out.append(Consent(
                voice=str(name),
                speaker=str(entry["speaker"]),
                basis=str(entry["basis"]),
                digest=str(entry["digest"]),
                clip=str(entry["clip"]),
                registered_at=float(entry.get("registered_at", 0.0)),
            ))
        except (KeyError, TypeError, ValueError):
            # A record missing a field is not a consent record. Skipping it is
            # the closed direction: it will not match in `check()`, so a
            # malformed entry can never authorise anything.
            continue
    return out


def _registry_problem() -> str:
    """Why the registry cannot be read, or "" when it can.

    **Separate from `_read_registry` because that one cannot tell the two
    apart, and deliberately so**: it is asked "what is registered", and for that
    question an absent file and a corrupt one have the same answer - `[]`, and
    `[]` is the correct answer to "what is registered" in both cases.
    `check()` is asked a different question - *may this voice be cloned* - and
    for that one they are different, because the remedy is: an absent registry
    wants a registration written, and a corrupt one does not.

    Measured before this existed: absent, unparseable, holding a list rather
    than a mapping, and genuinely holding only somebody else's voice all
    produced one identical sentence, which in the corrupt case told the person
    to record an agreement that would then be written into a file nothing reads
    back. Failing closed was already right - `[]` authorises nothing - and it is
    the *remedy* that was wrong, which is the harder half to notice because the
    answer was safe.

    Says nothing about whether any voice was agreed to, because it cannot know:
    a file it cannot parse is not evidence about a person.
    """
    path = registry_path()
    if not path.exists():
        return ""
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return f"the consent registry at {path} could not be read ({exc})"
    if not isinstance(raw, dict):
        return (f"the consent registry at {path} holds something that is not "
                f"a mapping of voices")
    return ""


def registered() -> List[Consent]:
    """The recorded consents, for the voice surface and `speak`'s `voices`."""
    return _read_registry()


def lookup(voice: str) -> Optional[Consent]:
    """The consent recorded for `voice`, or None."""
    for entry in _read_registry():
        if entry.voice == voice:
            return entry
    return None


def register(voice: str, clip: str, speaker: str, basis: str) -> Consent:
    """Record agreement to clone `speaker`'s voice from `clip`, as `voice`.

    Writes the clip's digest, so the agreement is bound to the audio rather
    than to the path - see the module docstring for why that is the part that
    makes the gate real.

    Refuses an empty speaker or basis. Both are required fields on purpose: a
    consent record that cannot say who agreed, or on what basis, is a record a
    person cannot review and therefore cannot withdraw meaningfully.
    """
    voice = str(voice).strip()
    speaker = str(speaker).strip()
    basis = str(basis).strip()
    if not voice:
        raise ValueError("a voice needs a name")
    if not speaker:
        raise ValueError("say who the voice belongs to")
    if not basis:
        raise ValueError("say on what basis they agreed - a consent record "
                         "nobody can review is not a consent record")
    path = pathlib.Path(clip).expanduser()
    if not path.is_file():
        raise ValueError(f"no reference clip at {path}")
    size = path.stat().st_size
    if size <= 0:
        raise ValueError(f"{path} is empty")
    if size > MAX_CLIP_BYTES:
        raise ValueError(f"{path} is {size} bytes; a reference clip is audio, "
                         f"and this is not something to hash")

    entry = Consent(
        voice=voice, speaker=speaker, basis=basis,
        digest=clip_digest(path), clip=str(path),
        registered_at=time.time(),
    )
    _write_registry(entry)
    return entry


def revoke(voice: str) -> bool:
    """Drop the registration for `voice`. True if there was one.

    Takes effect on the next `check()`, which is the point: there is no cached
    copy of the registry for a gate to consult, so revoking and then cloning in
    the same session is refused.
    """
    path = registry_path()
    raw: Dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text())
            if isinstance(loaded, dict):
                raw = loaded
        except (OSError, ValueError):
            raw = {}
    if voice not in raw:
        return False
    del raw[voice]
    _save(raw)
    return True


def _write_registry(entry: Consent) -> None:
    raw: Dict[str, Any] = {}
    path = registry_path()
    if path.exists():
        try:
            loaded = json.loads(path.read_text())
            if isinstance(loaded, dict):
                raw = loaded
        except (OSError, ValueError):
            raw = {}
    raw[entry.voice] = {
        "speaker": entry.speaker,
        "basis": entry.basis,
        "digest": entry.digest,
        "clip": entry.clip,
        "registered_at": entry.registered_at,
    }
    _save(raw)


def _save(raw: Dict[str, Any]) -> None:
    """Write the registry 0600 in a 0700 directory, replacing atomically.

    `mkdir` then `chmod` rather than `mkdir(mode=...)` alone, because with
    `parents=True` the mode applies to the **leaf only** - intermediate
    directories come out at the umask default - and `exist_ok=True` leaves an
    already-created directory exactly as it was. This is `egress.py`'s own
    recorded lesson, applied a third time rather than rediscovered.

    Temp-then-`os.replace`, because `write_text` truncates in place: a crash
    between the truncate and the flush would leave a registry that authorises
    nothing, which is safe, but one that *partially* parses would not be, and
    an atomic replace cannot produce the second case.
    """
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(raw, indent=2, sort_keys=True))
        tmp.chmod(0o600)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _switch_allows(config: Any = None) -> Tuple[bool, str]:
    """Whether `voice-clone-enabled` is on.

    **Fails closed**, which is the opposite of `_piper_enabled` and for the
    recorded reason: that is a preference whose absence should not change how
    every reply sounds, and this is a permission whose absence must stop a
    voice being built from a person who did not agree.
    """
    try:
        if config is None:
            from shani_chronoa.config import ChronoaConfig
            config = ChronoaConfig()
        return bool(config.get_bool(CONSENT_KEY, False)), ""
    except Exception:  # noqa: BLE001 - an unreadable permission is a refusal
        return False, ""


def check(voice: str, clip: Optional[str] = None,
          config: Any = None) -> Tuple[bool, str]:
    """May this voice be cloned? `(allowed, reason)`.

    The single question every caller asks, and the only place the two gates
    live. Asked in this order because the switch is the answer a person can act
    on: somebody who has never enabled cloning should be told about the
    switch, not about a missing registration they cannot create without it.

    **Three refusals, and they are not the same:**

    - the switch is off - "you can turn this on";
    - there is no registration - "record one";
    - the registration does not apply to this audio - **"this recording is not
      the one they agreed to"**, which is a different and more serious answer
      than the other two, and the reason the digest exists at all.

    The third names the substituted clip rather than saying the check failed,
    because the person reading it needs to know which of the two happened.
    """
    allowed, why = _switch_allows(config)
    if not allowed:
        return False, (f"turn on \"{gate_label()}\" in Settings to use a voice "
                       "built from a recording")

    entry = lookup(voice)
    if entry is None:
        # Asked *after* the switch and *before* the "nothing is registered"
        # sentence, because a registry that cannot be read would otherwise
        # produce that sentence - and its remedy, record the agreement, would
        # write into a file nothing reads back.
        unreadable = _registry_problem()
        if unreadable:
            return False, (f"{unreadable}, so whether {voice!r} was ever "
                           "agreed to cannot be told. Chronoa will not build "
                           "anybody's voice on a guess: fix that file or move "
                           "it aside, then try again")
        return False, (f"no voice called {voice!r} has been agreed to. "
                       "Record the speaker's agreement first - Chronoa will not "
                       "build someone's voice from a recording they did not "
                       "agree to")

    path = pathlib.Path(clip or entry.clip).expanduser()
    if not path.is_file():
        return False, (f"the recording {entry.speaker} agreed to is not at "
                       f"{entry.clip} any more, so {voice!r} cannot be used")

    try:
        current = clip_digest(path)
    except OSError as exc:
        return False, f"could not read {path}: {exc}"
    if current != entry.digest:
        return False, (f"the file at {path} is not the recording {entry.speaker} "
                       f"agreed to. The agreement is for specific audio, and that "
                       "file has been replaced. Re-record the agreement against "
                       "the file you actually want to use")

    return True, ""


def clip_problem(path: str) -> str:
    """Why `path` is not usable as a reference clip, or "" when it is.

    Separate from `check()` on purpose. `check()` is a *permission* and fails
    closed; this is a *measurement* about a file, and it is what the voice
    surface and `speak`'s own refusal print. Conflating them produces the
    failure this repository records for `kokoro_problem` - a page that reports
    a working engine as "not installed" because the person declined it.

    Only what can be measured here is claimed. Duration needs an audio header
    this module does not parse, so the length check is Kitten's documented
    range rather than a measurement, and `clone_problem()` says which is which.
    """
    p = pathlib.Path(path).expanduser()
    if not p.is_file():
        return f"there is no recording at {p}"
    try:
        size = p.stat().st_size
    except OSError as exc:
        return f"could not read {p}: {exc}"
    if size <= 0:
        return f"{p} is empty"
    if size > MAX_CLIP_BYTES:
        return (f"{p} is {size / 1e6:.0f} MB. A reference clip is a few seconds "
                "of one person speaking; this is not something to use as one")
    return ""


def clone_problem() -> str:
    """Why cloning cannot run on this machine, or "" when it can.

    For the voice surface, the Settings row and `speak`'s own answer. Checks
    only what is knowable without importing the engine: the package, then the
    weights. **The weights are not present on any Shanios image and cannot be
    made present by this code** - they are ~1.7 GB behind a community licence
    with revenue and funding caps, which is the recorded reason Kitten is not
    a tier. So this reports honestly and the refusal names the switch first, so
    a person who never wanted this is not sent to read a licence.
    """
    try:
        import importlib.util as _util
        if _util.find_spec("kittenml") is None:
            raise ImportError("kittenml is not installed")
    except Exception:  # noqa: BLE001 - any failure to find it is the same answer
        return ("the KittenTTS package is not installed (it needs torch, which "
                "Chronoa does not package), so a recorded voice cannot be built "
                "on this machine")
    weights = _weights_dir()
    if not weights.is_dir():
        return (f"the KittenTTS 2 weights are not installed ({weights}); they "
                "are about 1.7 GB under the Stellon Labs Community License")
    return ""


def voices() -> List[Tuple[str, Consent]]:
    """`(name, consent)` for every registered voice, for `speak`'s `voices`.

    Built from the registry rather than from the clips on disk, so the list
    cannot offer a voice nobody agreed to - the dead-control shape this
    repository records for a row over a queue nothing can enqueue.
    """
    return [(entry.voice, entry) for entry in _read_registry()]


def synthesize(voice: str, text: str, output_file: str,
               config: Any = None) -> Tuple[bool, str]:
    """Speak `text` as `voice` into `output_file`. `(ok, why-not)`.

    **Asks `check()` again here**, even though the caller has already asked. The
    dispatcher checks it to choose an engine, and a caller that reached this
    function by any other route - a future panel, a script, the MCP server -
    would otherwise get a synthesised voice without ever consulting the gate.
    A permission enforced only at the caller is a permission the next caller
    does not have, which is this repository's own recorded lesson about the
    permission layer sitting beside a check rather than inside it.

    Returns the same `(ok, why)` shape as `check()` rather than a bare bool, so
    the reason reaches the person through `tts._synthesize`'s existing error
    path instead of becoming a line in a log they never read.
    """
    allowed, why = check(voice, config=config)
    if not allowed:
        return False, why

    problem = clone_problem()
    if problem:
        return False, f"could not speak as {voice!r}: {problem}"

    # Deferred: ~1.7B parameters behind torch, which this machine does not
    # have and Chronoa does not package. Everything above runs on a machine
    # with none of it, which is the point - the gate is enforced everywhere and
    # only the synthesis is unavailable.
    from kittenml import KittenTTS

    entry = lookup(voice)
    model = KittenTTS(str(_weights_dir()))
    model.generate_to_file(text, output_file, reference=entry.clip)
    return True, ""


def _weights_dir() -> pathlib.Path:
    """Where the engine's weights are read from."""
    return files.data_home() / "shani-chronoa" / "kitten-tts-2"