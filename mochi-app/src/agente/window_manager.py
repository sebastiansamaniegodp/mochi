import os
from difflib import SequenceMatcher

if os.name == "nt":
    import win32con
    import win32gui

else:
    # Linux: mismas funciones, implementadas sobre KWin.
    from linux_platform import win32_compat as win32con
    from linux_platform import win32_compat as win32gui

from discovery.windows import (
    discover_windows,
    normalize_window_name
)


# "cerrá el Dolphin": el artículo no es parte del nombre. Los
# nombres genéricos ("el explorador de archivos") los resuelve el
# LLM con la lista de ventanas abiertas (ver AgentLoop.run).
ARTICLES = ("el ", "la ", "los ", "las ", "al ", "del ")


class WindowManager:

    FUZZY_MATCH_THRESHOLD = 0.84
    FUZZY_MATCH_MARGIN = 0.08

    def __init__(self):
        self._last_match = None


    # =====================================================
    # OBTENER VENTANAS
    # =====================================================

    def get_windows(self):

        return discover_windows()


    @staticmethod
    def _distinct(windows):
        """Remove duplicate handles while preserving the discovery order."""

        seen = set()
        result = []
        for window in windows:
            handle = window.get("hwnd")
            key = handle if handle is not None else id(window)
            if key in seen:
                continue
            seen.add(key)
            result.append(window)
        return result


    @staticmethod
    def _prefer_application_windows(windows):
        """Drop duplicate UWP frame hosts when the real app is also visible.

        Windows can enumerate one title twice: once for the application
        process and once for ``ApplicationFrameHost.exe``.  They represent a
        single user-visible window, so treating them as two choices makes
        ordinary commands such as "close Calculator" falsely ambiguous.
        The host is removed only when a non-host candidate has the exact same
        normalized title; unrelated frame-host windows remain untouched.
        """

        windows = list(windows or [])
        native_titles = {
            window.get("normalized_title")
            for window in windows
            if window.get("normalized_process") != "applicationframehost"
        }
        return [
            window
            for window in windows
            if not (
                window.get("normalized_process") == "applicationframehost"
                and window.get("normalized_title") in native_titles
            )
        ]


    def _one_or_ambiguous(self, query, candidates):
        candidates = self._prefer_application_windows(
            self._distinct(candidates)
        )
        if len(candidates) == 1:
            self._last_match = {"status": "matched", "window": candidates[0]}
            return candidates[0]
        if candidates:
            self._last_match = {"status": "ambiguous", "query": query, "windows": candidates}
        return None


    def last_match_message(self, query):
        """Explain why a single-window operation was refused."""

        if self._last_match and self._last_match.get("status") == "ambiguous":
            names = [
                window.get("title") or window.get("process") or "sin título"
                for window in self._last_match.get("windows", [])[:3]
            ]
            suffix = ", ".join(names)
            if len(self._last_match.get("windows", [])) > len(names):
                suffix += ", …"
            return (
                f"Encontré varias ventanas que coinciden con {query} "
                f"({suffix}). Indicá una parte más específica del título."
            )
        return f"No encontré una ventana {self._de(query)}"


    @staticmethod
    def _de(query):
        """'de el explorador' -> 'del explorador'."""

        text = str(query).strip()
        if text.lower().startswith("el "):
            return "del " + text[3:]
        return "de " + text


    # =====================================================
    # ALIAS DEL NOMBRE PEDIDO
    # =====================================================

    @staticmethod
    def _alternative_queries(query):
        """Nombres a probar si ``query`` no encontró ninguna ventana."""

        normalized = normalize_window_name(query)
        for article in ARTICLES:
            if normalized.startswith(article):
                return [normalized[len(article):]]
        return []


    def last_match_was_ambiguous(self):

        return bool(
            self._last_match
            and self._last_match.get("status") == "ambiguous"
        )


    # =====================================================
    # BUSCAR VENTANA
    # =====================================================

    def find(
        self,
        query
    ):

        window = self._find_by_name(query)
        if window or self._last_match:
            return window

        for alternative in self._alternative_queries(query):
            window = self._find_by_name(alternative)
            if window or self._last_match:
                if self._last_match.get("status") == "ambiguous":
                    self._last_match["query"] = query
                return window

        matches = self._find_by_description(query)
        if matches:
            return self._one_or_ambiguous(query, matches)

        return None


    def _find_by_name(
        self,
        query
    ):

        original_query = query
        query = normalize_window_name(query)
        self._last_match = None


        if not query:
            return None


        windows = self.get_windows()


        exact_matches = [
            window for window in windows
            if window["normalized_process"] == query
            or window["normalized_title"] == query
        ]
        if exact_matches:
            return self._one_or_ambiguous(original_query, exact_matches)

        contained_matches = [
            window for window in windows
            if query in window["normalized_process"]
            or query in window["normalized_title"]
        ]
        if contained_matches:
            return self._one_or_ambiguous(original_query, contained_matches)

        ranked = sorted(
            (
                (
                    max(
                        SequenceMatcher(None, query, window["normalized_process"]).ratio(),
                        SequenceMatcher(None, query, window["normalized_title"]).ratio(),
                    ),
                    window,
                )
                for window in windows
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        if not ranked or ranked[0][0] < self.FUZZY_MATCH_THRESHOLD:
            return None

        best_score = ranked[0][0]
        candidates = [
            window for score, window in ranked
            if best_score - score < self.FUZZY_MATCH_MARGIN
        ]
        return self._one_or_ambiguous(original_query, candidates)

    # =====================================================
    # BUSCAR TODAS LAS VENTANAS COINCIDENTES
    # =====================================================

    def find_all(
        self,
        query
    ):

        windows = self._find_all_by_name(query)
        if windows:
            return windows

        for alternative in self._alternative_queries(query):
            windows = self._find_all_by_name(alternative)
            if windows:
                return windows

        matches = self._prefer_application_windows(
            self._find_by_description(query)
        )
        if len({window["normalized_process"] for window in matches}) == 1:
            return matches

        return []


    # =====================================================
    # BUSCAR POR LO QUE ES LA APLICACIÓN
    # =====================================================

    def _find_by_description(
        self,
        query
    ):
        """Ventanas cuya aplicación se describe como ``query``.

        En Linux cada ventana trae el nombre genérico y las palabras
        clave de su .desktop ("Gestor de archivos", "explorador"),
        así "el explorador de archivos" encuentra Dolphin. En Windows
        esos datos no existen y no encuentra nada.
        """

        queries = [normalize_window_name(query)]
        queries += self._alternative_queries(query)
        queries = [q for q in queries if q]
        if not queries:
            return []

        exact = []
        approximate = []

        for window in self.get_windows():
            descriptions = [
                normalize_window_name(text)
                for text in (
                    [window.get("generic_name"), window.get("app_name")]
                    + list(window.get("keywords") or [])
                )
                if text
            ]
            if not descriptions:
                continue

            # "el navegador" es el "Navegador web".
            if any(
                description == q or description.startswith(q + " ")
                for q in queries
                for description in descriptions
            ):
                exact.append(window)
                continue

            score = max(
                SequenceMatcher(None, q, description).ratio()
                for q in queries
                for description in descriptions
            )
            if score >= self.FUZZY_MATCH_THRESHOLD:
                approximate.append(window)

        return self._distinct(exact or approximate)


    def _find_all_by_name(
        self,
        query
    ):

        query = normalize_window_name(
            query
        )


        if not query:

            return []


        windows = (
            self.get_windows()
        )


        # =================================================
        # 1. PROCESO EXACTO
        # =================================================

        matches = [

            window

            for window in windows

            if (
                window[
                    "normalized_process"
                ]
                ==
                query
            )
        ]


        if matches:
            return self._prefer_application_windows(matches)


        # =================================================
        # 2. TÍTULO EXACTO
        # =================================================

        matches = [

            window

            for window in windows

            if (
                window[
                    "normalized_title"
                ]
                ==
                query
            )
        ]


        if matches:
            return self._prefer_application_windows(matches)


        # =================================================
        # 3. QUERY DENTRO DEL PROCESO
        # =================================================

        matches = [

            window

            for window in windows

            if (
                query
                in window[
                    "normalized_process"
                ]
            )
        ]


        if matches:
            # "cerrar todas" may include several windows of one app, but a
            # partial name must not merge Chrome with Chromium, for example.
            matches = self._prefer_application_windows(matches)
            if len({window["normalized_process"] for window in matches}) == 1:
                return matches
            return []


        # =================================================
        # 4. QUERY DENTRO DEL TÍTULO
        # =================================================

        matches = [

            window

            for window in windows

            if (
                query
                in window[
                    "normalized_title"
                ]
            )
        ]


        if matches:
            matches = self._prefer_application_windows(matches)
            if len({window["normalized_process"] for window in matches}) == 1:
                return matches
            return []


        # =================================================
        # 5. COINCIDENCIA APROXIMADA
        # =================================================

        approximate_matches = []


        for window in windows:

            process_score = (
                SequenceMatcher(
                    None,
                    query,
                    window[
                        "normalized_process"
                    ]
                )
                .ratio()
            )


            title_score = (
                SequenceMatcher(
                    None,
                    query,
                    window[
                        "normalized_title"
                    ]
                )
                .ratio()
            )


            score = max(
                process_score,
                title_score
            )


            # Mantenemos el mismo umbral alto que find() para evitar operar
            # sobre ventanas equivocadas.

            if score >= self.FUZZY_MATCH_THRESHOLD:

                approximate_matches.append(
                    (
                        score,
                        window
                    )
                )


        approximate_matches.sort(
            key=lambda item: item[0],
            reverse=True
        )


        if not approximate_matches:
            return []

        best_score = approximate_matches[0][0]
        clearly_best = [
            window
            for score, window in approximate_matches
            if best_score - score < self.FUZZY_MATCH_MARGIN
        ]
        clearly_best = self._prefer_application_windows(clearly_best)
        if len({window["normalized_process"] for window in clearly_best}) != 1:
            return []
        return clearly_best

    # =====================================================
    # RESTAURAR
    # =====================================================

    def _restore(
        self,
        hwnd
    ):

        try:

            if win32gui.IsIconic(hwnd):

                win32gui.ShowWindow(
                    hwnd,
                    win32con.SW_RESTORE
                )

        except Exception:
            pass


    # =====================================================
    # TRAER AL FRENTE
    # =====================================================

    def focus(
        self,
        query
    ):

        window = self.find(
            query
        )


        if not window:

            return (
                False,
                f"No encontré una ventana {self._de(query)}"
            )


        try:

            hwnd = window[
                "hwnd"
            ]


            self._restore(hwnd)


            win32gui.ShowWindow(
                hwnd,
                win32con.SW_SHOW
            )


            win32gui.SetForegroundWindow(
                hwnd
            )


            return (
                True,
                f"Mostrando {window['title']}"
            )


        except Exception as e:

            print(
                "Error enfocando ventana:",
                repr(e)
            )


            return (
                False,
                f"No pude traer {query} al frente"
            )


    # =====================================================
    # MINIMIZAR
    # =====================================================

    def minimize(
        self,
        query
    ):

        window = self.find(
            query
        )


        if not window:

            return (
                False,
                f"No encontré una ventana {self._de(query)}"
            )


        try:

            win32gui.ShowWindow(
                window["hwnd"],
                win32con.SW_MINIMIZE
            )


            return (
                True,
                f"Minimizando {window['title']}"
            )


        except Exception as e:

            print(
                "Error minimizando:",
                repr(e)
            )


            return (
                False,
                f"No pude minimizar {query}"
            )


    # =====================================================
    # MAXIMIZAR
    # =====================================================

    def maximize(
        self,
        query
    ):

        window = self.find(
            query
        )


        if not window:

            return (
                False,
                f"No encontré una ventana {self._de(query)}"
            )


        try:

            win32gui.ShowWindow(
                window["hwnd"],
                win32con.SW_MAXIMIZE
            )


            try:

                win32gui.SetForegroundWindow(
                    window["hwnd"]
                )

            except Exception:
                pass


            return (
                True,
                f"Maximizando {window['title']}"
            )


        except Exception as e:

            print(
                "Error maximizando:",
                repr(e)
            )


            return (
                False,
                f"No pude maximizar {query}"
            )


    # =====================================================
    # CERRAR
    # =====================================================

    def close(
        self,
        query
    ):

        window = self.find(
            query
        )


        if not window:

            return (
                False,
                f"No encontré una ventana {self._de(query)}"
            )


        try:

            print("")
            print(
                "======================================"
            )

            print(
                f"CERRANDO: {window['title']}"
            )

            print(
                f"Proceso: {window['process']}"
            )

            print(
                f"PID: {window['pid']}"
            )

            print(
                "======================================"
            )


            # Solicitud de cierre normal.
            # NO mata el proceso.

            win32gui.PostMessage(
                window["hwnd"],
                win32con.WM_CLOSE,
                0,
                0
            )


            return (
                True,
                f"Cerrando {window['title']}"
            )


        except Exception as e:

            print(
                "Error cerrando ventana:",
                repr(e)
            )


            return (
                False,
                f"No pude cerrar {query}"
            )

        # =====================================================
    # CERRAR TODAS LAS VENTANAS COINCIDENTES
    # =====================================================

    def close_all(
        self,
        query
    ):

        windows = (
            self.find_all(
                query
            )
        )


        if not windows:

            return (
                False,
                f"No encontré ventanas de {query}"
            )


        closed = 0
        errors = []


        for window in windows:

            try:

                print("")
                print(
                    "======================================"
                )

                print(
                    f"CERRANDO: {window['title']}"
                )

                print(
                    f"Proceso: {window['process']}"
                )

                print(
                    f"PID: {window['pid']}"
                )

                print(
                    "======================================"
                )


                # Cierre normal de la ventana.
                # No mata el proceso.

                win32gui.PostMessage(
                    window["hwnd"],
                    win32con.WM_CLOSE,
                    0,
                    0
                )


                closed += 1


            except Exception as e:

                errors.append(
                    {
                        "title":
                            window.get(
                                "title"
                            ),

                        "error":
                            str(e)
                    }
                )


        if closed == 0:

            return (
                False,
                f"No pude cerrar las ventanas de {query}"
            )


        if errors:

            return (
                True,
                (
                    f"Cerré {closed} ventana(s) de {query}, "
                    f"pero {len(errors)} no pudieron cerrarse."
                )
            )


        return (
            True,
            f"Cerré {closed} ventana(s) de {query}"
        )


    # =====================================================
    # LISTAR
    # =====================================================

    def list_windows(
        self
    ):

        windows = (
            self.get_windows()
        )


        result = []


        seen = set()


        for window in windows:

            key = (
                window["process"],
                window["title"]
            )


            if key in seen:
                continue


            seen.add(key)

            result.append(
                window
            )


        return result


    # =====================================================
    # RESUMEN PARA MOCHI
    # =====================================================

    def describe_open_windows(
        self
    ):

        windows = (
            self.list_windows()
        )


        if not windows:

            return (
                False,
                "No encontré ventanas abiertas"
            )


        names = []


        for window in windows:

            process = (
                window[
                    "normalized_process"
                ]
            )


            if process:

                names.append(
                    process
                )


        names = list(
            dict.fromkeys(names)
        )


        # Evitar una respuesta gigantesca
        names = names[:15]


        return (
            True,
            "Programas abiertos: "
            + ", ".join(names)
        )


window_manager = WindowManager()
