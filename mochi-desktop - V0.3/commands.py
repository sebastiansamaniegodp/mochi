import re
import unicodedata
from urllib.parse import quote_plus

from media_controller import (
    media_controller
)

from application_registry import (
    application_registry
)

from game_registry import (
    game_registry
)

from window_manager import (
    window_manager
)

from actions.apps import (
    open_chrome,
    open_url,
    open_spotify
)

from actions.browser import (
    BrowserController
)

from file_commands import (
    try_file_command
)

# =========================================================
# CONTROLADOR DEL NAVEGADOR
# =========================================================

browser = BrowserController()


# =========================================================
# NORMALIZAR TEXTO
# =========================================================

def normalize_text(text):

    if not text:
        return ""

    text = text.lower().strip()

    # Quitar acentos
    text = unicodedata.normalize(
        "NFD",
        text
    )

    text = "".join(
        char
        for char in text
        if unicodedata.category(char) != "Mn"
    )

    # Quitar algunos signos de puntuación
    # Conservamos números y %
    text = re.sub(
        r"[,.!?;:]",
        " ",
        text
    )

    # Espacios repetidos
    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# =========================================================
# NÚMEROS EN ESPAÑOL
# =========================================================

SPANISH_NUMBERS = {

    "cero": 0,

    "uno": 1,
    "un": 1,
    "una": 1,

    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,

    "diez": 10,
    "once": 11,
    "doce": 12,
    "trece": 13,
    "catorce": 14,
    "quince": 15,

    "dieciseis": 16,
    "diecisiete": 17,
    "dieciocho": 18,
    "diecinueve": 19,

    "veinte": 20,

    "veintiuno": 21,
    "veintidos": 22,
    "veintitres": 23,
    "veinticuatro": 24,
    "veinticinco": 25,
    "veintiseis": 26,
    "veintisiete": 27,
    "veintiocho": 28,
    "veintinueve": 29,

    "treinta": 30,
    "cuarenta": 40,
    "cincuenta": 50,
    "sesenta": 60,
    "setenta": 70,
    "ochenta": 80,
    "noventa": 90,

    "cien": 100,
    "ciento": 100
}


# =========================================================
# CONVERTIR TEXTO A NÚMERO
# =========================================================

def parse_number(text):

    if text is None:
        return None

    text = normalize_text(
        text
    )

    # Limpiar expresiones relacionadas
    # con porcentaje.
    text = (
        text
        .replace("%", "")
        .replace("por ciento", "")
        .replace("porciento", "")
        .strip()
    )


    # =====================================================
    # NÚMERO DIRECTO
    # Ej:
    # 35
    # 80
    # =====================================================

    if re.fullmatch(
        r"\d{1,3}",
        text
    ):

        return int(text)


    # =====================================================
    # PALABRA DIRECTA
    # Ej:
    # veinte
    # cuarenta
    # cien
    # =====================================================

    if text in SPANISH_NUMBERS:

        return SPANISH_NUMBERS[
            text
        ]


    # =====================================================
    # EJ:
    # treinta y cinco
    # cuarenta y dos
    # =====================================================

    text = text.replace(
        " y ",
        " "
    )


    parts = text.split()


    if len(parts) == 2:

        tens = SPANISH_NUMBERS.get(
            parts[0]
        )

        unit = SPANISH_NUMBERS.get(
            parts[1]
        )


        if (
            tens is not None
            and unit is not None
            and tens in {
                20,
                30,
                40,
                50,
                60,
                70,
                80,
                90
            }
            and 0 <= unit <= 9
        ):

            return (
                tens + unit
            )


    return None


# =========================================================
# EXTRAER BÚSQUEDA DE YOUTUBE
# =========================================================

def extract_youtube_search(command):

    patterns = [

        r"busca (.+?) en youtube",

        r"buscar (.+?) en youtube",

    ]


    for pattern in patterns:

        match = re.search(
            pattern,
            command
        )


        if match:

            return (
                match
                .group(1)
                .strip()
            )


    return None


# =========================================================
# EXTRAER VIDEO A REPRODUCIR
# =========================================================

def extract_video_query(command):

    patterns = [

        r"pon un video del (.+)$",

        r"pon un video de (.+)$",

        r"pon video del (.+)$",

        r"pon video de (.+)$",

        r"reproduce un video del (.+)$",

        r"reproduce un video de (.+)$",

        r"reproduce (.+) en youtube$",

        r"pon (.+) en youtube$",

    ]


    for pattern in patterns:

        match = re.search(
            pattern,
            command
        )


        if match:

            result = (
                match
                .group(1)
                .strip()
            )


            result = re.sub(
                r"\s+en youtube$",
                "",
                result
            )


            return (
                result.strip()
            )


    return None


# =========================================================
# INTERPRETAR ORDEN
# =========================================================

def execute_command(
    original_command
):

    # =====================================================
    # SIEMPRE NORMALIZAR PRIMERO
    # =====================================================

    command = normalize_text(
        original_command
    )

    # =====================================================
    # ARCHIVOS Y CARPETAS
    # =====================================================

    file_result = (
        try_file_command(
            command
        )
    )


    if file_result is not None:

        return file_result

    print("")
    print(
        "======================================"
    )

    print(
        f"ORDEN ORIGINAL: {original_command}"
    )

    print(
        f"ORDEN NORMALIZADA: {command}"
    )

    print(
        "======================================"
    )


    # =====================================================
    # VALIDAR ORDEN VACÍA
    # =====================================================

    if not command:

        return (
            False,
            "No recibí ninguna orden"
        )


    # =====================================================
    # VOLUMEN EXACTO
    #
    # "volumen al 35"
    # "volumen al treinta y cinco"
    # "pon el volumen al 50"
    # "sube el volumen al 80"
    # =====================================================

    volume_exact_patterns = [

        r"^(?:pon|pone|poneme|ajusta|coloca|deja) "
        r"(?:el )?volumen "
        r"(?:a|al|en) "
        r"(.+)$",

        r"^(?:sube|baja) "
        r"(?:el )?volumen "
        r"(?:a|al) "
        r"(.+)$",

        r"^volumen "
        r"(?:a|al|en) "
        r"(.+)$",

        r"^volumen "
        r"(.+)$",
    ]


    for pattern in volume_exact_patterns:

        match = re.match(
            pattern,
            command
        )


        if not match:
            continue


        value_text = (
            match
            .group(1)
            .strip()
        )


        percent = parse_number(
            value_text
        )


        if percent is None:
            continue


        percent = max(
            0,
            min(percent, 100)
        )


        print(
            f"PORCENTAJE INTERPRETADO: {percent}%"
        )


        return (
            media_controller
            .set_volume(
                percent
            )
        )


    # =====================================================
    # CONSULTAR VOLUMEN
    # =====================================================

    volume_queries = [

        "cuanto volumen tengo",

        "a cuanto esta el volumen",

        "que volumen tengo",

        "nivel de volumen",

        "cual es el volumen",

        "que nivel de volumen tengo",

        "dime el volumen",

        "decime el volumen"
    ]


    if command in volume_queries:

        return (
            media_controller
            .get_volume()
        )


    # =====================================================
    # SUBIR VOLUMEN UNA CANTIDAD
    #
    # "sube el volumen 10 por ciento"
    #
    # 35 → 45
    # =====================================================

    match = re.match(
        r"^(?:sube|aumenta) "
        r"(?:el )?volumen "
        r"(.+)$",
        command
    )


    if match:

        amount_text = (
            match
            .group(1)
            .strip()
        )


        amount = parse_number(
            amount_text
        )


        if amount is not None:

            return (
                media_controller
                .change_volume(
                    amount
                )
            )


    # =====================================================
    # BAJAR VOLUMEN UNA CANTIDAD
    #
    # "baja el volumen 20 por ciento"
    #
    # 60 → 40
    # =====================================================

    match = re.match(
        r"^(?:baja|reduce) "
        r"(?:el )?volumen "
        r"(.+)$",
        command
    )


    if match:

        amount_text = (
            match
            .group(1)
            .strip()
        )


        amount = parse_number(
            amount_text
        )


        if amount is not None:

            return (
                media_controller
                .change_volume(
                    -amount
                )
            )


    # =====================================================
    # SUBIR VOLUMEN NORMAL
    # =====================================================

    if command in [

        "sube el volumen",

        "subir volumen",

        "sube volumen",

        "aumenta el volumen",

        "mas volumen"

    ]:

        return (
            media_controller
            .volume_up()
        )


    # =====================================================
    # BAJAR VOLUMEN NORMAL
    # =====================================================

    if command in [

        "baja el volumen",

        "bajar volumen",

        "baja volumen",

        "reduce el volumen",

        "menos volumen"

    ]:

        return (
            media_controller
            .volume_down()
        )


    # =====================================================
    # SILENCIAR
    # =====================================================

    if command in [

        "silencia",

        "silencia la pc",

        "silencia el sonido",

        "silencia la computadora",

        "mute",

        "mutea",

        "mutea la pc",

        "quita el sonido"

    ]:

        return (
            media_controller
            .mute()
        )


    # =====================================================
    # QUITAR SILENCIO
    # =====================================================

    if command in [

        "quita el silencio",

        "activa el sonido",

        "activa sonido",

        "desmutea",

        "saca el mute",

        "quita el mute"

    ]:

        return (
            media_controller
            .unmute()
        )


    # =====================================================
    # PLAY / PAUSE
    # =====================================================

    if command in [

        "pausa",

        "pausa la musica",

        "pausa el video",

        "pausa la reproduccion",

        "continua",

        "continua la musica",

        "continua el video",

        "continua la reproduccion",

        "reanuda",

        "reanuda la musica",

        "reanuda el video",

        "reanuda la reproduccion",

        "play",

        "play pausa"

    ]:

        return (
            media_controller
            .play_pause()
        )


    # =====================================================
    # SIGUIENTE
    # =====================================================

    if command in [

        "siguiente",

        "siguiente cancion",

        "siguiente pista",

        "pasa a la siguiente",

        "pon la siguiente",

        "cambia de cancion"

    ]:

        return (
            media_controller
            .next_track()
        )


    # =====================================================
    # ANTERIOR
    # =====================================================

    if command in [

        "anterior",

        "cancion anterior",

        "pista anterior",

        "vuelve a la anterior",

        "pon la anterior"

    ]:

        return (
            media_controller
            .previous_track()
        )


    # =====================================================
    # DETENER MULTIMEDIA
    # =====================================================

    if command in [

        "deten la musica",

        "deten el video",

        "deten la reproduccion",

        "para la musica",

        "para el video",

        "para la reproduccion"

    ]:

        return (
            media_controller
            .stop_media()
        )


    # =====================================================
    # CONTROL DE VENTANAS
    # =====================================================


    # =====================================================
    # MINIMIZAR
    # =====================================================

    match = re.match(
        r"^(?:minimiza|minimizar) (.+)$",
        command
    )


    if match:

        target = (
            match
            .group(1)
            .strip()
        )


        return (
            window_manager
            .minimize(
                target
            )
        )


    # =====================================================
    # MAXIMIZAR
    # =====================================================

    match = re.match(
        r"^(?:maximiza|maximizar) (.+)$",
        command
    )


    if match:

        target = (
            match
            .group(1)
            .strip()
        )


        return (
            window_manager
            .maximize(
                target
            )
        )


    # =====================================================
    # TRAER AL FRENTE
    # =====================================================

    focus_patterns = [

        r"^trae (.+) al frente$",

        r"^traeme (.+) al frente$",

        r"^muestra (.+)$",

        r"^enfoca (.+)$",

        r"^ve a (.+)$"

    ]


    for pattern in focus_patterns:

        match = re.match(
            pattern,
            command
        )


        if match:

            target = (
                match
                .group(1)
                .strip()
            )


            return (
                window_manager
                .focus(
                    target
                )
            )


    # =====================================================
    # CERRAR VENTANA
    # =====================================================

    match = re.match(
        r"^(?:cierra|cerrar) (.+)$",
        command
    )


    if match:

        target = (
            match
            .group(1)
            .strip()
        )


        return (
            window_manager
            .close(
                target
            )
        )


    # =====================================================
    # PROGRAMAS ABIERTOS
    # =====================================================

    open_window_queries = [

        "que programas tengo abiertos",

        "que aplicaciones tengo abiertas",

        "que tengo abierto",

        "programas abiertos",

        "aplicaciones abiertas",

        "que programas estan abiertos",

        "que aplicaciones estan abiertas"

    ]


    if command in open_window_queries:

        return (
            window_manager
            .describe_open_windows()
        )


    # =====================================================
    # YOUTUBE
    # =====================================================


    # =====================================================
    # YOUTUBE - REPRODUCIR VIDEO
    # =====================================================

    video_query = (
        extract_video_query(
            command
        )
    )


    if video_query:

        print("")
        print(
            "PLAN:"
        )

        print(
            f"1. Buscar '{video_query}'"
        )

        print(
            "2. Encontrar video"
        )

        print(
            "3. Abrirlo en YouTube"
        )


        return (
            browser
            .play_youtube_video(
                video_query
            )
        )


    # =====================================================
    # YOUTUBE - BÚSQUEDA
    # =====================================================

    youtube_search = (
        extract_youtube_search(
            command
        )
    )


    if youtube_search:

        print("")
        print(
            "PLAN:"
        )

        print(
            "1. Abrir YouTube"
        )

        print(
            f"2. Buscar '{youtube_search}'"
        )


        return (
            browser
            .search_youtube(
                youtube_search
            )
        )


    # =====================================================
    # YOUTUBE - ABRIR
    # =====================================================

    if (
        "youtube" in command
        and
        (
            "abre" in command
            or
            "abrir" in command
            or
            "entra" in command
            or
            "ve a" in command
        )
    ):

        return (
            browser
            .open_youtube()
        )


    # =====================================================
    # GOOGLE - BUSCAR
    # =====================================================

    match = re.search(
        r"(?:busca|buscar) (.+?) en google",
        command
    )


    if match:

        query = (
            match
            .group(1)
            .strip()
        )


        url = (
            "https://www.google.com/search?q="
            f"{quote_plus(query)}"
        )


        if open_url(
            url
        ):

            return (
                True,
                f"Buscando {query} en Google"
            )


        return (
            False,
            "No pude abrir Google"
        )


    # =====================================================
    # GOOGLE - ABRIR
    # =====================================================

    if (
        "google" in command
        and
        (
            "abre" in command
            or
            "abrir" in command
            or
            "entra" in command
        )
    ):

        if open_url(
            "https://www.google.com"
        ):

            return (
                True,
                "Google abierto"
            )


        return (
            False,
            "No pude abrir Google"
        )


    # =====================================================
    # CHROME
    # =====================================================

    if (
        "chrome" in command
        and
        (
            "abre" in command
            or
            "abrir" in command
        )
    ):

        if open_chrome():

            return (
                True,
                "Chrome abierto"
            )


        return (
            False,
            "No encontré Chrome"
        )


    # =====================================================
    # SPOTIFY
    # =====================================================

    if (
        "spotify" in command
        and
        (
            "abre" in command
            or
            "abrir" in command
            or
            command == "spotify"
        )
    ):

        if open_spotify():

            return (
                True,
                "Spotify abierto"
            )


        return (
            False,
            "No pude abrir Spotify"
        )


    # =====================================================
    # JUEGOS - COMANDOS EXPLÍCITOS
    # =====================================================

    game_patterns = [

        r"^abre el juego (.+)$",

        r"^abre juego (.+)$",

        r"^inicia el juego (.+)$",

        r"^iniciar el juego (.+)$",

        r"^juega (.+)$"

    ]


    for pattern in game_patterns:

        match = re.match(
            pattern,
            command
        )


        if match:

            game_name = (
                match
                .group(1)
                .strip()
            )


            return (
                game_registry
                .launch(
                    game_name
                )
            )


    # =====================================================
    # ABRIR / EJECUTAR ALGO
    #
    # Primero:
    # comprobar si es juego.
    #
    # Si no:
    # comprobar si es aplicación.
    # =====================================================

    simple_open = re.match(
        r"^(?:abre|abrir|inicia|iniciar|ejecuta|ejecutar) (.+)$",
        command
    )


    if simple_open:

        target = (
            simple_open
            .group(1)
            .strip()
        )


        # =================================================
        # ¿ES UN JUEGO?
        # =================================================

        detected_game = (
            game_registry
            .find(
                target
            )
        )


        if detected_game:

            return (
                game_registry
                .launch(
                    target
                )
            )


        # =================================================
        # SI NO ES JUEGO:
        # BUSCAR APLICACIÓN
        # =================================================

        return (
            application_registry
            .launch(
                target
            )
        )


    # =====================================================
    # COMANDO DESCONOCIDO
    # =====================================================

    print("")
    print(
        f"COMANDO NO RECONOCIDO: {command}"
    )


    return (
        False,
        "Todavía no sé cómo hacer esa acción"
    )


# =========================================================
# CERRAR SERVICIOS
# =========================================================

def shutdown_services():

    browser.shutdown()