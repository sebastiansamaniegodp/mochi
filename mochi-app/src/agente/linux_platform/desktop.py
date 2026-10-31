"""
Portapapeles, captura de pantalla y notificaciones en KDE Plasma
(Wayland). Cada función devuelve (ok, detalle), igual que
DesktopTools en Windows.
"""

import subprocess

from jeepney import DBusAddress, new_method_call
from jeepney.io.blocking import open_dbus_connection


def _run(arguments, input_text=None, timeout=10):

    try:
        result = subprocess.run(
            arguments,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    except (OSError, subprocess.SubprocessError) as error:
        return False, str(error)

    if result.returncode != 0:
        return False, (result.stderr or result.stdout or f"{arguments[0]} falló").strip()

    return True, result.stdout


# =========================================================
# PORTAPAPELES (wl-clipboard)
# =========================================================

def read_clipboard():

    ok, output = _run(["wl-paste", "--no-newline", "--type", "text"], timeout=5)

    if not ok:
        if "No selection" in output or "nothing is copied" in output.lower():
            return False, "El portapapeles no contiene texto."
        return False, f"No pude leer el portapapeles: {output}"

    return True, output


def write_clipboard(text):

    try:
        # wl-copy queda en segundo plano sirviendo el contenido:
        # no se espera a que termine.
        process = subprocess.Popen(
            ["wl-copy", "--type", "text/plain;charset=utf-8"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        process.stdin.write(text.encode("utf-8"))
        process.stdin.close()

    except OSError as error:
        return False, f"No pude copiar el texto al portapapeles: {error}"

    return True, "Texto copiado al portapapeles."


# =========================================================
# CAPTURA DE PANTALLA (Spectacle)
# =========================================================

def capture_screenshot(path):

    # -b sin interfaz, -n sin notificación, -f pantalla completa.
    ok, detail = _run(
        ["spectacle", "-b", "-n", "-f", "-o", str(path)],
        timeout=20,
    )

    if not ok or not path.exists():
        return False, detail or "Spectacle no creó la captura de pantalla."

    return True, path


# =========================================================
# NOTIFICACIONES (org.freedesktop.Notifications)
# =========================================================

def show_notification(title, message):

    notifications = DBusAddress(
        "/org/freedesktop/Notifications",
        bus_name="org.freedesktop.Notifications",
        interface="org.freedesktop.Notifications",
    )

    try:
        with open_dbus_connection(bus="SESSION") as conn:
            conn.send_and_get_reply(
                new_method_call(
                    notifications,
                    "Notify",
                    "susssasa{sv}i",
                    (
                        "Mochi",          # aplicación
                        0,                # no reemplaza ninguna
                        "dialog-information",
                        title,
                        message,
                        [],               # sin botones
                        {},
                        5000,             # ms
                    ),
                ),
                timeout=5,
            )

    except Exception as error:
        return False, f"No pude enviar la notificación: {error}"

    return True, "Notificación enviada al escritorio."
