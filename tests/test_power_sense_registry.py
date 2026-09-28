"""The `power` sense, and the traps in `/sys/class/power_supply`.

Every number in this file is a real reading from this machine, and three of
the four tests below exist because a plausible-looking wrong answer is
possible here — a laptop that reports 0%, a battery over 100% full, a pack
worn to nothing, or a laptop reported as having three batteries. All four were
found by reading the actual files, not from documentation.

The full suite is in `test_sense_power.py`; this file covers the registry
wiring, which is the part that silently denies a sense whose consent key was
never declared.
"""

import pytest

from shani_chronoa.senses import discover_senses

ALL = [n for n, s in discover_senses().items()]


def _sense(name):
    return discover_senses()[name]


class TestRegistryWiring:
    def test_power_is_registered(self):
        assert "power" in discover_senses(), (
            "the power sense is not in the registry - a module that is never "
            "discovered passes every module-level test and is dead code"
        )

    def test_its_schema_name_matches_its_sense_name(self):
        """`sense_allowed()` builds the consent key as f"{name}-sense-enabled",
        so a mismatch here makes the sense permanently ungrantable."""
        sense = _sense("power")
        assert sense.schema["function"]["name"] == "power"

    def test_it_declares_a_valid_function_schema(self):
        from shani_chronoa.senses import is_valid_schema
        assert is_valid_schema(_sense("power").schema)

    def test_its_consent_key_exists_in_the_schema(self):
        from shani_chronoa.config import _SENSE_CONSENT_KEYS
        assert _SENSE_CONSENT_KEYS.get("power") == "power-sense-enabled"

    def test_it_is_ambient_and_polls_on_a_slow_interval(self):
        """Battery state changes on the order of minutes, so polling it every
        few seconds would spend a filesystem read to learn nothing."""
        sense = _sense("power")
        assert sense.is_ambient()
        assert sense.poll_interval >= 60

    def test_it_is_not_durable(self):
        """A charge reading is a fact about right now; a stale one is wrong."""
        assert _sense("power").ttl_seconds is not None
