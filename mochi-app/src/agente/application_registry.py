import json
import os
import subprocess
import time
from difflib import SequenceMatcher
from pathlib import Path

from discovery.applications import (
    discover_applications,
    normalize_name
)
from linux_platform import system_open
from linux_platform.applications import launch_desktop_entry
from mochi_environment import environment


DEFAULT_APPLICATION_CACHE_TTL_SECONDS = 600
APPLICATION_CACHE_VERSION = 2

# "abrí la consola": el artículo no es parte del nombre.
ARTICLES = ("el ", "la ", "los ", "las ", "al ", "del ")


class ApplicationRegistry:

    def __init__(
        self,
        cache_path=None,
        ttl_seconds=DEFAULT_APPLICATION_CACHE_TTL_SECONDS,
        clock=time.time,
        discoverer=None,
    ):

        self.applications = []
        self._loaded = False
        self._cache_path = (
            Path(cache_path)
            if cache_path is not None
            else environment.cache_dir / "applications.json"
        )
        self._ttl_seconds = max(60, int(ttl_seconds))
        self._clock = clock
        self._discoverer = discoverer or discover_applications

    def _ensure_loaded(self):
        """Discover installed apps only when an app tool actually needs them.

        Browser-only agent runs should not enumerate the Start menu and three
        registry locations simply because the tool registry was imported.
        """
        if not self._loaded:
            cached_applications = self._read_cache()
            if cached_applications is not None:
                self.applications = cached_applications
                self._loaded = True
            else:
                self.refresh()


    @staticmethod
    def _deduplicate(applications):
        """Normalize names and retain the most direct launch method per app."""

        unique = {}
        priority = {
            "desktop_entry": 3,
            "shortcut": 3,
            "executable": 2,
            "start_app": 1,
        }
        for app in applications:
            if not isinstance(app, dict):
                continue
            normalized_name = normalize_name(app.get("name"))
            if not normalized_name:
                continue
            canonical = {**app, "normalized_name": normalized_name}
            existing = unique.get(normalized_name)
            if existing is None or priority.get(canonical.get("type"), 0) > priority.get(existing.get("type"), 0):
                unique[normalized_name] = canonical
        return list(unique.values())


    def _read_cache(self):
        try:
            with self._cache_path.open("r", encoding="utf-8") as file:
                record = json.load(file)
            expires_at = float(record.get("expires_at", 0))
        except (OSError, ValueError, TypeError, AttributeError):
            return None

        if (
            not isinstance(record, dict)
            or record.get("version") != APPLICATION_CACHE_VERSION
            or expires_at <= self._clock()
            or not isinstance(record.get("applications"), list)
        ):
            return None
        return self._deduplicate(record["applications"])


    def _write_cache(self):
        record = {
            "version": APPLICATION_CACHE_VERSION,
            "expires_at": self._clock() + self._ttl_seconds,
            # Local launcher metadata only; no application state or content.
            "applications": self.applications,
        }
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._cache_path.with_suffix(".tmp")
            with temporary.open("w", encoding="utf-8") as file:
                json.dump(record, file, ensure_ascii=False, separators=(",", ":"))
            temporary.replace(self._cache_path)
        except OSError:
            # A cache failure must never block application discovery or launch.
            pass


    # =====================================================
    # ACTUALIZAR CATÁLOGO
    # =====================================================

    def refresh(self):

        self.applications = self._deduplicate(self._discoverer())
        self._write_cache()

        self._loaded = True


        print(
            f"Aplicaciones únicas: "
            f"{len(self.applications)}"
        )


    # =====================================================
    # BUSCAR APP
    # =====================================================

    def find(
        self,
        query
    ):

        self._ensure_loaded()

        query_normalized = (
            normalize_name(query)
        )


        if not query_normalized:
            return None


        app = self._find_by_name(query_normalized)

        if not app:
            for article in ARTICLES:
                if query_normalized.startswith(article):
                    app = self._find_by_name(query_normalized[len(article):])
                    break

        return app or self._find_by_keywords(query_normalized)


    @staticmethod
    def _is_kde():

        return "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper()


    @staticmethod
    def _default_browser():
        """Id del .desktop del navegador predeterminado (Linux)."""

        try:
            result = subprocess.run(
                ["xdg-settings", "get", "default-web-browser"],
                capture_output=True,
                text=True,
                timeout=3,
            )
        except Exception:
            return ""
        return result.stdout.strip().removesuffix(".desktop")


    def _find_by_keywords(
        self,
        query_normalized
    ):
        """Por las palabras clave del .desktop (Linux).

        "consola" es Konsole y "explorador de archivos" se parece a
        "exploración de archivos", de Dolphin.
        """

        queries = [query_normalized]
        for article in ARTICLES:
            if query_normalized.startswith(article):
                queries.append(query_normalized[len(article):])
                break

        exact = {}
        approximate = {}

        for app in self.applications:
            keywords = app.get("keywords") or []
            if not keywords:
                continue
            # "el navegador" es el "Navegador web" (nombre genérico).
            generic = (
                app.get("source") == "desktop_entry_generic"
                and any(
                    app["normalized_name"].startswith(q + " ")
                    for q in queries
                )
            )
            if generic or any(q in keywords for q in queries):
                exact.setdefault(app.get("target"), app)
                continue
            score = max(
                SequenceMatcher(None, q, keyword).ratio()
                for q in queries
                for keyword in keywords
            )
            if score >= 0.84:
                approximate.setdefault(app.get("target"), app)

        candidates = list((exact or approximate).values())
        if len(candidates) == 1:
            return candidates[0]

        # Varios navegadores: el predeterminado.
        browser = self._default_browser() if len(candidates) > 1 else ""
        for app in candidates:
            if browser and app.get("target") == browser:
                return app

        # Varias (Konsole y GNOME Terminal): la del escritorio en uso.
        if self._is_kde():
            kde = [
                app for app in candidates
                if str(app.get("target", "")).startswith("org.kde.")
            ]
            if len(kde) == 1:
                return kde[0]

        return None


    def _find_by_name(
        self,
        query_normalized
    ):

        # ---------------------------------------------
        # 1. Coincidencia exacta
        # ---------------------------------------------

        for app in self.applications:

            if (
                app["normalized_name"]
                == query_normalized
            ):

                return app


        # ---------------------------------------------
        # 2. El nombre buscado está contenido
        # ---------------------------------------------

        contained = []


        for app in self.applications:

            app_name = app[
                "normalized_name"
            ]


            if (
                query_normalized
                in app_name
            ):

                contained.append(
                    app
                )


        if len(contained) == 1:

            return contained[0]


        # Several contained names (for example "visual studio") are not
        # enough evidence to launch one arbitrary program.


        # ---------------------------------------------
        # 3. Coincidencia aproximada
        # ---------------------------------------------

        ranked = sorted(
            (
                (
                    SequenceMatcher(
                        None,
                        query_normalized,
                        app["normalized_name"]
                    ).ratio(),
                    app,
                )
                for app in self.applications
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        best_score, best_app = ranked[0] if ranked else (0.0, None)
        runner_up_score = ranked[1][0] if len(ranked) > 1 else 0.0


        # Evitar lanzar programas incorrectos
        # cuando la coincidencia es mala.

        if (
            best_app is not None
            and
            best_score >= 0.82
            and (len(ranked) == 1 or best_score - runner_up_score >= 0.08)
        ):

            return best_app


        return None


    # =====================================================
    # EJECUTAR
    # =====================================================

    def launch(
        self,
        query
    ):

        app = self.find(
            query
        )


        if app is None:

            return (
                False,
                f"No encontré una aplicación llamada {query}"
            )


        print("")
        print(
            "======================================"
        )

        print(
            f"APP SOLICITADA: {query}"
        )

        print(
            f"ENCONTRADA: {app['name']}"
        )

        print(
            f"TIPO: {app['type']}"
        )

        print(
            f"ORIGEN: {app['source']}"
        )

        print(
            "======================================"
        )


        try:

            if app["type"] == "shortcut":

                system_open(
                    app["target"]
                )


            elif app["type"] == "desktop_entry":

                launch_desktop_entry(
                    app["target"]
                )


            elif app["type"] == "executable":

                subprocess.Popen(
                    [
                        app["target"]
                    ]
                )


            elif app["type"] == "start_app":

                subprocess.Popen(
                    [
                        "explorer.exe",
                        (
                            "shell:AppsFolder\\"
                            + app["target"]
                        )
                    ]
                )


            else:

                return (
                    False,
                    "No sé cómo ejecutar esa aplicación"
                )


            return (
                True,
                f"Abriendo {app['name']}"
            )


        except Exception as e:

            print(
                "Error ejecutando aplicación:"
            )

            print(
                repr(e)
            )


            return (
                False,
                f"No pude abrir {app['name']}"
            )


    # =====================================================
    # LISTAR
    # =====================================================

    def list_names(self):

        self._ensure_loaded()

        return sorted(
            app["name"]
            for app in self.applications
        )


application_registry = ApplicationRegistry()
