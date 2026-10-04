"""Skill: find photos in the Pictures folder by what is in them - "the receipt from the hardware shop".

`photo_library` keeps the index: file names, when each was taken (EXIF), any
text in it (tesseract), and - with the optional extras - the objects in it, a
caption, and meaning. `index` brings it up to date a batch at a time; `search`
answers from it. Everything stays on this machine.
"""

from __future__ import annotations

from shani_chronoa.skills import Skill

_ACTIONS = ("search", "index", "status")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "photos",
        "description": (
            "Find the user's photos by what is in them or written on them, or when they were taken. "
            "search: query like 'receipt hardware shop', 'dog on the beach', 'december 2025'; index: "
            "update the index of the Pictures folder (a batch at a time); status: how much is indexed."
        ),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": list(_ACTIONS)},
            "query": {"type": "string"},
            "captions": {"type": "boolean", "description": "index: also describe each photo (slow; needs Eyes)."},
        }, "required": ["action"]},
    },
}


def _run(arguments: dict) -> str:
    from shani_chronoa import photo_library
    action = (arguments.get("action") or "").strip().lower()
    if action == "status":
        s = photo_library.status()
        if not s["total"]:
            return "No photos are indexed yet; ask to index the Pictures folder."
        return (f"{s['total']} photos indexed: {s['with_text']} with text in them, "
                f"{s['with_objects']} with recognised objects.")
    if action == "index":
        r = photo_library.index(captions=bool(arguments.get("captions")))
        more = f"; {r['waiting']} more to do - ask again to continue" if r["waiting"] else ""
        return f"Indexed {r['indexed']} photos in {r['seconds']:.0f} s ({r['total']} in the index{more})."
    if action != "search":
        return f"action must be one of {', '.join(_ACTIONS)}."
    query = (arguments.get("query") or "").strip()
    if not query:
        return "Say what to look for."
    hits = photo_library.search(query)
    if not hits:
        s = photo_library.status()
        return ("No photos are indexed yet; ask to index the Pictures folder first." if not s["total"]
                else f"No indexed photo matches {query!r}.")
    lines = []
    for h in hits:
        bits = [x for x in (h["taken"][:10].replace(":", "-") if h["taken"] else "", h["labels"], h["caption"],
                            (f"text: {h['text'][:60]}" if h["text"] else "")) if x]
        lines.append(f"- {h['path']}" + (f" ({'; '.join(bits)})" if bits else ""))
    return f"Photos matching {query!r}:\n" + "\n".join(lines)


SKILLS = [Skill(name="photos", schema=SCHEMA, run=_run)]
