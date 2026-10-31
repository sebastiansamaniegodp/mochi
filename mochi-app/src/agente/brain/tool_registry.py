import json
import inspect

import cancellation


# =========================================================
# TOOL REGISTRY
# =========================================================

class ToolRegistry:

    def __init__(self):

        self._tools = {}


    # =====================================================
    # REGISTRAR HERRAMIENTA
    # =====================================================

    def register(
        self,
        name,
        description,
        parameters,
        handler
    ):

        if not name:

            raise ValueError(
                "La herramienta necesita un nombre."
            )


        if not callable(
            handler
        ):

            raise ValueError(
                f"El handler de '{name}' "
                f"no es ejecutable."
            )


        self._tools[name] = {

            "name":
                name,

            "description":
                description,

            "parameters":
                parameters,

            "handler":
                handler
        }


    # =====================================================
    # EXISTE
    # =====================================================

    def has_tool(
        self,
        name
    ):

        return (
            name in self._tools
        )


    # =====================================================
    # OBTENER NOMBRES
    # =====================================================

    def get_tool_names(
        self
    ):

        return list(
            self._tools.keys()
        )


    # =====================================================
    # FORMATO PARA EL MODELO
    #
    # Ollama espera:
    #
    # {
    #   "type": "function",
    #   "function": {
    #       "name": "...",
    #       "description": "...",
    #       "parameters": {...}
    #   }
    # }
    # =====================================================

    def get_tool_schemas(
        self
    ):

        schemas = []


        for tool in (
            self._tools.values()
        ):

            schemas.append({

                "type":
                    "function",

                "function": {

                    "name":
                        tool["name"],

                    "description":
                        tool["description"],

                    "parameters":
                        tool["parameters"]
                }
            })


        return schemas


    # =====================================================
    # EJECUTAR HERRAMIENTA
    # =====================================================

    @staticmethod
    def _matches_json_type(value, expected):
        if expected == "string":
            return isinstance(value, str)
        if expected == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected == "boolean":
            return isinstance(value, bool)
        if expected == "array":
            return isinstance(value, list)
        if expected == "object":
            return isinstance(value, dict)
        return True

    @classmethod
    def _validate_arguments(cls, schema, arguments, handler=None):
        """Validate the small JSON-schema subset used by Mochi tools."""

        if not isinstance(arguments, dict):
            return ["Los argumentos deben ser un objeto JSON."]

        properties = (schema or {}).get("properties") or {}
        required = (schema or {}).get("required") or []
        errors = []

        for name in required:
            if name not in arguments:
                errors.append(f"Falta el argumento obligatorio '{name}'.")

        accepts_arbitrary_keywords = False
        if handler is not None:
            try:
                accepts_arbitrary_keywords = any(
                    parameter.kind == inspect.Parameter.VAR_KEYWORD
                    for parameter in inspect.signature(handler).parameters.values()
                )
            except (TypeError, ValueError):
                pass

        for name in arguments:
            if name not in properties and not accepts_arbitrary_keywords:
                errors.append(f"El argumento '{name}' no existe para esta herramienta.")

        for name, value in arguments.items():
            definition = properties.get(name)
            if not definition:
                continue
            expected = definition.get("type")
            if expected and not cls._matches_json_type(value, expected):
                errors.append(f"El argumento '{name}' debe ser de tipo {expected}.")
                continue
            choices = definition.get("enum")
            if choices is not None and value not in choices:
                errors.append(
                    f"El argumento '{name}' debe ser uno de: "
                    + ", ".join(str(choice) for choice in choices)
                    + "."
                )
            if expected == "array" and isinstance(value, list):
                item_type = (definition.get("items") or {}).get("type")
                if item_type and any(
                    not cls._matches_json_type(item, item_type)
                    for item in value
                ):
                    errors.append(
                        f"Cada elemento de '{name}' debe ser de tipo {item_type}."
                    )

        return errors

    def execute(
        self,
        name,
        arguments=None
    ):

        if arguments is None:

            arguments = {}


        tool = self._tools.get(
            name
        )


        if tool is None:

            return {

                "ok":
                    False,

                "error":
                    "tool_not_found",

                "message":
                    (
                        f"Mochi no conoce la herramienta "
                        f"'{name}'."
                    )
            }

        validation_errors = self._validate_arguments(
            tool.get("parameters") or {},
            arguments,
            tool.get("handler"),
        )
        if validation_errors:
            return {
                "ok": False,
                "error": "invalid_arguments",
                "message": " ".join(validation_errors),
                "details": validation_errors,
            }


        # Si se canceló la orden, no arranca otra herramienta.
        cancellation.check()


        try:

            result = (
                tool["handler"](
                    **arguments
                )
            )


            # =============================================
            # Nuestro código actual suele devolver:
            #
            # (True, "Aplicación abierta")
            # =============================================

            if (
                isinstance(
                    result,
                    tuple
                )
                and
                len(result) == 2
            ):

                return {

                    "ok":
                        bool(result[0]),

                    "message":
                        str(result[1])
                }


            # =============================================
            # Si ya devuelve diccionario,
            # conservarlo.
            # =============================================

            if isinstance(
                result,
                dict
            ):

                return result


            # =============================================
            # Otros resultados
            # =============================================

            return {

                "ok":
                    True,

                "result":
                    result
            }


        except Exception as e:

            print("")
            print(
                f"ERROR TOOL '{name}':"
            )

            print(
                repr(e)
            )


            return {

                "ok":
                    False,

                "error":
                    "tool_execution_error",

                "message":
                    str(e)
            }


    # =====================================================
    # EJECUTAR Y DEVOLVER JSON
    # =====================================================

    def execute_json(
        self,
        name,
        arguments=None
    ):

        result = (
            self.execute(
                name=name,
                arguments=arguments
            )
        )


        return json.dumps(
            result,
            ensure_ascii=False,
            default=str
        )


    # =====================================================
    # MOSTRAR TOOLS
    # =====================================================

    def print_tools(
        self
    ):

        print("")
        print(
            "======================================"
        )
        print(
            "          MOCHI TOOL REGISTRY"
        )
        print(
            "======================================"
        )


        if not self._tools:

            print(
                "No hay herramientas registradas."
            )


        else:

            for name, tool in (
                self._tools.items()
            ):

                print("")
                print(
                    f"Tool: {name}"
                )

                print(
                    f"  {tool['description']}"
                )


        print("")
        print(
            f"Total: {len(self._tools)}"
        )

        print(
            "======================================"
        )


# =========================================================
# INSTANCIA GLOBAL
# =========================================================

tool_registry = (
    ToolRegistry()
)


# =========================================================
# PRUEBA
# =========================================================

if __name__ == "__main__":

    # -----------------------------------------------------
    # Tool temporal solamente para comprobar
    # que el Registry funciona.
    # -----------------------------------------------------

    def test_tool(
        text
    ):

        return {
            "ok": True,
            "received": text
        }


    tool_registry.register(

        name="test_echo",

        description=(
            "Herramienta temporal para probar "
            "el sistema de tools de Mochi."
        ),

        parameters={

            "type":
                "object",

            "properties": {

                "text": {
                    "type":
                        "string"
                }
            },

            "required": [
                "text"
            ]
        },

        handler=test_tool
    )


    tool_registry.print_tools()


    print("")
    print(
        "SCHEMA:"
    )

    print(
        json.dumps(
            tool_registry.get_tool_schemas(),
            indent=2,
            ensure_ascii=False
        )
    )


    print("")
    print(
        "PRUEBA DE EJECUCIÓN:"
    )

    print(
        tool_registry.execute(
            "test_echo",
            {
                "text":
                    "Mochi Tool Registry listo"
            }
        )
    )
