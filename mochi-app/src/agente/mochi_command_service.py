import threading
import traceback

import cancellation
import vault_memory
from brain.agent_loop import (
    AGENT_SYSTEM_PROMPT,
    agent_loop,
    user_message_content,
)
from brain.mochi_tools import create_mochi_tool_registry
from conversation_memory import (
    SUMMARY_REQUEST,
    ConversationMemory,
)


class MochiCommandService:
    """
    Punto único de entrada para ejecutar órdenes en Mochi.

    Mantiene:
    - el mismo AgentLoop;
    - el mismo ToolRegistry;
    - el estado persistente que mantenga AgentLoop (por ejemplo, una
      selección pendiente) y las sesiones de las herramientas;
    - la memoria de la conversación (conversation_memory);
    - la memoria permanente, si la app activó la bóveda (vault_memory);
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

        # Las órdenes anteriores y sus respuestas: se mandan
        # con cada orden. Se cambia solo con el lock tomado.
        # Empieza con los recuerdos de la bóveda, si hay una.
        self.memory = ConversationMemory(
            pinned_source=vault_memory.pinned_text,
        )


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

        token = None

        try:
            with self._execution_lock:

                # Desde acá la orden se puede cancelar (cancellation.cancel()).
                token = cancellation.begin()

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

                # Después de media hora sin órdenes, la conversación
                # se olvida (la app además la limpia sola en ese momento).
                if self.memory.expired():
                    print("[memoria] media hora sin órdenes: conversación nueva")
                    self.memory.clear()

                history = self.memory.messages()

                if history:
                    run_arguments["history"] = history

                result = self.agent.run(**run_arguments)

                # Se llenó el contexto de llama-server: queda en la
                # memoria solo la última orden. Si todavía no se hizo
                # nada, se reintenta una vez; si no, repetirla
                # podría hacer dos veces lo que ya se hizo.
                if history and self._context_full(result):
                    print("[memoria] se llenó el contexto: borro lo más viejo")
                    self.memory.shrink()

                    if not result.get("actions"):
                        run_arguments["history"] = self.memory.messages()
                        result = self.agent.run(**run_arguments)

                    elif not result.get("text"):
                        result["text"] = (
                            "Se me llenó la memoria en medio de la orden "
                            "y tuve que borrar lo más viejo. Fijate qué "
                            "quedó hecho y repetime lo que falte."
                        )

                self.memory.record(
                    user_message_content(command, reply_hint),
                    self._clean_text(result.get("text")),
                )

                # Queda anotado en la bóveda (la nota del día).
                vault = vault_memory.active()

                if vault is not None:
                    vault.log_turn(
                        command,
                        self._clean_text(result.get("text")),
                    )

        except cancellation.CommandCancelled:

            return {
                "ok": False,
                "status": "cancelled",
                "text": "",
                "error": "cancelled",
                "cancelled": True,
                "source": clean_source,
            }

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

        finally:
            if token is not None:
                cancellation.end(token)

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


    @staticmethod
    def _context_full(result):

        error = str(result.get("error") or "")

        return (
            "exceed_context_size" in error
            or "exceeds the available context size" in error
        )


    # =====================================================
    # MEMORIA DE LA CONVERSACIÓN
    # =====================================================

    def compact_memory(self):
        """
        Si la conversación llegó al 80 % del contexto, resume
        las órdenes viejas con el LLM. Mientras tanto, las
        órdenes esperan. Devuelve True si compactó.
        """

        with self._execution_lock:

            if not self.memory.needs_compaction():
                return False

            router = self.agent.router

            # Las mismas tools que una orden en modo "auto"
            # (ver warm_up_llm): así reusa la caché.
            tools = (
                self.registry.get_tool_schemas()
                + [router._deep_request_tool()]
            )

            def summarize(history):

                result = router.fast(
                    messages=(
                        [{"role": "system", "content": AGENT_SYSTEM_PROMPT}]
                        + history
                        + [{"role": "user", "content": SUMMARY_REQUEST}]
                    ),
                    tools=tools,
                    options={"num_predict": 320},
                )

                if not result.success:
                    print(f"[memoria] no pude resumir: {result.error}")
                    return ""

                if result.tool_calls:
                    print("[memoria] no pude resumir: pidió una herramienta")
                    return ""

                return result.text

            self.memory.compact(summarize)
            return True


    def forget(self):
        """
        Empieza una conversación nueva: también olvida la lista
        de opciones pendiente ("poné la segunda") y el sitio del
        que se venía hablando. Devuelve False si no había
        ninguna conversación.
        """

        with self._execution_lock:

            pending_choices = getattr(self.agent, "pending_choice_cache", None)

            if pending_choices is not None:
                pending_choices.clear()

            if hasattr(self.agent, "_context_provider"):
                self.agent._context_provider = None

            if self.memory.empty:
                return False

            self.memory.clear()
            return True


# =========================================================
# SINGLETON
# =========================================================

command_service = MochiCommandService()
