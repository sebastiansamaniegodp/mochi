"""Estado compacto de objetivo y planificación semántica para Mochi.

No intenta interpretar todo el lenguaje natural. Reconoce las estructuras
multi-scope más importantes para que el modelo no tenga que retenerlas solo
en el historial de mensajes.
"""

from dataclasses import asdict, dataclass, field
import re
import unicodedata

from brain.web_destinations import WebDestinationResolver
from brain.web_specialists import PLAY_VERBS, MediaOperation, SpecialistRequest, parse_specialized_web_command


def _clean(value):
    return str(value or "").strip(" .,;:")


def _norm(value):
    text = unicodedata.normalize("NFD", _clean(value).casefold())
    return "".join(char for char in text if unicodedata.category(char) != "Mn")


def _semantic_name_matches(requested_value, observed_value):
    """Match a short public acronym to an expanded observed page title."""

    requested, observed = _norm(requested_value), _norm(observed_value)
    if not requested or not observed:
        return False
    if requested in observed:
        return True
    requested_tokens = re.findall(r"\w+", requested)
    observed_tokens = re.findall(r"\w+", observed)
    forms = set(observed_tokens)
    for width in range(2, min(5, len(observed_tokens)) + 1):
        forms.update(
            "".join(token[0] for token in observed_tokens[start:start + width] if token)
            for start in range(len(observed_tokens) - width + 1)
        )
    return bool(requested_tokens) and all(token in forms for token in requested_tokens)


@dataclass
class GoalStep:
    id: str
    scope: str
    operation: str
    status: str = "pending"  # pending, completed, failed, blocked
    target: str | None = None
    value: str | None = None
    depends_on: str | None = None
    reason: str | None = None


# YouTube muestra los órdenes del canal en el idioma de la cuenta.
_PAGE_CONTROL_ALIASES = {
    "mas recientes": {"mas recientes", "recientes", "latest", "newest"},
    "mas antiguos": {"mas antiguos", "antiguos", "oldest"},
}


def page_control_names(value):
    """Los nombres (normalizados) con que puede aparecer un control."""
    requested = _norm(value)
    return _PAGE_CONTROL_ALIASES.get(requested, {requested})


@dataclass
class GoalState:
    goal: str
    target: str | None = None
    browser_profile: str | None = None
    # The declared active profile is optional evidence for a safe menu-open
    # action ("cambia de Isaías a Marian").  It is never a Chrome profile.
    site_profile_source: str | None = None
    site_profile: str | None = None
    page_query: str | None = None
    page_result: str | None = None
    # Optional public channel identity and deterministic ordering requested by
    # the user.  They are semantic constraints, never provider IDs.
    channel_name: str | None = None
    result_order: str | None = None
    # Short-lived, user-visible descriptions of equally plausible page
    # results.  They never carry UIA IDs or references.
    page_result_options: list[dict] = field(default_factory=list)
    # Public page metadata from a persisted choice list.  Never contains UIA
    # element ids or COM references; it only prevents a numbered follow-up
    # from being applied after the user navigated elsewhere.
    page_choice_context: dict | None = None
    pending_choice_operation: str | None = None
    explicit_search: bool = False
    web_action_pending_verification: bool = False
    web_action_operation: str | None = None
    site_profile_menu_pending: bool = False
    steps: list[GoalStep] = field(default_factory=list)

    def compact(self):
        return {
            "goal": self.goal,
            "status": self.status,
            "target": self.target,
            "browser_profile": self.browser_profile,
            "site_profile_source": self.site_profile_source,
            "site_profile": self.site_profile,
            "page_query": self.page_query,
            "page_result": self.page_result,
            "channel_name": self.channel_name,
            "result_order": self.result_order,
            "page_result_options": self.page_result_options,
            "page_choice_context": self.page_choice_context,
            "pending_choice_operation": self.pending_choice_operation,
            "steps": [asdict(step) for step in self.steps],
        }

    @property
    def complex(self):
        return len(self.steps) > 1 or bool(self.site_profile)

    @property
    def status(self):
        """Estado del objetivo, independiente del éxito de una tool."""
        if not self.steps:
            return "completed"
        statuses = [step.status for step in self.steps]
        if all(status == "completed" for status in statuses):
            return "completed"
        if "completed" in statuses:
            return "partial"
        if any(
            step.reason == "action_dispatched_unverified"
            for step in self.steps
        ):
            return "partial"
        if "blocked" in statuses:
            return "blocked"
        if all(status == "pending" for status in statuses):
            return "pending"
        return "failed"

    def step(self, operation_or_id):
        # Dependencies are intentionally allowed to name a stable step ID.
        # Most older plans used operation names because each operation was
        # unique; structured channel workflows use IDs such as `page_search`
        # and `visible_video` so the graph remains unambiguous.
        return next(
            (step for step in self.steps if step.id == operation_or_id),
            next(
                (step for step in self.steps if step.operation == operation_or_id),
                None,
            ),
        )

    def pending_step(self, operation_or_id):
        """Return the first still-pending occurrence of an operation/id.

        Media commands may legitimately contain the same verb more than
        once (pause, resume, pause).  ``step()`` remains the stable lookup
        used for summaries, while execution always consumes the next
        occurrence instead of mutating the first completed one again.
        """
        return next(
            (
                step for step in self.steps
                if step.status == "pending"
                and (step.id == operation_or_id or step.operation == operation_or_id)
            ),
            None,
        )

    def mark_completed(self, operation):
        step = self.pending_step(operation) or self.step(operation)
        if step:
            step.status = "completed"

    def mark_failed(self, operation, reason):
        step = self.pending_step(operation) or self.step(operation)
        if step:
            step.status = "failed"
            step.reason = reason

    def block_missing_capabilities(self):
        for step in self.steps:
            if step.scope == "web_page" and step.status == "pending":
                step.status = "blocked"
                step.reason = "capability_missing"

    def finalize(self):
        """Convierte pendientes restantes en un resultado final verificable."""
        self.block_missing_capabilities()
        for step in self.steps:
            if step.status == "pending":
                step.status = "failed"
                step.reason = "not_completed"

    def note_web_action(self, operation="select_site_profile"):
        if self.pending_step(operation):
            self.web_action_pending_verification = True
            self.web_action_operation = operation

    def record_page_result_options(self, options):
        self.page_result_options = list(options or [])[:5]

    def note_site_profile_menu_opening(self):
        """A reversible account-menu opening is not profile selection itself."""
        if self.step("select_site_profile"):
            self.site_profile_menu_pending = True

    def observe_site_profile_menu(self, has_requested_profile):
        """Allow one inspected menu transition, then stop if target is absent."""
        step = self.step("select_site_profile")
        if not step or not self.site_profile_menu_pending:
            return False
        self.site_profile_menu_pending = False
        if has_requested_profile:
            return True
        step.status = "blocked"
        step.reason = "profile_selection_not_available"
        return False

    def verify_web_observation(self, observation):
        self.confirm_selected_page_control(observation)
        self.confirm_playing_media(observation)
        if (
            self.web_action_pending_verification
            and self.web_action_operation == "select_result"
            and self.page_result
        ):
            title = _norm((observation.get("page") or {}).get("title"))
            requested = _norm(self.page_result)
            if requested and _semantic_name_matches(requested, title):
                self.mark_completed("select_result")
                self.web_action_pending_verification = False
                self.web_action_operation = None
                return
        # Playback has a stronger completion contract than ordinary page
        # selection.  A new player document, an ad, or a controls overlay can
        # change the snapshot without media having started.  It is completed
        # only by a visible Pause control or measured timeline progression.
        if (
            self.web_action_pending_verification
            and self.web_action_operation != "play_media"
            and observation.get("changed")
        ):
            self.mark_completed(self.web_action_operation or "select_site_profile")
            self.web_action_pending_verification = False
            self.web_action_operation = None

    def confirm_selected_page_control(self, observation):
        """Use SelectionItem state as evidence for tabs/sort choices."""
        for operation in ("select_content_tab", "select_sort"):
            step = self.pending_step(operation)
            if not step or step.status != "pending" or not step.value:
                continue
            requested = page_control_names(step.value)
            if any(
                item.get("selected") is True
                and _norm(item.get("name")) in requested
                for item in observation.get("elements", [])
            ):
                step.status = "completed"
                step.reason = "visible_selected_control"
                if self.web_action_operation == operation:
                    self.web_action_pending_verification = False
                    self.web_action_operation = None
                return True
        return False

    def confirm_visible_site_profile(self, observation):
        """Recognize a named account/profile indicator as completed state.

        Page-level profile pickers commonly expose choices as links or menu
        items. Once selected, the active profile is instead shown as a button
        in the application's header.  Seeing that named indicator is evidence
        of the requested end state; it must not be clicked just to repeat an
        already-completed selection.
        """
        step = self.step("select_site_profile")
        if not step or step.status != "pending" or self.site_profile_menu_pending or not self.site_profile:
            return False
        requested = _norm(self.site_profile)
        for item in observation.get("elements", []):
            name = _norm(item.get("name"))
            if item.get("role") == "button" and name == requested:
                step.status = "completed"
                step.reason = "visible_active_profile"
                self.web_action_pending_verification = False
                return True
        return False

    def confirm_playing_media(self, observation):
        """Recognize a visible Pause control as evidence that media is playing.

        This stays provider-neutral: a semantic Pause button is a standard
        accessibility contract for active media, whether it belongs to
        YouTube, Netflix, Twitch, or another web player.  A successful click
        on a search result is not enough evidence by itself.
        """
        step = self.pending_step("play_media")
        if not step or step.status != "pending":
            return False
        # When a timeline is available, its provider can also leave the Pause
        # label stale. The deterministic playback micro-plan actively
        # normalizes and verifies that richer state; a passive snapshot alone
        # is not enough. Providers without a timeline may still use the
        # semantic Pause control as their only observable playback contract.
        has_timeline = any(
            item.get("role") == "slider"
            and not any(
                word in _norm(f"{item.get('name', '')} {item.get('value', '')}")
                for word in {"volumen", "volume", "loudness"}
            )
            and isinstance(item.get("range"), dict)
            for item in observation.get("elements", [])
        )
        if has_timeline:
            return False
        for item in observation.get("elements", []):
            if item.get("role") not in {"button", "link"}:
                continue
            # Browsers commonly append a keyboard shortcut (for example
            # "Pausar (k)").  The visible semantic verb remains reliable;
            # exact equality would incorrectly report an active player as
            # unverified.
            if re.search(r"(?:^|\s)(?:pausa|pausar|pause)(?:\s|$)", _norm(item.get("name"))):
                step.status = "completed"
                step.reason = "visible_pause_control"
                if self.web_action_operation == "play_media":
                    self.web_action_pending_verification = False
                    self.web_action_operation = None
                return True
        return False

    def confirm_media_timeline_progression(self):
        """Record observed forward progress when a player lacks a Pause name."""
        step = self.pending_step("play_media")
        if not step or step.status != "pending":
            return False
        step.status = "completed"
        step.reason = "timeline_progressed"
        if self.web_action_operation == "play_media":
            self.web_action_pending_verification = False
            self.web_action_operation = None
        return True


def _extract_target(command):
    # Compound web requests often start with an explicit Chrome account,
    # e.g. "abre Netflix en la cuenta de Google de Marian".  The generic
    # fallback below used to absorb the entire remaining sentence as the
    # target because it did not recognise "en la cuenta" as a boundary.
    account_open = re.search(
        r"\b(?:abre|abrir|entra(?:\s+en)?|ir\s+a)\s+(.+?)"
        r"(?=\s+(?:en|con)\s+(?:(?:la|el)\s+)?(?:cuenta\s+de\s+google|cuenta|perfil)\b)",
        command,
        re.I,
    )
    if account_open:
        return _clean(account_open.group(1))
    chained_open = re.search(
        r"\b(?:abre|abrir|entra(?:\s+en)?|ir\s+a)\s+(.+?)"
        r"(?=\s*(?:,|y)\s+(?:busca|buscar|elige|elije|selecciona|pon|reproduce|pausa|continua|reanuda)\b)",
        command,
        re.I,
    )
    if chained_open:
        return _clean(chained_open.group(1))
    switch_match = re.search(r"\b(?:cambia|cambiar)\s+(?:la\s+)?(?:cuenta|perfil)\s+de\s+(.+?)\s+de\s+.+?\s+a\s+.+?(?=\s*(?:por\s+favor|,|$))", command, re.I)
    if switch_match:
        return _clean(switch_match.group(1))
    switch_match = re.search(r"\b(?:cambia|cambiar)\s+(?:de\s+)?(?:el\s+)?perfil\s+(?:en|de)\s+(.+?)\s+(?:al\s+)?(?:perfil\s+)?de\s+.+?(?=\s*(?:por\s+favor|,|$))", command, re.I)
    if switch_match:
        return _clean(switch_match.group(1))
    match = re.search(r"\b(?:abre|abrir|entra(?:\s+en)?|ir\s+a)\s+(.+?)(?=\s+(?:con|y\s+luego|luego|despues|dentro\s+de|en\s+[^ ]+\s+(?:usa|como|elige|selecciona|utiliza))\b|$)", command, re.I)
    return _clean(match.group(1)) if match else None


def _extract_browser_profile(command, target):
    patterns = [
        r"\bcuenta\s+de\s+google\s+de\s+(.+?)(?=\s*(?:y|luego|despues|,|$))",
        r"\bchrome\s+de\s+(.+?)(?=\s*(?:y|luego|despues|,|$))",
        r"\bperfil\s+de\s+navegador\s+(.+?)(?=\s*(?:y|luego|despues|,|$))",
    ]
    for pattern in patterns:
        match = re.search(pattern, command, re.I)
        if match:
            return _clean(match.group(1))
    # "abre X con Y" only means browser profile when the phrase does not
    # explicitly place Y inside the site.
    if target:
        match = re.search(r"\b(?:abre|entra(?:\s+en)?)\s+" + re.escape(target) + r"\s+con\s+(?:el\s+)?perfil\s+(.+?)(?=\s*(?:y|luego|despues|,|$))", command, re.I)
        if match:
            return _clean(match.group(1))
        match = re.search(r"\b(?:abre|entra(?:\s+en)?)\s+" + re.escape(target) + r"\s+con\s+(.+?)(?=\s*(?:y\s+(?:en|dentro)|en\s+" + re.escape(target) + r"\s+(?:como|usa)|,|$))", command, re.I)
        if match:
            return _clean(match.group(1))
    return None


def _extract_site_profile(command, target):
    if not target:
        match = re.search(r"\b(?:en|dentro\s+de)\s+(.+?)\s+(?:usa|utiliza|elige|elije|selecciona|como)\s+(?:(?:el\s+)?perfil(?:\s+de)?\s+)?(.+)$", command, re.I)
        return (_clean(match.group(1)), _clean(match.group(2))) if match else (None, None)
    patterns = [
        r"\b(?:cambia|cambiar)\s+(?:la\s+)?(?:cuenta|perfil)\s+de\s+" + re.escape(target) + r"\s+de\s+.+?\s+a\s+(.+?)(?=\s*(?:por\s+favor|,|$))",
        r"\b(?:en|dentro\s+de)\s+" + re.escape(target) + r"\s+(?:usa|utiliza|elige|elije|selecciona|como)\s+(?:(?:el\s+)?perfil(?:\s+de)?\s+)?(.+?)(?=\s*(?:,|$))",
        r"\b(?:usa|utiliza|elige|elije|selecciona)\s+(?:el\s+)?perfil(?:\s+de)?\s+(.+?)\s+(?:en|dentro\s+de)\s+" + re.escape(target) + r"\b",
        r"\by\s+luego\s+(?:usa|utiliza|elige|elije|selecciona)\s+(?:el\s+)?perfil(?:\s+de)?\s+(.+?)\s+(?:en|dentro\s+de)\s+" + re.escape(target) + r"\b",
        r"\b(?:cambia|cambiar)\s+(?:de\s+)?(?:el\s+)?perfil\s+(?:en|de)\s+" + re.escape(target) + r"\s+(?:al\s+)?(?:perfil\s+)?de\s+(.+?)(?=\s*(?:por\s+favor|,|$))",
        # "abre Netflix ... y entra en el perfil de Marian". The page is
        # already identified by the opening clause, so repeating its name is
        # optional. Stop at the next command in the chain, not at its last
        # word.
        r"\b(?:entra|entrar|ingresa|ingresar)\s+(?:en\s+)?(?:el\s+)?perfil(?:\s+de)?\s+(.+?)(?=\s*(?:,|y\s+(?:luego\s+)?(?:busca|pon|reproduce|abre|elige|selecciona|pausa)|$))",
        r"\b(?:usa|utiliza|elige|elije|selecciona)\s+(?:el\s+)?perfil(?:\s+de)?\s+(.+?)(?=\s*(?:,|y\s+(?:luego\s+)?(?:busca|pon|reproduce|abre|elige|selecciona|pausa)|$))",
    ]
    for pattern in patterns:
        match = re.search(pattern, command, re.I)
        if match:
            return target, _clean(match.group(1))
    return target, None


def _extract_follow_up_page_search(command):
    """Extract a page search embedded after an opening/profile clause."""

    match = re.search(
        r"\b(?:busca|buscar|buscame|b[uú]scame|busque)\s+(?P<query>.+?)"
        r"(?=\s*(?:,|y\s+(?:luego\s+)?(?:pon|reproduce|abre|elige|selecciona|pausa)|$))",
        command,
        re.I,
    )
    if not match:
        return None
    query = _clean(match.group("query"))
    # These are request-type words, not part of a catalogue entity name.
    return _content_entity_name(query) or None


def _extract_episode_request(command):
    """Return an exact, UIA-matchable episode label when one was requested."""

    match = re.search(
        r"\b(?:pon|reproduce|reproducir|abre|abrir|elige|elije|selecciona|"
        r"play|open|choose|select)\s+"
        r"(?:el|the)?\s*(?P<ordinal>primer|primero|primera|segundo|segunda|tercer|tercero|tercera|"
        r"cuarto|cuarta|quinto|quinta)?\s*"
        r"(?P<kind>cap[ií]tulo|episodio|chapter|episode)\s*"
        r"(?P<number>\d+|primer|primero|primera|segundo|segunda|tercer|tercero|tercera|"
        r"cuarto|cuarta|quinto|quinta)?\b",
        command,
        re.I,
    )
    if not match:
        return None
    number = _ordinal_number(match.group("number") or match.group("ordinal"))
    return f"{match.group('kind').capitalize()} {number}" if number else None


def _extract_season_request(command):
    """Return the requested season label without assuming a streaming site."""

    following = re.search(
        r"\b(?:temporada|season)\s*(?P<number>\d+|primera?|segunda?|tercera?|cuarta?|quinta?)\b",
        command,
        re.I,
    )
    preceding = re.search(
        r"\b(?P<number>primera?|segunda?|tercera?|cuarta?|quinta?)\s+(?:temporada|season)\b",
        command,
        re.I,
    )
    match = following or preceding
    number = _ordinal_number(match.group("number")) if match else None
    return f"Temporada {number}" if number else None


def _ordinal_number(value):
    normalized = _norm(value)
    if normalized.isdigit():
        return int(normalized)
    words = {
        "primer": 1, "primero": 1, "primera": 1,
        "segundo": 2, "segunda": 2,
        "tercer": 3, "tercero": 3, "tercera": 3,
        "cuarto": 4, "cuarta": 4,
        "quinto": 5, "quinta": 5,
    }
    return words.get(normalized)


def _clock_seconds(value):
    """Parse a spoken ``M:SS`` or integer-minute position into seconds."""

    text = _clean(value)
    if not text:
        return None
    if ":" not in text:
        try:
            return int(text) * 60
        except ValueError:
            return None
    try:
        minutes, seconds = text.split(":", 1)
        seconds = int(seconds)
        if not 0 <= seconds < 60:
            return None
        return int(minutes) * 60 + seconds
    except ValueError:
        return None


def _extract_player_control(command):
    """Return one explicit generic web-player operation from natural language.

    Values are seconds, not provider-specific progress percentages.  The
    execution layer translates them only when the inspected slider publishes
    enough accessible evidence to establish its time scale.
    """

    normalized = _norm(command)
    if re.search(
        r"\b(?:pausa|pausar|deten|detener)\b(?:"
        r"\s+(?:el|la)\s+(?:video|reproduccion|serie|pelicula|episodio|capitulo)"
        r"|\s+(?:video|reproduccion|serie|pelicula|episodio|capitulo)|$)",
        normalized,
    ):
        return "pause_media", "Pausa", False
    if re.search(
        r"\b(?:reanuda|reanudar|continua|continuar|sigue|seguir)\b(?:"
        r"\s+(?:con\s+)?(?:el|la)\s+(?:video|reproduccion|serie|pelicula|episodio|capitulo)"
        r"|\s+(?:video|reproduccion|serie|pelicula|episodio|capitulo)|$)",
        normalized,
    ):
        return "play_media", "Reproducir", False
    if re.search(r"\b(?:siguiente|proximo)\s+(?:episodio|capitulo|video|pista)\b|\bpas(?:a|ar)\s+al\s+siguiente\b", normalized):
        return "next_media", None, False
    if re.search(
        r"\b(?:anterior|previo)\s+(?:episodio|capitulo|video|pista)\b|"
        r"\b(?:vuelve|volver)\s+(?:al\s+)?(?:episodio|capitulo|video|pista)\s+anterior\b|"
        r"\b(?:vuelve|volver)\s+al\s+anterior\b",
        normalized,
    ):
        return "previous_media", None, False
    if re.search(
        r"\b(?:desde|al)\s+(?:el\s+)?inicio\b|"
        r"\b(?:reinicia|reiniciar)\b|"
        r"\b(?:empieza|empezar|comienza|comenzar)\b.{0,24}\b(?:el\s+)?inicio\b",
        normalized,
    ):
        return "seek_media", "0", True

    relative = re.search(
        r"\b(?P<verb>avanza|avanzar|avance|adelanta|adelantar|adelante|retrocede|retroceder|retroceda|rebobina|rebobinar)\s+"
        r"(?P<number>\d+(?:[\.,]\d+)?)\s*(?P<unit>segundo(?:s)?|seg|minuto(?:s)?|min)\b",
        normalized,
    )
    if relative:
        amount = float(relative.group("number").replace(",", "."))
        if relative.group("unit").startswith("min"):
            amount *= 60
        if relative.group("verb").startswith(("retro", "rebob")):
            amount = -amount
        if amount:
            return "adjust_media_position", f"{amount:g}", False

    absolute = re.search(
        r"\b(?:pon|pone|poner|ve|ir|lleva|llevar|salta|saltar|posiciona|posicionar)\b.{0,28}?"
        r"\b(?:minuto|min)\s*(?P<time>\d+(?::\d{1,2})?)\b",
        normalized,
    )
    if absolute:
        seconds = _clock_seconds(absolute.group("time"))
        if seconds is not None:
            return "seek_media", str(seconds), False
    # Keep this fallback after seek/navigation detection.  In a compound
    # phrase such as "pon el minuto 15 y luego páralo", the absolute seek is
    # the primary operation and `_requests_pause` appends the final pause.
    if re.search(r"\b(?:paralo|parala|pare|parar)\b", normalized):
        return "pause_media", "Pausa", False
    return None, None, False


def _requests_pause(command):
    return bool(re.search(
        r"\bpausa(?:lo|la)?\b|\bpausar\b|\bpause(?:\s+it)?\b|"
        r"\bp[aá]ra(?:lo|la)\b|\bpare\b|\bparar\b",
        command,
        re.I,
    ))


def _extract_site_profile_transition(command, target):
    """Return (current, requested) for a named in-site profile switch."""
    if not target:
        return None, None
    match = re.search(
        r"\b(?:cambia|cambiar)\s+(?:la\s+)?(?:cuenta|perfil)\s+de\s+"
        + re.escape(target)
        + r"\s+de\s+(.+?)\s+a\s+(.+?)(?=\s*(?:por\s+favor|,|$))",
        command,
        re.I,
    )
    return (_clean(match.group(1)), _clean(match.group(2))) if match else (None, None)


def _extract_page_task(command):
    """Return a scoped page search and its optional named follow-up target."""

    # A pronoun means the result of this very search.  Parsing this before the
    # general grammar prevents "YouTube y reprodúcelo" from becoming a fake
    # site name and preserves the requested playback action.
    pronoun = re.search(
        r"\b(?:busca|buscar|buscame|b[uú]scame|busque)\s+(?P<query>.+)\s+(?:en|dentro\s+de)\s+"
        r"(?P<site>.+?)\s+y\s+(?:reprod[uú]celo|ponlo|[aá]brelo)\b",
        command,
        re.I,
    )
    if pronoun:
        site = _clean(pronoun.group("site"))
        query = _clean(pronoun.group("query"))
        return site, query, query

    match = re.search(
        r"\b(?:busca|buscar|buscame|b[uú]scame|busque)\s+(?P<query>.+)\s+(?:en|dentro\s+de)\s+"
        r"(?P<site>.+?)(?:\s+(?:y\s+)?(?P<action>reproduce|reproducir|abre|abrir|elige|elije|selecciona)\s+"
        r"(?P<result>.+?))?(?=\s*(?:por\s+favor|,|$))",
        command,
        re.I,
    )
    if not match:
        return None, None, None
    site, query = _clean(match.group("site")), _clean(match.group("query"))
    # A following compound player clause belongs to the same goal, not to
    # the site name.  The optional action grammar above intentionally covers
    # named result selection; positional/player verbs are parsed separately.
    site = _clean(re.split(
        r"\s+(?:y\s+)?(?:pon|pone|poner|pausa|pausar|para|parar|ve|lleva|salta|"
        r"avanza|adelanta|retrocede|selecciona|elige|elije)\b",
        site,
        maxsplit=1,
        flags=re.I,
    )[0])
    if _norm(site) in {"google", "la web", "web", "internet"}:
        return None, None, None
    return site, query, _clean(match.group("result")) if match.group("result") else None


def _requests_pronoun_playback(command):
    return bool(re.search(r"\b(?:reprod[uú]celo|ponlo)\b", command, re.I))


def _is_indefinite_media_query(query):
    return bool(re.match(
        r"^\s*(?:un|una|algun|alguna|alg[uú]n|cualquier)\s+",
        str(query or ""),
        re.I,
    ))


def _content_entity_name(query):
    """Remove spoken content-kind wrappers from a catalogue entity name."""

    text = _clean(query)
    simplified = re.sub(
        r"^\s*(?:(?:el|la|los|las|un|una)\s+)?"
        r"(?:tr[aá]iler|avance|serie|pel[ií]cula|video|v[ií]deo|programa)"
        r"(?:\s+(?:de|del|sobre))?\s+",
        "",
        text,
        count=1,
        flags=re.I,
    )
    return _clean(simplified) or text


def _extract_channel_task(command):
    """Return (channel, order, open_only) for explicit channel workflows."""

    latest = re.search(
        r"\bbusca(?:r)?\s+el\s+[uú]ltimo\s+v[ií]deo\s+que\s+public[oó]\s+"
        r"(?P<channel>.+?)(?:\s+en\s+su\s+canal(?:\s+principal)?)?(?=\s*[.!]?$)",
        command,
        re.I,
    )
    if latest:
        return _clean(latest.group("channel")), "newest", False

    oldest = re.search(
        r"\bbusca(?:r)?\s+el\s+primer\s+v[ií]deo\s+del\s+canal\s+de\s+"
        r"(?P<channel>.+?)(?=\s*[.!]?$)",
        command,
        re.I,
    )
    if oldest:
        return _clean(oldest.group("channel")), "oldest", False

    enter = re.search(
        r"\bbusca(?:r)?\s+(?P<channel>.+?)\s+y\s+entra(?:r)?\s+(?:a|en)\s+su\s+canal\b",
        command,
        re.I,
    )
    if enter:
        return _clean(enter.group("channel")), None, True
    return None, None, False


def _extract_media_play_task(command):
    """Parse ``pon/reproduce X en YouTube`` as an in-page media request."""

    match = re.match(
        rf"^\s*{PLAY_VERBS}\s+(?P<content>.+?)\s+"
        r"(?:en|dentro\s+de)\s+(?P<site>.+?)\s*[.!]?$",
        command,
        re.I,
    )
    if not match:
        return None, None, None
    site = _clean(match.group("site"))
    content = _clean(match.group("content"))
    if not site or not content:
        return None, None, None

    # Retain author words in the search query, but make the title the element
    # that must be selected. Limit this split to a multiword suffix so normal
    # titles such as "La casa de papel" are not mistakenly truncated.
    result = content
    title, separator, possible_author = content.rpartition(" de ")
    if separator and len(title.split()) >= 2 and len(possible_author.split()) >= 2:
        result = _clean(title)
    return site, content, result


def _extract_page_search(command):
    """Return (site, query) only when the user explicitly scopes a search."""

    site, query, _result = _extract_page_task(command)
    return site, query


def _extract_active_video_change(command):
    """Recognize a request to replace media in the currently open tab."""
    match = re.match(
        r"^\s*(?:cambia|cambiar)\s+(?:de\s+)?(?:video|vídeo|contenido)\s+"
        r"(?:a|por)\s+(?P<result>.+?)(?=\s*(?:por\s+favor)?[.!]?$)",
        command,
        re.I,
    )
    return _clean(match.group("result")) if match else None


def _extract_visible_video_request(command):
    """Recognize numbered or recommended videos in the current page view."""

    normalized = _norm(command)
    if "video" not in normalized:
        return None, None
    words = {
        "primer": 1, "primero": 1, "primera": 1,
        "segundo": 2, "segunda": 2,
        "tercer": 3, "tercero": 3, "tercera": 3,
        "cuarto": 4, "cuarta": 4,
        "quinto": 5, "quinta": 5,
    }
    ordinal = None
    for word, index in words.items():
        if re.search(r"\b" + word + r"\b", normalized):
            ordinal = index
            break
    if ordinal is None:
        match = re.search(r"\b(?:el|la)?\s*(?P<number>[1-5])(?:er|ro|to)?\s*(?:video|video)\b", normalized)
        if match:
            ordinal = int(match.group("number"))
    if ordinal is None:
        return None, None
    recommended = bool(re.search(r"\b(?:recomend|suger|related|up next)\w*\b", normalized))
    on_screen = bool(re.search(r"\b(?:en pantalla|visible|que se ve|que ves)\b", normalized))
    # Do not reinterpret an arbitrary ordinal from an unrelated command.
    if not (recommended or on_screen):
        return None, None
    return ordinal, "recommended" if recommended else "visible"


def _extract_local_file_search(command):
    """Recognize a local file/folder search before treating it as web search."""

    match = re.match(
        r"^\s*(?:busca|buscar)\s+"
        r"(?:(?:un|una|el|la|mis?)\s+)?"
        r"(?P<kind>archivo|carpeta|excel|word|pdf|documento|imagen|foto|video|audio)"
        r"\s+(?:(?:llamado|llamada|que\s+se\s+llame)\s+)?"
        r"(?P<query>.+?)\s*$",
        command,
        re.I,
    )
    if not match:
        return None, None
    kind = _norm(match.group("kind"))
    operation = "find_folders" if kind == "carpeta" else "find_files"
    return operation, _clean(match.group("query"))


def _extract_local_open(command):
    """Recognize explicitly local opening requests before web/app routing."""

    match = re.match(
        r"^\s*(?:abre|abrir|abr[íi])\s+(?P<target>.+?)\s*$",
        command,
        re.I,
    )
    if not match:
        return None, None

    target = _clean(match.group("target"))
    normalized_target = _norm(target)

    latest_downloads = {
        "el ultimo archivo descargado",
        "mi ultimo archivo descargado",
        "la ultima descarga",
        "lo ultimo que descargue",
        "lo ultimo descargado",
    }
    if normalized_target in latest_downloads:
        return "open_latest_download", target

    known_folders = {
        "descargas", "mis descargas", "documentos", "mis documentos",
        "escritorio", "mi escritorio", "imagenes", "mis imagenes",
        "fotos", "mis fotos", "musica", "mi musica", "videos",
        "mis videos", "onedrive", "mi onedrive",
    }
    folder_target = re.sub(r"^(?:la\s+)?carpeta\s+", "", normalized_target)
    if folder_target in known_folders:
        return "open_known_folder", target

    typed = re.match(
        r"^(?:(?:el|la|un|una|mi|mis)\s+)?"
        r"(?P<kind>archivo|carpeta|excel|word|pdf|documento|imagen|foto|video|audio)"
        r"\s+(?:(?:llamado|llamada|que\s+se\s+llame)\s+)?"
        r"(?P<query>.+?)\s*$",
        target,
        re.I,
    )
    if not typed:
        return None, None
    kind = _norm(typed.group("kind"))
    operation = "open_folder" if kind == "carpeta" else "open_file"
    return operation, _clean(typed.group("query"))


_SPOKEN_UNITS = {
    # "un/una" stay as words: "un poco" or "a un tercio" are not levels.
    "cero": 0, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19,
    "veinte": 20, "veintiuno": 21, "veintidos": 22, "veintitres": 23,
    "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26,
    "veintisiete": 27, "veintiocho": 28, "veintinueve": 29,
    "cien": 100, "ciento": 100,
}
_SPOKEN_TENS = {
    "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60,
    "setenta": 70, "ochenta": 80, "noventa": 90,
}


def _spoken_numbers_to_digits(normalized):
    """Turn Spanish number words (already accent-free) into digits."""

    units = "|".join(sorted(_SPOKEN_UNITS, key=len, reverse=True))
    tens = "|".join(_SPOKEN_TENS)

    def replace_tens(match):
        value = _SPOKEN_TENS[match.group(1)]
        if match.group(2):
            value += _SPOKEN_UNITS.get(match.group(2), 1)
        return str(value)

    text = re.sub(
        rf"\b({tens})(?:\s+y\s+(uno|una|un|dos|tres|cuatro|cinco|seis|siete|ocho|nueve))?\b",
        replace_tens,
        normalized,
    )
    # "por ciento" must survive: "ciento" only counts when it is not
    # preceded by "por".
    return re.sub(
        rf"(?<!por )\b({units})\b",
        lambda match: str(_SPOKEN_UNITS[match.group(1)]),
        text,
    )


_VOLUME_SENTENCE_EDGES = (
    r"^(?:mochi\s+)?(?:por\s+favor\s+)?",
    r"(?:\s+por\s+favor)?$",
)
_SET_VERBS = r"(?:pon|pone|poner|deja|establece|ajusta|configura|lleva|baja|sube|reduce|aumenta)"
_UP_VERBS = r"(?:sube|subi|aumenta|incrementa)"
_DOWN_VERBS = r"(?:baja|reduce|disminuye)"
_PERCENT = r"(?:\s*%|\s+por\s*ciento|\s+puntos)?"
_NAMED_VOLUME_LEVELS = {
    "a la mitad": "50", "al maximo": "100", "en maximo": "100",
    "al minimo": "0", "en silencio": "0", "a un tercio": "33",
    "a un cuarto": "25",
}


def _strict_volume_operation(normalized):
    """Volume operation only when the rule explains the whole sentence."""

    text = re.sub(r"[.,;:!?\u00a1\u00bf]+", " ", _spoken_numbers_to_digits(normalized))
    text = re.sub(r"\s+", " ", text).strip()
    head, tail = _VOLUME_SENTENCE_EDGES

    def whole(pattern):
        return re.match(head + pattern + tail, text)

    # "pon/baja/sube el volumen a 0%": a/al/hasta name the final level.
    match = whole(
        rf"{_SET_VERBS}\s+(?:el\s+)?volumen\s+(?:a|al|hasta)\s+(\d{{1,3}}){_PERCENT}"
    ) or whole(
        rf"(?:pon|pone|poner|deja|establece|ajusta|configura)\s+(?:el\s+)?volumen\s+(?:en\s+)?(\d{{1,3}}){_PERCENT}"
    )
    if match:
        return "set_system_volume", match.group(1), None

    levels = "|".join(re.escape(level) for level in _NAMED_VOLUME_LEVELS)
    match = whole(rf"{_SET_VERBS}\s+(?:el\s+)?volumen\s+({levels})")
    if match:
        return "set_system_volume", _NAMED_VOLUME_LEVELS[match.group(1)], None

    # "baja el volumen", "sube el volumen 5", "baja el volumen en 20 por
    # ciento", "sube el volumen un poco": the number is a delta.
    match = whole(
        rf"({_UP_VERBS}|{_DOWN_VERBS})\s+(?:el\s+)?volumen"
        rf"(?:\s+(?:en\s+|un\s+)?(\d{{1,3}}(?:[.,]\d+)?){_PERCENT}|\s+un\s+poco(?:\s+mas)?)?"
    )
    if match:
        action = "volume_up" if re.fullmatch(_UP_VERBS, match.group(1)) else "volume_down"
        return "control_media", action, (match.group(2) or "10").replace(",", ".")

    if whole(r"(?:silencia|mutea)\s+(?:el\s+)?volumen"):
        return "control_media", "mute", None
    return None


_LOCAL_SHOW = (
    r"(?:muestra|muestrame|mostra|mostrame|ensena|ensename|lista|listame|"
    r"listar|dime|decime|cuales\s+son)"
)
_LOCAL_PC = r"(?:mi|la|esta)\s+(?:computadora|pc|equipo|compu|maquina)"
_CLAUSE_WORDS = re.compile(
    r"\b(?:que|porque|y|luego|despues|cuando|si|pero|para|no|todo|toda|todos|todas)\b"
)


def _aligned_sentence(command):
    """Normalized sentence plus, per character, its index in the original.

    Patterns match the accent-free text; captured names and texts are cut
    from the original so "Visual Studio Code" keeps its spelling.
    """

    original = str(command or "")
    normalized, index = [], []
    for position, char in enumerate(original):
        folded = unicodedata.normalize("NFD", char.casefold())
        for piece in folded:
            if unicodedata.category(piece) == "Mn":
                continue
            # Punctuation becomes a space so it never glues words together
            # and never shifts the alignment.
            normalized.append(" " if piece in ".,;:!?¡¿\"" else piece)
            index.append(position)
    return original, "".join(normalized), index


def _strict_local_operation(command):
    """Local (non-volume) operation only when a rule covers the sentence."""

    original, text, index = _aligned_sentence(command)

    def whole(pattern):
        return re.match(
            r"^\s*(?:mochi\s+)?(?:por\s+favor\s+)?" + pattern + r"(?:\s+por\s+favor)?\s*$",
            text,
        )

    def captured(match):
        start, end = match.span("t")
        if start == end:
            return None
        return _clean(original[index[start]:index[end - 1] + 1]) or None

    def plain_target(match):
        target = captured(match)
        if not target or len(target) > 80 or _CLAUSE_WORDS.search(_norm(target)):
            return None
        return re.sub(r"^(?:el|la|los|las)\s+", "", target, flags=re.I)

    media = (
        (r"(?:pausa|pausar|reanuda|reanudar|continua|continuar|segui|seguir)"
         r"\s+(?:con\s+)?(?:la\s+)?musica", "play_pause"),
        (r"(?:deten|detene|detener|para|parar|frena|frenar|apaga|apagar)"
         r"\s+(?:la\s+)?musica", "stop"),
        (r"(?:(?:pon|pone|pasa|salta|cambia)(?:\s+a)?\s+)?(?:la\s+)?"
         r"(?:(?:siguiente|proxima)\s+(?:cancion|pista|tema)|(?:cancion|pista|tema)\s+siguiente)", "next"),
        (r"(?:(?:pon|pone|vuelve|volve|regresa)(?:\s+a)?\s+)?(?:la\s+)?"
         r"(?:(?:cancion|pista|tema)\s+anterior|anterior\s+(?:cancion|pista|tema))", "previous"),
    )
    for pattern, action in media:
        if whole(pattern):
            return "control_media", action, None

    no_argument = (
        (rf"{_LOCAL_SHOW}\s+(?:las\s+)?ventanas(?:\s+abiertas)?", "list_open_windows", None),
        (r"(?:que|cuales)\s+ventanas\s+(?:hay|tengo|estan)(?:\s+abiertas)?", "list_open_windows", None),
        (rf"{_LOCAL_SHOW}\s+(?:mis\s+carpetas(?:\s+de\s+usuario)?|las\s+carpetas\s+(?:conocidas|de\s+usuario))",
         "list_user_folders", None),
        (r"(?:cuales|que)\s+son\s+mis\s+carpetas", "list_user_folders", None),
        (rf"{_LOCAL_SHOW}\s+(?:los\s+)?perfiles(?:\s+disponibles)?\s+de\s+chrome", "browser_profiles", "list"),
        (r"(?:que|cuales)\s+perfiles\s+(?:hay|tengo)\s+en\s+chrome", "browser_profiles", "list"),
        (r"(?:(?:dime|decime|muestra|muestrame|mostra|mostrame)\s+)?(?:cual\s+es\s+)?"
         r"el\s+perfil\s+(?:activo|actual|predeterminado)\s+de\s+chrome", "browser_profiles", "active"),
        (r"(?:que|cual)\s+perfil\s+(?:de\s+chrome\s+estoy\s+usando|estoy\s+usando\s+en\s+chrome)",
         "browser_profiles", "active"),
        (r"(?:lee|leeme|muestra|muestrame|mostra|mostrame|dime|decime)\s+"
         r"(?:el\s+portapapeles|el\s+contenido\s+del\s+portapapeles|(?:lo\s+)?que\s+(?:hay|tengo)\s+en\s+el\s+portapapeles)",
         "read_clipboard", None),
        (r"(?:lo\s+)?que\s+(?:hay|tengo)\s+en\s+el\s+portapapeles", "read_clipboard", None),
        (r"(?:(?:haz|hace|hacer|saca|sacar|toma|tomar)(?:me)?\s+)?(?:una\s+)?"
         r"(?:captura(?:\s+de\s+(?:la\s+)?pantalla)?|screenshot|pantallazo)", "capture_screenshot", None),
        (r"(?:captura|capturar)\s+(?:la\s+)?pantalla", "capture_screenshot", None),
        (rf"{_LOCAL_SHOW}\s+(?:los\s+|mis\s+)?juegos\s+instalados", "list_games", None),
        (r"(?:que|cuales)\s+juegos\s+(?:tengo|hay)\s+instalados", "list_games", None),
        (rf"{_LOCAL_SHOW}\s+(?:las\s+|los\s+|mis\s+)?(?:aplicaciones|programas|apps)\s+instalad[oa]s",
         "list_installed_applications", None),
        (r"(?:que|cuales)\s+(?:aplicaciones|programas|apps)\s+(?:tengo|hay)\s+instalad[oa]s",
         "list_installed_applications", None),
        (rf"(?:{_LOCAL_SHOW}\s+)?(?:las\s+)?(?:especificaciones|caracteristicas|specs)\s+de\s+{_LOCAL_PC}",
         "get_system_info", None),
        (rf"(?:{_LOCAL_SHOW}\s+)?(?:el\s+)?hardware\s+de\s+{_LOCAL_PC}", "get_system_info", None),
    )
    for pattern, operation, target in no_argument:
        if whole(pattern):
            return operation, target, None

    copy_verbs = r"(?:copia|copiar|guarda|guardar|escribe|escribi|escribir|pon|pone)"
    match = (
        whole(rf"{copy_verbs}\s+(?P<t>.+?)\s+(?:en|al)\s+(?:el\s+)?portapapeles")
        or whole(rf"{copy_verbs}\s+(?:en|al)\s+(?:el\s+)?portapapeles\s+(?P<t>.+)")
    )
    if match and captured(match):
        return "write_clipboard", captured(match), None

    match = whole(
        r"(?:muestra|muestrame|mostra|mostrame|envia|enviame|manda|mandame|crea|tira|tirame|lanza)"
        r"\s+(?:una\s+)?(?:notificacion|aviso)\s+"
        r"(?:que\s+diga|con\s+el\s+(?:mensaje|texto)|diciendo|de)\s+(?P<t>.+)"
    )
    if match and captured(match):
        return "show_notification", "Mochi", captured(match)

    close_verbs = r"(?:cierra|cerra|cerrar)"
    match = whole(rf"{close_verbs}\s+(?:todas\s+)?(?:las\s+)?ventanas\s+(?:de\s+)?(?P<t>.+)")
    if match:
        target = plain_target(match)
        return ("close_application_windows", target, None) if target else None
    match = whole(rf"{close_verbs}\s+(?:la\s+)?(?:ventana\s+(?:de\s+)?)?(?P<t>.+)")
    if match:
        target = plain_target(match)
        return ("close_window", target, None) if target else None

    front = r"(?:\s+(?:al\s+frente|en\s+primer\s+plano))"
    match = (
        whole(rf"(?P<v>minimiza|maximiza|restaura|enfoca)\s+(?:la\s+ventana\s+(?:de\s+)?)?(?P<t>.+?){front}?")
        or whole(rf"(?P<v>muestra|mostra|trae|pon|pone)\s+(?:la\s+ventana\s+(?:de\s+)?)?(?P<t>.+?){front}")
        or whole(r"(?P<v>muestra|mostra|trae|pon|pone)\s+la\s+ventana\s+(?:de\s+)?(?P<t>.+)")
    )
    if match:
        target = plain_target(match)
        if not target:
            return None
        verb = match.group("v")
        action = (
            "minimize" if verb == "minimiza"
            else "maximize" if verb == "maximiza"
            else "focus"
        )
        return "control_window", target, action

    match = whole(
        r"(?:abre|abri|abrir|inicia|iniciar|ejecuta|ejecutar|juga|jugar)\s+(?:a\s+)?"
        r"(?:(?:el|un)\s+)?juego\s+(?P<t>.+)"
    )
    if match:
        target = plain_target(match)
        return ("launch_game", target, None) if target else None
    return None


def _extract_direct_local_operation(command):
    """Recognize short local requests that do not need an extra model turn."""

    normalized = _norm(command)

    # System volume is fully constrained by the user's words.  Keeping these
    # operations in the structured plan avoids depending on whether a small
    # local model happens to remember that the corresponding tool exists.
    # The rules only fire when they explain the whole sentence; anything
    # else is left to the model (see brain.local_intent) instead of being
    # completed with a guessed default.
    if "volumen" in normalized:
        if any(
            phrase in normalized
            for phrase in {
                "volumen actual", "nivel de volumen", "cuanto volumen",
                "a cuanto esta el volumen", "como esta el volumen",
            }
        ):
            return "get_system_volume", None, None

        strict = _strict_volume_operation(normalized)
        if strict:
            return strict

    # The remaining local rules follow the same contract as the volume ones:
    # each pattern must cover the whole sentence.  A sentence that only
    # contains the right keywords ("no hagas captura de pantalla", "muestra
    # el clima") is not a match; brain.local_intent asks the model instead.
    strict = _strict_local_operation(command)
    if strict:
        return strict

    # A bare dotted acronym is not a normal domain (for example R.E.P.O.).
    # Treat it as an explicit local game candidate, but leave URLs/domains and
    # ordinary "abre X" requests to their existing browser/app routing.
    dotted_game = re.match(
        r"^\s*(?:abre|abrir|abr[íi]|inicia|iniciar|ejecuta|ejecutar)\s+"
        r"(?P<game>(?:[A-Za-z]\.){2,}[A-Za-z]\.?)\s*$",
        command,
        re.I,
    )
    if dotted_game:
        return "launch_game", _clean(dotted_game.group("game")), None

    # For an unknown bare destination ("abre repo", "abre discrod"), defer
    # the category decision to open_target. Known services, URLs and domains
    # keep their regular web plan; this avoids treating Netflix or a URL as a
    # local program just because the user used a short imperative.
    simple_open = re.match(
        r"^\s*(?:abre|abrir|abr[íi]|inicia|iniciar|ejecuta|ejecutar)\s+"
        r"(?P<target>.+?)\s*$",
        command,
        re.I,
    )
    if simple_open:
        target = _clean(simple_open.group("target"))
        contextual_target = _norm(target)
        has_follow_up_context = any(
            re.search(r"\b" + re.escape(word) + r"\b", contextual_target)
            for word in {
                "con", "luego", "despues", "perfil", "cuenta",
                "minimiza", "maximiza", "frente",
            }
        )
        destination = WebDestinationResolver().resolve(target)
        if target and not has_follow_up_context and destination.get("kind") == "search":
            return "open_target", target, None

    return None, None, None


def _plan_specialized_web_request(request):
    """Translate one provider specialist result into a verified step graph."""

    state = GoalState(goal="user_request", target=request.provider)
    state.browser_profile = request.browser_profile
    state.site_profile_source = request.site_profile_source
    state.site_profile = request.site_profile
    state.page_query = request.query or request.channel
    state.page_result = request.result_title
    state.channel_name = request.channel
    state.result_order = request.order

    dependency = None
    # A provider named by the user is normally opened/adopted.  Explicit
    # current-tab language keeps the operation on the verified active page,
    # unless a Chrome account was requested and therefore must be selected.
    if not request.reuse_current or request.browser_profile:
        state.steps.append(
            GoalStep(
                "open", "browser", "browser_open",
                target=request.provider, value=request.browser_profile,
            )
        )
        dependency = "open"

    if request.site_profile:
        state.steps.append(
            GoalStep(
                "site_profile", "web_page", "select_site_profile",
                target=request.provider, value=request.site_profile,
                depends_on=dependency,
            )
        )
        dependency = "select_site_profile"

    if request.channel:
        state.goal = "open_channel" if request.channel_only else "channel_video"
        state.steps.extend([
            GoalStep(
                "page_search", "web_page", "search_page",
                target=request.provider, value=request.channel,
                depends_on=dependency,
            ),
            GoalStep(
                "channel", "web_page", "select_channel",
                target=request.provider, value=request.channel,
                depends_on="page_search",
            ),
        ])
        dependency = "channel"
        if not request.channel_only:
            state.steps.append(
                GoalStep(
                    "content_tab", "web_page", "select_content_tab",
                    target=request.provider, value="Videos",
                    depends_on=dependency,
                )
            )
            dependency = "content_tab"
            if request.order:
                state.steps.append(
                    GoalStep(
                        "sort", "web_page", "select_sort",
                        target=request.provider,
                        value="Mas antiguos" if request.order == "oldest" else "Mas recientes",
                        depends_on=dependency,
                    )
                )
                dependency = "sort"
            if request.list_only:
                state.goal = "search_in_page"
                state.steps.append(
                    GoalStep(
                        "result_list", "web_page", "collect_results",
                        target=request.provider,
                        value=str(request.list_limit or 5),
                        depends_on=dependency,
                    )
                )
                dependency = "result_list"
            else:
                index = request.result_index or 1
                state.steps.append(
                    GoalStep(
                        "visible_video", "web_page", "select_visible_video",
                        target=request.provider,
                        value=f"{request.result_scope or 'visible'}:{index}",
                        depends_on=dependency,
                    )
                )
                dependency = "visible_video"
    elif request.query:
        state.steps.append(
            GoalStep(
                "page_search", "web_page", "search_page",
                target=request.provider, value=request.query,
                depends_on=dependency,
            )
        )
        dependency = "page_search"
        if request.list_only:
            state.goal = "search_in_page"
            state.steps.append(
                GoalStep(
                    "result_list", "web_page", "collect_results",
                    target=request.provider,
                    value=str(request.list_limit or 5),
                    depends_on=dependency,
                )
            )
            dependency = "collect_results"
        elif request.result_index:
            state.goal = "site_content_sequence"
            state.steps.append(
                GoalStep(
                    "visible_video", "web_page", "select_visible_video",
                    target=request.provider,
                    value=f"{request.result_scope or 'visible'}:{request.result_index}",
                    depends_on=dependency,
                )
            )
            dependency = "visible_video"
        elif request.result_title:
            state.goal = (
                "search_and_open_result"
                if str(request.provider).casefold() == "youtube"
                else "site_content_sequence"
            )
            state.steps.append(
                GoalStep(
                    "content_result", "web_page", "select_result",
                    target=request.provider, value=request.result_title,
                    depends_on=(
                        "search_page"
                        if str(request.provider).casefold() == "netflix"
                        else dependency
                    ),
                )
            )
            dependency = "select_result"
        elif any(item.operation == "play_media" for item in request.media):
            # "Busca un tutorial y reprodúcelo" authorises the first actual
            # media card, not an autocomplete suggestion or navigation link.
            state.goal = "search_and_play_any_result"
            state.steps.append(
                GoalStep(
                    "visible_video", "web_page", "select_visible_video",
                    target=request.provider, value="visible:1",
                    depends_on=dependency,
                )
            )
            dependency = "visible_video"
        else:
            state.goal = "search_in_page"
    elif request.result_index:
        # Ordinals such as "el tercer video en pantalla" are grounded in
        # the current provider view.  They are not search text and must not
        # be collapsed into a generic player toggle.
        state.goal = "select_visible_video"
        state.steps.append(
            GoalStep(
                "visible_video", "web_page", "select_visible_video",
                target=request.provider,
                value=f"{request.result_scope or 'visible'}:{request.result_index}",
                depends_on=dependency,
            )
        )
        dependency = "visible_video"
    elif request.has_work:
        state.goal = "site_player_control" if request.media else "site_context_action"
    else:
        state.goal = "open_destination"

    if request.season:
        state.steps.append(
            GoalStep(
                "season", "web_page", "select_season",
                target=request.provider, value=f"Temporada {request.season:g}",
                depends_on=dependency,
            )
        )
        dependency = "select_season"
    if request.episode:
        state.steps.append(
            GoalStep(
                "episode", "web_page", "select_episode",
                target=request.provider,
                value=f"{request.episode_kind or 'Episodio'} {request.episode:g}",
                depends_on=dependency,
            )
        )
        dependency = "select_episode"

    media = list(request.media)
    if request.channel and not request.channel_only and not request.list_only and not any(
        item.operation == "play_media" for item in media
    ):
        media.insert(0, MediaOperation("play_media", "Reproducir", -1))
    needs_player = bool(request.query and any(
        item.operation in {
            "pause_media", "seek_media", "adjust_media_position",
            "next_media", "previous_media",
        }
        for item in media
    ))
    if request.episode:
        needs_player = True
    if needs_player and not any(item.operation == "play_media" for item in media):
        media.insert(0, MediaOperation("play_media", "Reproducir", -1))

    # Retain the historical dependency contract for a player-only request:
    # the media operation depends on the browser operation itself.  Page
    # workflows depend on the stable ``open`` step id because they have
    # intermediate UIA actions.
    if dependency == "open" and not any(
        step.scope == "web_page" for step in state.steps
    ):
        dependency = "browser_open"

    seen_counts = {}
    for item in media:
        operation = item.operation
        seen_counts[operation] = seen_counts.get(operation, 0) + 1
        step_id = operation if seen_counts[operation] == 1 else f"{operation}_{seen_counts[operation]}"
        state.steps.append(
            GoalStep(
                step_id,
                "local_system" if operation == "adjust_volume" else "web_page",
                operation,
                target=request.provider,
                value=item.value,
                depends_on=dependency,
            )
        )
        dependency = step_id

    for index, capability in enumerate(request.unsupported, 1):
        state.steps.append(
            GoalStep(
                f"unsupported_{index}", "web_page", capability,
                status="blocked", target=request.provider,
                reason="capability_not_implemented",
                depends_on=dependency,
            )
        )

    return state


# El nombre de Mochi y la cortesía no son parte de la orden:
# "Mochi, ¿me podés poner X en YouTube, porfa?" → "poner X en YouTube".
_ADDRESS_PREFIX = re.compile(
    r"^\s*(?:(?:che|hola|ey|oye)\s*,?\s+(?=mochi\b))?(?:mochi\b[\s,:]*)?[¿¡]?\s*"
    r"(?:(?:por\s+favor|porfa)\s*,?\s+)?"
    r"(?:(?:me\s+)?(?:pod[eé]s|podr[ií]as|puedes)\s+)?",
    re.I,
)
_COURTESY_SUFFIX = re.compile(r"(?:\s*,?\s*(?:por\s+favor|porfa))?\s*[?!.]*\s*$", re.I)


def _strip_address(command):
    stripped = _clean(_COURTESY_SUFFIX.sub("", _ADDRESS_PREFIX.sub("", command, count=1), count=1))
    return stripped or command


# Pedidos de poner algo sin decir qué ("poné una canción", "poneme
# música en YouTube", "quiero escuchar Queen"): no hay un contenido
# para buscar al pie de la letra. No se arma un plan fijo; los
# interpreta el LLM con play_web_media.
_OPEN_ENDED_CONTENT = re.compile(
    r"(?:un|una|unos|unas|algun\w*|cualquier|otr[oa]s?)\s.+"
    r"|(?:algo|musica|canciones|temas|videos)(?:\s.*)?"
)


def _open_ended_media_request(command):
    normalized = _norm(command)
    if re.match(r"(?:quiero|quisiera|me\s+gustaria)\s+(?:escuchar|oir|ver|mirar)\b", normalized):
        return True
    match = re.match(
        rf"{PLAY_VERBS}\s+(?P<content>.+?)"
        r"(?:\s+(?:en|dentro\s+de)\s+(?:el\s+)?(?:youtube|netflix))?$",
        normalized,
    )
    return bool(match and _OPEN_ENDED_CONTENT.fullmatch(match.group("content")))


def plan_web_media(site, query, title=None):
    """
    Plan de la tool play_web_media. El LLM ya interpretó el pedido
    (sitio, qué buscar y, si lo nombró, el título exacto); lo ejecuta
    el mismo plan verificado que "poné X en YouTube". Sin título se
    reproduce el primer resultado. None si falta qué buscar.
    """

    query = _clean(query)
    if not query:
        return None
    request = SpecialistRequest(
        provider="Netflix" if _norm(site) == "netflix" else "YouTube",
        query=query,
        result_title=_clean(title) or None,
        media=[MediaOperation("play_media", "Reproducir", 0)],
    )
    return _plan_specialized_web_request(request)


def plan_goal(user_command, context_provider=None):
    command = _strip_address(_clean(user_command))
    if _open_ended_media_request(command):
        return GoalState(goal="user_request")
    normalized = _norm(command)
    is_search = bool(re.search(r"\b(busca|buscar|buscame|busque|search)\b", normalized))
    target = _extract_target(command)
    state = GoalState(goal="web_search" if is_search and not target else "user_request", target=target, explicit_search=is_search)

    # An explicit provider disambiguates nouns such as "video":
    # "busca el video ... en YouTube" is web work, while "busca un video
    # llamado ..." without a provider remains a local file request.  Provider
    # specialists therefore get the first chance to claim an explicit or
    # safely contextual command.
    specialist_request = parse_specialized_web_command(
        command,
        context_provider=context_provider,
    )
    if specialist_request:
        return _plan_specialized_web_request(specialist_request)

    # A file title can naturally contain "en" (for example "qué hacer en
    # mis vacaciones"). Once no provider specialist claimed the utterance,
    # local intent wins over the legacy generic web parser.
    local_operation, local_query = _extract_local_file_search(command)
    if local_operation and local_query:
        state.goal = "local_file_search"
        state.target = local_query
        state.steps.append(
            GoalStep(
                "local_search",
                "filesystem",
                local_operation,
                target=local_query,
            )
        )
        return state

    visible_video_index, visible_video_scope = _extract_visible_video_request(command)
    if visible_video_index:
        # This is explicitly scoped to what the user can currently see.  It
        # must inspect the active page, never open a fresh service tab.
        state.goal = "select_visible_video"
        # `_extract_target` may read "abre el primer video recomendado" as a
        # generic open target. Here "abre" means activate a card on the
        # current page, not open a new destination. Keep only independently
        # verified provider context when one exists.
        state.target = (
            context_provider
            if str(context_provider or "").casefold() in {"youtube", "netflix"}
            else None
        )
        state.steps.extend([
            GoalStep(
                "visible_video",
                "web_page",
                "select_visible_video",
                value=f"{visible_video_scope}:{visible_video_index}",
            ),
            GoalStep(
                "playback",
                "web_page",
                "play_media",
                value="Reproducir",
                depends_on="select_visible_video",
            ),
        ])
        if _requests_pause(command):
            state.steps.append(
                GoalStep(
                    "pause_media",
                    "web_page",
                    "pause_media",
                    value="Pausa",
                    depends_on="playback",
                )
            )
        return state

    channel_name, channel_order, channel_only = _extract_channel_task(command)
    if channel_name:
        state.goal = "open_channel" if channel_only else "channel_video"
        state.target = "YouTube"
        state.channel_name = channel_name
        state.result_order = channel_order
        state.page_query = channel_name
        state.steps.extend([
            GoalStep("open", "browser", "browser_open", target="YouTube"),
            GoalStep("page_search", "web_page", "search_page", target="YouTube", value=channel_name, depends_on="open"),
            GoalStep("channel", "web_page", "select_channel", target="YouTube", value=channel_name, depends_on="page_search"),
        ])
        if not channel_only:
            state.steps.extend([
                GoalStep("content_tab", "web_page", "select_content_tab", target="YouTube", value="Videos", depends_on="channel"),
                GoalStep(
                    "sort",
                    "web_page",
                    "select_sort",
                    target="YouTube",
                    value="Mas antiguos" if channel_order == "oldest" else "Mas recientes",
                    depends_on="content_tab",
                ),
                GoalStep("visible_video", "web_page", "select_visible_video", target="YouTube", value="visible:1", depends_on="sort"),
                GoalStep("playback", "web_page", "play_media", target="YouTube", value="Reproducir", depends_on="visible_video"),
            ])
        return state

    page_target, page_query, page_result = _extract_page_task(command)
    if page_target and page_query:
        pronoun_playback = _requests_pronoun_playback(command)
        any_result = pronoun_playback and _is_indefinite_media_query(page_query)
        season = _extract_season_request(command)
        episode = _extract_episode_request(command)
        player_operation, player_value, play_after_position = _extract_player_control(command)
        pause_requested = _requests_pause(command)
        compound_content_control = bool(
            season or episode or player_operation or pause_requested
        )
        if compound_content_control:
            page_query = _content_entity_name(page_query)
        state.goal = (
            "site_content_sequence"
            if compound_content_control
            else "search_and_play_any_result"
            if any_result
            else "search_and_open_result" if page_result else "search_in_page"
        )
        state.target = page_target
        state.page_query = page_query
        # A compound control (season/episode/seek/pause) needs an actual media
        # result before it can act on a player.  The requested search phrase
        # is therefore also the semantic result target when no separate title
        # was supplied.  This preserves every clause in requests such as
        # "busca X en Netflix, pon el minuto 15 y páralo".
        state.page_result = (
            page_result or page_query
            if compound_content_control
            else None if any_result else page_result
        )
        state.browser_profile = _extract_browser_profile(command, page_target)
        state.steps.extend([
            GoalStep("open", "browser", "browser_open", target=page_target, value=state.browser_profile),
            GoalStep("page_search", "web_page", "search_page", target=page_target, value=page_query, depends_on="open"),
        ])
        if compound_content_control:
            dependency = "search_page"
            state.steps.append(
                GoalStep(
                    "content_result",
                    "web_page",
                    "select_result",
                    target=page_target,
                    value=state.page_result,
                    depends_on=dependency,
                )
            )
            dependency = "select_result"
            if season:
                state.steps.append(
                    GoalStep(
                        "season",
                        "web_page",
                        "select_season",
                        target=page_target,
                        value=season,
                        depends_on=dependency,
                    )
                )
                dependency = "select_season"
            if episode:
                state.steps.append(
                    GoalStep(
                        "episode",
                        "web_page",
                        "select_episode",
                        target=page_target,
                        value=episode,
                        depends_on=dependency,
                    )
                )
                dependency = "select_episode"

            needs_initial_play = bool(
                episode
                or pause_requested
                or player_operation in {
                    "seek_media", "adjust_media_position",
                    "next_media", "previous_media",
                }
            )
            if needs_initial_play:
                state.steps.append(
                    GoalStep(
                        "playback",
                        "web_page",
                        "play_media",
                        target=page_target,
                        value="Reproducir",
                        depends_on=dependency,
                    )
                )
                dependency = "play_media"

            if player_operation and player_operation != "pause_media":
                if player_operation == "play_media" and needs_initial_play:
                    pass
                else:
                    state.steps.append(
                        GoalStep(
                            "player_control",
                            "web_page",
                            player_operation,
                            target=page_target,
                            value=player_value,
                            depends_on=dependency,
                        )
                    )
                    dependency = player_operation
                    if play_after_position and not state.step("play_media"):
                        state.steps.append(
                            GoalStep(
                                "playback_after_position",
                                "web_page",
                                "play_media",
                                target=page_target,
                                value="Reproducir",
                                depends_on=dependency,
                            )
                        )
                        dependency = "play_media"
            if pause_requested or player_operation == "pause_media":
                state.steps.append(
                    GoalStep(
                        "pause",
                        "web_page",
                        "pause_media",
                        target=page_target,
                        value="Pausa",
                        depends_on=dependency,
                    )
                )
        elif any_result:
            state.steps.extend([
                GoalStep(
                    "visible_video",
                    "web_page",
                    "select_visible_video",
                    target=page_target,
                    value="visible:1",
                    depends_on="page_search",
                ),
                GoalStep(
                    "playback",
                    "web_page",
                    "play_media",
                    target=page_target,
                    value="Reproducir",
                    depends_on="visible_video",
                ),
            ])
        elif page_result:
            state.steps.append(
                GoalStep(
                    "select_result",
                    "web_page",
                    "select_result",
                    target=page_target,
                    value=page_result,
                    depends_on="page_search",
                )
            )
            if pronoun_playback:
                state.steps.append(
                    GoalStep(
                        "playback",
                        "web_page",
                        "play_media",
                        target=page_target,
                        value="Reproducir",
                        depends_on="select_result",
                    )
                )
        return state

    media_target, media_query, media_result = _extract_media_play_task(command)
    if media_target and media_query and media_result:
        state.goal = "search_and_open_result"
        state.target = media_target
        state.page_query = media_query
        state.page_result = media_result
        state.steps.extend([
            GoalStep("open", "browser", "browser_open", target=media_target),
            GoalStep("page_search", "web_page", "search_page", target=media_target, value=media_query, depends_on="open"),
            GoalStep("select_result", "web_page", "select_result", target=media_target, value=media_result, depends_on="page_search"),
            GoalStep("playback", "web_page", "play_media", target=media_target, value="Reproducir", depends_on="select_result"),
        ])
        return state

    # A common spoken instruction scopes the site first, then asks for a
    # search inside it: "abre Netflix con X, entra al perfil Y, busca Z".
    # It is not covered by "busca Z en Netflix", but it has the same ordered
    # browser → profile → page-search semantics.
    follow_up_query = _extract_follow_up_page_search(command) if target else None
    if target and follow_up_query:
        _site, site_profile = _extract_site_profile(command, target)
        season = _extract_season_request(command)
        episode = _extract_episode_request(command)
        player_operation, player_value, play_after_position = _extract_player_control(command)
        pause_requested = _requests_pause(command)
        state.goal = "site_content_sequence"
        state.target = target
        state.browser_profile = _extract_browser_profile(command, target)
        state.site_profile = site_profile
        state.page_query = follow_up_query
        # Selecting a named content result is required before an episode can
        # be selected. A plain in-site search remains a search-only request.
        state.page_result = follow_up_query if (season or episode or player_operation) else None
        state.steps.append(
            GoalStep("open", "browser", "browser_open", target=target, value=state.browser_profile)
        )
        dependency = "open"
        if site_profile:
            state.steps.append(
                GoalStep(
                    "site_profile",
                    "web_page",
                    "select_site_profile",
                    target=target,
                    value=site_profile,
                    depends_on=dependency,
                )
            )
            dependency = "select_site_profile"
        state.steps.append(
            GoalStep(
                "page_search",
                "web_page",
                "search_page",
                target=target,
                value=follow_up_query,
                depends_on=dependency,
            )
        )
        dependency = "search_page"
        if state.page_result:
            state.steps.append(
                GoalStep(
                    "content_result",
                    "web_page",
                    "select_result",
                    target=target,
                    value=state.page_result,
                    depends_on=dependency,
                )
            )
            dependency = "select_result"
        if season:
            state.steps.append(
                GoalStep(
                    "season",
                    "web_page",
                    "select_season",
                    target=target,
                    value=season,
                    depends_on=dependency,
                )
            )
            dependency = "select_season"
        if episode:
            state.steps.append(
                GoalStep(
                    "episode",
                    "web_page",
                    "select_episode",
                    target=target,
                    value=episode,
                    depends_on=dependency,
                )
            )
            dependency = "select_episode"
            state.steps.append(
                GoalStep(
                    "playback",
                    "web_page",
                    "play_media",
                    target=target,
                    value="Reproducir",
                    depends_on=dependency,
                )
            )
            dependency = "play_media"
        elif player_operation and state.page_result:
            # A title card is not a player.  Before seeking within a result
            # requested from a search, require a verified Play control.
            state.steps.append(
                GoalStep(
                    "playback",
                    "web_page",
                    "play_media",
                    target=target,
                    value="Reproducir",
                    depends_on=dependency,
                )
            )
            dependency = "play_media"
        if player_operation:
            state.steps.append(
                GoalStep(
                    "player_control",
                    "web_page",
                    player_operation,
                    target=target,
                    value=player_value,
                    depends_on=dependency,
                )
            )
            dependency = player_operation
            if play_after_position and not state.step("play_media"):
                state.steps.append(
                    GoalStep(
                        "playback_after_position",
                        "web_page",
                        "play_media",
                        target=target,
                        value="Reproducir",
                        depends_on=dependency,
                    )
                )
                dependency = "play_media"
        if pause_requested:
            state.steps.append(
                GoalStep(
                    "pause", "web_page", "pause_media", target=target, value="Pausa", depends_on=dependency
                )
            )
        return state

    player_operation, player_value, play_after_position = _extract_player_control(command)
    if target and player_operation:
        # "Abrí Netflix y avanzá 30 segundos" explicitly changes scope to a
        # site, whereas a bare "avanzá 30 segundos" stays in the current tab.
        state.goal = "site_player_control"
        state.browser_profile = _extract_browser_profile(command, target)
        state.steps.append(
            GoalStep("open", "browser", "browser_open", target=target, value=state.browser_profile)
        )
        state.steps.append(
            GoalStep(
                "player_control",
                "web_page",
                player_operation,
                target=target,
                value=player_value,
                depends_on="browser_open",
            )
        )
        if play_after_position:
            state.steps.append(
                GoalStep(
                    "playback_after_position",
                    "web_page",
                    "play_media",
                    target=target,
                    value="Reproducir",
                    depends_on=player_operation,
                )
            )
        return state

    # A short continuation such as "pon el capítulo 7 y páusalo" is scoped
    # to the page that is already active.  It must not fall through to the
    # generic local-window rule for "pon X", nor reopen the browser and lose
    # the current content context.
    episode = _extract_episode_request(command)
    if episode:
        season = _extract_season_request(command)
        pause_requested = _requests_pause(command)
        state.goal = "current_page_episode"
        dependency = None
        if season:
            state.steps.append(
                GoalStep(
                    "season",
                    "web_page",
                    "select_season",
                    value=season,
                )
            )
            dependency = "select_season"
        state.steps.append(
            GoalStep(
                "episode",
                "web_page",
                "select_episode",
                value=episode,
                depends_on=dependency,
            )
        )
        state.steps.append(
            GoalStep(
                "playback",
                "web_page",
                "play_media",
                value="Reproducir",
                depends_on="select_episode",
            )
        )
        if pause_requested:
            state.steps.append(
                GoalStep(
                    "pause",
                    "web_page",
                    "pause_media",
                    value="Pausa",
                    depends_on="play_media",
                )
            )
        return state

    active_video = _extract_active_video_change(command)
    if active_video:
        # This intentionally has no browser_open step: "cambia de video"
        # means operate in the page the user already has open.
        state.goal = "change_active_video"
        state.page_query = active_video
        state.page_result = active_video
        state.steps.extend([
            GoalStep("page_search", "web_page", "search_page", value=active_video),
            GoalStep("select_result", "web_page", "select_result", value=active_video, depends_on="page_search"),
        ])
        return state

    player_operation, player_value, play_after_position = _extract_player_control(command)
    if player_operation:
        # Playback navigation is deliberately scoped to the active web page.
        # There is no browser_open step: a command such as "avanzá 30
        # segundos" must reuse the player already in front of the user.
        state.goal = "player_control"
        state.steps.append(
            GoalStep(
                "player_control",
                "web_page",
                player_operation,
                value=player_value,
            )
        )
        if play_after_position:
            state.steps.append(
                GoalStep(
                    "playback_after_position",
                    "web_page",
                    "play_media",
                    value="Reproducir",
                    depends_on=player_operation,
                )
            )
        return state

    local_open_operation, local_open_target = _extract_local_open(command)
    if local_open_operation and local_open_target:
        state.goal = "local_file_open"
        state.target = local_open_target
        state.steps.append(
            GoalStep(
                "local_open",
                "filesystem",
                local_open_operation,
                target=local_open_target,
            )
        )
        return state

    direct_operation, direct_target, direct_value = _extract_direct_local_operation(command)
    if direct_operation:
        state.goal = (
            "open_named_target"
            if direct_operation == "open_target"
            else "local_direct_action"
        )
        state.target = direct_target
        state.steps.append(
            GoalStep(
                "local_direct",
                "destination" if direct_operation == "open_target" else "local_system",
                direct_operation,
                target=direct_target,
                value=direct_value,
            )
        )
        return state

    if is_search and not target:
        query = re.sub(
            r"^\s*(?:busca|buscar|buscame|b[uú]scame|busque|search)\s+",
            "",
            command,
            flags=re.I,
        )
        query = re.sub(r"\s+en\s+(?:google|la web|internet)\s*$", "", query, flags=re.I)
        state.steps.append(GoalStep("search", "browser", "browser_search", target=_clean(query)))
        return state

    site, site_profile = _extract_site_profile(command, target)
    site_profile_source, transition_target = _extract_site_profile_transition(command, target)
    if transition_target:
        site_profile = transition_target
    switch_inside_site = bool(
        target
        and re.search(
            r"\b(?:cambia|cambiar)\s+(?:(?:la\s+)?(?:cuenta|perfil)\s+de\s+"
            + re.escape(target)
            + r"\s+de\s+.+?\s+a\s+.+?|(?:de\s+)?(?:el\s+)?perfil\s+(?:en|de)\s+"
            + re.escape(target)
            + r"\s+(?:al\s+)?(?:perfil\s+)?de\s+.+?)",
            command,
            re.I,
        )
    )
    # This grammar denotes the active site's own profile, not a Chrome
    # sign-out/sign-in. Start by inspecting the current page.
    if switch_inside_site and site_profile:
        state.goal = "site_context_action"
        # This contextual Chrome-account phrase is not an instruction to
        # reopen Chrome. It becomes a page-menu hint only after an inspected
        # button exposes the same visible name.
        state.site_profile_source = site_profile_source or _extract_browser_profile(command, target)
        state.site_profile = site_profile
        state.steps.append(GoalStep("site_profile", "web_page", "select_site_profile", target=target, value=site_profile, reason="page_context_required"))
        return state
    if not target and site:
        state.target = site
        state.site_profile = site_profile
        state.goal = "site_context_action"
        state.steps.append(GoalStep("site_profile", "web_page", "select_site_profile", target=site, value=site_profile, reason="page_context_missing"))
        return state

    if target:
        state.browser_profile = _extract_browser_profile(command, target)
        state.site_profile = site_profile
        destination = WebDestinationResolver().resolve(target)
        # A known web destination is deterministic even when the request is
        # short ("abre Netflix").  Leaving this to the model caused valid
        # browser tools to be ignored or replaced by an explanatory answer.
        if state.browser_profile or site_profile or (
            destination.get("ok") and destination.get("kind") != "search"
        ):
            state.goal = "open_web_service" if site_profile else "open_destination"
            state.steps.append(GoalStep("open", "browser", "browser_open", target=target, value=state.browser_profile))
        if site_profile:
            state.steps.append(GoalStep("site_profile", "web_page", "select_site_profile", target=target, value=site_profile, depends_on="open"))
    if not state.steps:
        sequence = re.search(r"\b(?:abre|abrir)\s+(.+?)\s+(?:y\s+)?luego\s+(minim[íi]za(?:lo)?|maxim[íi]za(?:lo)?|(?:trae|pon).+?(?:frente))", command, re.I)
        if sequence:
            app = _clean(sequence.group(1))
            action_text = _norm(sequence.group(2))
            action = "minimize" if action_text.startswith("minimiz") else "maximize" if action_text.startswith("maximiz") else "focus"
            state.goal = "sequential_os_actions"
            state.target = app
            state.steps.extend([
                GoalStep("open_app", "os", "open_application", target=app),
                GoalStep("control_window", "os", "control_window", target=app, value=action, depends_on="open_app"),
            ])
    return state
