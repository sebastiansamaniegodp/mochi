import json

from http.server import (
    BaseHTTPRequestHandler,
    ThreadingHTTPServer,
)

from urllib.parse import (
    urlparse,
    parse_qs,
)

from config_manager import print_configuration

from mochi_command_service import (
    command_service,
)


PORT = 8765


class MochiHTTPServer(ThreadingHTTPServer):
    """Keep health checks responsive while one serialized command runs."""

    daemon_threads = True
    allow_reuse_address = True


class MochiHandler(
    BaseHTTPRequestHandler
):

    # =====================================================
    # RESPUESTA TEXTO
    # =====================================================

    def send_text(
        self,
        code,
        text,
    ):

        self.send_response(
            code
        )

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8",
        )

        self.send_header(
            "Access-Control-Allow-Origin",
            "*",
        )

        self.end_headers()

        self.wfile.write(
            str(text).encode(
                "utf-8"
            )
        )


    # =====================================================
    # RESPUESTA JSON
    # =====================================================

    def send_json(
        self,
        code,
        data,
    ):

        payload = json.dumps(
            data,
            ensure_ascii=False,
            default=str,
        ).encode(
            "utf-8"
        )

        self.send_response(
            code
        )

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )

        self.send_header(
            "Content-Length",
            str(len(payload)),
        )

        self.send_header(
            "Access-Control-Allow-Origin",
            "*",
        )

        self.end_headers()

        self.wfile.write(
            payload
        )


    # =====================================================
    # GET
    # =====================================================

    def do_GET(
        self
    ):

        parsed = urlparse(
            self.path
        )

        # =============================================
        # PING
        #
        # Lo conservamos en texto para no romper
        # el cliente Android actual.
        # =============================================

        if parsed.path == "/ping":

            self.send_text(
                200,
                "Mochi Desktop conectado",
            )

            return


        # =============================================
        # COMMAND LEGACY
        #
        # GET /command?q=abre+chrome
        #
        # Sigue funcionando para Android actual,
        # pero YA NO usa commands.py.
        # =============================================

        if parsed.path == "/command":

            params = parse_qs(
                parsed.query
            )

            command = params.get(
                "q",
                [""],
            )[0].strip()


            debug_value = (
                params.get(
                    "debug",
                    ["0"],
                )[0]
            )

            debug = (
                str(debug_value).lower()
                in {
                    "1",
                    "true",
                    "yes",
                    "si",
                    "sí",
                }
            )


            if not command:

                self.send_json(
                    400,
                    {
                        "ok": False,
                        "status": "invalid_request",
                        "text": "No recibí ninguna orden.",
                        "error": "empty_command",
                    },
                )

                return


            result = (
                command_service
                .execute(
                    text=command,
                    source="http_get",
                    debug=debug,
                    verbose=True,
                )
            )


            if result.get(
                "internal_error"
            ):

                self.send_json(
                    500,
                    result,
                )

            else:

                # Una orden válida llegó y fue procesada.
                #
                # partial / blocked / failed son resultados
                # del objetivo, NO errores de transporte HTTP.
                self.send_json(
                    200,
                    result,
                )


            return


        self.send_json(
            404,
            {
                "ok": False,
                "status": "not_found",
                "text": "Ruta desconocida.",
                "error": "route_not_found",
            },
        )


    # =====================================================
    # POST
    # =====================================================

    def do_POST(
        self
    ):

        parsed = urlparse(
            self.path
        )


        if parsed.path != "/command":

            self.send_json(
                404,
                {
                    "ok": False,
                    "status": "not_found",
                    "text": "Ruta desconocida.",
                    "error": "route_not_found",
                },
            )

            return


        # =============================================
        # CONTENT LENGTH
        # =============================================

        content_length = (
            self.headers.get(
                "Content-Length"
            )
        )


        if content_length is None:

            self.send_json(
                400,
                {
                    "ok": False,
                    "status": "invalid_request",
                    "text": "La petición no contiene datos.",
                    "error": "missing_content_length",
                },
            )

            return


        try:

            content_length = int(
                content_length
            )

        except ValueError:

            self.send_json(
                400,
                {
                    "ok": False,
                    "status": "invalid_request",
                    "text": "Content-Length no es válido.",
                    "error": "invalid_content_length",
                },
            )

            return


        if content_length <= 0:

            self.send_json(
                400,
                {
                    "ok": False,
                    "status": "invalid_request",
                    "text": "La petición está vacía.",
                    "error": "empty_body",
                },
            )

            return


        # Límite razonable para una orden de voz/texto.
        if content_length > 64 * 1024:

            self.send_json(
                413,
                {
                    "ok": False,
                    "status": "invalid_request",
                    "text": "La petición es demasiado grande.",
                    "error": "request_too_large",
                },
            )

            return


        raw_body = self.rfile.read(
            content_length
        )


        # =============================================
        # JSON
        # =============================================

        try:

            body = json.loads(
                raw_body.decode(
                    "utf-8"
                )
            )

        except Exception:

            self.send_json(
                400,
                {
                    "ok": False,
                    "status": "invalid_request",
                    "text": "El JSON recibido no es válido.",
                    "error": "invalid_json",
                },
            )

            return


        if not isinstance(
            body,
            dict,
        ):

            self.send_json(
                400,
                {
                    "ok": False,
                    "status": "invalid_request",
                    "text": "El cuerpo debe ser un objeto JSON.",
                    "error": "invalid_body",
                },
            )

            return


        # Campo oficial nuevo.
        command = str(
            body.get(
                "text",
                "",
            )
            or ""
        ).strip()


        if not command:

            self.send_json(
                400,
                {
                    "ok": False,
                    "status": "invalid_request",
                    "text": "No recibí ninguna orden.",
                    "error": "empty_command",
                },
            )

            return


        source = str(
            body.get(
                "source",
                "http_post",
            )
            or "http_post"
        ).strip()


        debug = bool(
            body.get(
                "debug",
                False,
            )
        )


        result = (
            command_service
            .execute(
                text=command,
                source=source,
                debug=debug,
                verbose=True,
            )
        )


        if result.get(
            "internal_error"
        ):

            self.send_json(
                500,
                result,
            )

        else:

            self.send_json(
                200,
                result,
            )


    # =====================================================
    # LOG HTTP
    # =====================================================

    def log_message(
        self,
        format,
        *args,
    ):

        # Evita el log HTTP estándar porque Mochi
        # ya muestra sus propios diagnósticos.
        return


def create_server(host="0.0.0.0", port=PORT):
    """Create, but do not start, the HTTP bridge."""

    return MochiHTTPServer((host, port), MochiHandler)


def run_server(host="0.0.0.0", port=PORT):
    """Run the mobile/voice bridge until it is interrupted."""

    print_configuration()
    server = create_server(host, port)
    print("")
    print("================================")
    print("      MOCHI DESKTOP AGENT")
    print("================================")
    print("")
    print(f"Puerto: {server.server_address[1]}")
    print("Motor de comandos: Agent Loop")
    print("Esperando órdenes...")
    print("")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("")
        print("Cerrando Mochi...")
    finally:
        server.server_close()
        print("Mochi Desktop detenido.")


if __name__ == "__main__":
    run_server()
