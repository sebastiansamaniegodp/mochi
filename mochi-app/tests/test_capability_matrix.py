import unittest

from brain.agent_loop import MochiAgentLoop
from brain.goal_state import plan_goal
from brain.mochi_tools import create_mochi_tool_registry
from brain.tool_registry import ToolRegistry


class NeverRouter:
    last_mode = "unused"

    def should_use_deep_for(self, _command):
        return False

    def generate(self, **_kwargs):
        raise AssertionError("A deterministic capability plan must not call the model.")


def register(registry, name, properties, required, handler):
    registry.register(
        name,
        name,
        {
            "type": "object",
            "properties": properties,
            "required": required,
        },
        handler,
    )


class RegistryContractMatrixTests(unittest.TestCase):
    def test_every_required_argument_is_rejected_before_a_handler_runs(self):
        registry = create_mochi_tool_registry()
        for schema in registry.get_tool_schemas():
            function = schema["function"]
            if not function["parameters"].get("required"):
                continue
            with self.subTest(tool=function["name"]):
                result = registry.execute(function["name"], {})
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"], "invalid_arguments")

    def test_every_declared_property_rejects_the_wrong_json_type(self):
        registry = create_mochi_tool_registry()
        wrong_values = {
            "string": 7,
            "number": "seven",
            "integer": 1.5,
            "array": "not-an-array",
            "object": [],
            "boolean": "true",
        }
        checked = 0
        for schema in registry.get_tool_schemas():
            function = schema["function"]
            properties = function["parameters"].get("properties") or {}
            for name, definition in properties.items():
                expected = definition.get("type")
                if expected not in wrong_values:
                    continue
                arguments = {name: wrong_values[expected]}
                with self.subTest(tool=function["name"], argument=name):
                    result = registry.execute(function["name"], arguments)
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["error"], "invalid_arguments")
                checked += 1
        self.assertGreaterEqual(checked, 30)

    def test_enums_and_unknown_arguments_are_rejected_consistently(self):
        registry = create_mochi_tool_registry()
        cases = [
            ("control_media", {"action": "explode"}),
            ("browser_profiles", {"action": "switch"}),
            ("web_act", {"element_id": "e1", "action": "delete"}),
            ("control_window", {"window": "x", "action": "rotate"}),
            ("get_system_volume", {"unexpected": True}),
        ]
        for name, arguments in cases:
            with self.subTest(tool=name):
                result = registry.execute(name, arguments)
                self.assertEqual(result.get("error"), "invalid_arguments")


class NaturalLanguageCapabilityMatrixTests(unittest.TestCase):
    def test_representative_commands_map_to_the_expected_capability(self):
        cases = {
            "dime cuál es el volumen actual": ("get_system_volume", None, None),
            "pon el volumen al 25 por ciento": ("set_system_volume", "25", None),
            "sube el volumen 5": ("control_media", "volume_up", "5"),
            "baja el volumen a cero por ciento": ("set_system_volume", "0", None),
            "baja el volumen a cero porciento": ("set_system_volume", "0", None),
            "sube el volumen al cincuenta por ciento": ("set_system_volume", "50", None),
            "baja el volumen cinco por ciento": ("control_media", "volume_down", "5"),
            "pausa la música": ("control_media", "play_pause", None),
            "copia hola mundo al portapapeles": ("write_clipboard", "hola mundo", None),
            "lee el portapapeles": ("read_clipboard", None, None),
            "haz una captura de pantalla": ("capture_screenshot", None, None),
            "muestra una notificación que diga prueba": ("show_notification", "Mochi", "prueba"),
            "lista mis carpetas": ("list_user_folders", None, None),
            "busca el archivo vacaciones": ("find_files", "vacaciones", None),
            "busca la carpeta Mochi": ("find_folders", "Mochi", None),
            "abre Descargas": ("open_known_folder", "Descargas", None),
            "abre el último archivo descargado": ("open_latest_download", "el último archivo descargado", None),
            "lista aplicaciones instaladas": ("list_installed_applications", None, None),
            "lista juegos instalados": ("list_games", None, None),
            "dime las especificaciones de mi PC": ("get_system_info", None, None),
            "muestra las ventanas abiertas": ("list_open_windows", None, None),
            "minimiza Visual Studio Code": ("control_window", "Visual Studio Code", "minimize"),
            "cierra la calculadora": ("close_window", "calculadora", None),
            "busca gatos en internet": ("browser_search", "gatos", None),
            "abre Netflix": ("browser_open", "Netflix", None),
            "lista los perfiles de Chrome": ("browser_profiles", "list", None),
            "dime el perfil activo de Chrome": ("browser_profiles", "active", None),
            "pausa el video": ("pause_media", None, "Pausa"),
            "reanuda el video": ("play_media", None, "Reproducir"),
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                state = plan_goal(command)
                self.assertTrue(state.steps)
                step = state.steps[0]
                self.assertEqual((step.operation, step.target, step.value), expected)


class DeterministicCapabilityIntegrationTests(unittest.TestCase):
    def run_loop(self, command, registry):
        return MochiAgentLoop(router=NeverRouter(), max_steps=3).run(
            command,
            registry,
        )

    def test_read_only_system_request_runs_without_a_model_turn(self):
        registry = ToolRegistry()
        register(registry, "get_system_volume", {}, [], lambda: {
            "ok": True,
            "percent": 37,
            "message": "Volumen actual: 37%",
        })
        result = self.run_loop("dime cuál es el volumen actual", registry)
        self.assertEqual((result["status"], result["steps"]), ("completed", 1))
        self.assertEqual(result["actions"][0]["tool"], "get_system_volume")
        self.assertIn("37%", result["text"])

    def test_arguments_are_preserved_for_volume_clipboard_and_notification(self):
        cases = [
            (
                "pon el volumen al 25 por ciento",
                "set_system_volume",
                {"percent": {"type": "number"}},
                ["percent"],
                {"percent": 25.0},
            ),
            (
                "copia hola mundo al portapapeles",
                "write_clipboard",
                {"text": {"type": "string"}},
                ["text"],
                {"text": "hola mundo"},
            ),
            (
                "muestra una notificación que diga prueba",
                "show_notification",
                {"title": {"type": "string"}, "message": {"type": "string"}},
                ["title", "message"],
                {"title": "Mochi", "message": "prueba"},
            ),
        ]
        for command, tool, properties, required, expected in cases:
            calls = []
            registry = ToolRegistry()
            register(
                registry,
                tool,
                properties,
                required,
                lambda **arguments: calls.append(arguments) or {"ok": True, "message": "done"},
            )
            with self.subTest(command=command):
                result = self.run_loop(command, registry)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(calls, [expected])

    def test_mixed_open_then_window_control_preserves_dependency_order(self):
        calls = []
        registry = ToolRegistry()
        register(
            registry,
            "open_application",
            {"application": {"type": "string"}},
            ["application"],
            lambda **arguments: calls.append(("open", arguments)) or {"ok": True, "message": "opened"},
        )
        register(
            registry,
            "control_window",
            {"window": {"type": "string"}, "action": {"type": "string"}},
            ["window", "action"],
            lambda **arguments: calls.append(("window", arguments)) or {"ok": True, "message": "minimized"},
        )
        result = self.run_loop("abre Chrome y luego minimízalo", registry)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(calls, [
            ("open", {"application": "Chrome"}),
            ("window", {"window": "Chrome", "action": "minimize"}),
        ])

    def test_failed_dependency_prevents_the_following_action(self):
        calls = []
        registry = ToolRegistry()
        register(
            registry,
            "open_application",
            {"application": {"type": "string"}},
            ["application"],
            lambda **arguments: calls.append(("open", arguments)) or {"ok": False, "message": "not found"},
        )
        register(
            registry,
            "control_window",
            {"window": {"type": "string"}, "action": {"type": "string"}},
            ["window", "action"],
            lambda **arguments: calls.append(("window", arguments)) or {"ok": True},
        )
        result = self.run_loop("abre Chrome y luego minimízalo", registry)
        self.assertEqual(result["status"], "failed")
        self.assertEqual([name for name, _arguments in calls], ["open"])

    def test_unverified_global_media_signal_is_not_goal_success(self):
        registry = ToolRegistry()
        register(
            registry,
            "control_media",
            {"action": {"type": "string"}},
            ["action"],
            lambda **_arguments: {
                "ok": True,
                "message": "Orden enviada",
                "state_confirmed": False,
            },
        )
        result = self.run_loop("pausa la música", registry)
        self.assertEqual(result["status"], "partial")
        self.assertIn("no expone evidencia", result["text"])


if __name__ == "__main__":
    unittest.main()
