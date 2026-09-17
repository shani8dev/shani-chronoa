"""Tests for sandbox profiles."""

import pytest
from shani_chronoa.sandbox.profiles import (
    AgentProfile, PROFILE_DEFAULT, PROFILE_RESTRICTED,
    PROFILE_FULL_ACCESS, PROFILE_READ_ONLY, get_profile,
)


def test_default_profile():
    assert PROFILE_DEFAULT.name == "default"
    assert PROFILE_DEFAULT.memory_limit == 512
    assert PROFILE_DEFAULT.network_access is True


def test_restricted_profile():
    assert PROFILE_RESTRICTED.network_access is False
    assert PROFILE_RESTRICTED.memory_limit == 256


def test_get_profile():
    profile = get_profile("full-access")
    assert profile.name == "full-access"
    profile = get_profile("nonexistent")
    assert profile.name == "default"


def test_validate_against_profile():
    profile = PROFILE_DEFAULT
    assert profile.validate_against_profile({"tool_name": "any_tool"}) is True
