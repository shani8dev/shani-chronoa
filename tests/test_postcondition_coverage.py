"""Every mutating skill must be able to say whether it worked.

The learning layer is blocked on this, and the blockage is measurable rather
than theoretical. Three independent measurements in `learning.py` agree:

- `evaluate_bandit()` reports `beats_chance: False`;
- `OutcomeModel` accuracy equals the majority baseline exactly;
- `train_and_save()` **refuses**, with "the training split contains no
  example of verified - a model cannot learn a class it never sees".

All three are downstream of the same cause: the overwhelming majority of
recorded calls come back `unverified` because most mutators have no
post-condition, so nothing ever confirms the action took effect.

This records the gap rather than pretending it is closed. A test demanding full
coverage would fail today and get deleted; this names each missing skill and
fails **only if the set grows**.
"""

import sys
from pathlib import Path

#: `pytest` was never imported here while three tests call `pytest.skip(...)`
#: (pyflakes: "undefined name 'pytest'"). They only fire when `set_privacy` is
#: absent from the mutating set, so the file has read as green — and on the
#: machine where the guard was meant to do something it would have raised
#: `NameError` instead of skipping.
import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import capabilities, verification  # noqa: E402
from shani_chronoa.skills import discover_skills  # noqa: E402

#: The 9 of 38 mutating skills with no post-condition.
#:
#: Two are deliberate. `ask_user` and `accessibility` are no-ops or pure
#: queries: a check can only answer "I don't know", and one that returns
#: TRUE unconditionally would manufacture `verified` verdicts out of
#: nothing - the exact corruption these checks exist to stop. The other
#: four need a service query whose success is genuinely ambiguous
#: (`media_control`: PulseAudio accepting a volume set does not mean a
#: speaker got louder) or produce no artifact at all.
#:
#: Three more arrived with the video/audio skills (`capture_video`,
#: `create_video`, `edit_audio`), and their reason is structural rather than
#: an oversight: each picks **the first free filename**
#: (`camera-10s.mp4`, then `-1`, `-2`, ...), so the path a call wrote is not
#: derivable from its arguments afterwards. A check would have to glob for
#: "the newest match", which is the stale-file trap in its purest form - a
#: refused second call finds the first call's file and reports success for
#: work that never happened. Closing this honestly means the skill has to
#: *return* or *accept* its output path, which is a change to those skills
#: rather than to this list.
#:
#: 29 of 38 now verify themselves; this list is what is left. measured from the
#: code rather than listed by hand - a hand-written list was wrong in both
#: directions when this was first written, claiming skills that already had
#: checks and missing others that did not. Listed rather than derived so that REMOVING a
#: name is a deliberate, reviewable act: it means that skill gained the ability
#: to confirm itself.
MISSING = frozenset({
    "accessibility", "ask_user", "bluetooth_devices", "capture_video",
    "create_video", "edit_audio", "find_emoji", "generate_password",
    "media_control",
})


def _mutating_handlers():
    """(skill name, handler module) for every mutating skill.

    `post_condition_for` takes a **module**, and a skill's name is not its
    module's name - `set_privacy` lives in `skills/privacy.py`. The first
    version of this file guessed `shani_chronoa.skills.<skill name>` and found
    nothing, which is exactly the failure mode that would have made the gap look
    empty.
    """
    tools, handlers = discover_skills()
    caps = capabilities.find_capabilities(tools)
    mutating = {c.tool for c in caps if c.tool in capabilities.MUTATING_TOOLS}
    return sorted(
        (name, handler.__module__)
        for name, handler in handlers.items() if name in mutating
    )


def test_the_declared_gap_still_matches_reality():
    """The recorded list must be the real one.

    If it drifts, the note above it lies - and a note that lies about which
    skills cannot verify themselves is worse than no note.
    """
    missing = set()
    for _name, module in _mutating_handlers():
        try:
            declared = verification.post_condition_for(module)
        except Exception:  # noqa: BLE001 - a broken module is "no check"
            declared = None
        if declared is None:
            missing.add(_name)
    assert missing == set(MISSING), (
        "the recorded set of mutators without a post-condition is out of date.\n"
        f"  newly missing: {sorted(missing - set(MISSING))}\n"
        f"  now covered  : {sorted(set(MISSING) - missing)}"
    )


def test_a_declared_post_condition_is_actually_found():
    """The lookup must work, or every other skill's would be missed too.

    `set_privacy` reads the microphone's GSettings key back, so it is the
    cheapest real check available and the proof the mechanism is live rather
    than merely present.
    """
    modules = dict(_mutating_handlers())
    module = modules.get("set_privacy")
    if module is None:
        pytest.skip("set_privacy is not a mutating skill in this build")
    declared = verification.post_condition_for(module)
    assert declared is not None, (
        "set_privacy declares POST_CONDITION and post_condition_for is not "
        "finding it, so every other skill's is being missed too"
    )


def test_a_check_returns_a_verdict_and_its_evidence():
    """A check that returns only True/False cannot say *why*, and the evidence
    string is what makes a failed verdict actionable."""
    modules = dict(_mutating_handlers())
    module = modules.get("set_privacy")
    if module is None:
        pytest.skip("set_privacy is not a mutating skill in this build")
    declared = verification.post_condition_for(module)
    outcome = declared({"action": "mute_mic"})
    assert outcome is not None, "a mutating action produced no verdict at all"
    ok, evidence = outcome
    assert isinstance(ok, bool)
    assert isinstance(evidence, str) and evidence


def test_a_non_mutating_action_checks_nothing():
    """`status` cannot be verified or refuted, so it must claim neither."""
    modules = dict(_mutating_handlers())
    module = modules.get("set_privacy")
    if module is None:
        pytest.skip("set_privacy is not a mutating skill in this build")
    declared = verification.post_condition_for(module)
    assert declared({"action": "status"}) is None