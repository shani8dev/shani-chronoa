"""Skill: search the web by opening a query in the default browser.

Local-only privacy mode blocks this skill: opening a browser and sending a
query to a search engine is external network activity, so while privacy mode
is on the handler returns a clear local-only result instead of launching
anything. The decision comes from the same authoritative policy used
everywhere else (`PrivacyManager.get_network_policy()`), read fresh on every
call so a mid-session privacy toggle takes effect immediately - including
when the skill is invoked through the normal tool handler path.
"""

import urllib.parse

import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib  # type: ignore

from shani_chronoa.config import ChronoaConfig, PrivacyManager
from shani_chronoa.skills import Skill

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for a query by opening it in the default browser.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
            },
            "required": ["query"],
        },
    },
}


_LOCAL_ONLY_RESULT = "Web search is disabled in local-only privacy mode."


def _run(arguments: dict) -> str:
    query = (arguments.get("query") or "").strip()
    if not query:
        return "No search query given."
    policy = PrivacyManager(ChronoaConfig()).get_network_policy()
    if not policy.get("external_api", True):
        return _LOCAL_ONLY_RESULT
    url = "https://duckduckgo.com/?q=" + urllib.parse.quote(query)
    try:
        Gio.AppInfo.launch_default_for_uri(url, None)
    except GLib.Error as e:
        return f"Failed to open browser: {e.message}"
    return f"Searching the web for '{query}'."


SKILLS = [Skill(name="web_search", schema=_SCHEMA, run=_run)]
