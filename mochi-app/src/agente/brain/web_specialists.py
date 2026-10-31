"""Provider-scoped understanding for natural web/media commands.

The generic agent loop owns UI Automation and the dependency graph.  This
module owns the vocabulary that is genuinely provider-specific.  Keeping the
two concerns separate prevents a YouTube parsing fix from changing Netflix
profile/episode semantics (and vice versa).

The parsers are deliberately extractive: every recognised clause is returned
as data.  They never execute tools and never silently pick one intent while
discarding the others.
"""

from dataclasses import dataclass, field
import re
import unicodedata


def normal(value):
    text = unicodedata.normalize("NFD", str(value or "").strip().casefold())
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return re.sub(r"\s+", " ", text)


def clean(value):
    return str(value or "").strip(" \t\r\n.,;:!?\"")


# Verbos de "poné X en YouTube" con tú, vos y "me":
# pon, pone, poné, ponés, poneme, poner, reproduce, reproducí,
# reproducime...
PLAY_VERBS = r"(?:pon(?:[eé]s?)?(?:me)?|poner(?:me)?|reprod[uú]c(?:e|[ií]|ir)(?:me)?)"


_UNITS = {
    "cero": 0, "un": 1, "uno": 1, "una": 1, "primer": 1,
    "primero": 1, "primera": 1, "dos": 2, "segundo": 2,
    "segunda": 2, "tres": 3, "tercer": 3, "tercero": 3,
    "tercera": 3, "cuatro": 4, "cuarto": 4, "cuarta": 4,
    "cinco": 5, "quinto": 5, "quinta": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9,
}
_TEENS = {
    "diez": 10, "once": 11, "doce": 12, "trece": 13,
    "catorce": 14, "quince": 15, "dieciseis": 16,
    "diecisiete": 17, "dieciocho": 18, "diecinueve": 19,
    "veinte": 20,
}
_TENS = {
    "veinti": 20, "treinta": 30, "cuarenta": 40, "cincuenta": 50,
    "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90,
}


def spoken_number(value):
    """Parse a small spoken Spanish/English number used by UI commands."""

    text = normal(value).replace(",", ".")
    if not text:
        return None
    try:
        number = float(text)
        return int(number) if number.is_integer() else number
    except ValueError:
        pass
    if text in _UNITS:
        return _UNITS[text]
    if text in _TEENS:
        return _TEENS[text]
    if text.startswith("veinti") and text[6:] in _UNITS:
        return 20 + _UNITS[text[6:]]
    words = [word for word in text.split() if word != "y"]
    if not words:
        return None
    if words[0] in _TENS:
        return _TENS[words[0]] + (_UNITS.get(words[1], 0) if len(words) > 1 else 0)
    english = {
        "one": 1, "first": 1, "two": 2, "second": 2,
        "three": 3, "third": 3, "four": 4, "fourth": 4,
        "five": 5, "fifth": 5, "ten": 10, "fifteen": 15,
        "twenty": 20, "thirty": 30, "forty": 40, "ninety": 90,
    }
    return english.get(text)


_NUMBER_WORD = (
    r"\d+(?:[\.,]\d+)?|cero|un(?:o|a)?|primer(?:o|a)?|dos|segund[oa]|"
    r"tres|tercer[oa]?|cuatro|cuart[oa]|cinco|quint[oa]|seis|siete|ocho|"
    r"nueve|diez|once|doce|trece|catorce|quince|diecis[eé]is|diecisiete|"
    r"dieciocho|diecinueve|veinte|veinti\w+|treinta(?:\s+y\s+\w+)?|"
    r"cuarenta(?:\s+y\s+\w+)?|cincuenta(?:\s+y\s+\w+)?|"
    r"sesenta(?:\s+y\s+\w+)?|setenta(?:\s+y\s+\w+)?|"
    r"ochenta(?:\s+y\s+\w+)?|noventa(?:\s+y\s+\w+)?|"
    r"one|first|two|second|three|third|four|fourth|five|fifth|ten|"
    r"fifteen|twenty|thirty|forty|ninety"
)


@dataclass
class MediaOperation:
    operation: str
    value: str | None = None
    position: int = 0


@dataclass
class SpecialistRequest:
    provider: str
    browser_profile: str | None = None
    site_profile_source: str | None = None
    site_profile: str | None = None
    query: str | None = None
    result_title: str | None = None
    channel: str | None = None
    order: str | None = None
    channel_only: bool = False
    result_scope: str | None = None
    result_index: int | None = None
    list_limit: int | None = None
    list_only: bool = False
    reuse_current: bool = False
    season: int | None = None
    episode: int | None = None
    episode_kind: str | None = None
    media: list[MediaOperation] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)

    @property
    def has_work(self):
        return bool(
            self.query or self.channel or self.site_profile or self.result_index
            or self.result_title or self.season or self.episode or self.media
            or self.list_only or self.unsupported
        )


def _browser_profile(command):
    patterns = [
        r"\bcuenta\s+de\s+google\s+de\s+(.+?)(?=\s*(?:,|;|\by\b|$))",
        r"\bchrome\s+de\s+(.+?)(?=\s*(?:,|;|\by\b|$))",
        r"\bperfil\s+de\s+navegador\s+(.+?)(?=\s*(?:,|;|\by\b|$))",
    ]
    for pattern in patterns:
        match = re.search(pattern, command, re.I)
        if match:
            return clean(match.group(1))
    return None


def _media_operations(command):
    """Return every media clause in spoken order."""

    text = normal(command)
    found = []

    def add(match, operation, value=None):
        found.append(MediaOperation(operation, value, match.start()))

    for match in re.finditer(
        r"\b(?:reanuda(?:lo|la)?|continua(?:lo|la)?|sigue|reproduce(?:lo|la)?)\b",
        text,
    ):
        add(match, "play_media", "Reproducir")
    for match in re.finditer(
        r"\b(?:paus(?:a|ar)(?:lo|la)?|deten(?:lo|la)?|paralo|parala|pare|"
        r"deja(?:lo|la)?\s+pausad[oa]|"
        r"para\s+(?:el|la)\s+(?:video|serie|pelicula|episodio|capitulo|reproduccion))\b",
        text,
    ):
        add(match, "pause_media", "Pausa")
    for match in re.finditer(r"\b(?:pasa|ve)\s+al\s+(?:(?:episodio|video)\s+)?siguiente\b|\b(?:siguiente|proximo)\s+(?:episodio|video)\b", text):
        add(match, "next_media")
    for match in re.finditer(r"\b(?:pasa|vuelve|ve)\s+al\s+(?:(?:episodio|video)\s+)?anterior\b|\b(?:episodio|video)\s+anterior\b", text):
        add(match, "previous_media")
    for match in re.finditer(
        rf"\b(?P<verb>avanza|adelanta|retrocede|rebobina)\w*\s+"
        rf"(?P<number>{_NUMBER_WORD})\s*(?P<unit>segundos?|seg|minutos?|min)\b",
        text,
    ):
        amount = spoken_number(match.group("number"))
        if amount is None:
            continue
        if match.group("unit").startswith("min"):
            amount *= 60
        if match.group("verb").startswith(("retro", "rebob")):
            amount *= -1
        add(match, "adjust_media_position", f"{amount:g}")
    for match in re.finditer(
        rf"\b(?:ve|ir|pon|pone|lleva|salta|empieza|comienza)\w*"
        rf"(?:\s+(?:el\s+video\s+)?)?.{{0,18}}?\bminuto\s+(?P<number>{_NUMBER_WORD})\b",
        text,
    ):
        amount = spoken_number(match.group("number"))
        if amount is not None:
            add(match, "seek_media", f"{amount * 60:g}")
    for match in re.finditer(r"\b(?:desde|del)\s+(?:el\s+)?(?:inicio|comienzo)\b", text):
        add(match, "seek_media", "0")
    for match in re.finditer(
        rf"\b(?P<verb>baja|reduce|disminuye|sube|aumenta)\w*"
        rf"(?:\s+(?:un\s+poco|el\s+volumen))?(?:\s+(?:en|un|unos?))?\s*"
        rf"(?:(?P<number>{_NUMBER_WORD})\s*(?:por\s+ciento|%))?",
        text,
    ):
        # Do not turn a bare unrelated "sube" into volume.  Either volume,
        # a percentage or the conventional "un poco" must be present.
        phrase = match.group(0)
        if not any(token in phrase for token in {"volumen", "por ciento", "%", "un poco"}):
            continue
        amount = spoken_number(match.group("number")) if match.group("number") else 10
        if amount is None:
            amount = 10
        if match.group("verb").startswith(("baja", "reduce", "disminuye")):
            amount *= -1
        add(match, "adjust_volume", f"{amount:g}")

    # "abre/reproduce el episodio" is a content selection instruction, not
    # evidence that a player is already available.  Add an initial play after
    # selection only when the user explicitly asks for playback or a later
    # player operation requires it; the goal builder handles that dependency.
    unique = []
    seen = set()
    for operation in sorted(found, key=lambda item: item.position):
        key = (operation.operation, operation.value, operation.position)
        if key not in seen:
            seen.add(key)
            unique.append(operation)
    return unique


def _ordinal_near_video(command):
    text = normal(command)
    matches = list(re.finditer(
        rf"\b(?P<number>{_NUMBER_WORD})\s+(?:video|resultado|opcion)\b|"
        rf"\b(?:video|resultado|opcion)\s+(?:numero\s+)?(?P<number_after>{_NUMBER_WORD})\b",
        text,
    ))
    if not matches:
        return None, None
    match = matches[-1]
    value = spoken_number(match.group("number") or match.group("number_after"))
    if not isinstance(value, (int, float)) or value < 1:
        return None, None
    scope = "recommended" if re.search(r"\b(?:recomendad|sugerid|relacionad)\w*\b", text) else "visible"
    return int(value), scope


def _list_request(command):
    text = normal(command)
    if not re.search(r"\b(?:muestra|mostra|mostrame|dime|decime|lista|enumera)\w*\b", text):
        return None
    count = re.search(rf"\b(?P<number>{_NUMBER_WORD})\s+(?:videos?|resultados?|opciones?)\b", text)
    value = spoken_number(count.group("number")) if count else 5
    return max(1, min(10, int(value or 5)))


def _listed_subject(command, provider):
    """Extract the subject of an explicit result-list request."""
    match = re.search(
        rf"\b(?:muestra|mostra|mostrame|dime|decime|lista|enumera)\w*\s+"
        rf"(?:{_NUMBER_WORD})?\s*(?:videos?|resultados?|opciones?)\s+"
        rf"(?:sobre|de)?\s*(?P<query>.+?)\s+(?:en|dentro\s+de)\s+"
        rf"(?:el\s+)?{provider}\b",
        command,
        re.I,
    )
    return clean(match.group("query")) if match else None


def _strip_query_tail(value):
    text = clean(value)
    # These clauses modify result handling; they are not literal search text.
    return clean(re.split(
        r"\s*(?:,|;)\s*|\s+y\s+(?=(?:reproduce|abre|elige|selecciona|pon|"
        r"pausa|deten|para|dime|decime|muestra|mostra|ordena|entra|ve|"
        r"adelanta|retrocede|baja|sube|no\s+reproduzcas)\b)",
        text,
        maxsplit=1,
        flags=re.I,
    )[0])


class YouTubeSpecialist:
    provider = "YouTube"

    def parse(self, command):
        text = normal(command)
        if "youtube" not in text:
            return None
        provider_match = re.search(r"youtube", command, re.I)
        request = SpecialistRequest(provider=provider_match.group(0) if provider_match else self.provider)
        request.browser_profile = _browser_profile(command)
        request.reuse_current = bool(re.search(
            r"\b(?:ya\s+esta\s+abiert|que\s+ya\s+esta\s+abiert|sin\s+abrir\s+otra\s+pestana|"
            r"mism[oa]\s+(?:pestana|video)|(?:contenido|video)\s+que\s+esta\s+abiert|actual)\w*\b",
            text,
        ))
        request.media = _media_operations(command)
        request.list_limit = _list_request(command)
        request.list_only = bool(
            request.list_limit
            and not any(item.operation == "play_media" for item in request.media)
        )
        if request.list_limit:
            request.query = _listed_subject(command, r"youtube")

        latest_patterns = [
            r"\b(?:el\s+)?video\s+mas\s+reciente(?:\s+publicado|\s+subido)?\s+"
            r"(?:de|del|por)\s+(?:el\s+)?(?:canal(?:\s+de)?\s+)?(?P<channel>.+?)"
            r"(?=\s*(?:,|;|\ba\s+youtube\b|\ben\s+youtube\b|\ben\s+(?:su\s+)?canal|\by\b|$))",
            rf"\bultimos?\s+(?:{_NUMBER_WORD})\s+videos?\s+(?:publicados?\s+)?"
            r"(?:de|del|por)\s+(?:el\s+)?(?P<channel>.+?)"
            r"(?=\s*(?:,|;|\ba\s+youtube\b|\ben\s+youtube\b|\ben\s+(?:su\s+)?canal|\by\b|$))",
            r"\b(?:el|al)?\s*ultimo\s+video(?:\s+publicado|\s+subido)?\s+(?:de|del|por)\s+(?:el\s+)?(?P<channel>.+?)(?=\s*(?:,|;|\ba\s+youtube\b|\ben\s+youtube\b|\ben\s+(?:su\s+)?canal|\by\b|$))",
            r"\bultimo\s+video\s+que\s+(?:publico|subio)\s+(?:el\s+)?(?P<channel>.+?)(?=\s*(?:,|;|\ba\s+youtube\b|\ben\s+youtube\b|\ben\s+su\s+canal|\by\b|$))",
            r"\blo\s+ultimo\s+que\s+(?:publico|subio)\s+(?:el\s+)?(?P<channel>.+?)(?=\s*(?:,|;|\ba\s+youtube|\ben\s+youtube|$))",
        ]
        oldest_patterns = [
            r"\b(?:el|al)?\s*primer\s+video(?:\s+publicado|\s+subido)?\s+(?:de|del|por)\s+"
            r"(?:(?:el\s+)?canal(?:\s+de)?\s+)?(?:el\s+)?(?P<channel>.+?)"
            r"(?=\s*(?:,|;|\ba\s+youtube\b|\ben\s+youtube\b|\ben\s+(?:su\s+)?canal|\by\b|$))",
            r"\bvideo\s+mas\s+antiguo\s+(?:de|del)\s+"
            r"(?:(?:el\s+)?canal(?:\s+de)?\s+)?(?P<channel>.+?)"
            r"(?=\s*(?:,|;|\ba\s+youtube\b|\ben\s+youtube\b|\by\b|$))",
        ]
        # "el penúltimo video de X" es el segundo más reciente: se busca
        # como "el último" y después se elige el 2.º de la lista.
        rank_index = None
        if re.search(r"\bantepenult\w*\b", text):
            rank_index = 3
        elif re.search(r"\bpenult\w*\b", text):
            rank_index = 2
        channel_text = re.sub(r"\b(?:ante)?penult\w*\b", "ultimo", text)
        for order, patterns in (("newest", latest_patterns), ("oldest", oldest_patterns)):
            for pattern in patterns:
                match = re.search(pattern, channel_text, re.I)
                if match:
                    request.channel = clean(re.sub(r"^(?:el|la)\s+", "", match.group("channel"), flags=re.I))
                    request.order = order
                    break
            if request.channel:
                break

        if not request.channel:
            enter = re.search(
                r"\bentra(?:r)?\s+(?:al|en\s+el)\s+canal(?:\s+principal)?\s+(?:de\s+)?"
                r"(?P<channel>.+?)(?=\s*(?:,|;|\ben\s+youtube\b|\bsin\s+abrir\b|"
                r"\by\s+(?:ordena|reproduce|abre|pon)|$))",
                text,
                re.I,
            )
            if enter:
                request.channel = clean(enter.group("channel"))
                request.channel_only = bool(re.search(
                    r"\b(?:sin|no)\s+(?:abrir|reproducir)\b",
                    normal(command[enter.end():]),
                )) or not bool(re.search(
                    r"\b(?:video|reproduce|abre|pon|ordena|ultimo|primer|antiguo|reciente)\b",
                    normal(command[enter.end():]),
                ))
        if request.channel:
            if not request.order:
                if re.search(r"\b(?:mas\s+antigu|primer\s+video)\b", text):
                    request.order = "oldest"
                elif re.search(r"\b(?:mas\s+reciente|ultimo\s+video|nuevo)\b", text):
                    request.order = "newest"
            index, scope = _ordinal_near_video(command)
            if request.order == "newest" and rank_index:
                index = rank_index
            request.result_index = index or (1 if request.order else None)
            request.result_scope = scope or "visible"
            return request

        search_patterns = [
            r"\b(?:en\s+(?:el\s+)?youtube|youtube)\s+(?:que\s+ya\s+est[aá]\s+abierto\s*,?\s*)?(?:busc[aá]|buscar|busc[aá]me)\s+(?P<query>.+)$",
            r"\b(?:busc[aá]|buscar|busc[aá]me)\s+en\s+(?:el\s+)?youtube\s+(?P<query>.+)$",
            r"\b(?:busc[aá]|buscar|busc[aá]me)\s+(?P<query>.+?)\s+(?:en|dentro\s+de)\s+(?:el\s+)?youtube\b",
            r"\b(?:abre|vuelve\s+a|entra\s+en)\s+youtube\b.*?\b(?:busc[aá]|buscar|busc[aá]me)\s+(?P<query>.+)$",
        ]
        for pattern in search_patterns:
            # Search extraction uses the original utterance so titles and
            # language names keep their accents/casing in user-visible state.
            original_pattern = pattern.replace("youtube", "[Yy]ou[Tt]ube")
            match = re.search(original_pattern, command, re.I)
            if match:
                request.query = _strip_query_tail(match.group("query"))
                break

        if request.query:
            named_result = re.search(
                r"\by\s+(?:abre|abrir|reproduce|reproducir|pon)\s+(.+?)(?=\s*(?:,|;|$))",
                command,
                re.I,
            )
            if named_result and normal(named_result.group(1)) not in {"lo", "el", "la"}:
                candidate = clean(named_result.group(1))
                if not re.match(
                    rf"^(?:(?:el|la)\s+)?(?:{_NUMBER_WORD})\s+"
                    r"(?:video|resultado|opcion)\b",
                    normal(candidate),
                ):
                    request.result_title = candidate

        direct_media = re.match(
            rf"^\s*{PLAY_VERBS}\s+(?P<content>.+?)\s+"
            r"(?:en|dentro\s+de)\s+(?:el\s+)?youtube\s*[.!]?\s*$",
            command,
            re.I,
        )
        if direct_media:
            content = clean(direct_media.group("content"))
            request.query = content
            request.result_title = content
            title, separator, possible_author = content.rpartition(" de ")
            if separator and len(title.split()) >= 2 and len(possible_author.split()) >= 2:
                request.result_title = clean(title)
            if not any(item.operation == "play_media" for item in request.media):
                request.media.append(MediaOperation("play_media", "Reproducir", 0))

        change = re.search(
            r"\bcambia(?:r)?\s+(?:el\s+)?(?:video|contenido)(?:\s+actual)?"
            r"(?:\s+de\s+youtube)?\s+(?:por|a)\s+(.+?)"
            r"(?=\s*(?:,|;|\by\s+(?:reproduce|pon|empieza|pausa|deja|deten|"
            r"avanza|adelanta|retrocede|rebobina)|$))",
            command,
            re.I,
        )
        if change:
            request.reuse_current = True
            request.query = clean(change.group(1))
            request.result_title = request.query

        index, scope = _ordinal_near_video(command)
        request.result_index, request.result_scope = index, scope
        if (
            change
            and request.query
            and re.match(
                r"^(?:un\s+|una\s+|el\s+|la\s+)?(?:tutorial|historia|"
                r"explicacion|como\s+funciona|documental)\b",
                normal(request.query),
            )
        ):
            request.result_title = None
            request.result_index = request.result_index or 1
            request.result_scope = request.result_scope or "visible"
        if (
            request.result_index
            and not request.query
            and re.match(r"^\s*en\s+(?:el\s+)?youtube\b", command, re.I)
        ):
            request.reuse_current = True
        if request.query and not request.result_title and not request.list_only and not request.result_index:
            named = re.search(
                r"\b(?:abre|reproduce|pon)\s+(?:la\s+coincidencia\s+correcta|el\s+resultado\s+exacto)\b",
                text,
            )
            if named:
                request.result_title = request.query
        if (
            request.media
            and not request.query
            and not request.channel
            and not request.result_index
        ):
            request.reuse_current = True
        return request


class NetflixSpecialist:
    provider = "Netflix"

    def parse(self, command):
        text = normal(command)
        if "netflix" not in text:
            return None
        provider_match = re.search(r"netflix", command, re.I)
        request = SpecialistRequest(provider=provider_match.group(0) if provider_match else self.provider)
        request.browser_profile = _browser_profile(command)
        if not request.browser_profile:
            explicit_browser = re.search(
                r"\b(?:abre|abrir|entra\s+en)\s+netflix\s+con\s+"
                r"(?:(?:el\s+)?perfil\s+)?(?P<profile>.+?)(?=\s*(?:,|;|\by\b|$))",
                command,
                re.I,
            )
            if explicit_browser:
                request.browser_profile = clean(explicit_browser.group("profile"))
        request.reuse_current = bool(re.search(
            r"\b(?:sin\s+(?:cerrar|abrir)|ya\s+esta\s+abiert|mismo\s+(?:episodio|contenido)|"
            r"(?:contenido|episodio)\s+que\s+esta\s+abiert|actual)\w*\b",
            text,
        ))
        if re.match(r"^\s*en\s+netflix\b", text):
            request.reuse_current = True
        request.media = _media_operations(command)
        request.list_limit = _list_request(command)
        request.list_only = bool(
            request.list_limit
            and not any(item.operation == "play_media" for item in request.media)
        )
        if request.list_limit:
            request.query = _listed_subject(command, r"netflix")

        transitions = [
            r"\bcambia(?:r)?\s+(?:la\s+cuenta|el\s+perfil|perfil)\s+de\s+netflix\s+de\s+(?P<source>.+?)\s+(?:a|al\s+de)\s+(?P<target>.+?)(?=\s*(?:,|;|\by\s+(?:busca|abre|pon|reproduce)|$))",
            r"\bcambia(?:r)?\s+(?:del|de\s+el)\s+perfil\s+de\s+(?P<source>.+?)\s+(?:al|a\s+el)\s+(?:de\s+)?(?P<target>.+?)(?=\s*(?:,|;|\by\s+(?:busca|abre|pon|reproduce)|$))",
        ]
        for pattern in transitions:
            match = re.search(pattern, command, re.I)
            if match:
                request.site_profile_source = clean(match.group("source"))
                request.site_profile = clean(match.group("target"))
                request.reuse_current = True
                break
        if not request.site_profile:
            natural_switch = re.search(
                r"\bcambia(?:r)?\s+de\s+perfil\s+de\s+netflix\s+al\s+(?:de\s+)?(?P<target>.+?)(?=\s*(?:,|;|$))",
                command,
                re.I,
            )
            if natural_switch:
                request.site_profile = clean(natural_switch.group("target"))
                account = _browser_profile(command)
                request.site_profile_source = account
                request.browser_profile = None
                request.reuse_current = True
        if not request.site_profile:
            profile_patterns = [
                r"\b(?:cambia|cambiar)\s+al\s+perfil(?:\s+de)?\s+(?P<target>.+?)(?=\s*(?:,|;|\by\b|$))",
                r"\b(?:entra|ingresa|selecciona|elige|elije)\w*\s+(?:en\s+|al\s+)?(?:el\s+)?perfil(?:\s+de)?\s+(?P<target>.+?)(?=\s*(?:,|;|\by\s+(?:busca|abre|pon|reproduce|selecciona)|$))",
            ]
            for pattern in profile_patterns:
                match = re.search(pattern, command, re.I)
                if match:
                    request.site_profile = clean(match.group("target"))
                    break
        if not request.site_profile:
            contextual_profile = re.search(
                r"\b(?:en\s+netflix\s+)?(?:usa|utiliza|como)\s+(?:(?:el\s+)?perfil(?:\s+de)?\s+)?"
                r"(?P<target>.+?)(?=\s*(?:,|;|\ben\s+netflix\b|$))",
                command,
                re.I,
            )
            if contextual_profile:
                request.site_profile = clean(contextual_profile.group("target"))
        if not request.site_profile:
            current_page_profile = re.search(
                r"^\s*en\s+netflix\s+(?:selecciona|elige|elije|usa)\w*\s+"
                r"(?P<target>.+?)(?=\s*(?:,|;|$))",
                command,
                re.I,
            )
            if current_page_profile:
                request.site_profile = clean(current_page_profile.group("target"))
                request.reuse_current = True
        if request.site_profile:
            request.site_profile = clean(re.sub(
                r"\s+en\s+netflix\s*$", "", request.site_profile, flags=re.I,
            ))

        search = re.search(
            r"\b(?:busca|buscar|buscame)\s+(?P<query>.+?)\s+(?:en|dentro\s+de)\s+netflix\b",
            command,
            re.I,
        )
        if not search:
            search = re.search(r"\b(?:busca|buscar|buscame)\s+(?P<query>.+)$", command, re.I)
        if search:
            request.query = _strip_query_tail(search.group("query"))
            named_result = re.search(
                r"\by\s+(?:abre|abrir|reproduce|reproducir|pon)\s+"
                r"(?P<result>.+?)(?=\s*(?:,|;|$))",
                command,
                re.I,
            )
            if named_result:
                candidate = clean(named_result.group("result"))
                if not re.match(
                    r"^(?:(?:el|la)\s+)?(?:capitulo|episodio|temporada|"
                    r"minuto|video\s+siguiente|episodio\s+siguiente)\b",
                    normal(candidate),
                ):
                    request.result_title = candidate

        season = re.search(rf"\btemporada\s+(?P<number>{_NUMBER_WORD})\b|\b(?P<before>{_NUMBER_WORD})\s+temporada\b", text)
        if season:
            request.season = spoken_number(season.group("number") or season.group("before"))
        episode = re.search(
            rf"\b(?P<kind>episodio|capitulo)\s+(?P<number>{_NUMBER_WORD})\b",
            text,
        )
        if not episode:
            episode = re.search(
                rf"\b(?P<before>{_NUMBER_WORD})\s+(?P<kind_after>episodio|capitulo)\b",
                text,
            )
        if episode:
            groups = episode.groupdict()
            request.episode = spoken_number(groups.get("number") or groups.get("before"))
            request.episode_kind = (
                groups.get("kind") or groups.get("kind_after") or "episodio"
            ).capitalize()

        if request.query:
            request.query = clean(re.split(
                rf"\s*,?\s+(?=(?:temporada|episodio|capitulo|{_NUMBER_WORD}\s+"
                r"(?:temporada|episodio|capitulo))\b)",
                request.query,
                maxsplit=1,
                flags=re.I,
            )[0])
            # Catalogue kind words are not part of the title.
            request.query = clean(re.sub(
                r"^(?:(?:la|el|una|un)\s+)?(?:serie|pelicula|documental|trailer)(?:\s+de)?\s+",
                "",
                request.query,
                flags=re.I,
            ))
            request.query = clean(re.sub(
                r"^(?:(?:la|el|una|un)\s+)?(?:trailer|avance)(?:\s+(?:de|del))?\s+",
                "",
                request.query,
                flags=re.I,
            ))
            request.query = clean(re.sub(r"^(?:de|del)\s+", "", request.query, flags=re.I))
            if (request.season or request.episode or request.media) and not request.result_title:
                request.result_title = request.query
        if re.search(r"\bsubtitulos?\b", text):
            request.unsupported.append("subtitles")
        return request


_SPECIALISTS = (YouTubeSpecialist(), NetflixSpecialist())


def parse_specialized_web_command(command, context_provider=None):
    """Return a provider request, optionally scoped by recent screen context."""

    text = normal(command)
    explicit_provider = any(
        normal(specialist.provider) in text for specialist in _SPECIALISTS
    )
    # A recently active provider must not hijack an explicit filesystem
    # request received next by voice. An explicitly named provider still wins
    # ("busca el video X en YouTube").
    if (
        not explicit_provider
        and context_provider
        and re.search(
            r"\b(?:archivo|carpeta|documento|pdf|excel|word)\b|"
            r"\b(?:en|de)\s+(?:mis\s+)?(?:archivos|documentos|descargas|computadora|pc)\b",
            text,
        )
    ):
        return None
    for specialist in _SPECIALISTS:
        if specialist.provider.casefold() in text:
            return specialist.parse(command)
    # "poné el último video de el rubius" sin nombrar YouTube: los videos
    # de un canal (último, penúltimo, primero) solo los busca YouTube.
    if (
        not context_provider
        and re.search(
            r"\b(?:(?:ante)?penult\w*|ultimo|primer)\s+video\s+"
            r"(?:publicado\s+|subido\s+)?(?:de|del|por|que\s+(?:publico|subio))\s+\S",
            text,
        )
        and not re.search(r"\b(?:archivo|carpeta|descarg\w*|documentos|computadora)\b", text)
    ):
        request = YouTubeSpecialist().parse(f"YouTube: {command}")
        if request and request.channel:
            request.provider = "YouTube"
            return request
    # Provider-less continuations are still parsed by the selected specialist
    # when a verified/persisted context supplied that provider.  Injecting its
    # name only selects vocabulary; it is never used as page-state evidence.
    context = normal(context_provider)
    for specialist in _SPECIALISTS:
        if normal(specialist.provider) == context:
            synthetic = f"{specialist.provider}: {command}"
            request = specialist.parse(synthetic)
            if request:
                request.reuse_current = True
                request.provider = specialist.provider
            return request if request and request.has_work else None
    return None
