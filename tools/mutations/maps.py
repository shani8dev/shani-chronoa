"""Mutation set for the bounded OpenStreetMap cache.

Rebuilt on 2026-10-11 from the committed tests and code, after the original
`/tmp` harness was lost. **Nothing here is from memory**: the runner asserts each
anchor occurs exactly once in the current code and that a named witness fails, so
a mutation set that drifts from the code is an error and not a silent survivor.
"""

TEST_FILE = "tests/test_maps_cache.py"

_MAPS = "usr/lib/shani-chronoa/shani_chronoa/skills/maps.py"

MUTATIONS = [
    # -- a cache is not a memoised outage -------------------------------------
    {
        "file": _MAPS,
        "old": "    body = _get(url, params)\n    _cache_write(path, body)\n    return body\n",
        "new": "    try:\n        body = _get(url, params)\n    except Exception:\n"
               "        body = []\n    _cache_write(path, body)\n    return body\n",
        "label": "a raising fetch is cached as an empty answer",
        "expect": ["test_a_raising_fetch_is_not_memoised"],
    },
    {
        "file": _MAPS,
        "old": "    if not isinstance(entry, dict) or entry.get(\"v\") != _CACHE_VERSION:\n        return None\n",
        "new": "    if not isinstance(entry, dict):\n        return None\n",
        "label": "the version field no longer invalidates an old format",
        "expect": ["test_an_entry_from_another_version_is_ignored"],
    },
    {
        "file": _MAPS,
        "old": "    if time.time() - at > _CACHE_TTL:\n        return None\n",
        "new": "",
        "label": "an expired entry is served forever",
        "expect": ["test_an_expired_entry_is_refetched"],
    },
    {
        "file": _MAPS,
        "old": "    if isinstance(at, bool) or not isinstance(at, (int, float)):\n        return None\n",
        "new": "",
        "label": "a hand-edited timestamp is used in arithmetic",
        "expect": ["test_an_entry_with_a_garbage_timestamp_is_a_miss"],
    },

    # -- the key ---------------------------------------------------------------
    {
        "file": _MAPS,
        "old": "    canonical = url + \"\\x00\" + \"\\x00\".join(\n"
               "        f\"{key}={params[key]}\" for key in sorted(params or {}))\n",
        "new": "    canonical = url + \"\\x00\" + \"\\x00\".join(\n"
               "        f\"{key}={params[key]}\" for key in (params or {}))\n",
        "label": "param order changes the key, so one request is two entries",
        "expect": ["test_param_order_does_not_change_the_key"],
    },
    {
        "file": _MAPS,
        "old": "    return hashlib.sha256(canonical.encode(\"utf-8\")).hexdigest()[:32]\n",
        "new": "    return canonical[:64]\n",
        "label": "the key carries the query, so a listing names the search words",
        "expect": ["test_a_key_does_not_contain_the_query"],
    },

    # -- bounded, and the oldest goes -----------------------------------------
    {
        "file": _MAPS,
        "old": "    spare = len(entries) - cap\n    if spare <= 0:\n        return\n",
        "new": "    if cap <= 0:\n        return\n",
        "label": "the eviction never prunes, so the cache grows without limit",
        "expect": ["test_it_stops_growing"],
    },
    {
        "file": _MAPS,
        "old": "        entries = sorted(directory.iterdir(), key=lambda p: p.stat().st_mtime)\n",
        "new": "        entries = list(directory.iterdir())\n",
        "label": "eviction is unordered, so a live entry can be dropped",
        "expect": ["test_the_oldest_is_the_one_evicted"],
    },
    {
        "file": _MAPS,
        "old": "    for path in entries[:spare]:\n        try:\n            path.unlink()\n        except OSError:\n            pass\n",
        "new": "    for path in entries[:spare]:\n        pass\n",
        "label": "the oldest entries are counted but never removed",
        "expect": ["test_it_stops_growing"],
    },

    # -- a cache is an optimisation, never a failure --------------------------
    {
        "file": _MAPS,
        "old": "    except (OSError, TypeError, ValueError):\n        return\n",
        "new": "    except OSError:\n        return\n",
        "label": "an unencodable body raises instead of being skipped",
        "expect": ["test_a_cache_that_cannot_be_written_is_not_an_error"],
        # `_get` returns parsed JSON, so `json.dumps` cannot raise TypeError on the
        # real path. Equivalent until a source returns something unserialisable.
        "equivalent": True,
        "why": "nothing reaches _cache_write with a body json.dumps cannot encode; "
               "the OSError witness patches Path.write_text, which the narrowed "
               "except still catches",
    },

    # -- the caller's own behaviour -------------------------------------------
    {
        "file": _MAPS,
        "old": "    body = _cache_read(path)\n    if body is not None:\n        return body\n",
        "new": "",
        "label": "the cache is never consulted, so every repeat costs a request",
        "expect": ["test_the_second_call_does_not_touch_the_network",
                   "test_a_second_geocode_costs_no_request",
                   "test_a_second_route_costs_no_request"],
    },
    # A truncated write is invalid JSON, which `except (ValueError, TypeError)`
    # on the `json.loads` already covers, so narrowing the *decode* guard changes
    # nothing observable. Kept with its reason so the next person to write a
    # file with undecodable bytes has a witness to point at.
    {
        "file": _MAPS,
        "old": "    except (OSError, UnicodeDecodeError):\n        return None\n",
        "new": "    except OSError:\n        return None\n",
        "label": "an undecodable cache file raises instead of missing",
        "expect": ["test_a_corrupt_entry_is_a_miss_not_a_crash"],
        "equivalent": True,
        "why": "the corrupt-entry test writes invalid JSON, which the json.loads "
               "except already catches; no test writes undecodable bytes",
    },
]
