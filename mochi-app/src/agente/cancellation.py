import socket
import threading


# =========================================================
# CANCELAR UNA ORDEN EN CURSO
#
# MochiCommandService.execute() abre una orden con begin() y
# la cierra con end(). Mientras tanto, cancel() (desde otro
# hilo) hace dos cosas:
#
# - corta la petición HTTP al LLM que esté esperando
#   (cierra el socket: llama-server deja de generar);
# - hace que la próxima consulta al LLM o la próxima
#   herramienta levante CommandCancelled en vez de correr.
#
# Una herramienta que ya está ejecutándose termina sola:
# no se puede deshacer lo que hizo.
#
# CommandCancelled hereda de BaseException para que ningún
# "except Exception" del agente la trague. Los hilos que no
# abrieron una orden (precalentamiento) nunca la reciben.
# =========================================================

class CommandCancelled(BaseException):
    pass


class _Token:

    def __init__(self):

        self.event = threading.Event()
        self.connections = set()
        self.lock = threading.Lock()


_local = threading.local()
_active = None
_active_lock = threading.Lock()


def begin():
    """Abre una orden en este hilo y devuelve su token."""

    global _active

    token = _Token()
    _local.token = token

    with _active_lock:
        _active = token

    return token


def end(token):

    global _active

    _local.token = None

    with _active_lock:
        if _active is token:
            _active = None


def cancel():
    """Cancela la orden en curso. Devuelve False si no había ninguna."""

    with _active_lock:
        token = _active

    if token is None:
        return False

    token.event.set()

    with token.lock:
        connections = list(token.connections)

    for connection in connections:
        _abort(connection)

    return True


def _abort(connection):

    sock = getattr(connection, "sock", None)

    if sock is None:
        return

    # shutdown despierta al hilo que está esperando la respuesta;
    # close solo no alcanza mientras otro hilo está en recv().
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def is_cancelled():

    token = getattr(_local, "token", None)
    return token is not None and token.event.is_set()


def check():
    """Levanta CommandCancelled si la orden de este hilo se canceló."""

    if is_cancelled():
        raise CommandCancelled()


def watch(connection):
    """Registra una conexión HTTP para que cancel() pueda cortarla."""

    token = getattr(_local, "token", None)

    if token is None:
        return

    with token.lock:
        token.connections.add(connection)

    # Si se canceló justo antes de registrarla.
    if token.event.is_set():
        _abort(connection)


def unwatch(connection):

    token = getattr(_local, "token", None)

    if token is None:
        return

    with token.lock:
        token.connections.discard(connection)
