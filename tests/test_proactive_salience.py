"""Salience: score, queue and interrupt policy for unattended announcements."""

from shani_chronoa import rhythm, salience


def test_change_severity_and_rules_each_add_their_own_weight():
    base = salience.score("storage", severity="info")
    assert base == 0.0
    assert salience.score("storage", changed=True) == salience.CHANGE_WEIGHT
    assert (
        salience.score("storage", severity="critical") == salience.SEVERITY_WEIGHT * 3
    )
    assert (
        salience.score("storage", severity="critical", changed=True, rules=("storage",))
        > salience.SPEAK_ABOVE
    )


def test_an_unknown_severity_is_quiet_not_loud():
    assert salience.score("x", severity="definitely-critical") == 0.0


def test_scores_are_bounded():
    assert salience.score("x", changed=True, severity="critical", rules=("x",)) <= 10.0


def test_the_queue_pops_highest_first_and_keeps_ties_stable():
    q = salience.PriorityQueue()
    q.push("first", 5)
    q.push("second", 9)
    q.push("third", 5)
    assert [q.pop(), q.pop(), q.pop()] == ["second", "first", "third"]
    assert q.pop() is None


def test_the_queue_is_bounded_and_keeps_the_newest():
    q = salience.PriorityQueue(capacity=2)
    q.push("old-low", 1)
    q.push("old-high", 2)
    q.push("new-high", 3)
    assert len(q) == 2
    assert q.pop() == "new-high"
    assert q.pop() == "old-high"


def test_policy_speaks_only_for_high_unattended_score_with_a_person_present():
    assert salience.policy(9) == "speak"
    assert salience.policy(9, unattended=True) == "notify"
    assert salience.policy(3) == "notify"
    assert salience.policy(1) == "hold"


def test_do_not_disturb_and_sleep_hold_everything():
    assert salience.policy(10, do_not_disturb=True) == "hold"
    assert salience.policy(10, rhythm_state=rhythm.Rhythm.SLEEP) == "hold"
    assert salience.policy(10, rhythm_state=rhythm.Rhythm.IDLE) == "hold"
    assert salience.policy(9, rhythm_state=rhythm.Rhythm.ACTIVE) == "speak"


def test_a_held_item_is_not_dropped():
    q = salience.PriorityQueue()
    q.push("held", 8)
    assert salience.policy(8, do_not_disturb=True) == "hold"
    assert q.pop() == "held"
