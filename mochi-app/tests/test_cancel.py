import http.server
import queue
import threading
import time
import unittest

from mochi_app import paths

paths.enable_agent_imports()

import cancellation
from brain.llama_cpp_provider import LlamaCppProvider
from brain.tool_registry import ToolRegistry
from mochi_app.assistant import Assistant
from mochi_app.device.protocol import LLM_IDLE, LLM_SPEAKING, LLM_THINKING
from mochi_command_service import MochiCommandService


# =========================================================
# llama-server FALSO: tarda en contestar y avisa si le cortan
# =========================================================

class SlowServer:

    def __init__(self, delay):
        outer = self
        self.delay = delay
        self.received = threading.Event()
        self.disconnected = threading.Event()

        class Handler(http.server.BaseHTTPRequestHandler):

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.received.set()
                time.sleep(outer.delay)
                try:
                    body = b'{"choices": [{"message": {"content": "hola"}}]}'
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    self.wfile.flush()
                except OSError:
                    outer.disconnected.set()

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class CancellationTests(unittest.TestCase):

    def test_request_is_cut_while_waiting_for_the_llm(self):
        server = SlowServer(delay=5)
        self.addCleanup(server.close)
        provider = LlamaCppProvider(model_name="m", base_url=server.url)
        outcome = {}

        def run():
            token = cancellation.begin()
            try:
                provider._request("POST", "/v1/chat/completions", payload={"a": 1}, timeout=30)
                outcome["result"] = "terminó"
            except cancellation.CommandCancelled:
                outcome["result"] = "cancelada"
            finally:
                cancellation.end(token)

        thread = threading.Thread(target=run)
        started = time.monotonic()
        thread.start()
        self.assertTrue(server.received.wait(2))

        self.assertTrue(cancellation.cancel())
        thread.join(2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(outcome["result"], "cancelada")
        self.assertLess(time.monotonic() - started, 3)

    def test_request_works_without_cancelling(self):
        server = SlowServer(delay=0)
        self.addCleanup(server.close)
        provider = LlamaCppProvider(model_name="m", base_url=server.url)

        token = cancellation.begin()
        try:
            response = provider._request("POST", "/v1/chat/completions", payload={"a": 1}, timeout=5)
        finally:
            cancellation.end(token)

        self.assertEqual(response["choices"][0]["message"]["content"], "hola")

    def test_threads_without_a_command_are_not_affected(self):
        # El precalentamiento no abre una orden: nunca recibe CommandCancelled.
        server = SlowServer(delay=0)
        self.addCleanup(server.close)
        provider = LlamaCppProvider(model_name="m", base_url=server.url)

        token = cancellation.begin()
        cancellation.cancel()
        cancellation.end(token)

        other = {}

        def warm():
            other["response"] = provider._request("POST", "/x", payload={}, timeout=5)

        thread = threading.Thread(target=warm)
        thread.start()
        thread.join(3)
        self.assertIn("choices", other["response"])

    def test_cancel_without_command_returns_false(self):
        self.assertFalse(cancellation.cancel())

    def test_tool_does_not_start_after_cancelling(self):
        registry = ToolRegistry()
        ran = []
        registry.register(
            name="algo",
            description="x",
            parameters={"type": "object", "properties": {}},
            handler=lambda: ran.append(1) or (True, "ok"),
        )

        token = cancellation.begin()
        try:
            self.assertTrue(registry.execute("algo", {})["ok"])
            cancellation.cancel()
            with self.assertRaises(cancellation.CommandCancelled):
                registry.execute("algo", {})
        finally:
            cancellation.end(token)

        self.assertEqual(ran, [1])

    def test_command_service_reports_cancelled(self):
        class Agent:
            def run(self, **kwargs):
                cancellation.cancel()
                cancellation.check()

        service = MochiCommandService(agent=Agent(), registry=object())
        result = service.execute("hola", source="test")

        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(result["cancelled"])
        self.assertFalse(result["ok"])
        self.assertFalse(cancellation.cancel())     # se cerró la orden


# =========================================================
# ASISTENTE
# =========================================================

class FakeSession:

    ready_text = "listo"

    def __init__(self):
        self.states = []
        self.texts = []
        self.spoken = []
        self.log = []

    def show_state(self, state): self.states.append(state)
    def show_text(self, text): self.texts.append(text)
    def hold_music(self, active): pass
    def post(self, kind, value): self.log.append((kind, value))
    def speak(self, pcm, rate): self.spoken.append(pcm)


class FakeTts:

    sample_rate = 22050

    def synthesize(self, text):
        return b"\0\0" * 10


def wait_until(condition, seconds=3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


class AssistantCancelTests(unittest.TestCase):

    def make(self, execute):
        events = queue.Queue()
        assistant = Assistant(events)
        assistant.ready = True
        assistant.execute = execute
        assistant.tts = FakeTts()
        return assistant, events, FakeSession()

    @staticmethod
    def chat(events):
        found = []
        while not events.empty():
            kind, value = events.get()
            if kind == "chat":
                found.append(value)
        return found

    def test_cancel_while_thinking_discards_the_reply(self):
        release = threading.Event()
        started = threading.Event()

        def execute(text):
            started.set()
            release.wait(3)
            return {"ok": True, "text": "una respuesta"}

        assistant, events, session = self.make(execute)
        self.assertTrue(assistant.submit_text(session, "hola"))
        self.assertTrue(started.wait(2))

        self.assertTrue(assistant.cancel())
        release.set()

        self.assertTrue(wait_until(lambda: not assistant.busy))
        self.assertEqual(session.spoken, [])
        self.assertEqual(session.states[-1], LLM_IDLE)
        self.assertNotIn(LLM_SPEAKING, session.states)
        self.assertIn(("notice", "Cancelado. Podés repetirlo."), self.chat(events))

    def test_can_repeat_after_cancelling(self):
        release = threading.Event()
        started = threading.Event()
        calls = []

        def execute(text):
            calls.append(text)
            if len(calls) == 1:
                started.set()
                release.wait(3)
            return {"ok": True, "text": "listo"}

        assistant, events, session = self.make(execute)
        assistant.submit_text(session, "primera")
        self.assertTrue(started.wait(2))
        assistant.cancel()
        release.set()
        self.assertTrue(wait_until(lambda: not assistant.busy))

        self.assertTrue(assistant.submit_text(session, "segunda"))
        self.assertTrue(wait_until(lambda: not assistant.busy))

        self.assertEqual(calls, ["primera", "segunda"])
        self.assertEqual(len(session.spoken), 1)
        self.assertFalse(assistant.cancelled)

    def test_cancel_while_speaking_stops_the_rest(self):
        speaking = threading.Event()
        release = threading.Event()
        assistant, events, session = self.make(lambda text: {"ok": True, "text": "hola"})

        def speak(pcm, rate):
            speaking.set()
            release.wait(3)

        session.speak = speak
        assistant.submit_text(session, "hola")
        self.assertTrue(speaking.wait(2))

        assistant.cancel()
        release.set()

        self.assertTrue(wait_until(lambda: not assistant.busy))
        self.assertEqual(session.states[-1], LLM_IDLE)

    def test_cancel_without_a_command_does_nothing(self):
        assistant, events, session = self.make(lambda text: {"ok": True, "text": ""})
        self.assertFalse(assistant.cancel())
        self.assertFalse(assistant.cancelled)

    def test_normal_command_unaffected(self):
        assistant, events, session = self.make(lambda text: {"ok": True, "text": "hola"})
        assistant.submit_text(session, "hola")
        self.assertTrue(wait_until(lambda: not assistant.busy))
        self.assertIn(LLM_THINKING, session.states)
        self.assertEqual(len(session.spoken), 1)


if __name__ == "__main__":
    unittest.main()
