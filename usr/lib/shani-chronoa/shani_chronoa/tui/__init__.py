"""Chronoa's terminal client.

This package is a *client* of dev.shani.chronoa.Agent. It renders the events
the engine sends and turns keystrokes into D-Bus calls; it never picks a model,
runs a skill, or keeps a conversation. Everything that decides lives in the
engine, so the panel, the GUI and this terminal see the same answers.
"""

from shani_chronoa.agent_contract import BUS_NAME, IFACE, PATH

__all__ = ["BUS_NAME", "IFACE", "PATH"]
