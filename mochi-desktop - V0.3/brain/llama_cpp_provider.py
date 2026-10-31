import json
import urllib.error
import urllib.request

from brain.brain_provider import (
    BrainProvider,
    BrainCapabilities,
    BrainResult
)


# =========================================================
# LLAMA.CPP PROVIDER
#
# Habla con llama-server (llama.cpp) mediante su API
# compatible con OpenAI: /v1/chat/completions.
#
# El servidor corre en la PC con la RX 580 (Vulkan) y
# se arranca con --jinja para que las tools usen la
# plantilla de chat del modelo.
#
# El resto de Mochi habla "formato Ollama", así que este
# provider traduce en ambos sentidos:
#
# - opciones: think / num_predict / num_ctx
# - mensajes: tool_calls con arguments en dict,
#   mensajes "tool" con tool_name
# =========================================================

class LlamaCppProvider(
    BrainProvider
):

    def __init__(
        self,
        model_name=None,
        base_url="http://localhost:8080",
        allow_thinking=False,
        api_key=None
    ):

        super().__init__(
            model_name=model_name
        )

        self.base_url = (
            base_url.rstrip("/")
        )

        # Mochi usa el modelo SIN razonamiento.
        #
        # Aunque BrainRouter pida think=True en
        # modo deep, acá se fuerza a False salvo
        # que la configuración lo permita.
        self.allow_thinking = bool(
            allow_thinking
        )

        self.api_key = (
            api_key
        )

        # llama-server reutiliza la caché del prompt solo si el
        # comienzo es idéntico. Sin razonamiento, "deep" no aporta
        # nada: BrainRouter mantiene la misma lista de tools que en
        # "fast" para no invalidar la caché (~80 s en la RX 580).
        self.prefers_stable_tools = (
            not self.allow_thinking
        )


    # =====================================================
    # NOMBRE DEL PROVIDER
    # =====================================================

    @property
    def provider_name(
        self
    ):

        return "llama_cpp"


    # =====================================================
    # CAPACIDADES
    # =====================================================

    @property
    def capabilities(
        self
    ):

        return BrainCapabilities(

            tools=True,

            vision=False,

            streaming=False,

            conversations=True
        )


    # =====================================================
    # REQUEST HTTP
    # =====================================================

    def _request(
        self,
        method,
        endpoint,
        payload=None,
        timeout=30
    ):

        url = (
            self.base_url
            + endpoint
        )


        data = None


        if payload is not None:

            data = json.dumps(
                payload
            ).encode(
                "utf-8"
            )


        headers = {
            "Content-Type":
                "application/json"
        }


        if self.api_key:

            headers[
                "Authorization"
            ] = f"Bearer {self.api_key}"


        request = urllib.request.Request(

            url=url,

            data=data,

            method=method,

            headers=headers
        )


        try:

            with urllib.request.urlopen(
                request,
                timeout=timeout
            ) as response:

                body = (
                    response
                    .read()
                    .decode(
                        "utf-8"
                    )
                )


                if not body:

                    return {}


                return json.loads(
                    body
                )


        except urllib.error.HTTPError as e:

            detail = ""

            try:

                detail = (
                    e.read()
                    .decode(
                        "utf-8",
                        "replace"
                    )
                )

            except Exception:

                pass


            raise RuntimeError(
                f"llama-server respondió {e.code}: {detail[:500]}"
            )


        except urllib.error.URLError as e:

            raise RuntimeError(
                f"No pude conectar con llama-server en {self.base_url}: {e}"
            )


        except Exception as e:

            raise RuntimeError(
                f"Error comunicando con llama-server: {e}"
            )


    # =====================================================
    # COMPROBAR DISPONIBILIDAD
    #
    # /health devuelve 503 mientras carga el modelo.
    # =====================================================

    def is_available(
        self
    ):

        try:

            response = (
                self._request(
                    method="GET",
                    endpoint="/health",
                    timeout=3
                )
            )

            return (
                response.get(
                    "status"
                )
                == "ok"
            )


        except Exception:

            return False


    # =====================================================
    # LISTAR MODELOS
    #
    # llama-server sirve un único modelo, el que se
    # le pasó con -m / --alias.
    # =====================================================

    def list_models(
        self
    ):

        try:

            response = (
                self._request(
                    method="GET",
                    endpoint="/v1/models",
                    timeout=5
                )
            )


            return [

                {
                    "name":
                        model.get(
                            "id"
                        ),

                    "model":
                        model.get(
                            "id"
                        )
                }

                for model in (
                    response.get(
                        "data",
                        []
                    )
                    or []
                )
            ]


        except Exception:

            return []


    def model_exists(
        self
    ):

        # El nombre configurado es solo informativo:
        # llama-server responde con el modelo que tenga
        # cargado, se llame como se llame.
        return bool(
            self.list_models()
        )


    # =====================================================
    # CARGAR / DESCARGAR
    #
    # llama-server carga el modelo al arrancar y lo
    # mantiene en VRAM. Se administra desde la PC
    # (systemd), no desde Mochi.
    # =====================================================

    def load(
        self
    ):

        self.loaded = (
            self.is_available()
        )

        return self.loaded


    def unload(
        self
    ):

        return False


    # =====================================================
    # MENSAJES: FORMATO OLLAMA -> OPENAI
    # =====================================================

    def _to_openai_messages(
        self,
        messages
    ):

        converted = []

        pending_ids = []

        call_counter = 0


        for message in (
            messages
            or []
        ):

            if not isinstance(
                message,
                dict
            ):

                continue


            role = (
                message.get(
                    "role"
                )
            )


            # =============================================
            # ASSISTANT CON TOOL CALLS
            # =============================================

            if (
                role == "assistant"
                and message.get(
                    "tool_calls"
                )
            ):

                tool_calls = []

                pending_ids = []


                for call in message[
                    "tool_calls"
                ]:

                    function = (
                        call.get(
                            "function",
                            {}
                        )
                        or {}
                    )


                    arguments = (
                        function.get(
                            "arguments",
                            {}
                        )
                    )


                    if not isinstance(
                        arguments,
                        str
                    ):

                        arguments = json.dumps(
                            arguments or {},
                            ensure_ascii=False
                        )


                    call_counter += 1

                    call_id = (
                        call.get(
                            "id"
                        )
                        or f"call_{call_counter}"
                    )


                    pending_ids.append(
                        call_id
                    )


                    tool_calls.append({

                        "id":
                            call_id,

                        "type":
                            "function",

                        "function": {

                            "name":
                                function.get(
                                    "name"
                                ),

                            "arguments":
                                arguments
                        }
                    })


                converted.append({

                    "role":
                        "assistant",

                    "content":
                        message.get(
                            "content"
                        )
                        or "",

                    "tool_calls":
                        tool_calls
                })

                continue


            # =============================================
            # RESULTADO DE UNA TOOL
            # =============================================

            if role == "tool":

                tool_message = {

                    "role":
                        "tool",

                    "content":
                        str(
                            message.get(
                                "content"
                            )
                            or ""
                        )
                }


                call_id = (
                    message.get(
                        "tool_call_id"
                    )
                    or (
                        pending_ids.pop(0)
                        if pending_ids
                        else None
                    )
                )


                if call_id:

                    tool_message[
                        "tool_call_id"
                    ] = call_id


                if message.get(
                    "tool_name"
                ):

                    tool_message[
                        "name"
                    ] = message[
                        "tool_name"
                    ]


                converted.append(
                    tool_message
                )

                continue


            converted.append({

                "role":
                    role,

                "content":
                    message.get(
                        "content"
                    )
                    or ""
            })


        return converted


    # =====================================================
    # TOOL CALLS: FORMATO OPENAI -> OLLAMA
    # =====================================================

    def _to_ollama_tool_calls(
        self,
        tool_calls
    ):

        converted = []


        for call in (
            tool_calls
            or []
        ):

            function = (
                call.get(
                    "function",
                    {}
                )
                or {}
            )


            arguments = (
                function.get(
                    "arguments",
                    {}
                )
            )


            if isinstance(
                arguments,
                str
            ):

                try:

                    arguments = json.loads(
                        arguments
                    ) if arguments.strip() else {}

                except Exception:

                    # Se deja como texto: extract_tool_call
                    # ya sabe manejar ese caso.
                    pass


            converted.append({

                "id":
                    call.get(
                        "id"
                    ),

                "function": {

                    "name":
                        function.get(
                            "name"
                        ),

                    "arguments":
                        arguments
                }
            })


        return converted


    # =====================================================
    # LIMPIAR CONTENIDO DEL MODELO
    # =====================================================

    def _clean_model_content(
        self,
        content
    ):

        if not content:

            return ""


        content = str(
            content
        )


        if "</think>" in content:

            content = (
                content
                .split(
                    "</think>",
                    1
                )[1]
            )


        return (
            content
            .strip()
        )


    # =====================================================
    # GENERAR RESPUESTA
    # =====================================================

    def generate(
        self,
        messages,
        tools=None,
        options=None
    ):

        request_options = dict(
            options or {}
        )


        # =================================================
        # THINKING
        #
        # Qwen3.5 lo controla con la variable de la
        # plantilla "enable_thinking".
        # =================================================

        think = (
            request_options.pop(
                "think",
                None
            )
        )


        enable_thinking = (
            bool(think)
            and self.allow_thinking
        )


        payload = {

            "messages":
                self._to_openai_messages(
                    messages
                ),

            "stream":
                False,

            "chat_template_kwargs": {
                "enable_thinking":
                    enable_thinking
            }
        }


        if self.model_name:

            payload[
                "model"
            ] = self.model_name


        if tools:

            payload[
                "tools"
            ] = tools


        # =================================================
        # OPCIONES (nombres Ollama -> OpenAI)
        #
        # num_ctx se fija al arrancar llama-server
        # (-c), así que se ignora.
        # =================================================

        renamed = {
            "num_predict":
                "max_tokens",
        }

        ignored = {
            "num_ctx",
            "keep_alive",
        }


        for key, value in (
            request_options.items()
        ):

            if key in ignored:

                continue


            payload[
                renamed.get(
                    key,
                    key
                )
            ] = value


        try:

            response = (
                self._request(

                    method="POST",

                    endpoint="/v1/chat/completions",

                    payload=payload,

                    timeout=180
                )
            )


            choices = (
                response.get(
                    "choices"
                )
                or [{}]
            )


            message = (
                choices[0].get(
                    "message",
                    {}
                )
                or {}
            )


            content = (
                self._clean_model_content(
                    message.get(
                        "content",
                        ""
                    )
                )
            )


            tool_calls = (
                self._to_ollama_tool_calls(
                    message.get(
                        "tool_calls"
                    )
                )
            )


            self.loaded = True


            return BrainResult(

                success=True,

                text=content,

                raw=response,

                tool_calls=tool_calls
            )


        except Exception as e:

            return BrainResult(

                success=False,

                error=str(e)
            )


# =========================================================
# PRUEBA DIRECTA
#
# python -m brain.llama_cpp_provider http://IP_PC:8080
# =========================================================

if __name__ == "__main__":

    import sys

    from config_manager import get_config


    # La clave sale del config.json de Mochi (sección brain).
    brain_config = (
        get_config()
        .get("brain", {})
    )

    provider = LlamaCppProvider(
        base_url=(
            sys.argv[1]
            if len(sys.argv) > 1
            else brain_config.get("base_url") or "http://localhost:8080"
        ),
        api_key=brain_config.get("api_key")
    )


    print("")
    print(
        "======================================"
    )
    print(
        "      LLAMA.CPP PROVIDER TEST"
    )
    print(
        "======================================"
    )


    print(
        f"Servidor: {provider.base_url}"
    )

    print(
        f"Disponible: "
        f"{provider.is_available()}"
    )

    print(
        f"Modelos: "
        f"{[m['name'] for m in provider.list_models()]}"
    )


    result = provider.generate(

        messages=[
            {
                "role": "user",
                "content": "Respondé solo: hola, soy Mochi."
            }
        ],

        options={
            "temperature": 0.1,
            "think": False,
            "num_predict": 64
        }
    )


    print(
        f"Respuesta: {result.text or result.error}"
    )

    print(
        "======================================"
    )
