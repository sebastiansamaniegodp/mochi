import re

from file_manager import (
    file_manager
)


# =========================================================
# TIPOS DE ARCHIVOS CONOCIDOS
# =========================================================

FILE_TYPES = {

    # PDF
    "pdf": {
        ".pdf"
    },

    # IMÁGENES
    "imagen": {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".bmp",
        ".tiff",
        ".tif",
        ".svg",
        ".ico"
    },

    "foto": {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".bmp",
        ".tiff",
        ".tif",
        ".heic"
    },

    # VIDEO
    "video": {
        ".mp4",
        ".mkv",
        ".avi",
        ".mov",
        ".wmv",
        ".webm",
        ".m4v",
        ".mpeg",
        ".mpg",
        ".3gp"
    },

    # AUDIO
    "audio": {
        ".mp3",
        ".wav",
        ".flac",
        ".m4a",
        ".aac",
        ".ogg",
        ".wma"
    },

    "musica": {
        ".mp3",
        ".wav",
        ".flac",
        ".m4a",
        ".aac",
        ".ogg",
        ".wma"
    },

    # EXCEL
    "excel": {
        ".xlsx",
        ".xls",
        ".xlsm",
        ".xlsb",
        ".csv",
        ".ods"
    },

    "planilla": {
        ".xlsx",
        ".xls",
        ".xlsm",
        ".xlsb",
        ".csv",
        ".ods"
    },

    "hoja de calculo": {
        ".xlsx",
        ".xls",
        ".xlsm",
        ".xlsb",
        ".csv",
        ".ods"
    },

    # WORD
    "word": {
        ".docx",
        ".doc",
        ".docm",
        ".odt",
        ".rtf"
    },

    "documento": {
        ".docx",
        ".doc",
        ".docm",
        ".odt",
        ".rtf",
        ".txt",
        ".pdf"
    },

    # POWERPOINT
    "powerpoint": {
        ".pptx",
        ".ppt",
        ".pptm",
        ".odp"
    },

    "presentacion": {
        ".pptx",
        ".ppt",
        ".pptm",
        ".odp"
    },

    # TEXTO
    "texto": {
        ".txt",
        ".md",
        ".log",
        ".rtf"
    },

    # CÓDIGO
    "codigo": {
        ".py",
        ".js",
        ".ts",
        ".html",
        ".css",
        ".java",
        ".kt",
        ".kts",
        ".cpp",
        ".c",
        ".h",
        ".cs",
        ".json",
        ".xml",
        ".yaml",
        ".yml",
        ".sql",
        ".php"
    },

    # COMPRIMIDOS
    "zip": {
        ".zip"
    },

    "comprimido": {
        ".zip",
        ".rar",
        ".7z",
        ".tar",
        ".gz"
    }
}


# =========================================================
# ALIASES DE TIPOS
# =========================================================

TYPE_ALIASES = {

    "pdf":
        "pdf",

    "imagen":
        "imagen",

    "imagenes":
        "imagen",

    "foto":
        "foto",

    "fotos":
        "foto",

    "video":
        "video",

    "videos":
        "video",

    "audio":
        "audio",

    "musica":
        "musica",

    "cancion":
        "musica",

    "excel":
        "excel",

    "archivo excel":
        "excel",

    "planilla":
        "planilla",

    "planilla excel":
        "excel",

    "hoja de calculo":
        "hoja de calculo",

    "word":
        "word",

    "archivo word":
        "word",

    "documento word":
        "word",

    "documento":
        "documento",

    "powerpoint":
        "powerpoint",

    "power point":
        "powerpoint",

    "presentacion":
        "presentacion",

    "presentacion powerpoint":
        "powerpoint",

    "texto":
        "texto",

    "archivo de texto":
        "texto",

    "codigo":
        "codigo",

    "archivo de codigo":
        "codigo",

    "zip":
        "zip",

    "archivo zip":
        "zip",

    "archivo comprimido":
        "comprimido",
}


# =========================================================
# OBTENER EXTENSIONES PARA UN TIPO
# =========================================================

def get_extensions_for_type(
    file_type
):

    file_type = (
        file_type
        .lower()
        .strip()
    )

    canonical = (
        TYPE_ALIASES
        .get(
            file_type,
            file_type
        )
    )

    return (
        FILE_TYPES
        .get(
            canonical
        )
    )


# =========================================================
# LIMPIAR NOMBRE DEL ARCHIVO
# =========================================================

def clean_file_query(
    text
):

    text = text.strip()

    prefixes = [

        "llamado ",

        "llamada ",

        "que se llama ",

        "que se llame ",

        "con nombre ",

        "de nombre "
    ]


    for prefix in prefixes:

        if text.startswith(
            prefix
        ):

            text = text[
                len(prefix):
            ]

            break


    return text.strip()


# =========================================================
# DETECTAR TIPO + NOMBRE
# =========================================================

def detect_typed_file(
    text
):

    text = text.strip()


    # Ordenamos por longitud para que:
    #
    # "hoja de calculo"
    #
    # se detecte antes que palabras
    # más cortas.

    aliases = sorted(
        TYPE_ALIASES.keys(),
        key=len,
        reverse=True
    )


    for alias in aliases:

        if text == alias:

            return (
                alias,
                ""
            )


        prefix = (
            alias + " "
        )


        if text.startswith(
            prefix
        ):

            remaining = (
                text[
                    len(prefix):
                ]
                .strip()
            )


            remaining = (
                clean_file_query(
                    remaining
                )
            )


            return (
                alias,
                remaining
            )


    return (
        None,
        None
    )


# =========================================================
# COMANDO PRINCIPAL
# =========================================================

def try_file_command(
    command
):

    # =====================================================
    # CARPETAS CONOCIDAS
    # =====================================================

    known_folders = {

        "abre descargas":
            "descargas",

        "abre mis descargas":
            "descargas",

        "abre la carpeta descargas":
            "descargas",

        "abre documentos":
            "documentos",

        "abre mis documentos":
            "documentos",

        "abre la carpeta documentos":
            "documentos",

        "abre escritorio":
            "escritorio",

        "abre mi escritorio":
            "escritorio",

        "abre imagenes":
            "imagenes",

        "abre mis imagenes":
            "imagenes",

        "abre fotos":
            "fotos",

        "abre mis fotos":
            "fotos",

        "abre musica":
            "musica",

        "abre mi musica":
            "musica",

        "abre videos":
            "videos",

        "abre mis videos":
            "videos",

        "abre onedrive":
            "onedrive",

        "abre mi onedrive":
            "onedrive"
    }


    if command in known_folders:

        return (
            file_manager
            .open_known_folder(
                known_folders[
                    command
                ]
            )
        )


    # =====================================================
    # ÚLTIMA DESCARGA
    # =====================================================

    latest_download_commands = [

        "abre el ultimo archivo descargado",

        "abre mi ultimo archivo descargado",

        "abre la ultima descarga",

        "abre lo ultimo que descargue",

        "abre lo ultimo descargado"
    ]


    if command in latest_download_commands:

        return (
            file_manager
            .open_latest_download()
        )


    # =====================================================
    # BUSCAR ARCHIVO POR TIPO
    #
    # Ejemplos:
    #
    # busca un excel presupuesto
    # busca el video vacaciones
    # busca una imagen logo
    # busca un word proyecto final
    # =====================================================

    match = re.match(
        r"^(?:busca|buscar) "
        r"(?:(?:un|una|el|la) )?"
        r"(.+)$",
        command
    )


    if match:

        remainder = (
            match
            .group(1)
            .strip()
        )


        file_type, query = (
            detect_typed_file(
                remainder
            )
        )


        if (
            file_type
            and query
        ):

            extensions = (
                get_extensions_for_type(
                    file_type
                )
            )


            if extensions:

                print("")
                print(
                    f"TIPO DE ARCHIVO: {file_type}"
                )

                print(
                    f"BUSCANDO: {query}"
                )


                return (
                    file_manager
                    .describe_search(
                        query=query,
                        extensions=extensions,
                        want_directory=False
                    )
                )


    # =====================================================
    # ABRIR ARCHIVO POR TIPO
    #
    # Ejemplos:
    #
    # abre el excel presupuesto
    # abre una imagen logo
    # abre el video vacaciones
    # abre el word trabajo final
    # =====================================================

    match = re.match(
        r"^(?:abre|abrir) "
        r"(?:(?:un|una|el|la) )?"
        r"(.+)$",
        command
    )


    if match:

        remainder = (
            match
            .group(1)
            .strip()
        )


        file_type, query = (
            detect_typed_file(
                remainder
            )
        )


        if (
            file_type
            and query
        ):

            extensions = (
                get_extensions_for_type(
                    file_type
                )
            )


            if extensions:

                print("")
                print(
                    f"TIPO DE ARCHIVO: {file_type}"
                )

                print(
                    f"ABRIENDO: {query}"
                )


                return (
                    file_manager
                    .open_file(
                        query=query,
                        extensions=extensions
                    )
                )


    # =====================================================
    # BUSCAR ARCHIVO GENERAL
    #
    # busca el archivo factura
    # =====================================================

    match = re.match(
        r"^(?:busca|buscar) "
        r"(?:el |un )?"
        r"archivo "
        r"(?:llamado |llamada |que se llame )?"
        r"(.+)$",
        command
    )


    if match:

        query = (
            match
            .group(1)
            .strip()
        )


        return (
            file_manager
            .describe_search(
                query=query,
                want_directory=False
            )
        )


    # =====================================================
    # ABRIR ARCHIVO GENERAL
    # =====================================================

    match = re.match(
        r"^(?:abre|abrir) "
        r"(?:el |un )?"
        r"archivo "
        r"(?:llamado |llamada |que se llame )?"
        r"(.+)$",
        command
    )


    if match:

        query = (
            match
            .group(1)
            .strip()
        )


        return (
            file_manager
            .open_file(
                query=query
            )
        )


    # =====================================================
    # BUSCAR CARPETA
    # =====================================================

    match = re.match(
        r"^(?:busca|buscar) "
        r"(?:la |una )?"
        r"carpeta "
        r"(?:llamada |llamado |que se llame )?"
        r"(.+)$",
        command
    )


    if match:

        query = (
            match
            .group(1)
            .strip()
        )


        return (
            file_manager
            .describe_search(
                query=query,
                want_directory=True
            )
        )


    # =====================================================
    # ABRIR CARPETA
    # =====================================================

    match = re.match(
        r"^(?:abre|abrir) "
        r"(?:mi |la |una )?"
        r"carpeta "
        r"(?:llamada |llamado |que se llame )?"
        r"(.+)$",
        command
    )


    if match:

        query = (
            match
            .group(1)
            .strip()
        )


        return (
            file_manager
            .open_folder(
                query
            )
        )


    # =====================================================
    # NO ES COMANDO DE ARCHIVOS
    # =====================================================

    return None