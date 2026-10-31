"""
Multimedia y volumen en Linux.

- Sesiones multimedia: MPRIS por D-Bus (Chromium, Firefox, Spotify,
  VLC... todos lo publican). Es el equivalente de la sesión
  multimedia global de Windows (GlobalSystemMediaTransportControls).
- Volumen: wpctl (PipeWire), sobre la salida predeterminada.

Los identificadores que leen las herramientas del Brain ("scope",
códigos de "error") se mantienen iguales a los de Windows para que
el agent loop no tenga que distinguir plataformas.
"""

import re
import subprocess
import threading
import time

from jeepney import DBusAddress, new_method_call
from jeepney.io.blocking import open_dbus_connection

from media_controller import MediaController


MPRIS_PREFIX = "org.mpris.MediaPlayer2."
MPRIS_PATH = "/org/mpris/MediaPlayer2"
PLAYER_INTERFACE = "org.mpris.MediaPlayer2.Player"
ROOT_INTERFACE = "org.mpris.MediaPlayer2"

# KDE duplica las sesiones de los navegadores con esta extensión.
PLASMA_BROWSER = MPRIS_PREFIX + "plasma-browser-integration"

SESSION_SCOPE = "active_windows_media_session"

DEFAULT_SINK = "@DEFAULT_AUDIO_SINK@"

# Paso de las teclas de volumen de Windows.
VOLUME_STEP_PERCENT = 2


# =========================================================
# MPRIS
# =========================================================

def _unwrap(value):
    """jeepney devuelve los variants como (firma, valor)."""

    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str):
        return _unwrap(value[1])

    if isinstance(value, dict):
        return {key: _unwrap(item) for key, item in value.items()}

    if isinstance(value, list):
        return [_unwrap(item) for item in value]

    return value


class MprisPlayer:

    def __init__(self, conn, bus_name):

        self.conn = conn
        self.bus_name = bus_name
        self.props = {}
        self.identity = ""


    def _address(self, interface):

        return DBusAddress(
            MPRIS_PATH,
            bus_name=self.bus_name,
            interface=interface,
        )


    def refresh(self):

        properties = DBusAddress(
            MPRIS_PATH,
            bus_name=self.bus_name,
            interface="org.freedesktop.DBus.Properties",
        )

        reply = self.conn.send_and_get_reply(
            new_method_call(properties, "GetAll", "s", (PLAYER_INTERFACE,)),
            timeout=2,
        )
        self.props = _unwrap(reply.body[0])

        if not self.identity:
            try:
                reply = self.conn.send_and_get_reply(
                    new_method_call(properties, "Get", "ss", (ROOT_INTERFACE, "Identity")),
                    timeout=2,
                )
                self.identity = str(_unwrap(reply.body[0]) or "")
            except Exception:
                self.identity = ""

        return self


    def call(self, method, signature=None, body=()):

        self.conn.send_and_get_reply(
            new_method_call(self._address(PLAYER_INTERFACE), method, signature, body),
            timeout=2,
        )


    # -----------------------------------------------------

    @property
    def source(self):
        """Equivalente al source_app_user_model_id de Windows."""

        name = self.bus_name.removeprefix(MPRIS_PREFIX)
        # chromium.instance1234 -> chromium
        return name.split(".instance")[0]


    @property
    def metadata(self):

        return self.props.get("Metadata") or {}


    @property
    def title(self):

        return str(self.metadata.get("xesam:title") or "")


    @property
    def artist(self):

        artist = self.metadata.get("xesam:artist") or ""

        if isinstance(artist, list):
            return ", ".join(str(item) for item in artist)

        return str(artist)


    @property
    def status(self):
        """playing / paused / stopped, como en Windows."""

        return str(self.props.get("PlaybackStatus") or "stopped").casefold()


    @property
    def position_seconds(self):

        return float(self.props.get("Position") or 0) / 1_000_000


    @property
    def duration_seconds(self):

        return float(self.metadata.get("mpris:length") or 0) / 1_000_000


    @property
    def track_id(self):

        return self.metadata.get("mpris:trackid")


    def read_position(self):
        """Position no siempre viene en GetAll actualizado: se pide aparte."""

        properties = DBusAddress(
            MPRIS_PATH,
            bus_name=self.bus_name,
            interface="org.freedesktop.DBus.Properties",
        )

        try:
            reply = self.conn.send_and_get_reply(
                new_method_call(properties, "Get", "ss", (PLAYER_INTERFACE, "Position")),
                timeout=2,
            )
            return float(_unwrap(reply.body[0]) or 0) / 1_000_000

        except Exception:
            return self.position_seconds


def _list_players(conn):

    bus = DBusAddress(
        "/org/freedesktop/DBus",
        bus_name="org.freedesktop.DBus",
        interface="org.freedesktop.DBus",
    )

    names = conn.send_and_get_reply(new_method_call(bus, "ListNames")).body[0]

    players = []

    for name in sorted(names):

        if not name.startswith(MPRIS_PREFIX):
            continue

        try:
            players.append(MprisPlayer(conn, name).refresh())
        except Exception:
            # Un reproductor colgado no tiene que tapar a los demás.
            continue

    # Si la extensión de Plasma repite una sesión que el navegador
    # ya publica, se queda la del navegador.
    browser_titles = {
        player.title
        for player in players
        if player.bus_name != PLASMA_BROWSER
    }

    return [
        player
        for player in players
        if not (player.bus_name == PLASMA_BROWSER and player.title in browser_titles)
    ]


def _current_player(players):
    """
    La sesión "actual": la que está sonando; si ninguna suena,
    la primera pausada; si no, cualquiera.
    """

    for wanted in ("playing", "paused"):
        for player in players:
            if player.status == wanted:
                return player

    return players[0] if players else None


# =========================================================
# CONTROLADOR
# =========================================================

class LinuxMediaController(MediaController):

    def __init__(self):

        # No hay user32: no se llama al __init__ de Windows.
        self._lock = threading.RLock()


    # =====================================================
    # SESIÓN MULTIMEDIA (MPRIS)
    # =====================================================

    def _session_context(self, conn, expected_title=None):

        players = _list_players(conn)
        player = _current_player(players)

        if player is None:
            return None, {
                "ok": False,
                "error": "media_session_not_found",
                "message": "No hay ninguna sesión multimedia activa.",
            }

        title = player.title

        title_matches = self._session_matches_title(
            player.source,
            title,
            expected_title,
        )
        match_scope = "content_title" if title_matches else None

        if not title_matches:
            summaries = [
                {"source": item.source, "title": item.title}
                for item in players
            ]
            title_matches = self._unique_exact_browser_session_matches(
                player.source,
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
                "message": "La sesión multimedia activa no corresponde a la página activa.",
                "source": player.source,
                "title": title,
            }

        return player, {
            "source": player.source,
            "title": title,
            "artist": player.artist,
            "session_match": match_scope,
        }


    def _with_session(self, action):
        """Abre la conexión D-Bus, corre `action(conn)` y traduce errores."""

        try:
            with self._lock, open_dbus_connection(bus="SESSION") as conn:
                return action(conn)

        except Exception as error:
            return {
                "ok": False,
                "error": "windows_media_session_failed",
                "message": str(error),
            }


    # -----------------------------------------------------

    def get_media_session_state(self, expected_title=None):

        def read(conn):

            player, context = self._session_context(conn, expected_title)

            if player is None:
                return context

            return {
                "ok": True,
                "message": f"Sesión multimedia {player.status}.",
                "scope": SESSION_SCOPE,
                "state_confirmed": True,
                "playback_status": player.status,
                "position_seconds": player.read_position(),
                "duration_seconds": player.duration_seconds,
                **context,
            }

        return self._with_session(read)


    def set_media_session_state(self, desired, expected_title=None):

        if desired not in {"play", "pause"}:
            return {
                "ok": False,
                "error": "invalid_media_state",
                "message": "El estado multimedia debe ser play o pause.",
            }

        def apply(conn):

            player, context = self._session_context(conn, expected_title)

            if player is None:
                return context

            before = player.status

            if desired == "play" and before == "playing":
                accepted = True
            elif desired == "pause" and before in {"paused", "stopped"}:
                accepted = True
            else:
                accepted = bool(
                    player.props.get("CanPlay" if desired == "play" else "CanPause", True)
                )
                if accepted:
                    player.call("Play" if desired == "play" else "Pause")

            time.sleep(0.15)
            after = player.refresh().status

            confirmed = (
                after == "playing"
                if desired == "play"
                else after in {"paused", "stopped"}
            )

            return {
                "ok": bool(accepted),
                "message": (
                    f"El reproductor confirmó el estado {after}."
                    if confirmed
                    else f"El reproductor aceptó la orden, pero quedó en estado {after}."
                ),
                "action": desired,
                "scope": SESSION_SCOPE,
                "state_confirmed": confirmed,
                "playback_status": after,
                "previous_playback_status": before,
                **context,
            }

        return self._with_session(apply)


    def seek_media_session(self, amount, relative=False, expected_title=None):

        try:
            amount = float(amount)
        except (TypeError, ValueError):
            return {
                "ok": False,
                "error": "invalid_media_position",
                "message": "La posición multimedia debe ser numérica.",
            }

        def apply(conn):

            player, context = self._session_context(conn, expected_title)

            if player is None:
                return context

            if not player.props.get("CanSeek", False):
                return {
                    "ok": False,
                    "error": "media_seek_unsupported",
                    "message": "La sesión multimedia no permite cambiar la posición.",
                    **context,
                }

            before = player.read_position()
            end = player.duration_seconds
            requested = before + amount if relative else amount

            if end > 0:
                target, bounds_error = self._resolve_seek_target(0.0, end, requested, relative)
            else:
                # El reproductor no publica la duración: no hay límite que validar.
                target, bounds_error = max(0.0, requested), False

            if bounds_error:
                return {
                    "ok": False,
                    "error": "media_position_out_of_range",
                    "message": (
                        f"La posición solicitada ({requested:.1f} s) queda fuera de la duración "
                        f"publicada por el contenido ({max(0.0, end):.1f} s)."
                    ),
                    "action": "adjust_position" if relative else "seek",
                    "scope": SESSION_SCOPE,
                    "state_confirmed": False,
                    "requested_position_seconds": requested,
                    "duration_seconds": max(0.0, end),
                    **context,
                }

            target_us = int(round(target * 1_000_000))

            # SetPosition necesita el trackid; si no lo hay, Seek relativo.
            if player.track_id:
                player.call("SetPosition", "ox", (player.track_id, target_us))
            else:
                player.call("Seek", "x", (target_us - int(round(before * 1_000_000)),))

            time.sleep(0.35)
            player.refresh()
            observed = player.read_position()

            tolerance = 4.0 if player.status == "playing" else 0.75
            confirmed = abs(observed - target) <= tolerance

            return {
                "ok": True,
                "message": (
                    f"El reproductor confirmó la posición en {observed:.1f} segundos."
                    if confirmed
                    else "El reproductor aceptó el desplazamiento, pero no confirmó la posición solicitada."
                ),
                "action": "adjust_position" if relative else "seek",
                "scope": SESSION_SCOPE,
                "state_confirmed": confirmed,
                "previous_position_seconds": before,
                "target_position_seconds": target,
                "position_seconds": observed,
                **context,
            }

        return self._with_session(apply)


    def change_media_session_item(self, direction, expected_title=None):

        if direction not in {"next", "previous"}:
            return {
                "ok": False,
                "error": "invalid_media_direction",
                "message": "La dirección multimedia debe ser next o previous.",
            }

        def apply(conn):

            player, context = self._session_context(conn, expected_title)

            if player is None:
                return context

            before_title = player.title
            can = player.props.get("CanGoNext" if direction == "next" else "CanGoPrevious", True)

            if can:
                player.call("Next" if direction == "next" else "Previous")

            time.sleep(0.7)
            after_title = player.refresh().title

            changed = (
                bool(can)
                and bool(after_title)
                and self._normal(after_title) != self._normal(before_title)
            )

            return {
                "ok": bool(can),
                "message": (
                    f"El reproductor confirmó el cambio a {after_title}."
                    if changed
                    else "El reproductor aceptó la navegación, pero no confirmó un elemento multimedia distinto."
                ),
                "action": direction,
                "scope": SESSION_SCOPE,
                "state_confirmed": changed,
                "previous_title": before_title,
                "title": after_title,
                "source": player.source,
            }

        return self._with_session(apply)


    # =====================================================
    # "TECLAS" MULTIMEDIA: sobre la sesión actual
    # =====================================================

    def _player_command(self, method, ok_message, error_message):

        def apply(conn):

            player = _current_player(_list_players(conn))

            if player is None:
                return {"ok": False}

            player.call(method)
            return {"ok": True}

        result = self._with_session(apply)

        if result.get("ok"):
            return True, ok_message

        return False, error_message


    def play_pause(self):

        return self._player_command(
            "PlayPause",
            "Reproducción alternada",
            "No pude controlar la reproducción",
        )


    def next_track(self):

        return self._player_command(
            "Next",
            "Siguiente",
            "No pude cambiar de pista",
        )


    def previous_track(self):

        return self._player_command(
            "Previous",
            "Anterior",
            "No pude cambiar de pista",
        )


    def stop_media(self):

        return self._player_command(
            "Stop",
            "Reproducción detenida",
            "No pude detener la reproducción",
        )


    # =====================================================
    # VOLUMEN (wpctl / PipeWire)
    # =====================================================

    @staticmethod
    def _wpctl(*arguments):

        result = subprocess.run(
            ["wpctl", *arguments],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode != 0:
            raise RuntimeError(
                (result.stderr or result.stdout or "wpctl falló").strip()
            )

        return result.stdout


    def _read_volume(self):
        """Devuelve (porcentaje, silenciado)."""

        output = self._wpctl("get-volume", DEFAULT_SINK)
        match = re.search(r"Volume:\s*([0-9.]+)", output)

        if not match:
            raise RuntimeError(f"Respuesta inesperada de wpctl: {output!r}")

        percent = int(round(float(match.group(1)) * 100))

        return percent, "[MUTED]" in output


    def _write_volume(self, percent):

        percent = max(0, min(int(round(percent)), 100))

        # -l 1.0: nunca por encima del 100 %.
        self._wpctl("set-volume", "-l", "1.0", DEFAULT_SINK, f"{percent}%")

        if percent > 0:
            self._wpctl("set-mute", DEFAULT_SINK, "0")

        return self._read_volume()[0]


    def volume_up(self, steps=2):

        return self._step_volume(steps, +1, "Subiendo volumen")


    def volume_down(self, steps=2):

        return self._step_volume(steps, -1, "Bajando volumen")


    def _step_volume(self, steps, sign, message):

        try:
            steps = max(1, min(int(steps), 20))
        except Exception:
            steps = 2

        try:
            current, _muted = self._read_volume()
            self._write_volume(current + sign * steps * VOLUME_STEP_PERCENT)
            return True, message

        except Exception as error:
            print("Error cambiando volumen:", repr(error))
            return False, "No pude cambiar el volumen"


    def toggle_mute(self):

        try:
            self._wpctl("set-mute", DEFAULT_SINK, "toggle")
            return True, "Silencio alternado"

        except Exception as error:
            print("Error alternando silencio:", repr(error))
            return False, "No pude cambiar el silencio"


    def set_volume(self, percent):

        try:
            percent = int(round(float(percent)))
            actual = self._write_volume(percent)

            print(f"Volumen solicitado: {percent}%")
            print(f"Volumen real: {actual}%")

            return True, f"Volumen al {actual}%"

        except Exception as error:
            print("")
            print("ERROR ESTABLECIENDO VOLUMEN:")
            print(repr(error))

            return False, "No pude establecer el volumen"


    def get_volume(self):

        try:
            percent, muted = self._read_volume()

            if muted:
                return True, f"El volumen está al {percent}% y está silenciado"

            return True, f"El volumen está al {percent}%"

        except Exception as error:
            print("Error obteniendo volumen:", repr(error))
            return False, "No pude consultar el volumen"


    def change_volume(self, amount):

        try:
            current, _muted = self._read_volume()
            actual = self._write_volume(current + float(amount))
            return True, f"Volumen al {actual}%"

        except Exception as error:
            print("Error cambiando volumen:", repr(error))
            return False, "No pude cambiar el volumen"


    def mute(self):

        try:
            self._wpctl("set-mute", DEFAULT_SINK, "1")
            return True, "PC silenciada"

        except Exception as error:
            print("Error silenciando:", repr(error))
            return False, "No pude silenciar la PC"


    def unmute(self):

        try:
            self._wpctl("set-mute", DEFAULT_SINK, "0")
            return True, "Sonido activado"

        except Exception as error:
            print("Error activando sonido:", repr(error))
            return False, "No pude activar el sonido"
