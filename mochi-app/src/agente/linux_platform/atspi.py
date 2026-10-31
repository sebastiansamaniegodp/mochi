"""
Accesibilidad de Linux (AT-SPI) por D-Bus, con jeepney.

Es el equivalente de UI Automation de Windows: cada aplicación
publica su árbol de controles en un bus D-Bus propio (el bus de
accesibilidad, distinto del bus de sesión). Mochi lo usa para leer
y tocar la página activa de Chromium / Chrome.

Un elemento se identifica con (nombre en el bus, ruta del objeto).
Chromium no reutiliza las rutas: cuando un control desaparece, su
ruta deja de existir y cualquier llamada da error. Eso sirve como
el "runtime id" de UIA para detectar elementos viejos.

Las llamadas se mandan en lote (todas juntas y después se leen
las respuestas): recorrer una página de 1.000 nodos tarda unas
décimas de segundo.
"""

import threading

from jeepney import DBusAddress, HeaderFields, MessageType, new_method_call
from jeepney.io.blocking import open_dbus_connection


CALL_TIMEOUT_SECONDS = 3

# Mensajes por tanda: si se mandan miles sin leer, el socket se llena.
BATCH_SIZE = 200

ACCESSIBLE = "org.a11y.atspi.Accessible"
ACTION = "org.a11y.atspi.Action"
COMPONENT = "org.a11y.atspi.Component"
DOCUMENT = "org.a11y.atspi.Document"
SELECTION = "org.a11y.atspi.Selection"
TEXT = "org.a11y.atspi.Text"
VALUE = "org.a11y.atspi.Value"
PROPERTIES = "org.freedesktop.DBus.Properties"

REGISTRY_ROOT = (
    "org.a11y.atspi.Registry",
    "/org/a11y/atspi/accessible/root",
)

NULL_PATH = "/org/a11y/atspi/null"


# =========================================================
# ROLES (AtspiRole)
# =========================================================

ROLE_CHECK_BOX = 7
ROLE_CHECK_MENU_ITEM = 8
ROLE_COMBO_BOX = 11
ROLE_FRAME = 23
ROLE_LABEL = 29
ROLE_LIST_ITEM = 32
ROLE_MENU_ITEM = 35
ROLE_PAGE_TAB = 37
ROLE_PAGE_TAB_LIST = 38
ROLE_PASSWORD_TEXT = 40
ROLE_PUSH_BUTTON = 43
ROLE_RADIO_BUTTON = 44
ROLE_RADIO_MENU_ITEM = 45
ROLE_SLIDER = 51
ROLE_TEXT = 61
ROLE_TOGGLE_BUTTON = 62
ROLE_PARAGRAPH = 73
ROLE_ENTRY = 79
ROLE_DOCUMENT_FRAME = 82
ROLE_HEADING = 83
ROLE_LINK = 88
ROLE_TABLE_ROW = 90
ROLE_DOCUMENT_WEB = 95
ROLE_BLOCK_QUOTE = 105
ROLE_ARTICLE = 109
ROLE_LANDMARK = 110
ROLE_STATIC = 116
ROLE_PUSH_BUTTON_MENU = 129


# =========================================================
# ESTADOS (AtspiStateType, número de bit)
# =========================================================

STATE_ACTIVE = 1
STATE_CHECKED = 4
STATE_DEFUNCT = 6
STATE_EDITABLE = 7
STATE_ENABLED = 8
STATE_EXPANDED = 10
STATE_FOCUSABLE = 11
STATE_FOCUSED = 12
STATE_SELECTABLE = 22
STATE_SELECTED = 23
STATE_SHOWING = 25
STATE_VISIBLE = 30
STATE_CHECKABLE = 41
STATE_READ_ONLY = 43

# Component.ScrollTo: que el elemento quede a la vista, donde sea.
SCROLL_ANYWHERE = 6


def has_state(states, state):

    return bool(states & (1 << state))


def states_from_body(value):

    # GetState devuelve dos uint32 (bits 0-31 y 32-63).
    low = int(value[0]) if len(value) > 0 else 0
    high = int(value[1]) if len(value) > 1 else 0

    return low | (high << 32)


class AtspiError(RuntimeError):
    pass


# =========================================================
# CONEXIÓN AL BUS DE ACCESIBILIDAD
# =========================================================

class Atspi:
    """
    Una conexión al bus de accesibilidad, compartida y protegida
    con un lock. Si se corta (por ejemplo, se reinició la sesión
    de accesibilidad), la próxima llamada vuelve a conectar.
    """

    def __init__(self):

        self._connection = None
        self._lock = threading.RLock()
        self._pid_cache = {}


    # -----------------------------------------------------

    def _connect(self):

        if self._connection is not None:
            return self._connection

        try:
            with open_dbus_connection(bus="SESSION") as session:
                reply = session.send_and_get_reply(
                    new_method_call(
                        DBusAddress(
                            "/org/a11y/bus",
                            bus_name="org.a11y.Bus",
                            interface="org.a11y.Bus",
                        ),
                        "GetAddress",
                    ),
                    timeout=CALL_TIMEOUT_SECONDS,
                )

            address = reply.body[0]
            self._connection = open_dbus_connection(address)

        except (OSError, ConnectionError, TimeoutError, IndexError) as error:
            raise AtspiError(
                f"No pude conectarme al bus de accesibilidad: {error}"
            ) from error

        self._pid_cache = {}

        return self._connection


    def _reset(self):

        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:
                pass

        self._connection = None


    # -----------------------------------------------------

    @staticmethod
    def _message(request):

        ref, interface, method, signature, body = request

        return new_method_call(
            DBusAddress(ref[1], bus_name=ref[0], interface=interface),
            method,
            signature,
            body,
        )


    def call_many(self, requests):
        """
        Manda todas las llamadas y devuelve una lista de
        (ok, cuerpo) en el mismo orden. Una llamada que falla
        (objeto que ya no existe, interfaz que falta) devuelve
        (False, mensaje) sin cortar las demás.

        Cada pedido es (ref, interfaz, método, firma, argumentos).
        """

        requests = list(requests)
        results = [None] * len(requests)

        if not requests:
            return results

        with self._lock:

            connection = self._connect()

            try:

                for start in range(0, len(requests), BATCH_SIZE):

                    pending = {}

                    for index in range(start, min(start + BATCH_SIZE, len(requests))):
                        serial = next(connection.outgoing_serial)
                        connection.send(self._message(requests[index]), serial=serial)
                        pending[serial] = index

                    while pending:

                        message = connection.receive(timeout=CALL_TIMEOUT_SECONDS)
                        serial = message.header.fields.get(HeaderFields.reply_serial)

                        if serial not in pending:
                            continue

                        index = pending.pop(serial)

                        if message.header.message_type == MessageType.error:
                            detail = message.body[0] if message.body else "error"
                            results[index] = (False, str(detail))
                        else:
                            results[index] = (True, message.body)

            except TimeoutError as error:
                self._reset()
                raise AtspiError("La aplicación no respondió a tiempo por accesibilidad.") from error

            except (OSError, ConnectionError) as error:
                self._reset()
                raise AtspiError(f"Se cortó el bus de accesibilidad: {error}") from error

        return results


    def call(self, ref, interface, method, signature=None, body=()):
        """Una sola llamada. Lanza AtspiError si falla."""

        ok, result = self.call_many([(ref, interface, method, signature, body)])[0]

        if not ok:
            raise AtspiError(result)

        return result


    # -----------------------------------------------------
    # PROPIEDADES
    # -----------------------------------------------------

    @staticmethod
    def property_request(ref, interface, name):

        return (ref, PROPERTIES, "Get", "ss", (interface, name))


    def get_property(self, ref, interface, name):

        return self.call(ref, PROPERTIES, "Get", "ss", (interface, name))[0][1]


    def set_property(self, ref, interface, name, signature, value):

        self.call(
            ref,
            PROPERTIES,
            "Set",
            "ssv",
            (interface, name, (signature, value)),
        )


    # -----------------------------------------------------
    # APLICACIONES
    # -----------------------------------------------------

    def applications(self):
        """(ref, nombre, pid) de cada aplicación registrada."""

        children = self.call(REGISTRY_ROOT, ACCESSIBLE, "GetChildren")[0]
        refs = [tuple(child) for child in children if child[1] != NULL_PATH]

        names = self.call_many(
            [self.property_request(ref, ACCESSIBLE, "Name") for ref in refs]
        )

        applications = []

        for ref, (ok, body) in zip(refs, names):
            name = str(body[0][1]) if ok else ""
            applications.append((ref, name, self.pid(ref[0])))

        return applications


    def pid(self, bus_name):

        if bus_name in self._pid_cache:
            return self._pid_cache[bus_name]

        try:
            pid = int(
                self.call(
                    ("org.freedesktop.DBus", "/org/freedesktop/DBus"),
                    "org.freedesktop.DBus",
                    "GetConnectionUnixProcessID",
                    "s",
                    (bus_name,),
                )[0]
            )
        except (AtspiError, IndexError, TypeError, ValueError):
            pid = None

        self._pid_cache[bus_name] = pid

        return pid


    # -----------------------------------------------------
    # NODOS
    # -----------------------------------------------------

    def describe(self, refs, with_children=True):
        """
        Rol, nombre, estados (y opcionalmente hijos) de varios
        nodos en un solo lote. Los que ya no existen vuelven
        como None.
        """

        refs = list(refs)
        requests = []

        for ref in refs:
            requests.append((ref, ACCESSIBLE, "GetRole", None, ()))
            requests.append(self.property_request(ref, ACCESSIBLE, "Name"))
            requests.append((ref, ACCESSIBLE, "GetState", None, ()))
            if with_children:
                requests.append((ref, ACCESSIBLE, "GetChildren", None, ()))

        step = 4 if with_children else 3
        replies = self.call_many(requests)
        nodes = []

        for index, ref in enumerate(refs):

            role, name, state = replies[index * step:index * step + 3]

            if not role[0]:
                nodes.append(None)
                continue

            children = []
            if with_children:
                ok, body = replies[index * step + 3]
                if ok:
                    children = [
                        tuple(child) for child in body[0]
                        if child[1] != NULL_PATH
                    ]

            nodes.append({
                "ref": ref,
                "role": int(role[1][0]),
                "name": str(name[1][0][1]).strip() if name[0] else "",
                "states": states_from_body(state[1][0]) if state[0] else 0,
                "children": children,
            })

        return nodes


    def states(self, ref):

        return states_from_body(self.call(ref, ACCESSIBLE, "GetState")[0])


    def exists(self, ref):
        """
        False si el objeto ya no existe. Un bus cortado no prueba
        nada: ahí se lanza AtspiError.
        """

        ok, body = self.call_many([(ref, ACCESSIBLE, "GetState", None, ())])[0]

        if not ok:
            return False

        return not has_state(states_from_body(body[0]), STATE_DEFUNCT)


    def action_names(self, ref):

        # Chromium devuelve nombres vacíos en GetActions: se piden
        # uno por uno con GetName.
        ok, body = self.call_many([self.property_request(ref, ACTION, "NActions")])[0]
        count = int(body[0][1]) if ok else 0

        names = self.call_many(
            (ref, ACTION, "GetName", "i", (index,)) for index in range(count)
        )

        return [str(body[0]) if ok else "" for ok, body in names]


    def do_action(self, ref, index):

        return bool(self.call(ref, ACTION, "DoAction", "i", (int(index),))[0])
