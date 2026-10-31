"""
Ventanas en KDE Plasma (Wayland) a través de KWin.

En Wayland ninguna aplicación puede ver ni mover las ventanas de
otra. La única vía es el propio compositor: se le carga a KWin un
script corto por D-Bus, el script hace el trabajo y devuelve el
resultado llamando de vuelta a esta conexión D-Bus (callDBus).

Cada llamada abre su conexión, carga el script, lo corre y lo
descarga. Tarda unos 20-50 ms.
"""

import itertools
import json
import os
import tempfile
import threading

from jeepney import (
    DBusAddress,
    MatchRule,
    new_method_call,
    new_method_return,
)
from jeepney.bus_messages import message_bus
from jeepney.io.blocking import open_dbus_connection


SCRIPT_TIMEOUT_SECONDS = 3

BRIDGE_INTERFACE = "org.mochi.KWinBridge"

KWIN_SCRIPTING = DBusAddress(
    "/Scripting",
    bus_name="org.kde.KWin",
    interface="org.kde.kwin.Scripting",
)

_counter = itertools.count()
_lock = threading.Lock()


class KWinError(RuntimeError):
    pass


# =========================================================
# CORRER UN SCRIPT EN KWIN
# =========================================================

def _run_script(body):
    """
    Corre `body` (JavaScript) dentro de KWin. El script tiene que
    llamar a reply(valor) una vez; se devuelve ese valor.
    """

    with _lock:

        number = next(_counter)
        service = f"{BRIDGE_INTERFACE}.p{os.getpid()}n{number}"
        plugin_name = f"mochi-{os.getpid()}-{number}"

        script = (
            "function reply(value) {\n"
            f"  callDBus({json.dumps(service)}, \"/\", "
            f"{json.dumps(BRIDGE_INTERFACE)}, \"result\", "
            "JSON.stringify(value === undefined ? null : value));\n"
            "}\n"
            "try {\n"
            f"{body}\n"
            "} catch (error) {\n"
            "  reply({ __error: String(error) });\n"
            "}\n"
        )

        fd, path = tempfile.mkstemp(prefix="mochi-kwin-", suffix=".js")

        try:
            os.write(fd, script.encode("utf-8"))
            os.close(fd)

            with open_dbus_connection(bus="SESSION") as conn:

                # Nombre propio en el bus para que el script nos encuentre.
                conn.send_and_get_reply(message_bus.RequestName(service))

                script_id = conn.send_and_get_reply(
                    new_method_call(
                        KWIN_SCRIPTING,
                        "loadScript",
                        "ss",
                        (path, plugin_name),
                    )
                ).body[0]

                if script_id < 0:
                    raise KWinError("KWin no aceptó el script")

                script_address = DBusAddress(
                    f"/Scripting/Script{script_id}",
                    bus_name="org.kde.KWin",
                    interface="org.kde.kwin.Script",
                )

                rule = MatchRule(
                    type="method_call",
                    interface=BRIDGE_INTERFACE,
                )

                try:

                    # El filtro tiene que estar antes de run(): el
                    # script responde mientras KWin todavía procesa run.
                    with conn.filter(rule) as queue:

                        conn.send_and_get_reply(
                            new_method_call(script_address, "run")
                        )

                        message = conn.recv_until_filtered(
                            queue,
                            timeout=SCRIPT_TIMEOUT_SECONDS,
                        )

                        conn.send(new_method_return(message))

                finally:

                    conn.send_and_get_reply(
                        new_method_call(
                            KWIN_SCRIPTING,
                            "unloadScript",
                            "s",
                            (plugin_name,),
                        )
                    )

        except TimeoutError as error:
            raise KWinError("KWin no respondió a tiempo") from error

        except (OSError, ConnectionError) as error:
            raise KWinError(f"No pude hablar con KWin: {error}") from error

        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    result = json.loads(message.body[0])

    if isinstance(result, dict) and "__error" in result:
        raise KWinError(result["__error"])

    return result


# =========================================================
# LISTAR VENTANAS
# =========================================================

_LIST_SCRIPT = """
const active = workspace.activeWindow;
reply(workspace.windowList()
  .filter(w => (w.normalWindow || w.dialog) && !w.skipTaskbar)
  .map(w => ({
    id: String(w.internalId),
    title: w.caption,
    pid: w.pid,
    resource_class: w.resourceClass,
    desktop_file: w.desktopFileName,
    minimized: w.minimized,
    active: w === active
  })));
"""


def list_windows():
    """
    Ventanas de aplicación (sin paneles ni escritorio), en el
    orden de apilado de KWin.
    """

    return _run_script(_LIST_SCRIPT) or []


# =========================================================
# ACCIONES SOBRE UNA VENTANA
# =========================================================

_ACTIONS = {
    "restore":
        "w.minimized = false;",

    "focus":
        "w.minimized = false; workspace.activeWindow = w;",

    "minimize":
        "w.minimized = true;",

    "maximize":
        "w.minimized = false; w.setMaximize(true, true); "
        "workspace.activeWindow = w;",

    # Pide el cierre normal (como el botón X): no mata el proceso.
    "close":
        "w.closeWindow();",
}


def window_action(window_id, action):
    """Devuelve True si la ventana existía y se aplicó la acción."""

    if action not in _ACTIONS:
        raise ValueError(f"Acción de ventana desconocida: {action}")

    body = (
        f"const id = {json.dumps(str(window_id))};\n"
        "const w = workspace.windowList()"
        ".find(x => String(x.internalId) === id);\n"
        "if (!w) { reply(false); } else {\n"
        f"  {_ACTIONS[action]}\n"
        "  reply(true);\n"
        "}\n"
    )

    return bool(_run_script(body))


def is_minimized(window_id):

    body = (
        f"const id = {json.dumps(str(window_id))};\n"
        "const w = workspace.windowList()"
        ".find(x => String(x.internalId) === id);\n"
        "reply(w ? w.minimized : false);\n"
    )

    return bool(_run_script(body))


def active_window_id():

    return _run_script(
        "reply(workspace.activeWindow ? "
        "String(workspace.activeWindow.internalId) : null);"
    )
