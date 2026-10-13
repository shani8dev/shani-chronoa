"""Mutation set for the API key the cloud-keys page asked with - and for the row.

Four defects this pass fixed, three of them reachable from a person's chair:

1. **The key was never sent.** `fill()` called `offerable_models(provider_id)`
   with no `api_key`, so no `Authorization` header was built and the keyed
   providers could not list a model **on any machine, with any key saved**.
   Measured against each provider's own base_url: openai and groq answer 401
   without a key.

2. **`config.API_KEY_SETTINGS` did not exist.** `API_KEY_SETTINGS` is a
   module-level constant in `config.py`; inside `build_window` `config` is the
   `ChronoaConfig` **instance**, so the expression raised `AttributeError` on
   every key save. **That one is at HEAD** (`git show HEAD:...setup_wizard.py`
   line 1661) - the wizard's own save button has been broken on every install.

3. **"Your own server" had no row at all.** It is in neither
   `BYOK_PROVIDER_ORDER` (it needs no key) nor `DEFAULT_PROVIDER_ORDER` (it is
   not a free gateway) - and `brain.py` prepends it on its own when an address
   is set. So a loop over those two tuples builds nine provider rows out of the
   ten this page can configure, and the missing one is the only one needing an
   address, a model *and* a key.

4. **The gear only opened.** `opening = not box_.get_visible()` is what lets the
   same control close it again; a gear that can only open leaves no way back.

Every mutation restores one of these, or widens a fix wrongly. The set is the
only reason "14 passed" means anything.
"""

TEST_FILE = "tests/test_cloud_key_in_its_own_row.py"

_WIZ = "usr/lib/shani-chronoa/shani_chronoa/setup_wizard.py"

MUTATIONS = [
    {
        "label": "the API key is not handed to the fetch (the shipped defect)",
        "expect": ['test_a_saved_key_is_what_the_fetch_is_given'],
        "file": _WIZ,
        "old": 'ids, why = _load_models(provider_id,\n'
               '                                        api_key=have.get(provider_id, ""))',
        "new": 'ids, why = _load_models(provider_id)',
    },
    {
        "label": "the key is sent, but the row lies about having one",
        "expect": ['test_a_saved_key_is_what_the_fetch_is_given'],
        "file": _WIZ,
        "old": "have = _Config().cloud_llm_api_keys()",
        "new": "have = {pid: \"\" for pid in cloud_llm.PROVIDERS}",
    },
    {
        "label": "the save reads the instance attribute (the HEAD defect)",
        "expect": ['test_the_pages_own_save_button_reports_what_it_saved'],
        "file": _WIZ,
        "old": "config.set_api_key(API_KEY_SETTINGS[provider_id], value)",
        "new": "config.set_api_key(config.API_KEY_SETTINGS[provider_id], value)",
    },
    {
        "label": "your own server is dropped again - neither tuple names it",
        "expect": ['test_your_own_server_is_a_row_with_its_own_three_fields', 'test_every_ordered_provider_now_has_a_row'],
        "file": _WIZ,
        "old": "                        + [cloud_llm.CUSTOM_ID]):",
        "new": "                        ):",
    },
    {
        "label": "the gear opens and can never close",
        "expect": ['test_the_gear_reveals_the_key_and_the_same_gear_closes_it'],
        "file": _WIZ,
        "old": "opening = not box_.get_visible()",
        "new": "opening = True",
    },
    {
        "label": "an empty key overwrites a working one",
        "expect": ['test_an_empty_key_is_not_saved_over_a_good_one'],
        "file": _WIZ,
        "old": "            if key in API_KEY_SETTINGS.values():\n"
               "                if not value:\n",
        "new": "            if key in API_KEY_SETTINGS.values():\n"
               "                if False:\n",
    },
]