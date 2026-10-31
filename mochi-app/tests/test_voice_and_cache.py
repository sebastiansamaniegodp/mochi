import unittest

from brain.brain_provider import BrainResult
from brain.brain_router import BrainRouter
from mochi_command_service import MochiCommandService
from mochi_app.voice.speech import to_speech_text


# =========================================================
# TEXTO PARA HABLAR
# =========================================================

class SpeechTextTests(unittest.TestCase):

    def test_removes_markdown_lists_and_pids(self):
        text = (
            "Tenés 2 ventanas abiertas:\n"
            "1. **ROBOTICA — Dolphin** (PID: 2114)\n"
            "2. **media_controller.py - Visual Studio Code** (PID: 2115)"
        )
        self.assertEqual(
            to_speech_text(text),
            "Tenés 2 ventanas abiertas: ROBOTICA — Dolphin. "
            "media_controller.py - Visual Studio Code.",
        )

    def test_cuts_long_text_at_sentence(self):
        first = "Esta es la primera oración y es bastante completa."
        text = f"{first} " + "palabra " * 80
        self.assertEqual(to_speech_text(text, limit=100), first)

    def test_cuts_at_word_when_no_sentence_fits(self):
        text = "palabra " * 80
        self.assertEqual(to_speech_text(text, limit=30), "palabra palabra palabra...")

    def test_short_text_is_unchanged(self):
        self.assertEqual(to_speech_text("Volumen al 50 %."), "Volumen al 50 %.")


# =========================================================
# INDICACIÓN DE RESPUESTA (puente de voz)
# =========================================================

class ReplyHintTests(unittest.TestCase):

    class Agent:
        def __init__(self):
            self.calls = []

        def run(self, **kwargs):
            self.calls.append(kwargs)
            return {"ok": True, "text": "listo"}

    def test_reply_hint_reaches_agent_only_when_given(self):
        agent = self.Agent()
        service = MochiCommandService(agent=agent, registry=object())

        service.execute("subí el volumen", source="esp32", reply_hint="corto")
        service.execute("subí el volumen", source="android")

        self.assertEqual(agent.calls[0]["reply_hint"], "corto")
        self.assertNotIn("reply_hint", agent.calls[1])


# =========================================================
# DEEP CON LA MISMA LISTA DE TOOLS (caché de llama-server)
# =========================================================

class StableToolsTests(unittest.TestCase):

    class Provider:
        prefers_stable_tools = True

        def __init__(self, tool_calls):
            self.tool_calls = tool_calls
            self.sent_tools = []

        def generate(self, messages, tools=None, options=None):
            self.sent_tools.append([tool["function"]["name"] for tool in tools])
            return BrainResult(True, tool_calls=list(self.tool_calls))

    @staticmethod
    def _tool(name):
        return {"type": "function", "function": {"name": name, "parameters": {}}}

    def _router(self, provider):
        router = BrainRouter.__new__(BrainRouter)
        router.provider = provider
        router.last_mode = None
        return router

    def test_deep_sends_same_tools_as_auto(self):
        provider = self.Provider([])
        router = self._router(provider)
        tools = [self._tool("get_system_volume")]

        router.generate(messages=[], tools=tools, mode="auto")
        router.generate(messages=[], tools=tools, mode="deep")

        self.assertEqual(
            provider.sent_tools[0],
            ["get_system_volume", "request_deep_reasoning"],
        )
        self.assertEqual(provider.sent_tools[1], provider.sent_tools[0])

    def test_deep_drops_repeated_deep_request(self):
        provider = self.Provider([
            {"function": {"name": "request_deep_reasoning", "arguments": {}}},
            {"function": {"name": "get_system_volume", "arguments": {}}},
        ])
        router = self._router(provider)

        result = router.deep(messages=[], tools=[self._tool("get_system_volume")])

        self.assertEqual(
            [call["function"]["name"] for call in result.tool_calls],
            ["get_system_volume"],
        )


if __name__ == "__main__":
    unittest.main()
