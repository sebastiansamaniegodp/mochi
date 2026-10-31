import json
import threading
import time
import unittest
from urllib.parse import quote_plus
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import main
from mochi_command_service import MochiCommandService


class _FakeAgent:
    def __init__(self, result=None, error=None, delay=0):
        self.result = result or {
            "ok": True,
            "status": "completed",
            "text": "hecho",
            "steps": 1,
            "final_mode": "test",
        }
        self.error = error
        self.delay = delay
        self.calls = []
        self.active = 0
        self.max_active = 0
        self._state_lock = threading.Lock()

    def run(self, **kwargs):
        with self._state_lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            self.calls.append(kwargs)
            if self.delay:
                time.sleep(self.delay)
            if self.error:
                raise self.error
            return dict(self.result)
        finally:
            with self._state_lock:
                self.active -= 1


class MochiCommandServiceTests(unittest.TestCase):
    def test_empty_voice_text_is_rejected_without_running_agent(self):
        agent = _FakeAgent()
        service = MochiCommandService(agent=agent, registry=object())

        result = service.execute("  \r\n ", source="voice")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "empty_command")
        self.assertEqual(result["source"], "voice")
        self.assertEqual(agent.calls, [])

    def test_same_registry_is_reused_and_unicode_command_reaches_agent(self):
        agent = _FakeAgent()
        registry = object()
        service = MochiCommandService(agent=agent, registry=registry)

        first = service.execute("  reproducí Isaías  ", source="voice", debug=True)
        second = service.execute("pausá el vídeo", source="android")

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertIs(agent.calls[0]["registry"], registry)
        self.assertIs(agent.calls[1]["registry"], registry)
        self.assertEqual(agent.calls[0]["user_command"], "reproducí Isaías")

    def test_partial_engine_run_is_processed_but_not_goal_success(self):
        agent = _FakeAgent(result={
            "ok": True,
            "status": "partial",
            "text": "necesito una selección",
            "steps": 2,
        })
        service = MochiCommandService(agent=agent, registry=object())

        result = service.execute("reproduce el video", source="voice")

        self.assertFalse(result["ok"])
        self.assertTrue(result["processed"])
        self.assertFalse(result["goal_completed"])
        self.assertEqual(result["status"], "partial")

    def test_concurrent_commands_are_serialized(self):
        agent = _FakeAgent(delay=0.04)
        service = MochiCommandService(agent=agent, registry=object())
        threads = [
            threading.Thread(target=service.execute, args=(f"orden {index}",))
            for index in range(3)
        ]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(agent.max_active, 1)
        self.assertEqual(len(agent.calls), 3)


class MochiHTTPBridgeTests(unittest.TestCase):
    def setUp(self):
        self.original_service = main.command_service
        self.agent = _FakeAgent()
        main.command_service = MochiCommandService(
            agent=self.agent,
            registry=object(),
        )
        self.server = main.create_server("127.0.0.1", 0)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
        main.command_service = self.original_service

    def test_ping_and_utf8_get_command(self):
        with urlopen(self.base_url + "/ping", timeout=2) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read().decode("utf-8"), "Mochi Desktop conectado")

        command = "reproducí el vídeo de Isaías"
        with urlopen(
            self.base_url + "/command?q=" + quote_plus(command),
            timeout=2,
        ) as response:
            result = json.loads(response.read().decode("utf-8"))

        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "http_get")
        self.assertEqual(self.agent.calls[-1]["user_command"], command)

    def test_post_command_and_invalid_json(self):
        payload = json.dumps({
            "text": "buscá un vídeo",
            "source": "voice",
            "debug": True,
        }, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.base_url + "/command",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            result = json.loads(response.read().decode("utf-8"))

        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "voice")
        self.assertEqual(self.agent.calls[-1]["user_command"], "buscá un vídeo")

        bad_request = Request(
            self.base_url + "/command",
            data=b"not-json",
            method="POST",
        )
        with self.assertRaises(HTTPError) as captured:
            urlopen(bad_request, timeout=2)
        self.assertEqual(captured.exception.code, 400)
        captured.exception.close()

    def test_ping_remains_responsive_during_slow_command(self):
        entered = threading.Event()
        release = threading.Event()

        class _BlockingAgent(_FakeAgent):
            def run(inner_self, **kwargs):
                entered.set()
                release.wait(timeout=2)
                return dict(inner_self.result)

        main.command_service = MochiCommandService(
            agent=_BlockingAgent(),
            registry=object(),
        )
        command_done = threading.Event()

        def issue_command():
            try:
                with urlopen(self.base_url + "/command?q=orden", timeout=3) as response:
                    response.read()
            finally:
                command_done.set()

        worker = threading.Thread(target=issue_command, daemon=True)
        worker.start()
        self.assertTrue(entered.wait(timeout=1))
        started = time.monotonic()
        with urlopen(self.base_url + "/ping", timeout=1) as response:
            self.assertEqual(response.status, 200)
        self.assertLess(time.monotonic() - started, 0.5)
        release.set()
        self.assertTrue(command_done.wait(timeout=2))


if __name__ == "__main__":
    unittest.main()
