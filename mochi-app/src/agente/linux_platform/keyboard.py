"""
Teclas (Enter, Espacio, flechas) en Wayland, por el portal de
escritorio RemoteDesktop.

En Wayland una aplicación no puede mandar teclas a otra. AT-SPI no
sirve para eso (GenerateKeyboardEvent solo funciona en X11) y
Chromium no publica una acción de "enviar formulario". La vía
oficial es el portal: la primera vez KDE muestra un diálogo para
que el usuario permita que Mochi controle el teclado; con
"Recordar" ya no vuelve a preguntar (el permiso queda guardado en
~/.mochi/linux_remote_desktop_token).

Las teclas van a la ventana que tenga el foco: antes de usarlo hay
que activar la ventana del navegador.
"""

import secrets
import threading
import time
from pathlib import Path

from jeepney import DBusAddress, MatchRule, new_method_call
from jeepney.bus_messages import message_bus
from jeepney.io.blocking import open_dbus_connection


KEY_RETURN = 0xFF0D
KEY_SPACE = 0x0020
KEY_LEFT = 0xFF51
KEY_RIGHT = 0xFF53

KEYBOARD_DEVICE = 1

# Mientras el portal muestra su diálogo, se espera al usuario.
CONSENT_TIMEOUT_SECONDS = 60
REQUEST_TIMEOUT_SECONDS = 10

TOKEN_PATH = Path.home() / ".mochi" / "linux_remote_desktop_token"

REMOTE_DESKTOP = DBusAddress(
    "/org/freedesktop/portal/desktop",
    bus_name="org.freedesktop.portal.Desktop",
    interface="org.freedesktop.portal.RemoteDesktop",
)


class KeyboardUnavailable(RuntimeError):
    pass


# =========================================================
# SESIÓN DEL PORTAL
# =========================================================

class PortalKeyboard:
    """
    Una sesión de RemoteDesktop que dura lo que dura Mochi: el
    portal la cierra si se corta esta conexión D-Bus.
    """

    def __init__(self, token_path=TOKEN_PATH):

        self._token_path = Path(token_path)
        self._connection = None
        self._session = None
        self._lock = threading.Lock()


    # -----------------------------------------------------

    def _request(self, method, signature, body, options, timeout):
        """
        Llama a un método del portal y espera la señal Response
        de su Request. Devuelve el diccionario de resultados.
        """

        connection = self._connection
        token = "mochi" + secrets.token_hex(6)
        sender = connection.unique_name[1:].replace(".", "_")
        request_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

        rule = MatchRule(
            type="signal",
            interface="org.freedesktop.portal.Request",
            member="Response",
            path=request_path,
        )

        connection.send_and_get_reply(message_bus.AddMatch(rule))

        options = dict(options)
        options["handle_token"] = ("s", token)

        try:
            with connection.filter(rule) as queue:

                reply = connection.send_and_get_reply(
                    new_method_call(
                        REMOTE_DESKTOP,
                        method,
                        signature + "a{sv}",
                        tuple(body) + (options,),
                    ),
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )

                if reply.header.message_type.name == "error":
                    raise KeyboardUnavailable(
                        f"El portal RemoteDesktop rechazó {method}: {reply.body}"
                    )

                signal = connection.recv_until_filtered(queue, timeout=timeout)

        except TimeoutError as error:
            raise KeyboardUnavailable(
                "El portal RemoteDesktop no respondió (¿el diálogo quedó sin contestar?)."
            ) from error

        finally:
            try:
                connection.send_and_get_reply(message_bus.RemoveMatch(rule))
            except Exception:
                pass

        response, results = signal.body

        if response != 0:
            raise KeyboardUnavailable(
                "No hay permiso para mandar teclas: el pedido del portal "
                "se canceló o se rechazó."
            )

        return {key: value[1] for key, value in results.items()}


    # -----------------------------------------------------

    def _read_token(self):

        try:
            return self._token_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""


    def _write_token(self, token):

        if not token:
            return

        try:
            self._token_path.parent.mkdir(parents=True, exist_ok=True)
            self._token_path.write_text(token, encoding="utf-8")
        except OSError:
            pass


    def _start(self):

        try:
            self._connection = open_dbus_connection(bus="SESSION")
        except (OSError, ConnectionError) as error:
            raise KeyboardUnavailable(f"No pude abrir el bus de sesión: {error}") from error

        try:
            created = self._request(
                "CreateSession",
                "",
                (),
                {"session_handle_token": ("s", "mochi" + secrets.token_hex(6))},
                REQUEST_TIMEOUT_SECONDS,
            )
            session = created["session_handle"]

            select_options = {
                "types": ("u", KEYBOARD_DEVICE),
                # 2 = el permiso dura hasta que el usuario lo revoque.
                "persist_mode": ("u", 2),
            }
            token = self._read_token()
            if token:
                select_options["restore_token"] = ("s", token)

            self._request(
                "SelectDevices",
                "o",
                (session,),
                select_options,
                REQUEST_TIMEOUT_SECONDS,
            )

            started = self._request(
                "Start",
                "os",
                (session, ""),
                {},
                CONSENT_TIMEOUT_SECONDS,
            )

        except Exception:
            self._close()
            raise

        if not int(started.get("devices", 0)) & KEYBOARD_DEVICE:
            self._close()
            raise KeyboardUnavailable("El usuario no permitió el teclado.")

        self._write_token(started.get("restore_token", ""))
        self._session = session


    def _close(self):

        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:
                pass

        self._connection = None
        self._session = None


    # -----------------------------------------------------
    # TECLAS
    # -----------------------------------------------------

    def _send_key(self, keysym, pressed):

        reply = self._connection.send_and_get_reply(
            new_method_call(
                REMOTE_DESKTOP,
                "NotifyKeyboardKeysym",
                "oa{sv}iu",
                (self._session, {}, int(keysym), 1 if pressed else 0),
            ),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

        if reply.header.message_type.name == "error":
            raise KeyboardUnavailable(f"El portal no aceptó la tecla: {reply.body}")


    def press(self, keysym, count=1):
        """Aprieta y suelta una tecla `count` veces (como mucho 2000)."""

        count = max(0, min(int(count), 2000))

        with self._lock:

            for attempt in range(2):

                if self._session is None:
                    self._start()

                try:
                    for _index in range(count):
                        self._send_key(keysym, True)
                        self._send_key(keysym, False)
                        time.sleep(0.005)
                    return

                except (KeyboardUnavailable, OSError, ConnectionError, TimeoutError):
                    # La sesión pudo cerrarse (permiso revocado): una
                    # sola vez se pide de nuevo.
                    self._close()
                    if attempt:
                        raise


_keyboard = None


def get_keyboard():

    global _keyboard

    if _keyboard is None:
        _keyboard = PortalKeyboard()

    return _keyboard
