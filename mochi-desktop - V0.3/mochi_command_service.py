import threading
import traceback

from brain.agent_loop import agent_loop
from brain.mochi_tools import create_mochi_tool_registry


class MochiCommandService:
    """
    Punto único de entrada para ejecutar órdenes en Mochi.

    Mantiene:
    - el mismo AgentLoop;
    - el mismo ToolRegistry;
    - el estado persistente que mantenga AgentLoop (por ejemplo, una
      selección pendiente) y las sesiones de las herramientas;
    - las instancias persistentes de browser_tools.

    Todas las fuentes de comandos deberían terminar entrando aquí:
    Android, consola, HTTP, etc.
    """

    def __init__(self, agent=None, registry=None):
        # IMPORTANTE:
        # El registry se crea UNA sola vez.
        self.registry = (
            registry
            if registry is not None
            else create_mochi_tool_registry()
        )

        # agent_loop ya es singleton en brain.agent_loop.
        self.agent = agent if agent is not None else agent_loop

        # Protege el estado del AgentLoop/UIA si en el futuro
        # llegan dos comandos simultáneamente.
        self._execution_lock = threading.RLock()


    @staticmethod
    def _clean_text(text):
        if text is None:
            return ""

        return str(text).strip()


    def execute(
        self,
        text,
        source="unknown",
        debug=False,
        verbose=False,
        reply_hint=None,
    ):
        """
        Ejecuta una orden completa mediante el Agent Loop.

        source:
            voice
            android
            http_get
            http_post
            console
            etc.

        debug=True:
            devuelve también actions y goal_state.

        reply_hint:
            indicación para la respuesta final (el puente de voz pide
            respuestas cortas y sin formato).
        """

        command = self._clean_text(text)
        clean_source = self._clean_text(source) or "unknown"

        if not command:
            return {
                "ok": False,
                "status": "invalid_request",
                "text": "No recibí ninguna orden.",
                "error": "empty_command",
                "source": clean_source,
            }

        try:
            with self._execution_lock:

                run_arguments = {
                    "user_command": command,
                    "registry": self.registry,
                    "mode": "auto",
                    "verbose": verbose,
                }

                # Solo se pasa si se pidió: los agentes de prueba
                # no tienen este parámetro.
                if reply_hint:
                    run_arguments["reply_hint"] = reply_hint

                result = self.agent.run(**run_arguments)

        except Exception as error:

            print("")
            print("======================================")
            print("ERROR EN MOCHI COMMAND SERVICE")
            print("======================================")
            print(repr(error))

            traceback.print_exc()

            response = {
                "ok": False,
                "status": "error",
                "text": "Ocurrió un error interno al procesar la orden.",
                "error": "command_service_error",
                "source": clean_source,
                "internal_error": True,
            }
            if debug:
                response["details"] = str(error)
            return response

        status = result.get("status") or (
            "completed"
            if result.get("ok")
            else "failed"
        )
        engine_ok = bool(result.get("ok"))
        # An empty/failed model response used to leak the GoalState default
        # ``completed`` status even though the engine reported failure.  The
        # voice bridge must never call that a completed user objective.
        if not engine_ok and status == "completed":
            status = "failed"
        goal_completed = engine_ok and status == "completed"
        response = {
            # ``ok`` describes the user's objective.  ``processed`` keeps
            # transport/execution success separate, so a partial goal can be
            # returned over HTTP 200 without pretending it was completed.
            "ok": goal_completed,
            "processed": engine_ok,
            "goal_completed": goal_completed,
            "status": status,
            "text": result.get("text") or "",
            "error": result.get("error"),
            "steps": result.get("steps", 0),
            "final_mode": result.get("final_mode"),
            "source": clean_source,
        }

        if debug:
            response["actions"] = result.get(
                "actions",
                [],
            )

            response["goal_state"] = result.get(
                "goal_state",
            )

        return response


# =========================================================
# SINGLETON
# =========================================================

command_service = MochiCommandService()
