import json
import urllib.error
import urllib.request

from brain.brain_provider import (
    BrainProvider,
    BrainCapabilities,
    BrainResult
)


class OllamaProvider(
    BrainProvider
):

    def __init__(
        self,
        model_name=None,
        base_url="http://localhost:11434"
    ):

        super().__init__(
            model_name=model_name
        )

        self.base_url = (
            base_url.rstrip("/")
        )


    # =====================================================
    # NOMBRE DEL PROVIDER
    # =====================================================

    @property
    def provider_name(
        self
    ):

        return "ollama"


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


        request = urllib.request.Request(

            url=url,

            data=data,

            method=method,

            headers={
                "Content-Type":
                    "application/json"
            }
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


        except urllib.error.URLError as e:

            raise RuntimeError(
                f"No pude conectar con Ollama en {self.base_url}: {e}"
            )


        except Exception as e:

            raise RuntimeError(
                f"Error comunicando con Ollama: {e}"
            )


    # =====================================================
    # COMPROBAR DISPONIBILIDAD
    # =====================================================

    def is_available(
        self
    ):

        try:

            self._request(
                method="GET",
                endpoint="/api/tags",
                timeout=3
            )

            return True


        except Exception:

            return False


    # =====================================================
    # LISTAR MODELOS INSTALADOS
    # =====================================================

    def list_models(
        self
    ):

        try:

            response = (
                self._request(
                    method="GET",
                    endpoint="/api/tags",
                    timeout=5
                )
            )


            models = (
                response.get(
                    "models",
                    []
                )
                or []
            )


            return [

                {
                    "name":
                        model.get(
                            "name"
                        ),

                    "model":
                        model.get(
                            "model"
                        ),

                    "size":
                        model.get(
                            "size"
                        ),

                    "details":
                        model.get(
                            "details",
                            {}
                        )
                }

                for model in models
            ]


        except Exception:

            return []


    # =====================================================
    # COMPROBAR SI EL MODELO EXISTE
    # =====================================================

    def model_exists(
        self
    ):

        if not self.model_name:

            return False


        wanted = (
            self.model_name.lower()
        )


        for model in self.list_models():

            name = str(
                model.get(
                    "name",
                    ""
                )
            ).lower()


            model_id = str(
                model.get(
                    "model",
                    ""
                )
            ).lower()


            if (
                name == wanted
                or
                model_id == wanted
            ):

                return True


        return False


    # =====================================================
    # CARGAR MODELO
    # =====================================================

    def load(
        self
    ):

        if not self.model_name:

            return False


        if not self.is_available():

            return False


        if not self.model_exists():

            return False


        try:

            self._request(

                method="POST",

                endpoint="/api/generate",

                payload={

                    "model":
                        self.model_name,

                    "prompt":
                        "",

                    "stream":
                        False,

                    "keep_alive":
                        "5m"
                },

                timeout=120
            )


            self.loaded = True

            return True


        except Exception as e:

            print(
                "Error cargando modelo:"
            )

            print(
                repr(e)
            )

            return False


    # =====================================================
    # DESCARGAR MODELO DE RAM / VRAM
    # =====================================================

    def unload(
        self
    ):

        if not self.model_name:

            return False


        if not self.is_available():

            return False


        try:

            self._request(

                method="POST",

                endpoint="/api/generate",

                payload={

                    "model":
                        self.model_name,

                    "prompt":
                        "",

                    "stream":
                        False,

                    "keep_alive":
                        0
                },

                timeout=60
            )


            self.loaded = False

            return True


        except Exception as e:

            print(
                "Error descargando modelo:"
            )

            print(
                repr(e)
            )

            return False


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


        # Algunos modelos Qwen pueden devolver
        # razonamiento antes de </think>.
        #
        # Nunca queremos mostrar eso al usuario.

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

        if not self.model_name:

            return BrainResult(

                success=False,

                error=(
                    "No hay modelo configurado."
                )
            )


        if not self.is_available():

            return BrainResult(

                success=False,

                error=(
                    "Ollama no está disponible."
                )
            )


         # =================================================
        # OPCIONES DE LA PETICIÓN
        # =================================================

        request_options = dict(
            options or {}
        )


        # =================================================
        # THINKING
        #
        # No se mete dentro de "options".
        # Ollama lo recibe como propiedad principal.
        #
        # None:
        #   dejar que el modelo/runtime decida.
        #
        # True:
        #   razonamiento activado.
        #
        # False:
        #   respuesta directa.
        # =================================================

        think = (
            request_options.pop(
                "think",
                None
            )
        )


        payload = {

            "model":
                self.model_name,

            "messages":
                messages,

            "stream":
                False,

            "keep_alive":
                "5m"
        }


        if think is not None:

            payload[
                "think"
            ] = bool(
                think
            )


        # =================================================
        # TOOLS
        # =================================================

        if tools:

            payload[
                "tools"
            ] = tools


        # =================================================
        # OPCIONES DEL MODELO
        #
        # temperature
        # num_ctx
        # num_predict
        # etc.
        # =================================================

        if request_options:

            payload[
                "options"
            ] = request_options


        try:

            response = (
                self._request(

                    method="POST",

                    endpoint="/api/chat",

                    payload=payload,

                    timeout=180
                )
            )


            message = (
                response.get(
                    "message",
                    {}
                )
                or {}
            )


            # =============================================
            # TEXTO DEVUELTO POR EL MODELO
            # =============================================

            content = (
                message.get(
                    "content",
                    ""
                )
                or ""
            )


            # =============================================
            # LIMPIAR RAZONAMIENTO INTERNO
            # =============================================

            content = (
                self._clean_model_content(
                    content
                )
            )


            # =============================================
            # TOOL CALLS
            # =============================================

            tool_calls = (
                message.get(
                    "tool_calls",
                    []
                )
                or []
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
# =========================================================

if __name__ == "__main__":

    provider = OllamaProvider()


    print("")
    print(
        "======================================"
    )
    print(
        "        OLLAMA PROVIDER TEST"
    )
    print(
        "======================================"
    )


    print(
        f"Disponible: "
        f"{provider.is_available()}"
    )


    models = (
        provider.list_models()
    )


    print("")
    print(
        "Modelos instalados:"
    )


    if not models:

        print(
            "  Ninguno detectado."
        )


    else:

        for model in models:

            print(
                f"  - {model['name']}"
            )


    print(
        "======================================"
    )