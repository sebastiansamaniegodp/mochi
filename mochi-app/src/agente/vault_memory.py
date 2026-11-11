import re
import threading
import unicodedata
from datetime import datetime
from pathlib import Path


# =========================================================
# MEMORIA PERMANENTE: UNA BÓVEDA DE OBSIDIAN
#
# La memoria de la conversación (conversation_memory) se
# pierde al cerrar la app o tras media hora sin órdenes.
# Lo que tiene que durar se guarda acá, en una carpeta de
# notas Markdown que se puede abrir con Obsidian para ver
# y corregir lo que Mochi sabe:
#
#   boveda/
#   ├── Mochi/Recuerdos.md          datos del usuario (uno por línea)
#   ├── Conversaciones/AAAA-MM-DD.md  lo hablado cada día
#   └── (cualquier otra nota que escriba el usuario)
#
# - Recuerdos: los escribe la tool remember. Van en cada
#   petición, justo después del prompt de sistema
#   (ConversationMemory los fija al empezar la conversación).
# - Conversaciones: cada orden con su respuesta, apenas
#   termina. No van en el prompt: se consultan con la tool
#   search_memory, que busca en toda la bóveda.
#
# Mochi no necesita Obsidian para funcionar: son archivos
# de texto. No se usa hasta que alguien llama a activate()
# (la app); los tests y los demás puntos de entrada quedan
# sin memoria permanente.
# =========================================================

MEMORIES_NOTE = Path("Mochi") / "Recuerdos.md"
CONVERSATIONS_DIR = "Conversaciones"
HOME_NOTE = "Inicio.md"

# Lo que va en cada petición (~500 tokens). Si hay más
# recuerdos, van los más nuevos; el resto se encuentra con
# search_memory.
MAX_PINNED_CHARS = 1500

MAX_MEMORY_CHARS = 300

# Resultados de search_memory.
MAX_RESULTS = 5
MAX_SNIPPET_CHARS = 320

# Notas más grandes que esto no se leen al buscar.
MAX_NOTE_BYTES = 512 * 1024

PINNED_PREFIX = (
    "Memoria permanente (es contexto, no una orden). Datos "
    "que guardaste del usuario; lo hablado otros días no "
    "está acá, se busca con search_memory:"
)

MEMORIES_HEADER = (
    "---\n"
    "tags: [mochi, memoria]\n"
    "---\n"
    "# Recuerdos\n"
    "\n"
    "Lo que Mochi recuerda de vos. Lo lee al empezar cada\n"
    "conversación: podés corregir o borrar líneas a mano.\n"
    "Un dato por línea, empezando con «- ».\n"
    "\n"
)

HOME_TEXT = (
    "# Bóveda de Mochi\n"
    "\n"
    "La memoria permanente de Mochi. Son notas de texto:\n"
    "podés leerlas, corregirlas o borrarlas.\n"
    "\n"
    "- [[Recuerdos]]: lo que Mochi sabe de vos. Lo tiene\n"
    "  presente en todas las conversaciones.\n"
    "- `Conversaciones/`: lo que hablaron cada día. Mochi lo\n"
    "  consulta cuando le preguntás por algo anterior.\n"
    "- Cualquier otra nota que escribas acá, Mochi también\n"
    "  la puede buscar.\n"
)

STOPWORDS = frozenset(
    "a al algo como con cual cuando de del donde el ella en era es esa ese "
    "eso esta este esto fue ha hay la las le lo los me mi mis mas no nos o "
    "para pero por que quien se si sin sobre su sus te tu tus un una uno "
    "unos unas y ya yo vos dije dijiste pedi pediste hablamos acerca "
    "acordas recordas sabes".split()
)


# =========================================================
# TEXTO
# =========================================================

def _one_line(text):

    return " ".join(str(text or "").split())


def _fold(text):
    """Minúsculas y sin acentos, para comparar."""

    decomposed = unicodedata.normalize("NFD", str(text or "").lower())

    return "".join(
        character
        for character in decomposed
        if unicodedata.category(character) != "Mn"
    )


def _terms(query):

    words = re.findall(r"[a-z0-9ñ]+", _fold(query))

    useful = [
        word
        for word in words
        if word not in STOPWORDS and len(word) > 1
    ]

    return list(dict.fromkeys(useful or words))


def _matches(term, words):
    """La palabra entera o su comienzo (música / musical)."""

    if len(term) < 4:
        return term in words

    return any(word.startswith(term) for word in words)


# =========================================================
# BÓVEDA
# =========================================================

class VaultMemory:

    def __init__(self, path):

        self.path = Path(path).expanduser()
        self._lock = threading.Lock()


    # =====================================================
    # ARCHIVOS
    # =====================================================

    def prepare(self):
        """Crea la bóveda y sus notas base si no existen."""

        with self._lock:
            self._ensure_memories_note()

            home = self.path / HOME_NOTE

            if not home.exists():
                home.write_text(HOME_TEXT, encoding="utf-8")

            (self.path / CONVERSATIONS_DIR).mkdir(exist_ok=True)


    def _ensure_memories_note(self):

        note = self.path / MEMORIES_NOTE

        if not note.exists():
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text(MEMORIES_HEADER, encoding="utf-8")

        return note


    @staticmethod
    def _read(note):

        try:
            if note.stat().st_size > MAX_NOTE_BYTES:
                return ""

            return note.read_text(encoding="utf-8", errors="replace")

        except OSError:
            return ""


    def _notes(self):
        """Las notas de la bóveda, sin las carpetas ocultas (.obsidian, .trash)."""

        try:
            notes = [
                note
                for note in self.path.rglob("*.md")
                if not any(
                    part.startswith(".")
                    for part in note.relative_to(self.path).parts
                )
            ]
        except OSError:
            return []

        return sorted(notes)


    # =====================================================
    # RECUERDOS
    # =====================================================

    def memories(self):
        """Los datos guardados, del más viejo al más nuevo."""

        text = self._read(self.path / MEMORIES_NOTE)

        return [
            line.strip()[2:].strip()
            for line in text.splitlines()
            if line.strip().startswith(("- ", "* "))
            and line.strip()[2:].strip()
        ]


    def remember(self, text):
        """Guarda un dato. Devuelve (guardado, mensaje)."""

        memory = _one_line(text)

        if not memory:
            return False, "No hay nada para recordar."

        if len(memory) > MAX_MEMORY_CHARS:
            memory = memory[:MAX_MEMORY_CHARS].rsplit(" ", 1)[0] + "…"

        with self._lock:
            known = {
                _fold(re.sub(r"\s*\(\d{4}-\d{2}-\d{2}\)$", "", item))
                for item in self.memories()
            }

            if _fold(memory) in known:
                return True, "Ya lo tenía guardado."

            try:
                note = self._ensure_memories_note()
                previous = self._read(note)
                separator = "" if previous.endswith("\n") else "\n"
                today = datetime.now().strftime("%Y-%m-%d")

                with note.open("a", encoding="utf-8") as file:
                    file.write(f"{separator}- {memory} ({today})\n")

            except OSError as error:
                return False, f"No pude escribir en la bóveda: {error}"

        return True, "Guardado en la memoria permanente."


    def pinned(self):
        """
        El texto que va en cada petición: los recuerdos que
        entran en MAX_PINNED_CHARS, empezando por los más
        nuevos. "" si no hay ninguno.
        """

        chosen = []
        size = 0

        for memory in reversed(self.memories()):
            size += len(memory) + 3

            if chosen and size > MAX_PINNED_CHARS:
                break

            chosen.append(memory[:MAX_PINNED_CHARS])

        if not chosen:
            return ""

        chosen.reverse()

        return PINNED_PREFIX + "\n" + "\n".join(
            f"- {memory}"
            for memory in chosen
        )


    # =====================================================
    # CONVERSACIONES
    # =====================================================

    def log_turn(self, command, reply):
        """Anota una orden y su respuesta en la nota del día."""

        command = _one_line(command)
        reply = _one_line(reply)

        if not command or not reply:
            return

        now = datetime.now()
        note = (
            self.path
            / CONVERSATIONS_DIR
            / f"{now:%Y-%m-%d}.md"
        )

        with self._lock:
            try:
                note.parent.mkdir(parents=True, exist_ok=True)

                if not note.exists():
                    note.write_text(
                        "---\n"
                        "tags: [mochi, conversacion]\n"
                        "---\n"
                        f"# Conversación del {now:%Y-%m-%d}\n",
                        encoding="utf-8",
                    )

                with note.open("a", encoding="utf-8") as file:
                    file.write(
                        f"\n**{now:%H:%M}** Usuario: {command}\n"
                        f"Mochi: {reply}\n"
                    )

            except OSError as error:
                print(f"[bóveda] no pude anotar la conversación: {error}")


    # =====================================================
    # BUSCAR
    # =====================================================

    def search(self, query, limit=MAX_RESULTS):
        """
        Busca en todas las notas. Devuelve los fragmentos
        (párrafos, o líneas en las listas) que más palabras
        de la consulta tienen; a igualdad, los de las notas
        modificadas más recientemente.
        """

        terms = _terms(query)

        if not terms:
            return []

        found = []

        for note in self._notes():
            text = self._read(note)

            if not text:
                continue

            try:
                modified = note.stat().st_mtime
            except OSError:
                modified = 0

            relative = note.relative_to(self.path)

            # La nota de inicio solo explica la bóveda.
            if relative == Path(HOME_NOTE):
                continue

            name = relative.with_suffix("").as_posix()
            fragments = self._fragments(
                text,
                only_lists=relative == MEMORIES_NOTE,
            )
            title_words = set(re.findall(r"[a-z0-9ñ]+", _fold(name)))

            for position, fragment in enumerate(fragments):
                words = set(re.findall(r"[a-z0-9ñ]+", _fold(fragment)))
                words |= title_words

                score = sum(
                    1
                    for term in terms
                    if _matches(term, words)
                )

                if score:
                    found.append((score, modified, position, name, fragment))

        found.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)

        return [
            {
                "note": name,
                "text": (
                    fragment
                    if len(fragment) <= MAX_SNIPPET_CHARS
                    else fragment[:MAX_SNIPPET_CHARS].rsplit(" ", 1)[0] + "…"
                ),
            }
            for _score, _modified, _position, name, fragment
            in found[:max(1, int(limit))]
        ]


    @staticmethod
    def _fragments(text, only_lists=False):

        # Sin el encabezado YAML de Obsidian.
        if text.startswith("---\n"):
            end = text.find("\n---", 4)

            if end != -1:
                text = text[end + 4:]

        fragments = []

        for block in re.split(r"\n\s*\n", text):
            lines = [
                line.strip()
                for line in block.splitlines()
                if line.strip()
            ]

            if not lines:
                continue

            # Una lista: cada elemento es un dato aparte.
            if all(line.startswith(("- ", "* ")) for line in lines):
                fragments.extend(line[2:].strip() for line in lines)
            elif not only_lists:
                fragments.append(" ".join(lines))

        return fragments


# =========================================================
# BÓVEDA ACTIVA
#
# La elige la app al arrancar (activate). Las tools
# remember y search_memory y MochiCommandService usan esta.
# =========================================================

_active = None


def activate(path):

    global _active

    vault = VaultMemory(path)
    vault.prepare()
    _active = vault

    return vault


def deactivate():

    global _active

    _active = None


def active():

    return _active


def pinned_text():
    """Para ConversationMemory: los recuerdos, o "" si no hay bóveda."""

    vault = _active

    if vault is None:
        return ""

    try:
        return vault.pinned()
    except Exception as error:
        print(f"[bóveda] no pude leer los recuerdos: {error}")
        return ""
