"""Reward: a number from outcomes, recorded and never authoritative."""

from shani_chronoa import reward


def test_verified_is_positive_and_failed_is_negative():
    assert reward.reward_for({"verdict": "verified"}) == 1.0
    assert reward.reward_for({"verdict": "failed"}) == -1.0


def test_unverified_and_unknown_are_neutral():
    assert reward.reward_for({"verdict": "unverified"}) == 0.0
    assert reward.reward_for({}) == 0.0
    assert reward.reward_for("not a record") == 0.0


def test_a_person_undoing_or_correcting_is_the_strongest_negative():
    assert reward.reward_for({"verdict": "verified", "undone": True}) == 0.0
    assert reward.reward_for({"verdict": "failed", "corrected": True}) == -1.0


def test_approval_and_denial_add_their_weight_and_the_result_is_bounded():
    assert reward.reward_for({"verdict": "unverified", "approved": True}) == 0.5
    assert reward.reward_for({"verdict": "verified", "denied": True}) == 0.5
    assert -1.0 <= reward.reward_for({"verdict": "failed", "denied": True}) <= 1.0


def test_summary_does_not_turn_unmeasured_calls_into_a_perfect_score():
    got = reward.summary(
        [
            {"verdict": "verified"},
            {"ran": True},
            {"verdict": "failed"},
        ]
    )
    assert got == {"total": 3, "measured": 2, "unmeasured": 1, "mean": 0.0}


def test_an_empty_history_has_no_mean_not_zero():
    got = reward.summary([])
    assert got["mean"] is None and got["total"] == 0
