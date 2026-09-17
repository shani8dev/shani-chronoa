"""Tests for gateway supervisor."""

import pytest
from shani_chronoa.gateway_supervisor import (
    GatewaySupervisor, AgentState, AgentInfo,
)


def test_register_agent():
    supervisor = GatewaySupervisor()
    info = supervisor.register_agent("1", "TestAgent")
    assert info.name == "TestAgent"
    assert info.state == AgentState.CONNECTED


def test_unregister_agent():
    supervisor = GatewaySupervisor()
    supervisor.register_agent("1", "TestAgent")
    supervisor.unregister_agent("1")
    assert supervisor.get_agent_status("1") is None


def test_broadcast_message():
    supervisor = GatewaySupervisor()
    supervisor.register_agent("1", "Agent1")
    supervisor.register_agent("2", "Agent2")
    count = supervisor.broadcast_message({"cmd": "test"})
    assert count == 2


def test_heartbeat():
    supervisor = GatewaySupervisor(heartbeat_interval=1)
    supervisor.register_agent("1", "Agent1")
    supervisor.update_heartbeat("1")
    assert supervisor.get_agent_status("1").state == AgentState.IDLE
