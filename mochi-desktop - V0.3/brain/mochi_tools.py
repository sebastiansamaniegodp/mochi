import json

from brain.tool_registry import (
    ToolRegistry
)

from brain.brain_router import (
    brain_router
)

from media_controller import (
    media_controller
)

from application_registry import (
    application_registry
)

from file_manager import (
    file_manager,
    normalize_file_name,
)

from game_registry import (
    game_registry,
)

from hardware_profiler import (
    hardware_profiler,
)

from desktop_tools import (
    desktop_tools,
)

from window_manager import (
    window_manager
)

from brain.browser_tools import (
    browser_open,
    browser_search,
    browser_profiles,
    web_inspect,
    web_act,
)
from brain.web_destinations import WebDestinationResolver


_destination_resolver = WebDestinationResolver()

# =========================================================
# TOOL: ESTABLECER VOLUMEN
# =========================================================

def set_system_volume(
    percent
):

    try:

        percent = int(
            round(
                float(percent)
            )
        )

    except Exception:

        return {
            "ok": False,
            "message":
                "El porcentaje de volumen no es válido."
        }


    percent = max(
        0,
        min(percent, 100)
    )


    success, message = (
        media_controller
        .set_volume(
            percent
        )
    )


    return {
        "ok":
            success,

        "message":
            message,

        "percent":
            percent
    }

# =========================================================
# TOOL: OBTENER VOLUMEN ACTUAL
# =========================================================

def get_system_volume():

    try:

        result = (
            media_controller
            .get_volume()
        )


        # =================================================
        # FORMATO TUPLA:
        # (True, "El volumen está al 45%")
        # =================================================

        if isinstance(
            result,
            tuple
        ):

            if len(result) < 2:

                return {
                    "ok": False,
                    "message":
                        "MediaController devolvió "
                        "un resultado incompleto."
                }


            success = bool(
                result[0]
            )


            value = (
                result[1]
            )


            if not success:

                return {
                    "ok": False,
                    "message":
                        str(value)
                }


        else:

            value = result


        # =================================================
        # SI YA ES NÚMERO
        # =================================================

        if isinstance(
            value,
            (int, float)
        ):

            percent = int(
                round(
                    float(value)
                )
            )


        # =================================================
        # SI VIENE COMO TEXTO:
        # "El volumen está al 45%"
        # =================================================

        else:

            text = str(
                value
            )


            digits = ""

            for char in text:

                if char.isdigit():

                    digits += char

                elif digits:

                    break


            if not digits:

                return {
                    "ok": False,
                    "message":
                        (
                            "No pude interpretar "
                            f"el volumen: {text}"
                        )
                }


            percent = int(
                digits
            )


        # =================================================
        # VALIDAR RANGO
        # =================================================

        percent = max(
            0,
            min(percent, 100)
        )


        return {
            "ok": True,
            "percent": percent,
            "message":
                f"Volumen actual: {percent}%"
        }


    except Exception as e:

        return {
            "ok": False,
            "message":
                f"No pude obtener el volumen: {e}"
        }

# =========================================================
# TOOL: CONTROL MULTIMEDIA GLOBAL
# =========================================================

def control_media(
    action,
    amount=None,
    expected_title=None,
):
    """Envía una orden a la sesión multimedia global de Windows.

    Las acciones de transporte usan las teclas multimedia de Windows. Por
    eso sirven para Netflix, YouTube, Twitch, Spotify u otro reproductor,
    pero Windows decide cuál es la sesión multimedia activa. La tecla
    play_pause no permite inferir el estado final del reproductor.
    """

    action = str(action or "").strip().lower()

    if action == "get_state":
        result = media_controller.get_media_session_state(expected_title)
        result.setdefault("action", action)
        return result

    if action in {"play", "pause"}:
        return media_controller.set_media_session_state(action, expected_title)

    if action in {"seek", "adjust_position"}:
        if amount is None:
            return {
                "ok": False,
                "error": "media_position_required",
                "message": "Falta indicar la posición o el desplazamiento en segundos.",
            }
        return media_controller.seek_media_session(
            amount,
            relative=action == "adjust_position",
            expected_title=expected_title,
        )

    if action in {"next", "previous"} and expected_title:
        return media_controller.change_media_session_item(action, expected_title)

    handlers = {
        "play_pause": media_controller.play_pause,
        "next": media_controller.next_track,
        "previous": media_controller.previous_track,
        "stop": media_controller.stop_media,
        "mute": media_controller.mute,
        "unmute": media_controller.unmute,
    }

    if action in {"volume_up", "volume_down"}:
        if amount is None:
            amount = 10

        try:
            amount = abs(float(amount))
        except (TypeError, ValueError):
            return {
                "ok": False,
                "message": "El ajuste de volumen debe ser un número positivo.",
            }

        if amount == 0:
            return {
                "ok": False,
                "message": "El ajuste de volumen debe ser mayor que cero.",
            }

        # change_volume aplica un delta real sobre el volumen maestro y no
        # depende de qué reproductor tenga el foco.
        delta = amount if action == "volume_up" else -amount
        success, message = media_controller.change_volume(delta)
        return {
            "ok": bool(success),
            "message": str(message),
            "action": action,
            "amount": amount,
            "scope": "system_volume",
            "state_confirmed": bool(success),
        }

    handler = handlers.get(action)
    if handler is None:
        return {
            "ok": False,
            "message": (
                "Acción multimedia no válida: "
                f"{action or '(vacía)'}."
            ),
        }

    success, message = handler()
    return {
        "ok": bool(success),
        "message": str(message),
        "action": action,
        "scope": "active_windows_media_session",
        # A media key reports that Windows accepted the signal, not the
        # resulting player state. In particular play_pause is a toggle.
        "state_confirmed": False,
    }


# =========================================================
# TOOLS: PORTAPAPELES, CAPTURA Y NOTIFICACIONES
# =========================================================

def read_clipboard():
    success, value = desktop_tools.read_clipboard()
    if not success:
        return {
            "ok": False,
            "message": str(value),
        }
    return {
        "ok": True,
        "text": str(value),
        "characters": len(str(value)),
        "message": "Contenido de texto obtenido del portapapeles.",
    }


def write_clipboard(text):
    success, message = desktop_tools.write_clipboard(text)
    return {
        "ok": bool(success),
        "message": str(message),
        "characters": len(str(text or "")),
    }


def capture_screenshot(name=None):
    success, value = desktop_tools.capture_screenshot(name)
    if not success:
        return {
            "ok": False,
            "message": str(value),
        }
    return {
        "ok": True,
        "path": str(value),
        "message": "Captura de pantalla guardada.",
    }


def show_notification(title, message):
    success, result_message = desktop_tools.show_notification(title, message)
    return {
        "ok": bool(success),
        "message": str(result_message),
        "state_confirmed": False,
    }


# =========================================================
# TOOLS: ARCHIVOS Y CARPETAS
# =========================================================

def _result_limit(value, default=5, maximum=20):
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def _extensions(value):
    """Accept the schema's list, while tolerating a comma-separated value."""

    if value is None:
        return None
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple)):
        return None
    result = [str(item).strip() for item in value if str(item).strip()]
    return result[:20] or None


def _file_result(item):
    return {
        "name": item["name"],
        "path": str(item["path"]),
        "score": round(float(item["score"]), 3),
        "kind": "folder" if item["is_directory"] else "file",
    }


def _search_user_items(query, extensions=None, want_directory=False, limit=5):
    query = str(query or "").strip()
    if not query:
        return None, {
            "ok": False,
            "message": "No se indicó qué buscar.",
        }

    results = file_manager.search(
        query=query,
        extensions=_extensions(extensions),
        want_directory=want_directory,
        max_results=_result_limit(limit),
    )
    return results, None


def list_user_folders():
    """List only known user folders; never enumerates their contents."""

    folders = {
        name: str(path)
        for name, path in file_manager.folders.items()
        if path
    }
    return {
        "ok": True,
        "folders": folders,
        "count": len(folders),
        "message": f"Encontré {len(folders)} carpetas conocidas del usuario.",
    }


def find_files(query, extensions=None, limit=5):
    results, error = _search_user_items(
        query,
        extensions=extensions,
        want_directory=False,
        limit=limit,
    )
    if error:
        return error
    return {
        "ok": True,
        "query": str(query).strip(),
        "count": len(results),
        "results": [_file_result(item) for item in results],
        "message": f"Encontré {len(results)} archivo(s).",
    }


def find_folders(query, limit=5):
    results, error = _search_user_items(
        query,
        want_directory=True,
        limit=limit,
    )
    if error:
        return error
    return {
        "ok": True,
        "query": str(query).strip(),
        "count": len(results),
        "results": [_file_result(item) for item in results],
        "message": f"Encontré {len(results)} carpeta(s).",
    }


def _open_unambiguous_user_item(query, extensions=None, want_directory=False):
    """Open only a high-confidence, clearly better local search result."""

    results, error = _search_user_items(
        query,
        extensions=extensions,
        want_directory=want_directory,
        limit=2,
    )
    if error:
        return error
    if not results:
        kind = "carpeta" if want_directory else "archivo"
        return {
            "ok": False,
            "error": "not_found",
            "message": f"No encontré ningún {kind} relacionado con {query}.",
        }

    best = results[0]
    runner_up = results[1] if len(results) > 1 else None
    clearly_better = (
        runner_up is None
        or best["score"] - runner_up["score"] >= 0.10
    )
    if best["score"] < 0.90 or not clearly_better:
        return {
            "ok": False,
            "error": "ambiguous_match",
            "message": (
                "No abrí nada porque la coincidencia no es suficientemente "
                "inequívoca. Indicá un nombre más preciso."
            ),
            "candidates": [_file_result(item) for item in results],
        }

    success, message = file_manager.open_path(best["path"])
    return {
        "ok": bool(success),
        "message": str(message),
        "item": _file_result(best),
    }


def open_known_folder(folder):
    folder = str(folder or "").strip()
    internal_name = file_manager.folder_aliases.get(normalize_file_name(folder))
    path = file_manager.folders.get(internal_name) if internal_name else None
    if not path:
        return {
            "ok": False,
            "error": "unknown_folder",
            "message": f"No conozco o no encontré la carpeta {folder or '(vacía)'}.",
        }

    success, message = file_manager.open_path(path)
    return {
        "ok": bool(success),
        "message": str(message),
        "folder": internal_name,
        "path": str(path),
    }


def open_file(query, extensions=None):
    return _open_unambiguous_user_item(
        query,
        extensions=extensions,
        want_directory=False,
    )


def open_folder(query):
    return _open_unambiguous_user_item(query, want_directory=True)


def open_latest_download():
    success, message = file_manager.open_latest_download()
    return {
        "ok": bool(success),
        "message": str(message),
    }


# =========================================================
# TOOLS: CATÁLOGOS Y SISTEMA
# =========================================================

def _filter_names(names, query, limit):
    query = str(query or "").strip()
    if query:
        normalized = normalize_file_name(query)
        names = [
            name for name in names
            if normalized in normalize_file_name(name)
        ]
    return names[:_result_limit(limit, default=25, maximum=100)]


def list_installed_applications(query=None, limit=25):
    names = _filter_names(application_registry.list_names(), query, limit)
    return {
        "ok": True,
        "count": len(names),
        "applications": names,
        "message": f"Encontré {len(names)} aplicación(es).",
    }


def list_games(query=None, limit=25):
    names = _filter_names(game_registry.list_names(), query, limit)
    return {
        "ok": True,
        "count": len(names),
        "games": names,
        "message": f"Encontré {len(names)} juego(s).",
    }


def launch_game(game):
    game = str(game or "").strip()
    if not game:
        return {
            "ok": False,
            "message": "No se indicó qué juego abrir.",
        }

    found = game_registry.find(game)
    if not found:
        return {
            "ok": False,
            "error": "game_not_found",
            "message": f"No encontré el juego {game}.",
        }

    success, message = game_registry.launch(game)
    return {
        "ok": bool(success),
        "message": str(message),
        "game": found.get("name"),
        "launcher": found.get("launcher"),
    }


def open_target(target):
    """Open one human-named destination without guessing its category.

    Known URLs/services stay on the web.  Other short names are checked
    against installed games first and then applications.  A web search is a
    transparent fallback, not evidence that the requested destination opened.
    """

    target = str(target or "").strip()
    if not target:
        return {"ok": False, "message": "No se indicó qué abrir."}

    destination = _destination_resolver.resolve(target)
    if destination.get("ok") and destination.get("kind") != "search":
        result = browser_open(target)
        result.update({
            "resolution": "web",
            "resolved_target": destination.get("service") or target,
            "goal_completed": bool(result.get("ok")),
        })
        return result

    # The game inventory is cached and therefore cheaper to check first.  It
    # also gives an installed playable title precedence over a launcher entry
    # with the same human-facing name.
    game = game_registry.find(target)
    if game:
        success, message = game_registry.launch(target)
        return {
            "ok": bool(success),
            "message": str(message),
            "resolution": "game",
            "game": game.get("name"),
            "launcher": game.get("launcher"),
            "resolved_target": game.get("name"),
            "goal_completed": bool(success),
        }

    app = application_registry.find(target)
    if app:
        success, message = application_registry.launch(target)
        return {
            "ok": bool(success),
            "message": str(message),
            "resolution": "application",
            "application": app.get("name"),
            "resolved_target": app.get("name"),
            "goal_completed": bool(success),
        }

    result = browser_open(target)
    result.update({
        "resolution": "web_search_fallback",
        "resolved_target": target,
        "goal_completed": False,
        "message": (
            f"No encontré una aplicación o juego local llamado {target}; "
            "realicé una búsqueda web para que puedas elegir el destino."
        ),
    })
    return result


def get_system_info():
    try:
        info = hardware_profiler.analyze()
        return {
            "ok": True,
            "system": info,
            "message": "Información del sistema obtenida.",
        }
    except Exception as error:
        return {
            "ok": False,
            "message": f"No pude analizar el sistema: {error}",
        }


# =========================================================
# TOOL: ABRIR APLICACIÓN
# =========================================================

def open_application(
    application
):

    try:

        application = str(
            application
        ).strip()


        if not application:

            return {
                "ok": False,
                "message":
                    "No se indicó qué aplicación abrir."
            }


        # Primero buscamos para poder devolver
        # información concreta al Agent Loop.

        app = (
            application_registry
            .find(
                application
            )
        )


        if app is None:

            return {
                "ok": False,
                "message":
                    (
                        f"No encontré una aplicación "
                        f"llamada {application}."
                    )
            }


        success, message = (
            application_registry
            .launch(
                application
            )
        )


        return {
            "ok":
                bool(success),

            "message":
                str(message),

            "requested":
                application,

            "application":
                app.get(
                    "name"
                ),

            "type":
                app.get(
                    "type"
                ),

            "source":
                app.get(
                    "source"
                )
        }


    except Exception as e:

        return {
            "ok": False,
            "message":
                f"No pude abrir la aplicación: {e}"
        }


# =========================================================
# TOOL: LISTAR VENTANAS ABIERTAS
# =========================================================

def list_open_windows():

    try:

        windows = (
            window_manager
            .get_windows()
        )


        clean_windows = []


        for window in windows:

            clean_windows.append({

                "title":
                    window.get(
                        "title"
                    ),

                "process":
                    window.get(
                        "process"
                    ),

                "pid":
                    window.get(
                        "pid"
                    )
            })


        return {
            "ok": True,
            "count": len(clean_windows),
            "windows": clean_windows,
            "message":
                (
                    f"Hay {len(clean_windows)} "
                    "ventanas abiertas."
                )
        }


    except Exception as e:

        return {
            "ok": False,
            "message":
                f"No pude consultar las ventanas: {e}"
        }

# =========================================================
# TOOL: CERRAR VENTANA
# =========================================================

def close_window(
    window
):

    try:

        window = str(
            window
        ).strip()


        if not window:

            return {
                "ok": False,
                "message":
                    "No se indicó qué ventana cerrar."
            }


        # Buscar primero para devolver información
        # concreta al Agent Loop.

        found = (
            window_manager
            .find(
                window
            )
        )


        if not found:

            return {
                "ok": False,
                "message":
                    window_manager.last_match_message(window)
            }


        success, message = (
            window_manager
            .close(
                window
            )
        )


        return {
            "ok":
                bool(success),

            "message":
                str(message),

            "requested":
                window,

            "title":
                found.get(
                    "title"
                ),

            "process":
                found.get(
                    "process"
                ),

            "pid":
                found.get(
                    "pid"
                )
        }


    except Exception as e:

        return {
            "ok": False,
            "message":
                f"No pude cerrar la ventana: {e}"
        }

# =========================================================
# TOOL: CERRAR TODAS LAS VENTANAS DE UNA APLICACIÓN
# =========================================================

def close_application_windows(
    application
):

    try:

        application = str(
            application
        ).strip()


        if not application:

            return {
                "ok": False,
                "message":
                    "No se indicó qué aplicación cerrar."
            }


        windows = (
            window_manager
            .find_all(
                application
            )
        )


        if not windows:

            return {
                "ok": False,
                "message":
                    (
                        f"No encontré ventanas abiertas "
                        f"de {application}."
                    )
            }


        matched_windows = [

            {
                "title":
                    window.get(
                        "title"
                    ),

                "process":
                    window.get(
                        "process"
                    ),

                "pid":
                    window.get(
                        "pid"
                    )
            }

            for window in windows
        ]


        success, message = (
            window_manager
            .close_all(
                application
            )
        )


        return {
            "ok":
                bool(success),

            "message":
                str(message),

            "application":
                application,

            "window_count":
                len(windows),

            "windows":
                matched_windows
        }


    except Exception as e:

        return {
            "ok": False,
            "message":
                (
                    "No pude cerrar las ventanas "
                    f"de la aplicación: {e}"
                )
        }

# =========================================================
# TOOL: CONTROLAR VENTANA
# =========================================================

def control_window(
    window,
    action
):

    try:

        window = str(
            window
        ).strip()


        action = str(
            action
        ).strip().lower()


        if not window:

            return {
                "ok": False,
                "message":
                    "No se indicó qué ventana controlar."
            }


        allowed_actions = {
            "focus",
            "minimize",
            "maximize"
        }


        if action not in allowed_actions:

            return {
                "ok": False,
                "message":
                    f"Acción de ventana no válida: {action}"
            }


        # =================================================
        # BUSCAR VENTANA
        # =================================================

        found = (
            window_manager
            .find(
                window
            )
        )


        if not found:

            return {
                "ok": False,
                "message":
                    window_manager.last_match_message(window)
            }


        # =================================================
        # EJECUTAR ACCIÓN
        # =================================================

        if action == "focus":

            success, message = (
                window_manager
                .focus(
                    window
                )
            )


        elif action == "minimize":

            success, message = (
                window_manager
                .minimize(
                    window
                )
            )


        else:

            success, message = (
                window_manager
                .maximize(
                    window
                )
            )


        return {
            "ok":
                bool(success),

            "message":
                str(message),

            "action":
                action,

            "requested":
                window,

            "title":
                found.get(
                    "title"
                ),

            "process":
                found.get(
                    "process"
                ),

            "pid":
                found.get(
                    "pid"
                )
        }


    except Exception as e:

        return {
            "ok": False,
            "message":
                f"No pude controlar la ventana: {e}"
        }

# =========================================================
# CREAR REGISTRY DE MOCHI
# =========================================================

def create_mochi_tool_registry():

    registry = (
        ToolRegistry()
    )


    # =====================================================
    # ESTABLECER VOLUMEN
    # =====================================================

    registry.register(

        name="set_system_volume",

        description=(
            "Establece el volumen maestro de Windows "
            "a un porcentaje exacto entre 0 y 100. "
            "Usar esta herramienta cuando el usuario "
            "quiera poner, dejar o ajustar el volumen, "
            "aunque exprese el nivel de manera aproximada "
            "como 'a la mitad', 'a un tercio' o "
            "'bastante bajo'."
        ),

        parameters={

            "type":
                "object",

            "properties": {

                "percent": {

                    "type":
                        "number",

                    "description":
                        (
                            "Porcentaje de volumen "
                            "entre 0 y 100."
                        )
                }
            },

            "required": [
                "percent"
            ]
        },

        handler=set_system_volume
    )


    # =====================================================
    # CONSULTAR VOLUMEN
    # =====================================================

    registry.register(

        name="get_system_volume",

        description=(
            "Obtiene el porcentaje actual del volumen "
            "maestro de Windows. "
            "Usar cuando sea necesario conocer el volumen "
            "actual antes de decidir qué acción realizar."
        ),

        parameters={

            "type":
                "object",

            "properties": {}
        },

        handler=get_system_volume
    )


    # =====================================================
    # CONTROL MULTIMEDIA GLOBAL
    # =====================================================

    registry.register(

        name="control_media",

        description=(
            "Controla la sesión multimedia activa de Windows de forma "
            "genérica, por ejemplo Netflix, YouTube, Twitch o un reproductor. "
            "get_state consulta su estado; play y pause solicitan un estado "
            "explícito y lo verifican. play_pause sólo envía una alternancia "
            "y no confirma el estado final. Para volumen "
            "relativo usar volume_up o volume_down con amount opcional en "
            "puntos porcentuales; para un porcentaje exacto usar "
            "set_system_volume."
        ),

        parameters={

            "type": "object",

            "properties": {

                "action": {

                    "type": "string",

                    "enum": [
                        "get_state",
                        "play",
                        "pause",
                        "seek",
                        "adjust_position",
                        "play_pause",
                        "next",
                        "previous",
                        "stop",
                        "volume_up",
                        "volume_down",
                        "mute",
                        "unmute"
                    ],

                    "description": (
                        "Acción sobre la sesión multimedia global. "
                        "Preferir play/pause sobre play_pause cuando se "
                        "necesite confirmar el estado final."
                    )
                },

                "amount": {

                    "type": "number",

                    "description": (
                        "Puntos porcentuales para volumen o segundos para "
                        "seek/adjust_position. Por defecto del volumen: 10."
                    )
                },

                "expected_title": {

                    "type": "string",

                    "description": (
                        "Título de la página activa usado para impedir que "
                        "Windows controle otra sesión multimedia."
                    )
                }
            },

            "required": [
                "action"
            ]
        },

        handler=control_media
    )


    # =====================================================
    # PORTAPAPELES, CAPTURA Y NOTIFICACIONES
    # =====================================================

    registry.register(
        name="read_clipboard",
        description="Lee texto del portapapeles de Windows. Usar solo cuando el usuario pidió explícitamente ver o conocer su contenido, porque puede contener información privada.",
        parameters={"type": "object", "properties": {}},
        handler=read_clipboard,
    )
    registry.register(
        name="write_clipboard",
        description="Copia texto al portapapeles de Windows. Usar solo cuando el usuario proporcionó o confirmó explícitamente el texto que quiere copiar.",
        parameters={"type": "object", "properties": {"text": {"type": "string", "description": "Texto exacto que se debe copiar."}}, "required": ["text"]},
        handler=write_clipboard,
    )
    registry.register(
        name="capture_screenshot",
        description="Captura todas las pantallas visibles de Windows y guarda un PNG local. Usar solo si el usuario pidió explícitamente una captura; no interpreta su contenido.",
        parameters={"type": "object", "properties": {"name": {"type": "string", "description": "Nombre opcional del archivo PNG, sin ruta."}}},
        handler=capture_screenshot,
    )
    registry.register(
        name="show_notification",
        description="Envía una notificación no bloqueante a Windows. Usar solo cuando el usuario pidió explícitamente mostrar un aviso; el envío no confirma que Windows la haya mostrado, porque puede estar silenciada.",
        parameters={"type": "object", "properties": {"title": {"type": "string", "description": "Título breve del aviso."}, "message": {"type": "string", "description": "Texto breve del aviso."}}, "required": ["title", "message"]},
        handler=show_notification,
    )


    # =====================================================
    # ARCHIVOS, CARPETAS, CATÁLOGOS Y SISTEMA
    # =====================================================

    registry.register(
        name="list_user_folders",
        description="Lista las carpetas conocidas del usuario (por ejemplo Descargas, Documentos y Escritorio) sin abrirlas ni revisar sus contenidos.",
        parameters={"type": "object", "properties": {}},
        handler=list_user_folders,
    )
    registry.register(
        name="find_files",
        description="Busca archivos en las carpetas personales del usuario y devuelve coincidencias con nombre, ruta y confianza. Usar antes de abrir un archivo cuando el nombre pueda ser ambiguo.",
        parameters={"type": "object", "properties": {"query": {"type": "string", "description": "Nombre o parte del nombre a buscar."}, "extensions": {"type": "array", "items": {"type": "string"}, "description": "Extensiones opcionales, por ejemplo ['.pdf', '.docx']."}, "limit": {"type": "integer", "description": "Máximo de resultados, entre 1 y 20; por defecto 5."}}, "required": ["query"]},
        handler=find_files,
    )
    registry.register(
        name="find_folders",
        description="Busca carpetas en las ubicaciones personales del usuario y devuelve coincidencias sin abrirlas.",
        parameters={"type": "object", "properties": {"query": {"type": "string", "description": "Nombre o parte del nombre de la carpeta."}, "limit": {"type": "integer", "description": "Máximo de resultados, entre 1 y 20; por defecto 5."}}, "required": ["query"]},
        handler=find_folders,
    )
    registry.register(
        name="open_known_folder",
        description="Abre una carpeta personal conocida del usuario, como descargas, documentos, escritorio, imágenes, música, videos u OneDrive.",
        parameters={"type": "object", "properties": {"folder": {"type": "string", "description": "Nombre de una carpeta conocida."}}, "required": ["folder"]},
        handler=open_known_folder,
    )
    registry.register(
        name="open_file",
        description="Abre un archivo personal solo cuando la búsqueda produce una coincidencia inequívoca. Si hay duda devuelve alternativas y no abre nada.",
        parameters={"type": "object", "properties": {"query": {"type": "string", "description": "Nombre o parte del nombre del archivo."}, "extensions": {"type": "array", "items": {"type": "string"}, "description": "Extensiones opcionales para acotar la búsqueda."}}, "required": ["query"]},
        handler=open_file,
    )
    registry.register(
        name="open_folder",
        description="Abre una carpeta personal solo cuando la búsqueda produce una coincidencia inequívoca. Si hay duda devuelve alternativas y no abre nada.",
        parameters={"type": "object", "properties": {"query": {"type": "string", "description": "Nombre o parte del nombre de la carpeta."}}, "required": ["query"]},
        handler=open_folder,
    )
    registry.register(
        name="open_latest_download",
        description="Abre el archivo más recientemente modificado dentro de Descargas. Usar únicamente cuando el usuario lo pidió de forma explícita.",
        parameters={"type": "object", "properties": {}},
        handler=open_latest_download,
    )
    registry.register(
        name="list_installed_applications",
        description="Lista aplicaciones instaladas detectadas en Windows. La detección se realiza solo al usar esta herramienta o al abrir una aplicación.",
        parameters={"type": "object", "properties": {"query": {"type": "string", "description": "Filtro opcional por nombre."}, "limit": {"type": "integer", "description": "Máximo de resultados, entre 1 y 100; por defecto 25."}}},
        handler=list_installed_applications,
    )
    registry.register(
        name="list_games",
        description="Lista juegos detectados en Steam y Epic. La detección se realiza solo al usar esta herramienta o al abrir un juego.",
        parameters={"type": "object", "properties": {"query": {"type": "string", "description": "Filtro opcional por nombre."}, "limit": {"type": "integer", "description": "Máximo de resultados, entre 1 y 100; por defecto 25."}}},
        handler=list_games,
    )
    registry.register(
        name="launch_game",
        description="Abre un juego instalado que el usuario solicitó explícitamente mediante su lanzador detectado.",
        parameters={"type": "object", "properties": {"game": {"type": "string", "description": "Nombre del juego a abrir."}}, "required": ["game"]},
        handler=launch_game,
    )
    registry.register(
        name="open_target",
        description="Abre un nombre humano de destino sin exigir su categoría ni ortografía exacta: reconoce servicios web configurados, juegos instalados y aplicaciones locales. Dentro de cada catálogo solo abre una coincidencia claramente superior; un juego instalado tiene prioridad sobre una aplicación homónima.",
        parameters={"type": "object", "properties": {"target": {"type": "string", "description": "Nombre, URL o nombre aproximado del destino a abrir."}}, "required": ["target"]},
        handler=open_target,
    )
    registry.register(
        name="get_system_info",
        description="Obtiene información local de hardware y sistema operativo: CPU, RAM, GPU, Windows y perfil recomendado de Mochi. No modifica el equipo.",
        parameters={"type": "object", "properties": {}},
        handler=get_system_info,
    )


    # IMPORTANTE:
    # return va al final, después de registrar todas las tools.


    # =====================================================
    # ABRIR APLICACIÓN
    # =====================================================

    registry.register(

        name="open_application",

        description=(
            "Busca y abre una aplicación instalada en Windows. "
            "Usar cuando el usuario quiera abrir, iniciar, "
            "ejecutar un programa instalado localmente. No usar para "
            "sitios web, dominios o servicios como Netflix o YouTube; "
            "para ellos usar browser_open."
        ),

        parameters={

            "type":
                "object",

            "properties": {

                "application": {

                    "type":
                        "string",

                    "description":
                        (
                            "Nombre de la aplicación que "
                            "el usuario quiere abrir."
                        )
                }
            },

            "required": [
                "application"
            ]
        },

        handler=open_application
    )

    registry.register(
        name="browser_open",
        description=("Abre un destino web en el Chrome real del usuario. Acepta una URL, dominio o servicio web conocido; si el nombre no está catalogado realiza una búsqueda web en vez de inventar un dominio. Usa profile solo si el usuario pidió explícitamente un perfil de navegador."),
        parameters={"type": "object", "properties": {"target": {"type": "string", "description": "URL, dominio o nombre del destino web."}, "profile": {"type": "string", "description": "Nombre, email, GAIA, directorio o id de perfil; opcional."}}, "required": ["target"]},
        handler=browser_open,
    )
    registry.register(
        name="browser_search",
        description="Busca información general en la web usando el navegador real. No es una búsqueda dentro de un sitio específico.",
        parameters={"type": "object", "properties": {"query": {"type": "string", "description": "Texto a buscar."}, "profile": {"type": "string", "description": "Perfil de navegador opcional."}}, "required": ["query"]},
        handler=browser_search,
    )
    registry.register(
        name="browser_profiles",
        description="Consulta perfiles de Chrome detectados o cuál está activo/predeterminado. No abre ni cambia perfiles.",
        parameters={"type": "object", "properties": {"action": {"type": "string", "enum": ["list", "active"], "description": "list para perfiles disponibles; active para el perfil en uso."}}, "required": []},
        handler=browser_profiles,
    )
    registry.register(
        name="web_inspect",
        description="Inspecciona la página activa de Chrome y devuelve controles web accesibles con IDs temporales. Usar antes de interactuar y nuevamente después de una acción para observar el resultado.",
        parameters={"type": "object", "properties": {"query": {"type": "string", "description": "Filtro opcional por texto o nombre accesible."}}, "required": []},
        handler=web_inspect,
    )
    registry.register(
        name="web_act",
        description="Actúa sobre un elemento de web_inspect. Acciones: click, invoke, keyboard_activate, focus, type, submit, select, toggle, scroll_into_view o set_range. keyboard_activate enfoca un botón/enlace y usa su activación estándar de teclado; submit envía Enter al campo de texto inspeccionado; scroll_into_view desplaza semánticamente un resultado accesible fuera de pantalla; set_range ajusta un slider UIA dentro de los límites expuestos por inspección. Después de una acción que cambia página, usar web_inspect para verificar; éxito de acción no equivale a objetivo completado.",
        parameters={"type": "object", "properties": {"element_id": {"type": "string", "description": "ID temporal del elemento inspeccionado."}, "action": {"type": "string", "enum": ["click", "invoke", "keyboard_activate", "focus", "type", "submit", "select", "toggle", "scroll_into_view", "set_range"]}, "value": {"type": "string", "description": "Texto para type o valor numérico dentro del rango publicado para set_range; opcional para otras acciones."}, "snapshot_id": {"type": "string", "description": "Snapshot que originó el ID; opcional si es el último."}}, "required": ["element_id", "action"]},
        handler=web_act,
    )

        # =====================================================
    # LISTAR VENTANAS ABIERTAS
    # =====================================================

    registry.register(

        name="list_open_windows",

        description=(
            "Obtiene las ventanas visibles que están abiertas "
            "actualmente en Windows. "
            "Devuelve el título de cada ventana, su proceso y PID. "
            "Usar cuando el usuario pregunte qué programas o "
            "ventanas tiene abiertas, o cuando sea necesario "
            "comprobar si una aplicación está abierta antes "
            "de decidir qué hacer."
        ),

        parameters={
            "type": "object",
            "properties": {}
        },

        handler=list_open_windows
    )

        # =====================================================
    # CERRAR VENTANA
    # =====================================================

    registry.register(

        name="close_window",

        description=(
            "Cierra normalmente una ventana abierta de Windows. "
            "Usar cuando el usuario quiera cerrar una aplicación "
            "o ventana que actualmente esté abierta. "
            "No fuerza la terminación del proceso."
        ),

        parameters={

            "type":
                "object",

            "properties": {

                "window": {

                    "type":
                        "string",

                    "description":
                        (
                            "Nombre de la aplicación, proceso "
                            "o título de la ventana que se quiere cerrar."
                        )
                }
            },

            "required": [
                "window"
            ]
        },

        handler=close_window
    )

        # =====================================================
    # CERRAR TODAS LAS VENTANAS DE UNA APLICACIÓN
    # =====================================================

    registry.register(

        name="close_application_windows",

        description=(
            "Cierra todas las ventanas abiertas que correspondan "
            "a una aplicación. Usar cuando el usuario diga que "
            "quiere cerrar una aplicación completa, por ejemplo "
            "'cerrá Chrome' o 'cerrá todas las ventanas de Chrome'. "
            "No usar cuando el usuario quiera cerrar solamente "
            "una ventana específica."
        ),

        parameters={

            "type":
                "object",

            "properties": {

                "application": {

                    "type":
                        "string",

                    "description":
                        (
                            "Nombre de la aplicación cuyas "
                            "ventanas deben cerrarse."
                        )
                }
            },

            "required": [
                "application"
            ]
        },

        handler=close_application_windows
    )

        # =====================================================
    # CONTROLAR VENTANA
    # =====================================================

    registry.register(

        name="control_window",

        description=(
            "Controla una ventana abierta de Windows. "
            "Permite traerla al frente, minimizarla o maximizarla. "
            "Usar cuando el usuario diga cosas como "
            "'traé Chrome al frente', 'mostrame Discord', "
            "'minimizá Spotify' o 'maximizá Visual Studio Code'."
        ),

        parameters={

            "type":
                "object",

            "properties": {

                "window": {

                    "type":
                        "string",

                    "description":
                        (
                            "Nombre de la aplicación, proceso "
                            "o parte del título de la ventana."
                        )
                },

                "action": {

                    "type":
                        "string",

                    "enum": [
                        "focus",
                        "minimize",
                        "maximize"
                    ],

                    "description":
                        (
                            "Acción que debe realizarse "
                            "sobre la ventana."
                        )
                }
            },

            "required": [
                "window",
                "action"
            ]
        },

        handler=control_window
    )

    return registry


# =========================================================
# EXTRAER TOOL CALL
# =========================================================

def extract_tool_call(
    call
):

    if not isinstance(
        call,
        dict
    ):

        return (
            None,
            {}
        )


    function = (
        call.get(
            "function",
            {}
        )
        or {}
    )


    name = (
        function.get(
            "name"
        )
    )


    arguments = (
        function.get(
            "arguments",
            {}
        )
    )


    # Algunos runtimes pueden devolver
    # arguments como JSON en texto.

    if isinstance(
        arguments,
        str
    ):

        try:

            arguments = json.loads(
                arguments
            )

        except Exception:

            arguments = {}


    if not isinstance(
        arguments,
        dict
    ):

        arguments = {}


    return (
        name,
        arguments
    )


# =========================================================
# PRUEBA DIRECTA
# =========================================================

if __name__ == "__main__":

    print("")
    print(
        "======================================"
    )
    print(
        "       MOCHI REAL TOOL TEST"
    )
    print(
        "======================================"
    )


    # =====================================================
    # CREAR BRAIN DESDE CONFIG
    # =====================================================

    provider = (
        brain_router.provider
    )


    # =====================================================
    # CREAR TOOLS
    # =====================================================

    registry = (
        create_mochi_tool_registry()
    )


    print(
        f"Brain: {provider.provider_name}"
    )

    print(
        f"Modelo: {provider.model_name}"
    )

    print("")
    print(
        "Tools:"
    )


    for tool_name in (
        registry.get_tool_names()
    ):

        print(
            f"  - {tool_name}"
        )


    # =====================================================
    # FRASE ABSTRACTA DE PRUEBA
    # =====================================================

    user_command = (
        "Abrí Google Chrome."
    )


    print("")
    print(
        f"USUARIO: {user_command}"
    )


    messages = [

        {
            "role":
                "system",

            "content":
                (
                    "Sos Mochi, un asistente de Windows. "
                    "Comprendé la intención del usuario. "
                    "Cuando una herramienta pueda realizar "
                    "la acción solicitada, utilizala. "
                    "No inventes que ejecutaste acciones."
                )
        },

        {
            "role":
                "user",

            "content":
                user_command
        }
    ]


    # =====================================================
    # CONSULTAR AL MODELO
    # =====================================================

    result = (
        brain_router.auto(

            messages=messages,

            tools=(
                registry
                .get_tool_schemas()
            )
        )
    )

    print("")
    print(
        f"MODO ELEGIDO: {brain_router.last_mode}"
    )


    print("")
    print(
        f"SUCCESS: {result.success}"
    )


    if result.error:

        print(
            f"ERROR: {result.error}"
        )

        raise SystemExit(1)


    print(
        f"TEXTO: {result.text}"
    )

        # =====================================================
    # MÉTRICAS DE RENDIMIENTO
    # =====================================================

    if result.raw:

        total_duration = (
            result.raw.get(
                "total_duration",
                0
            )
        )

        load_duration = (
            result.raw.get(
                "load_duration",
                0
            )
        )

        prompt_duration = (
            result.raw.get(
                "prompt_eval_duration",
                0
            )
        )

        eval_duration = (
            result.raw.get(
                "eval_duration",
                0
            )
        )

        prompt_tokens = (
            result.raw.get(
                "prompt_eval_count",
                0
            )
        )

        generated_tokens = (
            result.raw.get(
                "eval_count",
                0
            )
        )


        def ns_to_seconds(
            value
        ):

            return round(
                value / 1_000_000_000,
                2
            )


        print("")
        print(
            "======================================"
        )
        print(
            "       MÉTRICAS DEL BRAIN"
        )
        print(
            "======================================"
        )

        print(
            f"Tiempo total: "
            f"{ns_to_seconds(total_duration)} s"
        )

        print(
            f"Carga del modelo: "
            f"{ns_to_seconds(load_duration)} s"
        )

        print(
            f"Procesamiento del prompt: "
            f"{ns_to_seconds(prompt_duration)} s"
        )

        print(
            f"Generación / razonamiento: "
            f"{ns_to_seconds(eval_duration)} s"
        )

        print(
            f"Tokens del prompt: "
            f"{prompt_tokens}"
        )

        print(
            f"Tokens generados: "
            f"{generated_tokens}"
        )


        if (
            eval_duration > 0
            and generated_tokens > 0
        ):

            tokens_per_second = (
                generated_tokens
                /
                (
                    eval_duration
                    / 1_000_000_000
                )
            )


            print(
                f"Velocidad: "
                f"{tokens_per_second:.1f} tokens/s"
            )


        print(
            "======================================"
        )

    print("")
    print(
        "TOOL CALLS:"
    )


    if not result.tool_calls:

        print(
            "  El modelo no pidió ninguna herramienta."
        )

        raise SystemExit(0)


    # =====================================================
    # EJECUTAR TOOL CALLS
    # =====================================================

    for call in result.tool_calls:

        print(
            json.dumps(
                call,
                indent=2,
                ensure_ascii=False
            )
        )


        tool_name, arguments = (
            extract_tool_call(
                call
            )
        )


        print("")
        print(
            f"Tool elegida: {tool_name}"
        )

        print(
            f"Argumentos: {arguments}"
        )


        if not tool_name:

            print(
                "Tool inválida."
            )

            continue


        tool_result = (
            registry.execute(
                name=tool_name,
                arguments=arguments
            )
        )


        print("")
        print(
            "RESULTADO DE LA TOOL:"
        )

        print(
            json.dumps(
                tool_result,
                indent=2,
                ensure_ascii=False
            )
        )


    print("")
    print(
        "======================================"
    )
