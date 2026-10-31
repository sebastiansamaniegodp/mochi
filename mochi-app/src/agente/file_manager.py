import os
import re
import unicodedata

from difflib import SequenceMatcher
from pathlib import Path

from linux_platform import system_open
from discovery.user_folders import (
    discover_user_folders
)


# =========================================================
# NORMALIZAR
# =========================================================

def normalize_file_name(text):

    if not text:
        return ""

    text = text.lower().strip()

    text = unicodedata.normalize(
        "NFD",
        text
    )

    text = "".join(
        character
        for character in text
        if unicodedata.category(character)
        != "Mn"
    )

    text = re.sub(
        r"[^a-z0-9]+",
        " ",
        text
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# =========================================================
# FILE MANAGER
# =========================================================

class FileManager:

    def __init__(self):

        self.folders = (
            discover_user_folders()
        )


        self.folder_aliases = {

            "descargas":
                "downloads",

            "downloads":
                "downloads",

            "documentos":
                "documents",

            "documento":
                "documents",

            "documents":
                "documents",

            "escritorio":
                "desktop",

            "desktop":
                "desktop",

            "imagenes":
                "pictures",

            "imagen":
                "pictures",

            "fotos":
                "pictures",

            "pictures":
                "pictures",

            "musica":
                "music",

            "music":
                "music",

            "videos":
                "videos",

            "video":
                "videos",

            "onedrive":
                "onedrive",
        }


        # Carpetas que no vale la pena recorrer
        # durante una búsqueda normal.

        self.skip_directories = {

            "appdata",

            "node_modules",

            ".git",

            ".gradle",

            ".idea",

            ".vscode",

            "__pycache__",

            "venv",

            ".venv",

            "$recycle.bin",

            "windows",

            "program files",

            "program files (x86)"
        }


    # =====================================================
    # ACTUALIZAR CARPETAS
    # =====================================================

    def refresh(self):

        self.folders = (
            discover_user_folders()
        )


    # =====================================================
    # ABRIR RUTA
    # =====================================================

    def open_path(
        self,
        path
    ):

        path = Path(path)

        if not path.exists():

            return (
                False,
                "La ruta ya no existe"
            )


        try:

            system_open(
                str(path)
            )


            return (
                True,
                f"Abriendo {path.name}"
            )


        except Exception as e:

            print(
                "Error abriendo ruta:",
                repr(e)
            )


            return (
                False,
                f"No pude abrir {path.name}"
            )


    # =====================================================
    # ABRIR CARPETA CONOCIDA
    # =====================================================

    def open_known_folder(
        self,
        name
    ):

        name = normalize_file_name(
            name
        )


        internal_name = (
            self.folder_aliases
            .get(name)
        )


        if not internal_name:

            return (
                False,
                f"No conozco la carpeta {name}"
            )


        path = (
            self.folders
            .get(internal_name)
        )


        if not path:

            return (
                False,
                f"No encontré la carpeta {name}"
            )


        return self.open_path(
            path
        )


    # =====================================================
    # RAÍCES DE BÚSQUEDA
    # =====================================================

    def _get_search_roots(self):

        preferred_order = [

            "documents",

            "downloads",

            "desktop",

            "pictures",

            "onedrive",

            "music",

            "videos"
        ]


        roots = []

        seen = set()


        for name in preferred_order:

            path = self.folders.get(
                name
            )


            if not path:
                continue


            try:

                resolved = str(
                    path.resolve()
                ).lower()

            except Exception:

                resolved = str(
                    path
                ).lower()


            if resolved in seen:
                continue


            seen.add(
                resolved
            )

            roots.append(
                path
            )


        return roots


    # =====================================================
    # PUNTUAR RESULTADO
    # =====================================================

    def _calculate_score(
        self,
        query,
        path
    ):

        query = normalize_file_name(
            query
        )


        name = normalize_file_name(
            path.name
        )


        stem = normalize_file_name(
            path.stem
        )


        if query == stem:
            return 1.0


        if query == name:
            return 1.0


        if stem.startswith(
            query
        ):

            return 0.95


        if query in stem:

            difference = abs(
                len(stem)
                - len(query)
            )

            penalty = min(
                difference / 200,
                0.10
            )

            return (
                0.90
                - penalty
            )


        return (
            SequenceMatcher(
                None,
                query,
                stem
            )
            .ratio()
        )


    # =====================================================
    # BUSCAR
    # =====================================================

    def search(
        self,
        query,
        extensions=None,
        want_directory=None,
        max_results=15
    ):

        query = normalize_file_name(
            query
        )


        if not query:
            return []


        if extensions:

            extensions = {
                extension.lower()
                if extension.startswith(".")
                else "." + extension.lower()

                for extension in extensions
            }


        results = []

        seen_paths = set()


        roots = (
            self._get_search_roots()
        )


        print("")
        print(
            f"Buscando archivo/carpeta: {query}"
        )


        for root in roots:

            if not root.exists():
                continue


            try:

                for (
                    current_root,
                    directories,
                    files
                ) in os.walk(root):


                    # =====================================
                    # EVITAR CARPETAS PESADAS
                    # =====================================

                    directories[:] = [

                        directory

                        for directory
                        in directories

                        if (
                            normalize_file_name(
                                directory
                            )
                            not in
                            self.skip_directories

                            and not
                            directory.startswith(".")
                        )
                    ]


                    candidates = []


                    if want_directory is not False:

                        candidates.extend(

                            Path(current_root)
                            / directory

                            for directory
                            in directories
                        )


                    if want_directory is not True:

                        candidates.extend(

                            Path(current_root)
                            / filename

                            for filename
                            in files
                        )


                    for candidate in candidates:

                        try:

                            candidate_key = str(
                                candidate.resolve()
                            ).lower()

                        except Exception:

                            candidate_key = str(
                                candidate
                            ).lower()


                        if candidate_key in seen_paths:
                            continue


                        seen_paths.add(
                            candidate_key
                        )


                        # =================================
                        # FILTRAR EXTENSIONES
                        # =================================

                        if (
                            extensions
                            and candidate.is_file()
                            and candidate.suffix.lower()
                            not in extensions
                        ):

                            continue


                        score = (
                            self._calculate_score(
                                query,
                                candidate
                            )
                        )


                        if score < 0.55:
                            continue


                        results.append({

                            "path":
                                candidate,

                            "name":
                                candidate.name,

                            "score":
                                score,

                            "is_directory":
                                candidate.is_dir()
                        })


            except (
                PermissionError,
                OSError
            ):

                continue


        results.sort(

            key=lambda item:
                item["score"],

            reverse=True
        )


        return results[
            :max_results
        ]


    # =====================================================
    # MEJOR RESULTADO
    # =====================================================

    def find_best(
        self,
        query,
        extensions=None,
        want_directory=None
    ):

        results = self.search(
            query=query,
            extensions=extensions,
            want_directory=want_directory,
            max_results=5
        )


        if not results:
            return None


        best = results[0]


        # No abrir una coincidencia demasiado dudosa.

        if best["score"] < 0.65:
            return None


        return best


    # =====================================================
    # ABRIR ARCHIVO
    # =====================================================

    def open_file(
        self,
        query,
        extensions=None
    ):

        result = self.find_best(
            query=query,
            extensions=extensions,
            want_directory=False
        )


        if not result:

            return (
                False,
                f"No encontré el archivo {query}"
            )


        print("")
        print(
            "======================================"
        )
        print(
            f"ARCHIVO: {result['name']}"
        )
        print(
            f"RUTA: {result['path']}"
        )
        print(
            f"COINCIDENCIA: {result['score']:.2f}"
        )
        print(
            "======================================"
        )


        return self.open_path(
            result["path"]
        )


    # =====================================================
    # ABRIR CARPETA BUSCADA
    # =====================================================

    def open_folder(
        self,
        query
    ):

        result = self.find_best(
            query=query,
            want_directory=True
        )


        if not result:

            return (
                False,
                f"No encontré la carpeta {query}"
            )


        return self.open_path(
            result["path"]
        )


    # =====================================================
    # MOSTRAR RESULTADOS
    # =====================================================

    def describe_search(
        self,
        query,
        extensions=None,
        want_directory=None
    ):

        results = self.search(
            query=query,
            extensions=extensions,
            want_directory=want_directory,
            max_results=5
        )


        if not results:

            return (
                False,
                f"No encontré nada relacionado con {query}"
            )


        print("")
        print(
            "RESULTADOS:"
        )


        lines = []


        for index, result in enumerate(
            results,
            start=1
        ):

            print(
                f"{index}. "
                f"{result['name']} "
                f"-> {result['path']}"
            )


            lines.append(
                result["name"]
            )


        return (
            True,
            "Encontré: "
            + ", ".join(lines)
        )


    # =====================================================
    # ÚLTIMO ARCHIVO DESCARGADO
    # =====================================================

    def open_latest_download(
        self
    ):

        downloads = (
            self.folders
            .get("downloads")
        )


        if (
            not downloads
            or not downloads.exists()
        ):

            return (
                False,
                "No encontré la carpeta Descargas"
            )


        newest_file = None
        newest_time = -1


        try:

            for (
                current_root,
                directories,
                files
            ) in os.walk(downloads):


                directories[:] = [

                    directory

                    for directory
                    in directories

                    if not
                    directory.startswith(".")
                ]


                for filename in files:

                    path = (
                        Path(current_root)
                        / filename
                    )


                    try:

                        modified = (
                            path.stat()
                            .st_mtime
                        )

                    except OSError:
                        continue


                    if modified > newest_time:

                        newest_time = (
                            modified
                        )

                        newest_file = (
                            path
                        )


        except Exception as e:

            print(
                "Error revisando Descargas:",
                repr(e)
            )


        if newest_file is None:

            return (
                False,
                "No encontré archivos descargados"
            )


        return self.open_path(
            newest_file
        )


file_manager = FileManager()