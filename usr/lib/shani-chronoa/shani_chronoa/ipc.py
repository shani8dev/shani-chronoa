"""Peer-Validated IPC for shani-chronoa."""

import hashlib
import json
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


class SecurityError(Exception):
    """Raised when a security validation fails."""
    pass


class Message:
    """Represents an IPC message."""

    def __init__(self, sender: str, recipient: str, payload: dict[str, Any]):
        self.sender = sender
        self.recipient = recipient
        self.payload = payload
        self.timestamp = datetime.now(timezone.utc)
        self.signature: Optional[str] = None

    def _unsigned_payload(self) -> dict[str, Any]:
        """Return message fields excluding the signature for signing."""
        return {
            "sender": self.sender,
            "recipient": self.recipient,
            "payload": self.payload,
            "timestamp": self.timestamp.isoformat(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "sender": self.sender,
            "recipient": self.recipient,
            "payload": self.payload,
            "timestamp": self.timestamp.isoformat(),
            "signature": self.signature,
        }


class PeerValidator:
    """Validates IPC messages between agents using SHA256 integrity."""

    def __init__(self):
        self._queue: deque[Message] = deque()
        self._max_queue_size = 1000
        logger.info("PeerValidator initialized")

    def sign_message(self, message: Message) -> str:
        """Sign a message using SHA256 of its unsigned payload."""
        data = json.dumps(message._unsigned_payload(), sort_keys=True)
        signature = hashlib.sha256(data.encode()).hexdigest()
        message.signature = signature
        return signature

    def verify_signature(self, message: Message) -> bool:
        """Verify a message's SHA256 signature."""
        if not message.signature:
            logger.warning("Message has no signature")
            return False
        data = json.dumps(message._unsigned_payload(), sort_keys=True)
        expected = hashlib.sha256(data.encode()).hexdigest()
        if expected != message.signature:
            logger.warning("Message signature verification failed")
            return False
        return True

    def validate_message(self, message: Message) -> bool:
        """Validate a message: check signature, timestamp freshness, and structure."""
        if not self.verify_signature(message):
            raise SecurityError("Invalid message signature")
        age = (datetime.now(timezone.utc) - message.timestamp).total_seconds()
        if age > 300:
            raise SecurityError("Message too old (potential replay attack)")
        return True

    def enqueue(self, message: Message) -> None:
        """Add a validated message to the queue."""
        self.validate_message(message)
        if len(self._queue) >= self._max_queue_size:
            self._queue.popleft()
        self._queue.append(message)
        logger.debug("Message queued from %s to %s", message.sender, message.recipient)

    def dequeue(self) -> Optional[Message]:
        """Remove and return the next message from the queue."""
        if self._queue:
            return self._queue.popleft()
        return None
