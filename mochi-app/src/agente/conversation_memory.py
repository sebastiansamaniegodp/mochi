import threading
import time


# =========================================================
# MEMORIA DE LA CONVERSACIÓN
#
# Mochi recuerda las órdenes anteriores y sus respuestas:
# van en cada petición entre el prompt de sistema y la
# orden nueva, como mensajes user/assistant. Así se puede
# conversar y encadenar órdenes ("abrí YouTube" ... "ahora
# poné música").
#
# Se guarda la orden tal como se mandó (con la hora y la
# indicación de voz) y la respuesta final. Las herramientas
# y sus resultados no: una página ocupa miles de tokens.
#
# Caché de llama-server: como la orden se guarda idéntica,
# cada petición empieza igual que la anterior y solo se
# procesa lo nuevo. Al compactar o al olvidar cambia el
# comienzo: hay que precalentar otra vez.
#
# - compactar: cuando la conversación (sistema + tools +
#   historial) llega al 80 % del contexto, las órdenes
#   viejas se reemplazan por un resumen que escribe el LLM.
#   Quedan enteras las últimas KEEP_TURNS;
# - olvidar: después de media hora sin órdenes.
# =========================================================

# Compactar cuando la conversación ocupa esta parte del
# contexto de llama-server.
COMPACT_AT = 0.8

# Después de compactar, las órdenes que quedan enteras no
# pueden ocupar más que esto (si no, quedan menos).
KEEP_AT_MOST = 0.5

KEEP_TURNS = 4

FORGET_AFTER_SECONDS = 30 * 60

# Medido con /tokenize de llama-server (Qwen3.5, español):
# ~3,3 caracteres por token. Con 3 se estima de más.
CHARS_PER_TOKEN = 3.0

# Lo que agrega la plantilla a cada mensaje
# (<|im_start|>user\n ... <|im_end|>\n).
MESSAGE_TOKENS = 6

# Hasta que el precalentamiento los mide: prompt de
# sistema + tools, y el -c de llama-server.
DEFAULT_BASE_TOKENS = 4850
DEFAULT_CONTEXT_SIZE = 16384

MAX_SUMMARY_CHARS = 1500

SUMMARY_PREFIX = (
    "Resumen de lo que hablamos antes "
    "(es contexto, no una orden): "
)

SUMMARY_REPLY = "Entendido."

# Se manda con el mismo sistema, las mismas tools y el
# mismo historial que una orden: así usa la caché.
SUMMARY_REQUEST = (
    "Pausa interna, no es una orden: no uses herramientas. "
    "Resumí la conversación hasta acá en un párrafo de 120 "
    "palabras como máximo: qué pidió el usuario, qué hiciste, "
    "qué quedó pendiente y los datos que convenga recordar "
    "(nombres, sitios, archivos, preferencias). Respondé solo "
    "con el resumen."
)


def estimate_tokens(text):

    return (
        int(len(text or "") / CHARS_PER_TOKEN)
        + MESSAGE_TOKENS
    )


class ConversationMemory:

    def __init__(self):

        self.base_tokens = DEFAULT_BASE_TOKENS
        self.context_size = DEFAULT_CONTEXT_SIZE

        self._summary = ""

        # [(orden tal como se mandó, respuesta)]
        self._turns = []

        # Cuándo llegó la última orden (para olvidar).
        self._last = None
        self._lock = threading.Lock()


    # =====================================================
    # LEER
    # =====================================================

    def messages(self):
        """El historial para poner entre el sistema y la orden."""

        with self._lock:
            return self._messages(self._summary, self._turns)


    @staticmethod
    def _messages(summary, turns):

        history = []

        if summary:
            history.append({
                "role": "user",
                "content": SUMMARY_PREFIX + summary,
            })
            history.append({
                "role": "assistant",
                "content": SUMMARY_REPLY,
            })

        for command, reply in turns:
            history.append({
                "role": "user",
                "content": command,
            })
            history.append({
                "role": "assistant",
                "content": reply,
            })

        return history


    @property
    def empty(self):

        with self._lock:
            return not self._summary and not self._turns


    def _tokens(self, summary, turns):

        return self.base_tokens + sum(
            estimate_tokens(message["content"])
            for message in self._messages(summary, turns)
        )


    def usage(self):
        """Parte del contexto que ocupa la conversación (0 a 1)."""

        with self._lock:
            tokens = self._tokens(self._summary, self._turns)

        return tokens / self.context_size


    def describe(self):

        with self._lock:
            turns = len(self._turns)
            summary = " + resumen" if self._summary else ""

        return (
            f"{turns} órdenes{summary}, "
            f"{round(self.usage() * 100)} % del contexto"
        )


    def expired(self):

        with self._lock:
            return (
                self._last is not None
                and time.monotonic() - self._last >= FORGET_AFTER_SECONDS
            )


    # =====================================================
    # ESCRIBIR
    # =====================================================

    def record(self, command, reply):

        if not command or not reply:
            return

        with self._lock:
            self._last = time.monotonic()
            self._turns.append((command, reply))


    def clear(self):

        with self._lock:
            self._summary = ""
            self._turns = []
            self._last = None


    def shrink(self):
        """Se llenó el contexto en medio de una orden: queda solo la última."""

        with self._lock:
            self._summary = ""
            self._turns = self._turns[-1:]


    # =====================================================
    # COMPACTAR
    # =====================================================

    def needs_compaction(self):

        with self._lock:
            return (
                len(self._turns) > 1
                and self._tokens(self._summary, self._turns)
                >= COMPACT_AT * self.context_size
            )


    def compact(self, summarize):
        """
        Reemplaza las órdenes viejas (y el resumen anterior)
        por un resumen nuevo. summarize(historial) devuelve
        el texto ("" si falló: se descartan sin resumir).
        Quien llama no deja grabar órdenes mientras tanto.
        """

        with self._lock:
            keep = min(KEEP_TURNS, len(self._turns) - 1)

            while keep > 0 and (
                self._tokens("", self._turns[-keep:])
                > KEEP_AT_MOST * self.context_size
            ):
                keep -= 1

            old = self._turns[:len(self._turns) - keep]
            old_messages = self._messages(self._summary, old)
            previous_summary = self._summary

        # Afuera del lock: el LLM tarda unos segundos.
        summary = " ".join((summarize(old_messages) or "").split())

        if len(summary) > MAX_SUMMARY_CHARS:
            summary = summary[:MAX_SUMMARY_CHARS].rsplit(" ", 1)[0] + "…"

        with self._lock:
            self._summary = summary or previous_summary
            self._turns = self._turns[len(old):]
