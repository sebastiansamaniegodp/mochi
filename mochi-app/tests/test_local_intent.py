import json
import unittest

from brain.agent_loop import MochiAgentLoop
from brain.brain_provider import BrainResult
from brain.goal_state import plan_goal
from brain.local_intent import (
    INTENT_SCHEMA,
    intent_to_operation,
    interpret_local_command,
    mentions_local_capability,
)
from brain.tool_registry import ToolRegistry


class IntentRouter:
    """Answers the intent call with a fixed JSON; the agent turn must not run."""

    last_mode = "fast"

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def should_use_deep_for(self, _command):
        return False

    def fast(self, messages, tools=None, options=None):
        self.calls.append({"messages": messages, "options": options})
        text = self.answer if isinstance(self.answer, str) else json.dumps(self.answer)
        return BrainResult(success=True, text=text)

    def generate(self, **_kwargs):
        raise AssertionError("A validated intent must not need an agent turn.")


class StrictVolumeRuleTests(unittest.TestCase):
    def test_whole_sentences_are_planned_without_the_model(self):
        cases = {
            "baja el volumen a cero por ciento": ("set_system_volume", "0", None),
            "Bajá el volumen a cero porciento.": ("set_system_volume", "0", None),
            "sube el volumen al cincuenta por ciento": ("set_system_volume", "50", None),
            "poné el volumen al treinta y cinco": ("set_system_volume", "35", None),
            "baja el volumen cinco por ciento": ("control_media", "volume_down", "5"),
            "baja el volumen en 20": ("control_media", "volume_down", "20"),
            "sube el volumen un poco": ("control_media", "volume_up", "10"),
            "mochi sube el volumen": ("control_media", "volume_up", "10"),
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                step = plan_goal(command).steps[0]
                self.assertEqual((step.operation, step.target, step.value), expected)

    def test_partial_matches_are_left_to_the_model(self):
        # The old keyword rule turned these into "volume_down 10".
        for command in (
            "bajale un toque al volumen",
            "baja el volumen que no escucho la tele",
            "a cero baja",
        ):
            with self.subTest(command=command):
                state = plan_goal(command)
                self.assertEqual((state.goal, state.steps), ("user_request", []))


class IntentValidationTests(unittest.TestCase):
    def test_valid_answers_map_to_operations(self):
        cases = [
            ({"accion": "fijar_volumen", "valor": 0, "pide_otra_cosa": False}, ("set_system_volume", "0", None)),
            ({"accion": "bajar_volumen", "valor": None, "pide_otra_cosa": False}, ("control_media", "volume_down", "10")),
            ({"accion": "subir_volumen", "valor": 15, "pide_otra_cosa": False}, ("control_media", "volume_up", "15")),
            ({"accion": "consultar_volumen", "valor": None, "pide_otra_cosa": False}, ("get_system_volume", None, None)),
            ({"accion": "silenciar", "valor": None, "pide_otra_cosa": False}, ("control_media", "mute", None)),
            ({"accion": "siguiente", "valor": None, "pide_otra_cosa": False}, ("control_media", "next", None)),
        ]
        for answer, expected in cases:
            with self.subTest(answer=answer):
                self.assertEqual(intent_to_operation(answer, "orden"), expected)

    def test_invalid_answers_are_rejected(self):
        for answer in (
            None,
            {"accion": "ninguna", "valor": None, "pide_otra_cosa": False},
            {"accion": "borrar_todo", "valor": None, "pide_otra_cosa": False},
            {"accion": "fijar_volumen", "valor": None, "pide_otra_cosa": False},
            {"accion": "fijar_volumen", "valor": 250, "pide_otra_cosa": False},
            {"accion": "bajar_volumen", "valor": -5, "pide_otra_cosa": False},
            {"accion": "fijar_volumen", "valor": "50", "pide_otra_cosa": False},
            {"accion": "fijar_volumen", "valor": True, "pide_otra_cosa": False},
            {"accion": "fijar_volumen", "valor": 0, "pide_otra_cosa": True},
            {"accion": "fijar_volumen", "valor": 0},
        ):
            with self.subTest(answer=answer):
                self.assertIsNone(intent_to_operation(answer, "orden"))

    def test_only_local_audio_sentences_reach_the_model(self):
        self.assertTrue(mentions_local_capability("bajale un toque al volumen"))
        self.assertTrue(mentions_local_capability("pasá a la siguiente canción"))
        self.assertTrue(mentions_local_capability("cerrá todo lo de chrome"))
        self.assertFalse(mentions_local_capability("a cero baja"))
        self.assertFalse(mentions_local_capability("cerrá la pestaña de Netflix"))
        self.assertFalse(mentions_local_capability("bajá el volumen del video de YouTube"))
        router = IntentRouter({"accion": "bajar_volumen", "valor": None, "pide_otra_cosa": False})
        self.assertIsNone(interpret_local_command(router, "abrí la calculadora y buscá recetas"))
        self.assertEqual(router.calls, [])

    def test_the_model_is_asked_for_the_fixed_json_shape(self):
        router = IntentRouter('{"accion": "fijar_volumen", "valor": 0, "pide_otra_cosa": false}')
        self.assertEqual(
            interpret_local_command(router, "dejá el volumen en cero que molesta"),
            ("set_system_volume", "0", None),
        )
        self.assertIs(router.calls[0]["options"]["json_schema"], INTENT_SCHEMA)

    def test_broken_or_failed_answers_fall_back(self):
        self.assertIsNone(interpret_local_command(IntentRouter("no sé"), "volumen raro"))

        class FailingRouter:
            def fast(self, **_kwargs):
                return BrainResult(success=False, error="sin servidor")

        self.assertIsNone(interpret_local_command(FailingRouter(), "volumen raro"))


class StrictLocalRuleTests(unittest.TestCase):
    def test_whole_sentences_are_planned_without_the_model(self):
        cases = {
            "cerrá el bloc de notas": ("close_window", "bloc de notas", None),
            "cierra todas las ventanas de Chrome": ("close_application_windows", "Chrome", None),
            "trae chrome al frente": ("control_window", "chrome", "focus"),
            "pasá a la siguiente canción": ("control_media", "next", None),
            'Copiá "Hola, Mundo" al portapapeles': ("write_clipboard", "Hola, Mundo", None),
            "qué ventanas tengo abiertas": ("list_open_windows", None, None),
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                step = plan_goal(command).steps[0]
                self.assertEqual((step.operation, step.target, step.value), expected)

    def test_keyword_only_sentences_are_left_to_the_model(self):
        # The old rules turned these into focus("el clima"), a screenshot,
        # close_window("que me molesta") and close_window("todo lo de chrome").
        for command in (
            "muestra el clima",
            "no hagas captura de pantalla",
            "cierra la ventana que me molesta",
            "cerrá todo lo de chrome",
        ):
            with self.subTest(command=command):
                state = plan_goal(command)
                self.assertEqual((state.goal, state.steps), ("user_request", []))


class TextIntentValidationTests(unittest.TestCase):
    def test_names_and_texts_must_come_from_the_sentence(self):
        command = "cerrá todo lo de Google Chrome"
        self.assertEqual(
            intent_to_operation(
                {"accion": "cerrar_todas_las_ventanas_de", "valor": None, "texto": "Google Chrome", "pide_otra_cosa": False},
                command,
            ),
            ("close_application_windows", "Google Chrome", None),
        )
        for answer in (
            {"accion": "cerrar_todas_las_ventanas_de", "valor": None, "texto": "Firefox", "pide_otra_cosa": False},
            {"accion": "cerrar_todas_las_ventanas_de", "valor": None, "texto": "Chro", "pide_otra_cosa": False},
            {"accion": "cerrar_todas_las_ventanas_de", "valor": None, "texto": None, "pide_otra_cosa": False},
            {"accion": "cerrar_todas_las_ventanas_de", "valor": None, "texto": "  ", "pide_otra_cosa": False},
            {"accion": "cerrar_todas_las_ventanas_de", "valor": None, "texto": "todo lo de Google Chrome", "pide_otra_cosa": False},
        ):
            with self.subTest(answer=answer):
                self.assertIsNone(intent_to_operation(answer, command))

    def test_clipboard_and_notification_keep_the_users_words(self):
        self.assertEqual(
            intent_to_operation(
                {"accion": "copiar_al_portapapeles", "valor": None, "texto": "«Nos vemos a las 8»", "pide_otra_cosa": False},
                "guardame en el portapapeles «Nos vemos a las 8» porfa",
            ),
            ("write_clipboard", "Nos vemos a las 8", None),
        )
        self.assertEqual(
            intent_to_operation(
                {"accion": "mostrar_notificacion", "valor": None, "texto": "sacar la pizza", "pide_otra_cosa": False},
                "avisame con una notificación: sacar la pizza",
            ),
            ("show_notification", "Mochi", "sacar la pizza"),
        )
        # The whole order copied as the message is rejected.
        command = "guardame en el portapapeles nos vemos a las 8"
        self.assertIsNone(intent_to_operation(
            {"accion": "copiar_al_portapapeles", "valor": None, "texto": command, "pide_otra_cosa": False},
            command,
        ))


class IntentIntegrationTests(unittest.TestCase):
    def test_interpreted_intent_runs_the_tool_without_an_agent_turn(self):
        calls = []
        registry = ToolRegistry()
        registry.register(
            "control_media",
            "control_media",
            {
                "type": "object",
                "properties": {"action": {"type": "string"}, "amount": {"type": "number"}},
                "required": ["action"],
            },
            lambda **arguments: calls.append(arguments) or {
                "ok": True,
                "message": "Volumen al 80%",
            },
        )
        router = IntentRouter({"accion": "bajar_volumen", "valor": 20, "pide_otra_cosa": False})
        result = MochiAgentLoop(router=router, max_steps=3).run(
            "bajale como veinte al volumen",
            registry,
        )
        self.assertEqual(calls, [{"action": "volume_down", "amount": 20.0}])
        self.assertEqual(result["status"], "completed")


if __name__ == "__main__":
    unittest.main()
