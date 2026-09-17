"""Tests for IPC module."""

import pytest
from shani_chronoa.ipc import Message, PeerValidator, SecurityError


def test_sign_and_verify():
    """Test that sign and verify work correctly."""
    validator = PeerValidator()
    msg = Message("alice", "bob", {"cmd": "test"})
    sig = validator.sign_message(msg)
    assert sig is not None
    assert validator.verify_signature(msg) is True


def test_verify_fails():
    """Test that tampered messages fail verification."""
    validator = PeerValidator()
    msg1 = Message("alice", "bob", {"cmd": "test"})
    msg2 = Message("alice", "bob", {"cmd": "tampered"})
    sig = validator.sign_message(msg1)
    msg2.signature = sig  # Wrong signature for different content
    assert validator.verify_signature(msg2) is False


def test_validate_message():
    """Test message validation."""
    validator = PeerValidator()
    msg = Message("alice", "bob", {"cmd": "test"})
    validator.sign_message(msg)
    assert validator.validate_message(msg) is True


def test_enqueue_dequeue():
    """Test message queue."""
    validator = PeerValidator()
    msg = Message("alice", "bob", {"cmd": "test"})
    validator.sign_message(msg)
    validator.enqueue(msg)
    dequeued = validator.dequeue()
    assert dequeued is not None
    assert dequeued.sender == "alice"
