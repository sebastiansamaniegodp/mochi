"""Model-backed understanding for local PC commands.

The rules in goal_state only fire when they explain a whole sentence
("baja el volumen a 0%", "cierra la calculadora").  Everything else that is
clearly about the PC (volume, music, windows, clipboard, screenshots,
notifications, installed apps and games, system info) used to be guessed
from keywords.  Here the model reads the sentence and answers with a fixed
JSON shape; the code validates it and turns it into the same
(operation, target, value) triple the rules produce, so the deterministic
plan still decides what runs.

The model never picks a tool directly: an unknown action, an out-of-range
level, a name or text that is not in the user's sentence, or a broken
answer yields None and the command goes to the regular agent loop.
"""

import json
import re
import unicodedata


# Word stems that make a sentence worth interpreting.  Pages (YouTube,
# Netflix, a video or a series) have their own volume and player, handled
# by the web specialists, so they are left out.
_LOCAL_STEMS = (
    "volumen", "sonido", "audio", "silenci", "mute", "desmute", "musica",
    "cancion", "tema", "pista", "ventana", "minimiz", "maximiz", "cierr",
    "cerr", "carpeta", "perfil", "portapapeles", "copi", "captura",
    "screenshot", "pantallazo", "notificacion", "aviso", "juego",
    "aplicacion", "programa", "apps", "especificacion", "hardware", "specs",
    "procesador", "memoria",
)
_PAGE_WORDS = (
    "youtube", "netflix", "twitch", "video", "serie", "temporada",
    "capitulo", "pelicula", "pestana",
)

# accion -> what the answer must carry: None, "valor" or "texto".
_ACTIONS = {
    "fijar_volumen": "valor",
    "subir_volumen": "valor",
    "bajar_volumen": "valor",
    "consultar_volumen": None,
    "silenciar": None,
    "quitar_silencio": None,
    "pausar_o_reanudar": None,
    "siguiente": None,
    "anterior": None,
    "detener": None,
    "listar_ventanas": None,
    "cerrar_ventana": "texto",
    "cerrar_todas_las_ventanas_de": "texto",
    "minimizar_ventana": "texto",
    "maximizar_ventana": "texto",
    "traer_ventana_al_frente": "texto",
    "listar_carpetas": None,
    "listar_perfiles_chrome": None,
    "perfil_activo_chrome": None,
    "leer_portapapeles": None,
    "copiar_al_portapapeles": "texto",
    "captura_de_pantalla": None,
    "mostrar_notificacion": "texto",
    "listar_juegos": None,
    "abrir_juego": "texto",
    "listar_aplicaciones": None,
    "info_del_sistema": None,
    "ninguna": None,
}

INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "accion": {"type": "string", "enum": sorted(_ACTIONS)},
        "valor": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "texto": {"anyOf": [{"type": "string", "maxLength": 300}, {"type": "null"}]},
        "pide_otra_cosa": {"type": "boolean"},
    },
    "required": ["accion", "valor", "texto", "pide_otra_cosa"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = (
    "Clasificás órdenes en español para la PC. Respondé solo un JSON "
    "{\"accion\": ..., \"valor\": ..., \"texto\": ..., "
    "\"pide_otra_cosa\": ...}.\n"
    "Volumen y música:\n"
    "- fijar_volumen: dejar el volumen en un nivel; valor = nivel 0-100. "
    "\"a 30\", \"al 30%\", \"hasta 30\", \"a cero\", \"al máximo\" (100), "
    "\"a la mitad\" (50), \"al mínimo\" (0) son niveles finales, aunque el "
    "verbo sea bajar o subir.\n"
    "- subir_volumen / bajar_volumen: cambiar el volumen; valor = cuántos "
    "puntos (\"5\", \"en 5\", \"un 5%\"), o null si no dice cuánto.\n"
    "- consultar_volumen, silenciar, quitar_silencio.\n"
    "- pausar_o_reanudar, siguiente, anterior, detener: la música que suena.\n"
    "Ventanas (texto = el nombre del programa o ventana, tal como lo dijo):\n"
    "- listar_ventanas, cerrar_ventana, cerrar_todas_las_ventanas_de, "
    "minimizar_ventana, maximizar_ventana, traer_ventana_al_frente.\n"
    "Otras:\n"
    "- listar_carpetas: las carpetas del usuario.\n"
    "- listar_perfiles_chrome, perfil_activo_chrome.\n"
    "- leer_portapapeles; copiar_al_portapapeles: texto = lo que hay que "
    "copiar, palabra por palabra.\n"
    "- captura_de_pantalla: solo si la pide.\n"
    "- mostrar_notificacion: texto = el mensaje, palabra por palabra.\n"
    "- listar_juegos; abrir_juego: texto = el nombre del juego.\n"
    "- listar_aplicaciones: las aplicaciones o programas instalados.\n"
    "- info_del_sistema: especificaciones o hardware de la PC.\n"
    "- ninguna: cualquier otra cosa, una negación (\"no hagas...\"), una "
    "orden que pide varias cosas, o si habla de YouTube, Netflix, un video "
    "o una página. Ante la duda, ninguna.\n"
    "texto se copia de la orden, nunca se inventa, y es solo el nombre o "
    "el mensaje: sin el verbo, sin \"portapapeles\" ni \"notificación\", "
    "sin \"todo lo de\" ni \"la de\". valor y texto son null cuando la "
    "acción no los usa. pide_otra_cosa es true si la orden pide además "
    "otra acción (\"abrí X y bajá el volumen\").\n"
    "Ejemplos:\n"
    "\"cerrá todo lo de firefox\" -> {\"accion\": "
    "\"cerrar_todas_las_ventanas_de\", \"valor\": null, \"texto\": "
    "\"firefox\", \"pide_otra_cosa\": false}\n"
    "\"cerrá esa ventana, la de discord\" -> {\"accion\": "
    "\"cerrar_ventana\", \"valor\": null, \"texto\": \"discord\", "
    "\"pide_otra_cosa\": false}\n"
    "\"pasame al portapapeles el código 4512\" -> {\"accion\": "
    "\"copiar_al_portapapeles\", \"valor\": null, \"texto\": "
    "\"el código 4512\", \"pide_otra_cosa\": false}\n"
    "\"mandame un aviso que diga regar las plantas\" -> {\"accion\": "
    "\"mostrar_notificacion\", \"valor\": null, \"texto\": "
    "\"regar las plantas\", \"pide_otra_cosa\": false}\n"
    "\"cerrá spotify y subí el volumen\" -> {\"accion\": "
    "\"cerrar_ventana\", \"valor\": null, \"texto\": \"spotify\", "
    "\"pide_otra_cosa\": true}"
)


def _norm(value):
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^\w%]+", " ", text)).strip()


def mentions_local_capability(command):
    """Whether the sentence talks about something the PC tools cover."""

    words = _norm(command).split()
    if any(word.startswith(_PAGE_WORDS) for word in words):
        return False
    return any(word.startswith(_LOCAL_STEMS) for word in words)


def _parse_answer(text):
    match = re.search(r"\{.*\}", str(text or ""), re.S)
    if not match:
        return None
    try:
        answer = json.loads(match.group(0))
    except ValueError:
        return None
    return answer if isinstance(answer, dict) else None


# What a quoted text must not contain: the order's own words leaking in
# ("guardame en el portapapeles ...", "todo lo de chrome").
_ORDER_WORDS = {
    "message": re.compile(r"\b(?:portapapeles|notificacion|aviso|avisame)\b"),
    "name": re.compile(
        r"\b(?:que|porque|y|luego|despues|cuando|si|pero|para|no|todo|toda|"
        r"todos|todas|ventana|ventanas|cerra|cierra|minimiza|maximiza)\b"
    ),
}


def _quoted_text(answer, command, kind):
    """The answer's texto, only when it was taken from the user's sentence."""

    text = answer.get("texto")
    if not isinstance(text, str):
        return None
    text = text.strip().strip("\"'«»“”")
    if kind == "name":
        text = re.sub(r"^(?:(?:la|el|lo)\s+de|la|el|los|las)\s+", "", text, flags=re.I)
    normalized = _norm(text)
    if not normalized or len(text) > (80 if kind == "name" else 300):
        return None
    if normalized == _norm(command) or _ORDER_WORDS[kind].search(normalized):
        return None
    if f" {normalized} " not in f" {_norm(command)} ":
        return None
    return text


def intent_to_operation(answer, command):
    """Validate the model's answer and map it to (operation, target, value)."""

    if not isinstance(answer, dict):
        return None
    action = answer.get("accion")
    if action not in _ACTIONS or action == "ninguna":
        return None
    # One sentence, one action: anything else is the agent loop's job.
    if answer.get("pide_otra_cosa") is not False:
        return None

    value = answer.get("valor")
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
        return None
    if action == "fijar_volumen":
        if value is None or not 0 <= value <= 100:
            return None
        return "set_system_volume", str(int(value)), None
    if action in {"subir_volumen", "bajar_volumen"}:
        amount = 10 if value is None else value
        if not 0 < amount <= 100:
            return None
        direction = "volume_up" if action == "subir_volumen" else "volume_down"
        return "control_media", direction, str(int(amount))

    if _ACTIONS[action] == "texto":
        kind = "message" if action in {"copiar_al_portapapeles", "mostrar_notificacion"} else "name"
        text = _quoted_text(answer, command, kind)
        if text is None:
            return None
        with_text = {
            "cerrar_ventana": ("close_window", text, None),
            "cerrar_todas_las_ventanas_de": ("close_application_windows", text, None),
            "minimizar_ventana": ("control_window", text, "minimize"),
            "maximizar_ventana": ("control_window", text, "maximize"),
            "traer_ventana_al_frente": ("control_window", text, "focus"),
            "copiar_al_portapapeles": ("write_clipboard", text, None),
            "mostrar_notificacion": ("show_notification", "Mochi", text),
            "abrir_juego": ("launch_game", text, None),
        }
        return with_text[action]

    without_text = {
        "consultar_volumen": ("get_system_volume", None, None),
        "silenciar": ("control_media", "mute", None),
        "quitar_silencio": ("control_media", "unmute", None),
        "pausar_o_reanudar": ("control_media", "play_pause", None),
        "siguiente": ("control_media", "next", None),
        "anterior": ("control_media", "previous", None),
        "detener": ("control_media", "stop", None),
        "listar_ventanas": ("list_open_windows", None, None),
        "listar_carpetas": ("list_user_folders", None, None),
        "listar_perfiles_chrome": ("browser_profiles", "list", None),
        "perfil_activo_chrome": ("browser_profiles", "active", None),
        "leer_portapapeles": ("read_clipboard", None, None),
        "captura_de_pantalla": ("capture_screenshot", None, None),
        "listar_juegos": ("list_games", None, None),
        "listar_aplicaciones": ("list_installed_applications", None, None),
        "info_del_sistema": ("get_system_info", None, None),
    }
    return without_text[action]


def interpret_local_command(router, command):
    """Ask the model what a local PC sentence means.

    Returns the (operation, target, value) triple, or None when the model is
    unavailable, unsure or answers something that does not validate.
    """

    fast = getattr(router, "fast", None)
    if fast is None or not mentions_local_capability(command):
        return None
    try:
        result = fast(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": str(command)},
            ],
            options={
                "temperature": 0,
                "num_predict": 160,
                "json_schema": INTENT_SCHEMA,
            },
        )
    except Exception:
        return None
    if not getattr(result, "success", False):
        return None
    return intent_to_operation(_parse_answer(getattr(result, "text", "")), command)
