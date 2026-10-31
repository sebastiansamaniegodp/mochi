"""Model-backed understanding for "a video of a YouTube channel" orders.

The web specialists recognize the usual phrasings ("el último video de X",
"el penúltimo video que subió X").  Anything else that sounds like a
channel video ("lo más nuevo del rubius", "poné el penúltimo" after a
previous channel order) is read here by the model, which answers a small
JSON: channel, order and position.  The code validates it and builds the
same deterministic plan as the rules (search the channel, Videos tab,
order, N-th video, play), so the model never touches the page itself.

The question goes with the same system prompt, tools and history as a
normal order, as the last user message: llama-server reuses its cache and
the next order does not have to reprocess the whole prompt (see the cache
notes in conversation_memory).

A channel that is neither in the sentence nor in the conversation, a
position out of range or a broken answer yields None and the command goes
to the regular agent loop.
"""

import json
import re
import unicodedata

from brain.web_specialists import SpecialistRequest


MAX_POSITION = 10

# Sentences worth asking about: they talk about a video/channel and about
# its place in time ("último", "penúltimo", "más nuevo", "primero"...).
_VIDEO_WORDS = re.compile(r"\b(?:videos?|canal|youtube|youtuber|subio|publico|subido|publicado)\b")
_ORDER_WORDS = re.compile(
    r"\b(?:ultim\w*|penult\w*|antepenult\w*|reciente\w*|nuev\w*|primer\w*|"
    r"antigu\w*|viej\w*|segund\w*|tercer\w*|cuart\w*|quint\w*|anterior)\b"
)

REQUEST = (
    "Pausa interna, no es una orden: no uses herramientas. "
    "¿La orden «{command}» pide reproducir un video de un canal o youtuber "
    "de YouTube, elegido por cuándo lo publicó? Si nombra el puesto sin el "
    "canal (\"poné el penúltimo\"), el canal es el de la conversación. "
    "Respondé solo un JSON {{\"canal\": ..., \"contando_desde\": ..., "
    "\"puesto\": ...}}: contando_desde es \"el_mas_nuevo\" o "
    "\"el_mas_viejo\" y puesto empieza en 1. Ejemplos: "
    "\"el último de X\", \"lo más nuevo de X\" -> el_mas_nuevo 1; "
    "\"el penúltimo\", \"el anterior al último\" -> el_mas_nuevo 2; "
    "\"el antepenúltimo\" -> el_mas_nuevo 3; "
    "\"el primero que subió X\" -> el_mas_viejo 1; "
    "\"el segundo que subió X\" -> el_mas_viejo 2. "
    "Si la orden es otra cosa o no sabés el canal: {{\"canal\": null}}."
)


def _norm(value):
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^\w@]+", " ", text)).strip()


def mentions_channel_video(command):
    text = _norm(command)
    return bool(_ORDER_WORDS.search(text)) and (
        bool(_VIDEO_WORDS.search(text))
        # "lo más nuevo del rubius": el puesto y de quién.
        or bool(re.search(r"\b(?:de|del)\s+\w", text))
        # "poné el penúltimo": solo el puesto, el canal sale del historial.
        or len(text.split()) <= 4
    )


def _parse_answer(text):
    match = re.search(r"\{.*\}", str(text or ""), re.S)
    if not match:
        return None
    try:
        answer = json.loads(match.group(0))
    except ValueError:
        return None
    return answer if isinstance(answer, dict) else None


def intent_to_request(answer, command, history=None, context_provider=None):
    """Validate the model's answer and build the YouTube channel request."""

    if not isinstance(answer, dict):
        return None
    channel = answer.get("canal")
    if not isinstance(channel, str):
        return None
    channel = re.sub(r"^(?:el|la|los|las)\s+", "", channel.strip().strip("\"'«»“”@"), flags=re.I)
    normalized = _norm(channel)
    if not normalized or len(channel) > 60:
        return None
    # El canal se copia de la orden o de la conversación, nunca se inventa.
    said = " ".join(
        [_norm(command)]
        + [_norm(message.get("content")) for message in history or [] if isinstance(message, dict)]
    )
    if f" {normalized} " not in f" {said} ":
        return None

    order = answer.get("contando_desde")
    if order not in {"el_mas_nuevo", "el_mas_viejo"}:
        return None
    position = answer.get("puesto")
    if isinstance(position, bool) or not isinstance(position, int):
        return None
    if not 1 <= position <= MAX_POSITION:
        return None

    request = SpecialistRequest(provider="YouTube")
    request.channel = channel
    request.order = "oldest" if order == "el_mas_viejo" else "newest"
    request.result_index = position
    request.result_scope = "visible"
    # Con YouTube ya abierto se sigue en esa pestaña, como las reglas.
    request.reuse_current = str(context_provider or "").casefold() == "youtube"
    return request


def interpret_channel_video(router, command, messages, tools, context_provider=None):
    """Ask the model whether the order is a channel video and which one.

    ``messages`` is the normal order request (system, history, order) and
    ``tools`` the same tools as the warm-up, so the cache is reused.
    Returns a SpecialistRequest or None.
    """

    fast = getattr(router, "fast", None)
    if fast is None or not messages or not mentions_channel_video(command):
        return None
    question = {"role": "user", "content": REQUEST.format(command=str(command).strip())}
    try:
        result = fast(
            messages=list(messages[:-1]) + [question],
            tools=tools,
            options={"temperature": 0, "num_predict": 80},
        )
    except Exception:
        return None
    if not getattr(result, "success", False) or getattr(result, "tool_calls", None):
        return None
    history = [
        message for message in messages[1:-1]
        if isinstance(message, dict) and message.get("role") in {"user", "assistant"}
    ]
    return intent_to_request(
        _parse_answer(getattr(result, "text", "")),
        command,
        history=history,
        context_provider=context_provider,
    )
