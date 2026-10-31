import ctypes
import asyncio
import os
from difflib import SequenceMatcher
from datetime import datetime, timezone
import time
import unicodedata

try:
    import comtypes
    from pycaw.pycaw import AudioUtilities

    PYCAW_AVAILABLE = True

except Exception:
    PYCAW_AVAILABLE = False

try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus,
    )

    WINDOWS_MEDIA_SESSION_AVAILABLE = True
except Exception:
    WINDOWS_MEDIA_SESSION_AVAILABLE = False


# =========================================================
# TECLAS MULTIMEDIA DE WINDOWS
# =========================================================

VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF

VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3

KEYEVENTF_KEYUP = 0x0002


class MediaController:

    def __init__(self):

        self.user32 = ctypes.windll.user32


    # =====================================================
    # ENVIAR TECLA MULTIMEDIA
    # =====================================================

    def _press_key(self, virtual_key):

        try:

            self.user32.keybd_event(
                virtual_key,
                0,
                0,
                0
            )

            time.sleep(0.03)

            self.user32.keybd_event(
                virtual_key,
                0,
                KEYEVENTF_KEYUP,
                0
            )

            return True

        except Exception as e:

            print(
                "Error enviando tecla multimedia:",
                repr(e)
            )

            return False

    # =====================================================
    # SESIÓN MULTIMEDIA OBSERVABLE DE WINDOWS
    # =====================================================

    @staticmethod
    def _normal(value):
        text = unicodedata.normalize("NFD", str(value or "").casefold())
        return "".join(char for char in text if unicodedata.category(char) != "Mn")

    @classmethod
    def _session_matches_title(cls, source, media_title, expected_title):
        """Keep a global media session scoped to the visible page when possible."""
        if not expected_title:
            return True
        expected = cls._normal(expected_title)
        media = cls._normal(media_title)
        source = cls._normal(source)
        ignored = {
            "chrome", "google", "youtube", "netflix", "twitch", "microsoft",
            "edge", "mozilla", "firefox",
        }
        expected_tokens = [
            token for token in expected.replace("-", " ").split()
            if len(token) > 2 and not token.isdigit() and token not in ignored
        ]
        media_tokens = [
            token for token in media.replace("-", " ").split()
            if len(token) > 2 and not token.isdigit() and token not in ignored
        ]
        if expected_tokens and media_tokens:
            expected_core = " ".join(expected_tokens)
            media_core = " ".join(media_tokens)
            if media_core in expected_core or expected_core in media_core:
                return True
            # Shared artist/provider words are not enough to identify a
            # particular item (two videos from the same channel may coexist).
            # Require the overall title to remain strongly similar.
            return SequenceMatcher(None, expected_core, media_core).ratio() >= 0.72
        # A provider-only page title (for example just "Netflix") cannot
        # distinguish two media tabs from the same browser.  Refuse it rather
        # than controlling whichever browser session Windows last preferred.
        return False

    @classmethod
    def _unique_exact_browser_session_matches(
        cls,
        source,
        media_title,
        expected_title,
        session_summaries,
    ):
        """Allow a provider-only title only when Windows makes it unique.

        Some protected players publish no episode/title metadata and expose
        only their provider name (for example ``Netflix``). Exact equality,
        a browser source, and one matching Windows session together are
        sufficient to control the active provider without weakening the
        multi-tab guard used for normal video titles.
        """
        source_name = cls._normal(source)
        media_name = cls._normal(media_title).strip()
        expected_name = cls._normal(expected_title).strip()
        browser_source = any(
            token in source_name
            for token in {"chrome", "msedge", "edge", "firefox", "brave", "opera"}
        )
        if not browser_source or not media_name or media_name != expected_name:
            return False
        matching = [
            item for item in (session_summaries or [])
            if cls._normal(item.get("source")) == source_name
            and cls._normal(item.get("title")).strip() == media_name
        ]
        return len(matching) == 1

    async def _session_context(self, expected_title=None):
        if not WINDOWS_MEDIA_SESSION_AVAILABLE:
            return None, {
                "ok": False,
                "error": "windows_media_session_unavailable",
                "message": "El runtime de sesión multimedia de Windows no está instalado.",
            }
        manager = await GlobalSystemMediaTransportControlsSessionManager.request_async()
        session = manager.get_current_session()
        if session is None:
            return None, {
                "ok": False,
                "error": "media_session_not_found",
                "message": "Windows no publica una sesión multimedia activa.",
            }
        source = str(session.source_app_user_model_id or "")
        try:
            properties = await session.try_get_media_properties_async()
            title = str(properties.title or "")
            artist = str(properties.artist or "")
        except Exception:
            title = ""
            artist = ""
        title_matches = self._session_matches_title(source, title, expected_title)
        match_scope = "content_title" if title_matches else None
        if not title_matches:
            summaries = []
            try:
                for candidate in manager.get_sessions():
                    candidate_properties = await candidate.try_get_media_properties_async()
                    summaries.append({
                        "source": str(candidate.source_app_user_model_id or ""),
                        "title": str(candidate_properties.title or ""),
                    })
            except Exception:
                summaries = []
            title_matches = self._unique_exact_browser_session_matches(
                source,
                title,
                expected_title,
                summaries,
            )
            if title_matches:
                match_scope = "unique_exact_browser_provider"
        if not title_matches:
            return None, {
                "ok": False,
                "error": "media_session_mismatch",
                "message": "La sesión multimedia de Windows no corresponde a la página activa.",
                "source": source,
                "title": title,
            }
        return session, {
            "source": source,
            "title": title,
            "artist": artist,
            "session_match": match_scope,
        }

    @staticmethod
    def _playback_status_name(status):
        name = getattr(status, "name", None)
        return str(name or status).casefold()

    @classmethod
    def _effective_position_seconds(cls, playback, timeline):
        position = float(timeline.position.total_seconds())
        if cls._playback_status_name(playback.playback_status) == "playing":
            try:
                elapsed = max(
                    0.0,
                    (datetime.now(timezone.utc) - timeline.last_updated_time).total_seconds(),
                )
                rate = float(playback.playback_rate or 1.0)
                position += elapsed * rate
            except Exception:
                pass
        start = float(timeline.start_time.total_seconds())
        end = float(timeline.end_time.total_seconds())
        return max(start, min(end, position)) if end > start else position

    async def _read_session_state_async(self, expected_title=None):
        session, context = await self._session_context(expected_title)
        if session is None:
            return context
        playback = session.get_playback_info()
        timeline = session.get_timeline_properties()
        status = self._playback_status_name(playback.playback_status)
        return {
            "ok": True,
            "message": f"Sesión multimedia {status}.",
            "scope": "active_windows_media_session",
            "state_confirmed": True,
            "playback_status": status,
            "position_seconds": self._effective_position_seconds(playback, timeline),
            "duration_seconds": float((timeline.end_time - timeline.start_time).total_seconds()),
            **context,
        }

    def get_media_session_state(self, expected_title=None):
        try:
            return asyncio.run(self._read_session_state_async(expected_title))
        except Exception as error:
            return {
                "ok": False,
                "error": "windows_media_session_failed",
                "message": str(error),
            }

    async def _set_session_state_async(self, desired, expected_title=None):
        session, context = await self._session_context(expected_title)
        if session is None:
            return context
        before = self._playback_status_name(session.get_playback_info().playback_status)
        if desired == "play" and before == "playing":
            accepted = True
        elif desired == "pause" and before in {"paused", "stopped"}:
            accepted = True
        elif desired == "play":
            accepted = bool(await session.try_play_async())
        else:
            accepted = bool(await session.try_pause_async())
        await asyncio.sleep(0.15)
        after = self._playback_status_name(session.get_playback_info().playback_status)
        confirmed = after == "playing" if desired == "play" else after in {"paused", "stopped"}
        return {
            "ok": bool(accepted),
            "message": (
                f"Windows confirmó la sesión en estado {after}."
                if confirmed
                else f"Windows aceptó la orden, pero la sesión quedó en estado {after}."
            ),
            "action": desired,
            "scope": "active_windows_media_session",
            "state_confirmed": confirmed,
            "playback_status": after,
            "previous_playback_status": before,
            **context,
        }

    def set_media_session_state(self, desired, expected_title=None):
        if desired not in {"play", "pause"}:
            return {
                "ok": False,
                "error": "invalid_media_state",
                "message": "El estado multimedia debe ser play o pause.",
            }
        try:
            return asyncio.run(self._set_session_state_async(desired, expected_title))
        except Exception as error:
            return {
                "ok": False,
                "error": "windows_media_session_failed",
                "message": str(error),
            }

    async def _seek_media_session_async(self, amount, relative, expected_title=None):
        session, context = await self._session_context(expected_title)
        if session is None:
            return context
        playback = session.get_playback_info()
        timeline = session.get_timeline_properties()
        controls = playback.controls
        if not bool(controls.is_playback_position_enabled):
            return {
                "ok": False,
                "error": "media_seek_unsupported",
                "message": "La sesión multimedia no permite cambiar la posición.",
                **context,
            }
        before = self._effective_position_seconds(playback, timeline)
        start = float(timeline.start_time.total_seconds())
        end = float(timeline.end_time.total_seconds())
        requested = before + float(amount) if relative else float(amount)
        target, bounds_error = self._resolve_seek_target(start, end, requested, relative)
        if bounds_error:
            return {
                "ok": False,
                "error": "media_position_out_of_range",
                "message": (
                    f"La posición solicitada ({requested:.1f} s) queda fuera de la duración "
                    f"publicada por el contenido ({max(0.0, end - start):.1f} s)."
                ),
                "action": "adjust_position" if relative else "seek",
                "scope": "active_windows_media_session",
                "state_confirmed": False,
                "requested_position_seconds": requested,
                "duration_seconds": max(0.0, end - start),
                **context,
            }
        accepted = bool(await session.try_change_playback_position_async(
            int(round(target * 10_000_000))
        ))
        await asyncio.sleep(0.35)
        refreshed, refreshed_context = await self._session_context(expected_title)
        if refreshed is None:
            return refreshed_context
        refreshed_playback = refreshed.get_playback_info()
        refreshed_timeline = refreshed.get_timeline_properties()
        observed = self._effective_position_seconds(refreshed_playback, refreshed_timeline)
        tolerance = 4.0 if self._playback_status_name(refreshed_playback.playback_status) == "playing" else 0.75
        confirmed = accepted and abs(observed - target) <= tolerance
        return {
            "ok": accepted,
            "message": (
                f"Windows confirmó la posición en {observed:.1f} segundos."
                if confirmed
                else "Windows aceptó el desplazamiento, pero no confirmó la posición solicitada."
            ),
            "action": "adjust_position" if relative else "seek",
            "scope": "active_windows_media_session",
            "state_confirmed": confirmed,
            "previous_position_seconds": before,
            "target_position_seconds": target,
            "position_seconds": observed,
            **refreshed_context,
        }

    @staticmethod
    def _resolve_seek_target(start, end, requested, relative):
        """Validate an absolute position and clamp only relative movement.

        A relative skip naturally stops at the beginning/end of media.  An
        absolute request such as "minuto 15" carries a different contract:
        silently converting it to the last frame would falsely report success
        for a short trailer.  Return an explicit bounds error instead.
        """
        start, end, requested = float(start), float(end), float(requested)
        if end < start:
            return requested, True
        if not relative and not start <= requested <= end:
            return requested, True
        return max(start, min(end, requested)), False

    def seek_media_session(self, amount, relative=False, expected_title=None):
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            return {
                "ok": False,
                "error": "invalid_media_position",
                "message": "La posición multimedia debe ser numérica.",
            }
        try:
            return asyncio.run(self._seek_media_session_async(amount, relative, expected_title))
        except Exception as error:
            return {
                "ok": False,
                "error": "windows_media_session_failed",
                "message": str(error),
            }

    async def _change_media_item_async(self, direction, expected_title=None):
        session, context = await self._session_context(expected_title)
        if session is None:
            return context
        before_title = str(context.get("title") or "")
        before_source = str(context.get("source") or "")
        if direction == "next":
            accepted = bool(await session.try_skip_next_async())
        else:
            accepted = bool(await session.try_skip_previous_async())
        await asyncio.sleep(0.7)
        refreshed, refreshed_context = await self._session_context(None)
        if refreshed is None:
            return refreshed_context
        after_title = str(refreshed_context.get("title") or "")
        after_source = str(refreshed_context.get("source") or "")
        changed = (
            accepted
            and bool(after_title)
            and self._normal(after_title) != self._normal(before_title)
            and self._normal(after_source) == self._normal(before_source)
        )
        return {
            "ok": accepted,
            "message": (
                f"Windows confirmó el cambio a {after_title}."
                if changed
                else "Windows aceptó la navegación, pero no confirmó un elemento multimedia distinto."
            ),
            "action": direction,
            "scope": "active_windows_media_session",
            "state_confirmed": changed,
            "previous_title": before_title,
            "title": after_title,
            "source": after_source,
        }

    def change_media_session_item(self, direction, expected_title=None):
        if direction not in {"next", "previous"}:
            return {
                "ok": False,
                "error": "invalid_media_direction",
                "message": "La dirección multimedia debe ser next o previous.",
            }
        try:
            return asyncio.run(self._change_media_item_async(direction, expected_title))
        except Exception as error:
            return {
                "ok": False,
                "error": "windows_media_session_failed",
                "message": str(error),
            }


    # =====================================================
    # PLAY / PAUSE
    # =====================================================

    def play_pause(self):

        if self._press_key(
            VK_MEDIA_PLAY_PAUSE
        ):

            return (
                True,
                "Reproducción alternada"
            )

        return (
            False,
            "No pude controlar la reproducción"
        )


    # =====================================================
    # SIGUIENTE
    # =====================================================

    def next_track(self):

        if self._press_key(
            VK_MEDIA_NEXT_TRACK
        ):

            return (
                True,
                "Siguiente"
            )

        return (
            False,
            "No pude cambiar de pista"
        )


    # =====================================================
    # ANTERIOR
    # =====================================================

    def previous_track(self):

        if self._press_key(
            VK_MEDIA_PREV_TRACK
        ):

            return (
                True,
                "Anterior"
            )

        return (
            False,
            "No pude cambiar de pista"
        )


    # =====================================================
    # DETENER
    # =====================================================

    def stop_media(self):

        if self._press_key(
            VK_MEDIA_STOP
        ):

            return (
                True,
                "Reproducción detenida"
            )

        return (
            False,
            "No pude detener la reproducción"
        )


    # =====================================================
    # SUBIR VOLUMEN
    # =====================================================

    def volume_up(
        self,
        steps=2
    ):

        try:

            steps = max(
                1,
                min(int(steps), 20)
            )

        except Exception:

            steps = 2


        for _ in range(steps):

            self._press_key(
                VK_VOLUME_UP
            )


        return (
            True,
            "Subiendo volumen"
        )


    # =====================================================
    # BAJAR VOLUMEN
    # =====================================================

    def volume_down(
        self,
        steps=2
    ):

        try:

            steps = max(
                1,
                min(int(steps), 20)
            )

        except Exception:

            steps = 2


        for _ in range(steps):

            self._press_key(
                VK_VOLUME_DOWN
            )


        return (
            True,
            "Bajando volumen"
        )


    # =====================================================
    # MUTE
    # =====================================================

    def toggle_mute(self):

        if self._press_key(
            VK_VOLUME_MUTE
        ):

            return (
                True,
                "Silencio alternado"
            )

        return (
            False,
            "No pude cambiar el silencio"
        )


# =====================================================
    # ENDPOINT DE AUDIO PRINCIPAL
    # =====================================================

    def _get_endpoint_volume(self):

        if not PYCAW_AVAILABLE:

            raise RuntimeError(
                "pycaw no está instalado"
            )

        try:

            comtypes.CoInitialize()

        except Exception:
            pass


        device = (
            AudioUtilities
            .GetSpeakers()
        )


        return device.EndpointVolume


    # =====================================================
    # FIJAR VOLUMEN EXACTO
    # =====================================================

    def set_volume(
        self,
        percent
    ):

        try:

            percent = int(
                round(float(percent))
            )


            percent = max(
                0,
                min(percent, 100)
            )


            volume = (
                self._get_endpoint_volume()
            )


            # Windows trabaja internamente
            # entre 0.0 y 1.0.

            scalar = (
                percent / 100.0
            )


            volume.SetMasterVolumeLevelScalar(
                scalar,
                None
            )


            # Si ponemos volumen > 0,
            # quitar mute.

            if percent > 0:

                volume.SetMute(
                    0,
                    None
                )


            actual_scalar = (
                volume
                .GetMasterVolumeLevelScalar()
            )


            actual_percent = int(
                round(
                    actual_scalar * 100
                )
            )


            print(
                f"Volumen solicitado: {percent}%"
            )

            print(
                f"Volumen real: {actual_percent}%"
            )


            return (
                True,
                f"Volumen al {actual_percent}%"
            )


        except Exception as e:

            print("")
            print(
                "ERROR ESTABLECIENDO VOLUMEN:"
            )

            print(
                repr(e)
            )


            return (
                False,
                "No pude establecer el volumen"
            )


    # =====================================================
    # CONSULTAR VOLUMEN
    # =====================================================

    def get_volume(self):

        try:

            volume = (
                self._get_endpoint_volume()
            )


            scalar = (
                volume
                .GetMasterVolumeLevelScalar()
            )


            percent = int(
                round(
                    scalar * 100
                )
            )


            muted = bool(
                volume.GetMute()
            )


            if muted:

                return (
                    True,
                    f"El volumen está al {percent}% "
                    f"y está silenciado"
                )


            return (
                True,
                f"El volumen está al {percent}%"
            )


        except Exception as e:

            print(
                "Error obteniendo volumen:",
                repr(e)
            )


            return (
                False,
                "No pude consultar el volumen"
            )


    # =====================================================
    # CAMBIO RELATIVO
    # =====================================================

    def change_volume(
        self,
        amount
    ):

        try:

            volume = (
                self._get_endpoint_volume()
            )


            current_scalar = (
                volume
                .GetMasterVolumeLevelScalar()
            )


            current_percent = (
                current_scalar * 100
            )


            new_percent = (
                current_percent
                + float(amount)
            )


            new_percent = max(
                0,
                min(new_percent, 100)
            )


            volume.SetMasterVolumeLevelScalar(
                new_percent / 100.0,
                None
            )


            if new_percent > 0:

                volume.SetMute(
                    0,
                    None
                )


            actual = int(
                round(
                    volume
                    .GetMasterVolumeLevelScalar()
                    * 100
                )
            )


            return (
                True,
                f"Volumen al {actual}%"
            )


        except Exception as e:

            print(
                "Error cambiando volumen:",
                repr(e)
            )


            return (
                False,
                "No pude cambiar el volumen"
            )


    # =====================================================
    # SILENCIAR EXPLÍCITAMENTE
    # =====================================================

    def mute(self):

        try:

            volume = (
                self._get_endpoint_volume()
            )


            volume.SetMute(
                1,
                None
            )


            return (
                True,
                "PC silenciada"
            )


        except Exception as e:

            print(
                "Error silenciando:",
                repr(e)
            )


            return (
                False,
                "No pude silenciar la PC"
            )


    # =====================================================
    # QUITAR SILENCIO
    # =====================================================

    def unmute(self):

        try:

            volume = (
                self._get_endpoint_volume()
            )


            volume.SetMute(
                0,
                None
            )


            return (
                True,
                "Sonido activado"
            )


        except Exception as e:

            print(
                "Error activando sonido:",
                repr(e)
            )


            return (
                False,
                "No pude activar el sonido"
            )


if os.name == "nt":
    media_controller = MediaController()

else:
    # Linux: MPRIS para las sesiones y wpctl para el volumen.
    from linux_platform.media import LinuxMediaController

    media_controller = LinuxMediaController()
