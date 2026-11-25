"""Skill: is this machine protected? Secure Boot, TPM, LSMs, lockdown, firewall.

Two senses already do the work: `security` (Secure Boot from efivars, the TPM,
the active LSMs, kernel lockdown, seccomp) and `firewall` (ufw, firewalld,
nftables, iptables). Both are background context; nothing let a person *ask*.
This is the tool-call surface over both, the way `get_location` is over the
location sense.

Each half follows its own sense's switch (`security-sense-enabled`,
`firewall-sense-enabled`, both on by default) - see `sense_reading.py` for why a
skill shares its sense's switch rather than minting one.

The senses' own rule carries over unchanged: a check that did not run is
UNKNOWN, never "secure". Each half is reported even if the other fails.
"""

from __future__ import annotations

from shani_chronoa import sense_reading
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

SCHEMA = {
    "type": "function",
    "function": {
        "name": "security_status",
        "description": (
            "This machine's security posture: whether Secure Boot is on, whether "
            "a TPM is present, which Linux security modules (AppArmor, Landlock, "
            "...) are active, kernel lockdown, and whether a firewall is actually "
            "enforcing anything. Use for 'is Secure Boot on', 'is my firewall "
            "running', 'is this machine secure'. Uses the "
            "'security-sense-enabled' switch (and the firewall sense's). Read-only."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """This skill's sense half follows the security sense's switch (see sense_reading.py)."""
    if config.sense_allowed("security"):
        return True, ""
    return False, sense_reading.refusal(config, "security")


def _half(config: ChronoaConfig, sense: str) -> str:
    if sense == "security":
        allowed, why = _consent(config)
    else:
        allowed = config.sense_allowed(sense)
        why = "" if allowed else sense_reading.refusal(config, sense)
    return sense_reading.reading(sense) if allowed else why


def _run(_arguments: dict) -> str:
    config = ChronoaConfig()
    return "\n\n".join([
        "Boot and kernel:\n" + _half(config, "security"),
        "Firewall:\n" + _half(config, "firewall"),
    ])


SKILLS = [Skill(name="security_status", schema=SCHEMA, run=_run)]
