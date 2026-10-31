from brain.brain_factory import (
    create_brain_provider
)


# =========================================================
# BRAIN ROUTER
# =========================================================

class BrainRouter:

    def __init__(
        self,
        provider=None
    ):

        self.provider = (
            provider
            or create_brain_provider()
        )


        if self.provider is None:

            raise RuntimeError(
                "No se pudo crear Mochi Brain."
            )


        self.last_mode = None

    def should_use_deep_for(self, user_command):
        """Señales generales de planificación; las acciones simples quedan FAST."""
        text = str(user_command or "").casefold()
        sequential = (" luego ", " después ", " despues ", " y luego ")
        conditional = (" si ", "cuando ")
        multi_scope = (" dentro de ", " cuenta de google ", " perfil de navegador ")
        return any(signal in f" {text} " for signal in (*sequential, *conditional, *multi_scope))


    # =====================================================
    # TOOL INTERNA PARA ESCALAR A DEEP
    # =====================================================

    def _deep_request_tool(
        self
    ):

        return {

            "type":
                "function",

            "function": {

                "name":
                    "request_deep_reasoning",

                "description":
                    (
                        "Usá esta herramienta solamente cuando "
                        "la petición del usuario requiera razonamiento "
                        "más profundo, planificación de varios pasos, "
                        "resolver ambigüedades importantes, investigar "
                        "antes de actuar o relacionar varias acciones "
                        "dependientes entre sí. "
                        "No usar para acciones simples y directas."
                    ),

                "parameters": {

                    "type":
                        "object",

                    "properties": {

                        "reason": {

                            "type":
                                "string",

                            "description":
                                (
                                    "Motivo breve por el que la "
                                    "petición necesita razonamiento "
                                    "profundo."
                                )
                        }
                    },

                    "required": [
                        "reason"
                    ]
                }
            }
        }


    # =====================================================
    # DETECTAR SOLICITUD DE DEEP
    # =====================================================

    def _requested_deep(
        self,
        tool_calls
    ):

        for call in (
            tool_calls
            or []
        ):

            if not isinstance(
                call,
                dict
            ):

                continue


            function = (
                call.get(
                    "function",
                    {}
                )
                or {}
            )


            name = (
                function.get(
                    "name",
                    ""
                )
            )


            if (
                name
                ==
                "request_deep_reasoning"
            ):

                return True


        return False


    # =====================================================
    # MODO RÁPIDO
    # =====================================================

    def fast(
        self,
        messages,
        tools=None,
        options=None
    ):

        final_options = {

            "temperature":
                0.1,

            "think":
                False,

            "num_predict":
                256
        }


        if options:

            final_options.update(
                options
            )


        self.last_mode = (
            "fast"
        )


        return self.provider.generate(

            messages=messages,

            tools=tools,

            options=final_options
        )


    # =====================================================
    # MODO PROFUNDO
    # =====================================================

    def deep(
        self,
        messages,
        tools=None,
        options=None
    ):

        final_options = {

            "temperature":
                0.1,

            "think":
                True
        }


        if options:

            final_options.update(
                options
            )


        self.last_mode = (
            "deep"
        )


        # Providers con caché de prompt (llama_cpp sin razonamiento):
        # se manda la misma lista de tools que en FAST, con la tool
        # de routing al final. Cambiar la lista invalida la caché.
        if getattr(
            self.provider,
            "prefers_stable_tools",
            False
        ):

            return self._deep_with_stable_tools(

                messages=messages,

                tools=tools,

                options=final_options
            )


        return self.provider.generate(

            messages=messages,

            tools=tools,

            options=final_options
        )


    def _deep_with_stable_tools(
        self,
        messages,
        tools,
        options
    ):

        routing_tools = list(
            tools
            or []
        )

        if not any(
            (tool.get("function") or {}).get("name")
            == "request_deep_reasoning"
            for tool in routing_tools
            if isinstance(tool, dict)
        ):

            routing_tools.append(
                self._deep_request_tool()
            )


        result = self.provider.generate(

            messages=messages,

            tools=routing_tools,

            options=options
        )


        # Ya estamos en DEEP: si el modelo vuelve a pedirlo,
        # se descarta esa llamada (no es una tool real).
        if result.success and result.tool_calls:

            result.tool_calls = [

                call

                for call in result.tool_calls

                if not self._requested_deep(
                    [call]
                )
            ]


        return result


    # =====================================================
    # MODO AUTOMÁTICO
    # =====================================================

    def auto(
        self,
        messages,
        tools=None,
        options=None
    ):

        real_tools = list(
            tools
            or []
        )


        # FAST recibe además una tool especial.
        # No ejecuta nada en Windows.
        # Solamente permite al modelo decir:
        #
        # "Esta petición necesita pensar más."

        routing_tools = (
            real_tools
            +
            [
                self._deep_request_tool()
            ]
        )


        fast_result = (
            self.fast(

                messages=messages,

                tools=routing_tools,

                options=options
            )
        )


        # Si FAST falló, devolvemos el error.

        if not fast_result.success:

            return fast_result


        # Si FAST pidió razonamiento profundo,
        # repetimos la petición con DEEP.
        #
        # Esta vez NO incluimos la tool interna
        # de routing.

        if self._requested_deep(
            fast_result.tool_calls
        ):

            return self.deep(

                messages=messages,

                tools=real_tools
            )


        # FAST pudo resolverlo directamente.

        self.last_mode = (
            "fast"
        )


        return fast_result


    # =====================================================
    # GENERAR SEGÚN MODO
    # =====================================================

    def generate(
        self,
        messages,
        tools=None,
        mode="auto",
        options=None
    ):

        normalized_mode = (
            str(mode)
            .strip()
            .lower()
        )


        if (
            normalized_mode
            ==
            "deep"
        ):

            return self.deep(

                messages=messages,

                tools=tools,

                options=options
            )


        if (
            normalized_mode
            ==
            "fast"
        ):

            return self.fast(

                messages=messages,

                tools=tools,

                options=options
            )


        return self.auto(

            messages=messages,

            tools=tools,

            options=options
        )


# =========================================================
# SINGLETON
# =========================================================

brain_router = BrainRouter()


# =========================================================
# PRUEBA
# =========================================================

if __name__ == "__main__":

    print("")
    print(
        "======================================"
    )
    print(
        "        MOCHI BRAIN ROUTER"
    )
    print(
        "======================================"
    )

    print(
        f"Provider: "
        f"{brain_router.provider.provider_name}"
    )

    print(
        f"Modelo: "
        f"{brain_router.provider.model_name}"
    )

    print("")

    print(
        "Modos disponibles:"
    )

    print(
        "  - auto"
    )

    print(
        "  - fast"
    )

    print(
        "  - deep"
    )

    print("")

    print(
        "Modo por defecto: auto"
    )

    print(
        "======================================"
    )
