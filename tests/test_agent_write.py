"""`Agent.Write`: text in, text out - the shell's writing tools, smart replies
and file naming. Not a turn: the model alone, its answer cleaned of anything
that would land in someone's email (a thinking block, fences, wrapping quotes).
"""

import asyncio

from shani_chronoa import agent_service
from shani_chronoa.asyncbridge import AsyncBridge


class _Params:
    def __init__(self, values):
        self._values = values

    def unpack(self):
        return self._values


class _Invocation:
    def __init__(self):
        self.value = None
        self.error = None

    def return_value(self, variant):
        self.value = variant.unpack()

    def return_dbus_error(self, name, message):
        self.error = (name, message)


class _LLM:
    def __init__(self, reply="", fail=None):
        self.reply, self.fail, self.seen = reply, fail, None

    # the interface every client really has (the local one has no chat(): a
    # stub with chat() is how Write first passed here and failed on the model)
    async def chat_message(self, messages, tools=None, stream=False):
        self.seen = messages
        await asyncio.sleep(0)
        if self.fail:
            raise self.fail
        return {"role": "assistant", "content": self.reply}


class _App:
    def __init__(self, llm):
        self.llm = llm
        self._async = AsyncBridge()


def write(app, instruction, text):
    inv = _Invocation()
    agent_service.AgentService(app).Write(None, ":1.9", None, None, "Write", _Params((instruction, text)), inv)
    app._async.shutdown()
    return inv


def test_the_model_rewrites_and_only_its_text_comes_back():
    llm = _LLM("<think>short it</think>\n“Meet at 5.”")
    inv = write(_App(llm), "Make it shorter", "We should probably meet at five o'clock if that works.")
    assert inv.error is None and inv.value == ("Meet at 5.",)
    system, user = llm.seen
    assert system["role"] == "system" and "only the resulting text" in system["content"]
    assert user["content"].startswith("Make it shorter") and "<text>\nWe should probably" in user["content"]


def test_no_model_is_said_not_guessed():
    inv = write(_App(None), "Proofread", "teh cat")
    assert inv.value is None and inv.error[0] == "dev.shani.chronoa.Error.NoModel"


def test_an_empty_instruction_is_refused_before_the_model():
    llm = _LLM("x")
    inv = write(_App(llm), "  ", "text")
    assert inv.error[0] == "org.freedesktop.DBus.Error.InvalidArgs" and llm.seen is None


def test_a_failing_model_is_an_error_not_an_empty_answer():
    inv = write(_App(_LLM(fail=RuntimeError("server gone"))), "Proofread", "teh")
    assert inv.value is None and "server gone" in inv.error[1]


def test_text_is_capped():
    llm = _LLM("ok")
    write(_App(llm), "Summarise", "a" * (agent_service.WRITE_MAX_CHARS + 500))
    assert llm.seen[1]["content"].count("a") <= agent_service.WRITE_MAX_CHARS + 20


def test_cleaning_keeps_what_belongs_to_the_text():
    c = agent_service.clean_written
    assert c("```\nfixed text\n```") == "fixed text"
    assert c('"quoted"') == "quoted"
    assert c('She said "hi" and left') == 'She said "hi" and left'     # quotes inside stay
    assert c("  plain  ") == "plain"
