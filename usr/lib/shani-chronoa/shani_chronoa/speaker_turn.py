"""Optional per-turn speaker labels, from the local diarizer and nothing else.

A spoken turn may carry a neutral label (``Speaker 1``) when three things hold:
the person granted the voice consent the speaker-ID skill already uses,
somebody is enrolled in its local registry, and the local diarization model is
installed. The label is a *diarization cluster*, the same kind ``speakers.parse``
already writes for recordings - never an enrolled person's name and never a
biometric identity claim.

No network, no new model and no new consent key: the enrollment registry is the
existing ``speaker_id`` one and the consent is ``hearing-sense-enabled``, which
already has a Settings row. Everything else is a silent no-op: no audio, no
consent, no enrollment, no installed model, an unreadable file or a failed
diarizer all return ``""`` so the turn behaves exactly as it did before.
"""

from __future__ import annotations

from pathlib import Path


def label_for(wav: object, config=None) -> str:
    """A neutral speaker label for one captured utterance, or ``""``.

    ``wav`` is the just-recorded audio, still on disk. Nothing is written and
    nothing is raised on any refusal path - a label is an optional annotation,
    never a reason to lose a transcript.
    """
    try:
        from shani_chronoa import speakers
        from shani_chronoa.skills import speaker_id

        allowed, _ = speaker_id._consent(config)
        if not allowed:
            return ""
        if not speaker_id._read_registry():
            return ""
        if not speakers.installed():
            return ""
        turns = speakers.diarize(Path(str(wav)))
    except Exception:  # noqa: BLE001 - an optional label must never cost a turn
        return ""

    totals: dict[str, float] = {}
    for start, end, label in turns:
        if not label:
            continue
        totals[label] = totals.get(label, 0.0) + max(0.0, float(end) - float(start))
    if not totals:
        return ""
    # The dominant cluster over this utterance. Ties keep the first label the
    # diarizer reported, so the result is stable rather than set-ordered.
    return max(totals, key=totals.get)
