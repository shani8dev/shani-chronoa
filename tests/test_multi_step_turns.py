"""A person's multi-step request can finish, and an unfinished one says so.

Measured in the real app on 2026-10-08 with a free cloud model: "book the
cheapest flight on blazedemo.com, then weather, rupees, itinerary, reminder"
was cut off after 4 tool rounds, `browse` was never offered (the model tried to
book with `web_search`), and the final no-tools answer was the model's next
call written as markup - which is what the person saw.
"""

from shani_chronoa import assistant, tool_select, tools


def test_a_person_gets_enough_rounds_for_a_real_task():
    assert assistant.MAX_TOOL_ROUNDS >= 20
    assert assistant._rounds_for_origin(tools.ORIGIN_USER) == assistant.MAX_TOOL_ROUNDS


def test_an_unattended_turn_still_gets_one_round():
    assert assistant._rounds_for_origin("unattended-or-unknown") == 1


def test_out_of_rounds_markup_is_never_the_answer():
    markup = ("<tool_call>\n<function=web_search>\n<parameter=url>\nhttps://blazedemo.com/purchase.php\n"
              "</parameter>\n</function>\n</tool_call>")
    out = assistant._out_of_rounds(markup, 24)
    assert "<" not in out and "24 steps" in out and "continue" in out


def test_plain_words_beside_the_markup_are_kept():
    out = assistant._out_of_rounds("The cheapest is $200.98.\n<function=browse><parameter=x>1</parameter></function>", 24)
    assert out.startswith("The cheapest is $200.98.") and "<function" not in out


def test_an_ordinary_answer_is_untouched():
    assert assistant._out_of_rounds("It is 8 degrees in London.", 24) == "It is 8 degrees in London."


def _offered(request):
    return {t["function"]["name"] for t in tool_select.select_tools(request, tools.TOOLS)}


def test_a_website_task_is_offered_the_browser():
    assert "browse" in _offered("On blazedemo.com book the cheapest flight from Boston to London")
    assert "browse" in _offered("fill in the form at https://example.org/signup")
    assert "browse" in _offered("order a pizza for me")


def test_a_question_with_no_site_is_not_sent_the_browser():
    assert "browse" not in _offered("what is the weather in London")


def test_a_website_said_aloud_is_offered_the_browser():
    """Transcripts say "dot com"; people say "the X website" (measured 2026-10-08:
    neither got `browse`, and the small model opened the desktop browser)."""
    for said in ("Open blaze demo dot com and find the cheapest flight",
                 "Go to the blaze demo website and find the cheapest flight",
                 "open youtube dot com"):
        assert "browse" in _offered(said), said


def test_a_website_task_is_not_offered_file_and_app_tools():
    offered = _offered("Open blaze demo dot com and find the cheapest flight from Boston to London")
    assert not offered & tool_select._DISPLACED_BY_A_SITE, offered & tool_select._DISPLACED_BY_A_SITE


def test_file_and_app_requests_keep_their_tools():
    assert "open_application" in _offered("open firefox")
    assert "open_file" in _offered("download the file from example.com to my Downloads folder")
    assert "browse" not in _offered("find my tax PDF")
