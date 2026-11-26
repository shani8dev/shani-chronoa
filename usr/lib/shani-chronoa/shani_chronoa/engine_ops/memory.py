"""Durable memory as engine data: no GTK, one memory implementation.

Facts come from the durable tier of ``senses.memory.get_store``; proposals come
from ``senses.memory.pending_proposals``; a decision goes through
``senses.memory.decide_candidate``. Nothing here writes a fact itself, and no
metadata (which can carry keywords) is copied into the returned rows.
"""

from __future__ import annotations

from shani_chronoa.senses import memory as sense_memory


_YES = ("yes", "y", "true", "1", "accept", "keep")
_NO = ("no", "n", "false", "0", "reject", "decline")


def _row_id(proposal) -> str:
    return f"{proposal.session_id}:{proposal.index}"


def _accept(decision):
    """The caller's yes/no as a bool, or None when it is ambiguous."""
    if isinstance(decision, bool):
        return decision
    if isinstance(decision, str):
        word = decision.strip().lower()
        if word in _YES:
            return True
        if word in _NO:
            return False
    return None


def list_memory(limit=100) -> dict:
    """Facts already kept and facts proposed but not yet decided."""
    denied = sense_memory._consent_error()
    if denied:
        return {"error": denied}
    try:
        cap = max(0, int(limit))
        facts = [
            {
                "text": p.content,
                "created": p.created_at,
                "source": p.source,
                "confidence": p.confidence,
            }
            for p in sense_memory.get_store().durable()[:cap]
        ]
        proposals = [
            {
                "id": _row_id(p),
                "text": p.text,
                "summary": p.summary,
                "source_index": p.source_index,
                "source_role": p.source_role,
                "created": p.created,
            }
            for p in sense_memory.pending_proposals(limit=cap)
        ]
        return {"facts": facts, "proposals": proposals}
    except Exception as exc:  # noqa: BLE001 - errors are data on this boundary
        return {"error": f"memory could not be listed: {exc}"}


def decide(proposal_id, decision, *, confirm=False) -> dict:
    """Accept or decline one proposal. Refuses without ``confirm``."""
    if not confirm:
        return {"error": "confirmation required to decide a proposed fact"}
    accept = _accept(decision)
    if accept is None:
        return {"error": "decision must be yes or no"}
    ref = str(proposal_id or "")
    try:
        session_id, raw_index = ref.rsplit(":", 1)
        index = int(raw_index)
        if not session_id:
            raise ValueError("no session in the proposal id")
    except (ValueError, AttributeError):
        return {"error": f"{ref!r} is not a proposal id"}
    denied = sense_memory._consent_error()
    if denied:
        return {"error": denied}
    try:
        outcome, reason = sense_memory.decide_candidate(session_id, index, accept)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"the proposal could not be decided: {exc}"}
    if outcome == "kept":
        return {"id": ref, "decision": "yes"}
    if outcome == "declined":
        return {"id": ref, "decision": "no"}
    if outcome == "kept-unrecorded":
        return {
            "error": f"the answer was recorded, but the fact was not stored: {reason}"
        }
    return {"error": reason or f"the proposal was not decided ({outcome})"}
