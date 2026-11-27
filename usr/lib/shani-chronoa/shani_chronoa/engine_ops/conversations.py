"""Conversations as engine data: no GTK, one conversation store.

Every operation delegates to ``conversation_store``. ``app`` is optional and
only used to point a live assistant at the conversation that was just opened;
redrawing a window stays with the caller.
"""

from __future__ import annotations

from pathlib import Path

from shani_chronoa import conversation_store


def _root() -> Path:
    return conversation_store.session_dir()


def _missing(ref, what: str) -> dict:
    return {"error": f"no conversation matches {ref!r} to {what}"}


def list_sessions(limit=100) -> list[dict]:
    """Conversations, most recently used first, capped at ``limit``."""
    try:
        return conversation_store.list_sessions(_root(), limit=limit)
    except Exception as exc:  # noqa: BLE001 - errors are data on this boundary
        return [{"error": f"conversations could not be listed: {exc}"}]


def resume(ref, app=None) -> dict:
    """Open ``ref`` (id or title); when ``app`` is given, switch its assistant too."""
    try:
        root = _root()
        sid = conversation_store.switch(root, str(ref or ""))
        if not sid:
            return _missing(ref, "resume")
        path = conversation_store.session_path(root, sid)
        if app is not None:
            assistant = getattr(app, "assistant", None)
            switch = getattr(assistant, "switch_session", None)
            if not callable(switch):
                return {"error": "the app has no assistant to switch"}
            switch(Path(path))
        return {"session": str(path), "ref": sid}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"the conversation could not be resumed: {exc}"}


def rename(ref, title) -> dict:
    """Give ``ref`` a new title. Returns the title the store actually kept."""
    try:
        root = _root()
        sid = conversation_store.rename(root, str(ref or ""), str(title or ""))
        if not sid:
            return _missing(ref, "rename")
        row = next(
            (s for s in conversation_store.list_sessions(root) if s["id"] == sid), None
        )
        return {"ref": sid, "title": row["title"] if row else str(title)}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"the conversation could not be renamed: {exc}"}


def delete(ref, *, confirm=False) -> dict:
    """Move ``ref`` to the trash. Refuses without ``confirm``."""
    if not confirm:
        return {"error": "confirmation required to delete a conversation"}
    try:
        sid = conversation_store.delete(_root(), str(ref or ""))
        if not sid:
            return _missing(ref, "delete")
        return {"deleted": sid}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"the conversation could not be deleted: {exc}"}
