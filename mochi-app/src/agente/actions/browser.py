import os
import subprocess
import unicodedata
from urllib.parse import quote_plus

import yt_dlp

from config_manager import get_config
from actions.apps import get_chrome_path


class BrowserController:

    def __init__(self):

        # Perfil elegido temporalmente durante
        # la ejecución actual de Mochi.
        #
        # Si es None, se utiliza el perfil
        # predeterminado guardado en config_manager.

        self.current_profile = None


    # =====================================================
    # CONFIGURACIÓN DEL NAVEGADOR
    # =====================================================

    def _load_config(self):

        """
        Obtiene únicamente la sección browser
        de la configuración portable de Mochi.

        La configuración general se administra
        mediante config_manager y se almacena
        fuera de la carpeta del proyecto.
        """

        config = get_config()

        return (
            config
            .get(
                "browser",
                {}
            )
            or {}
        )


    # =====================================================
    # NORMALIZAR TEXTO DE PERFIL
    # =====================================================

    @staticmethod
    def _normalize_profile_text(
        value
    ):

        if value is None:

            return ""


        text = (
            str(value)
            .strip()
            .casefold()
        )


        # Eliminar acentos para permitir:
        #
        # Isaías
        # Isaias
        #
        # sin depender de cómo lo haya
        # reconocido el sistema de voz.

        text = unicodedata.normalize(
            "NFD",
            text
        )


        text = "".join(
            char
            for char in text
            if unicodedata.category(char) != "Mn"
        )


        return text


    # =====================================================
    # OBTENER PERFILES
    # =====================================================

    def get_profiles(self):

        browser = (
            self._load_config()
        )


        return (
            browser
            .get(
                "profiles",
                {}
            )
            or {}
        )


    # =====================================================
    # PERFIL PREDETERMINADO
    # =====================================================

    def get_default_profile(self):

        browser = (
            self._load_config()
        )


        return browser.get(
            "default_profile"
        )


    # =====================================================
    # PERFIL ACTIVO
    # =====================================================

    def get_active_profile(self):

        if self.current_profile:

            return (
                self.current_profile
            )


        return (
            self.get_default_profile()
        )


    # =====================================================
    # RESOLVER PERFIL
    # =====================================================

    def _resolve_profile(
        self,
        profile_name
    ):

        requested = (
            str(profile_name)
            .strip()
        )


        if not requested:

            return None


        requested_normalized = (
            self._normalize_profile_text(
                requested
            )
        )


        profiles = (
            self.get_profiles()
        )


        # =================================================
        # BUSCAR COINCIDENCIAS EXACTAS
        # =================================================

        matches = []


        for profile_id, profile in profiles.items():

            candidates = [

                profile_id,

                profile.get(
                    "directory"
                ),

                profile.get(
                    "name"
                ),

                profile.get(
                    "gaia_name"
                ),

                profile.get(
                    "email"
                )
            ]


            for candidate in candidates:

                if not candidate:

                    continue


                candidate_normalized = (
                    self._normalize_profile_text(
                        candidate
                    )
                )


                if (
                    candidate_normalized
                    == requested_normalized
                ):

                    matches.append(
                        profile_id
                    )

                    break


        # Eliminar duplicados conservando orden.

        matches = list(
            dict.fromkeys(
                matches
            )
        )


        if len(matches) == 1:

            return (
                matches[0]
            )


        # Si existen varias coincidencias,
        # intentamos priorizar el nombre visible
        # exacto del perfil.

        if len(matches) > 1:

            for profile_id in matches:

                profile = profiles.get(
                    profile_id,
                    {}
                )


                visible_name = (
                    profile.get(
                        "name"
                    )
                )


                if (
                    self._normalize_profile_text(
                        visible_name
                    )
                    == requested_normalized
                ):

                    return (
                        profile_id
                    )


        return None

    def resolve_profile(self, profile_name):
        """API pública para las capabilities; no modifica el perfil activo."""
        return self._resolve_profile(profile_name)


    # =====================================================
    # CAMBIAR CUENTA / PERFIL
    # =====================================================

    def set_profile(
        self,
        profile_name
    ):

        requested = (
            str(profile_name)
            .strip()
        )


        if not requested:

            return (
                False,
                "No se indicó ningún perfil"
            )


        profile_id = (
            self._resolve_profile(
                requested
            )
        )


        if not profile_id:

            return (
                False,
                f"No conozco el perfil {requested}"
            )


        profiles = (
            self.get_profiles()
        )


        profile = (
            profiles.get(
                profile_id,
                {}
            )
        )


        self.current_profile = (
            profile_id
        )


        display_name = (
            profile.get(
                "name"
            )
            or profile.get(
                "gaia_name"
            )
            or profile_id
        )


        return (
            True,
            f"Ahora usaré el perfil {display_name}"
        )


    # =====================================================
    # RESTABLECER PERFIL AL PREDETERMINADO
    # =====================================================

    def reset_profile(self):

        self.current_profile = None


        default_profile = (
            self.get_default_profile()
        )


        if not default_profile:

            return (
                False,
                "No existe un perfil predeterminado"
            )


        profiles = (
            self.get_profiles()
        )


        profile = (
            profiles.get(
                default_profile,
                {}
            )
        )


        display_name = (
            profile.get(
                "name"
            )
            or profile.get(
                "gaia_name"
            )
            or default_profile
        )


        return (
            True,
            f"Volví al perfil predeterminado {display_name}"
        )


    # =====================================================
    # OBTENER DIRECTORIO DE CHROME
    # =====================================================

    def _get_profile_directory(self):

        profile_id = (
            self.get_active_profile()
        )


        if not profile_id:

            raise RuntimeError(
                "No existe un perfil predeterminado de navegador"
            )


        profiles = (
            self.get_profiles()
        )


        profile = (
            profiles.get(
                profile_id
            )
        )


        if not profile:

            raise RuntimeError(
                f"No existe el perfil {profile_id}"
            )


        return (
            profile.get(
                "directory"
            )
            or profile_id
        )


    # =====================================================
    # INFORMACIÓN DEL PERFIL ACTIVO
    # =====================================================

    def get_active_profile_info(self):

        profile_id = (
            self.get_active_profile()
        )


        if not profile_id:

            return None


        profiles = (
            self.get_profiles()
        )


        profile = (
            profiles.get(
                profile_id
            )
        )


        if not profile:

            return None


        return {

            "id":
                profile_id,

            "directory":
                profile.get(
                    "directory"
                ),

            "name":
                profile.get(
                    "name"
                ),

            "email":
                profile.get(
                    "email"
                ),

            "gaia_name":
                profile.get(
                    "gaia_name"
                )
        }


    # =====================================================
    # ABRIR URL EN CHROME REAL
    # =====================================================

    def open_url(
        self,
        url
    ):

        url = (
            str(url)
            .strip()
        )


        if not url:

            return (
                False,
                "No se indicó ninguna URL"
            )


        chrome = (
            get_chrome_path()
        )


        if not chrome:

            return (
                False,
                "No encontré Google Chrome"
            )


        try:

            profile_directory = (
                self._get_profile_directory()
            )


        except Exception as e:

            return (
                False,
                f"No pude resolver el perfil de Chrome: {e}"
            )


        active_profile = (
            self.get_active_profile_info()
        )


        print("")

        print(
            "=================================="
        )


        if active_profile:

            print(
                "Perfil Mochi: "
                f"{active_profile.get('name') or active_profile.get('id')}"
            )

            print(
                f"Profile ID: {active_profile.get('id')}"
            )

        else:

            print(
                "Perfil Mochi: desconocido"
            )


        print(
            f"Chrome directory: {profile_directory}"
        )


        print(
            f"URL: {url}"
        )


        print(
            "=================================="
        )


        try:

            arguments = [
                chrome,
                f"--profile-directory={profile_directory}",
            ]

            # Linux: sin este flag Chromium no publica el
            # contenido de las páginas por accesibilidad
            # (web_inspect / web_act).
            if os.name != "nt":
                arguments.append(
                    "--force-renderer-accessibility"
                )

            arguments.append(url)

            subprocess.Popen(
                arguments
            )


            return (
                True,
                "Chrome abierto"
            )


        except Exception as e:

            print(
                "Error abriendo Chrome:",
                repr(e)
            )


            return (
                False,
                "No pude abrir Chrome"
            )

    def open_url_for_profile(self, url, profile=None):
        """Abre una URL con un perfil explícito sin alterar la selección global."""
        requested_profile = profile
        if profile:
            profile_id = self._resolve_profile(profile)
            if not profile_id:
                return {"ok": False, "message": f"No encontré el perfil de navegador '{profile}'.", "requested_profile": profile}
        else:
            profile_id = self.get_default_profile()

        profiles = self.get_profiles()
        selected = profiles.get(profile_id, {})
        # Sin perfiles detectados (navegador recien instalado o nunca
        # abierto): Chrome crea "Default" al abrirlo con ese perfil.
        directory = selected.get("directory") or profile_id or "Default"
        if not directory:
            return {"ok": False, "message": "No hay un perfil predeterminado de navegador configurado."}
        chrome = get_chrome_path()
        if not chrome:
            return {"ok": False, "message": "No encontré Google Chrome."}
        try:
            arguments = [chrome, f"--profile-directory={directory}"]
            if self._load_config().get("force_renderer_accessibility", True):
                arguments.append("--force-renderer-accessibility")
            if requested_profile is not None:
                # When another Chrome profile owns the last active window,
                # a bare URL launch can be absorbed by that window and ignore
                # --profile-directory.  A new profile window preserves the
                # explicitly requested signed-in account; subsequent calls
                # are still reused by Mochi's verified session cache.
                arguments.append("--new-window")
            arguments.append(str(url).strip())
            subprocess.Popen(arguments)
        except Exception as error:
            return {"ok": False, "message": f"No pude abrir Chrome: {error}"}
        self.current_profile = profile_id
        return {
            "ok": True,
            "message": "Destino web abierto.",
            "url": str(url).strip(),
            "profile_id": profile_id,
            "profile_name": selected.get("name") or selected.get("gaia_name") or profile_id,
            "profile_directory": directory,
        }


    # =====================================================
    # GOOGLE
    #
    # Adaptador específico.
    # No constituye la capacidad base del navegador.
    # =====================================================

    def open_google(self):

        success, _ = (
            self.open_url(
                "https://www.google.com"
            )
        )


        if success:

            return (
                True,
                "Google abierto"
            )


        return (
            False,
            "No pude abrir Google"
        )


    # =====================================================
    # YOUTUBE
    #
    # Adaptador específico.
    # La arquitectura general de Mochi no debe
    # depender de este método.
    # =====================================================

    def open_youtube(self):

        success, _ = (
            self.open_url(
                "https://www.youtube.com"
            )
        )


        if success:

            return (
                True,
                "YouTube abierto"
            )


        return (
            False,
            "No pude abrir YouTube"
        )


    # =====================================================
    # BUSCAR EN YOUTUBE
    #
    # Adaptador específico opcional.
    # =====================================================

    def search_youtube(
        self,
        query
    ):

        query = (
            str(query)
            .strip()
        )


        if not query:

            return (
                False,
                "No se indicó qué buscar en YouTube"
            )


        encoded = (
            quote_plus(
                query
            )
        )


        url = (
            "https://www.youtube.com/"
            "results?search_query="
            f"{encoded}"
        )


        success, _ = (
            self.open_url(
                url
            )
        )


        if success:

            return (
                True,
                f"Buscando {query} en YouTube"
            )


        return (
            False,
            "No pude buscar en YouTube"
        )


    # =====================================================
    # ENCONTRAR PRIMER VIDEO DE YOUTUBE
    #
    # Adaptador específico opcional.
    # =====================================================

    def _find_youtube_video(
        self,
        query
    ):

        query = (
            str(query)
            .strip()
        )


        if not query:

            return None


        options = {

            "quiet":
                True,

            "no_warnings":
                True,

            "skip_download":
                True,

            "extract_flat":
                True,

            "noplaylist":
                True
        }


        with yt_dlp.YoutubeDL(
            options
        ) as ydl:

            info = (
                ydl.extract_info(
                    f"ytsearch1:{query}",
                    download=False
                )
            )


        entries = (
            info.get(
                "entries",
                []
            )
            or []
        )


        if not entries:

            return None


        video = (
            entries[0]
        )


        video_id = (
            video.get(
                "id"
            )
        )


        if not video_id:

            return None


        title = (
            video.get(
                "title",
                query
            )
        )


        return {

            "title":
                title,

            "url":
                (
                    "https://www.youtube.com/"
                    f"watch?v={video_id}"
                )
        }


    # =====================================================
    # BUSCAR Y REPRODUCIR VIDEO
    #
    # Adaptador específico opcional.
    # =====================================================

    def play_youtube_video(
        self,
        query
    ):

        query = (
            str(query)
            .strip()
        )


        if not query:

            return (
                False,
                "No se indicó qué video reproducir"
            )


        try:

            print("")

            print(
                f"Buscando video: {query}"
            )


            result = (
                self._find_youtube_video(
                    query
                )
            )


            if not result:

                return (
                    False,
                    "No encontré ningún video"
                )


            print(
                f"Encontrado: {result['title']}"
            )


            print(
                result["url"]
            )


            success, _ = (
                self.open_url(
                    result["url"]
                )
            )


            if not success:

                return (
                    False,
                    (
                        "Encontré el video "
                        "pero no pude abrir Chrome"
                    )
                )


            return (
                True,
                f"Reproduciendo: {result['title']}"
            )


        except Exception as e:

            print(
                "Error buscando video:"
            )

            print(
                repr(e)
            )


            return (
                False,
                "No pude encontrar el video"
            )


    # =====================================================
    # SHUTDOWN
    #
    # Actualmente BrowserController no mantiene
    # procesos propios que deban cerrarse.
    # =====================================================

    def shutdown(self):

        pass
