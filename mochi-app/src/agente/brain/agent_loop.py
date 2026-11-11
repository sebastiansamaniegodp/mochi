import json
import os
import platform
import re
import sys
import time
import unicodedata
from difflib import SequenceMatcher

from brain.brain_router import (
    brain_router
)

from brain.mochi_tools import (
    create_mochi_tool_registry,
    extract_tool_call
)

from brain.goal_state import (
    GoalState,
    GoalStep,
    plan_goal,
    plan_web_media,
    _norm,
    _plan_specialized_web_request,
    page_control_names,
)
from brain.local_intent import interpret_local_command
from brain.channel_intent import interpret_channel_video
from brain.pending_page_choice import PendingPageChoiceCache


# =========================================================
# SISTEMA OPERATIVO REAL
#
# Se le dice al modelo en qué sistema corre, para que no
# asuma Windows. El texto no cambia mientras la app está
# abierta: no rompe la caché del LLM.
# =========================================================

def describe_operating_system():

    system = platform.system() or "desconocido"

    name = system

    if system == "Linux":

        try:

            name = (
                platform.freedesktop_os_release()
                .get("PRETTY_NAME")
                or "Linux"
            )

            if "linux" not in name.lower():

                name = f"Linux ({name})"

        except OSError:

            name = "Linux"

        desktop = os.environ.get(
            "XDG_CURRENT_DESKTOP",
            ""
        ).strip()

        if desktop:

            name = f"{name}, escritorio {desktop}"

    elif system == "Windows":

        name = f"Windows {platform.release()}"

    return (
        f"El sistema operativo de esta computadora es {name}. "
        "No asumas otro sistema operativo ni des instrucciones "
        "para otro. "
    )


# =========================================================
# SYSTEM PROMPT DEL AGENTE
# =========================================================

AGENT_SYSTEM_PROMPT = (
    "Sos Mochi, un asistente que controla una computadora "
    "mediante herramientas estructuradas. "
    + describe_operating_system() +
    "Estás funcionando dentro de un agent loop, por lo que "
    "podés usar una herramienta, observar su resultado y "
    "continuar trabajando hasta completar la petición. "
    ""
    "Nunca inventes el estado actual de la computadora. "
    "Si una decisión depende de información actual del sistema, "
    "primero utilizá una herramienta que permita obtenerla. "
    ""
    "Si una acción depende del resultado de otra herramienta, "
    "no intentes adivinar ese resultado. "
    "Ejecutá primero la herramienta necesaria y después decidí. "
    ""
    "No repitas una herramienta innecesariamente. "
    "Para una orden breve de abrir un nombre cuyo tipo no sea evidente, usá "
    "open_target: resuelve servicios web configurados, juegos y aplicaciones "
    "con tolerancia a errores leves; si sigue siendo ambiguo, pedí aclaración. "
    "Si open_application falla porque no encontró un programa, no pruebes "
    "variantes del mismo nombre: usá browser_open para un sitio o servicio web. "
    "Tras una acción exitosa, respondé en español de forma breve y natural; "
    "no incluyas PID, rutas ni otros detalles técnicos salvo que se pidan. "
    "Cuando la petición esté completada, terminá sin solicitar "
    "más herramientas. Para interactuar con una página: primero usá web_inspect "
    "sin query para observar los controles visibles; usá query solo después si "
    "necesitás acotar resultados. "
    "después web_act usando un ID del snapshot y finalmente web_inspect otra vez "
    "para verificar. Un click exitoso no completa por sí solo el objetivo. "
    "Para cambiar un perfil interno: si el perfil solicitado no aparece como "
    "opción seleccionable pero hay un botón de cuenta o perfil actual, podés "
    "abrir un único menú de cuenta y reinspeccionar. Después elegí solamente "
    "la opción con el nombre exacto solicitado; nunca uses controles de "
    "administración, cuenta, transferencia o cierre de sesión como sustitutos. "
    "La orden 'cambia la cuenta o perfil de [sitio] de A a B' significa cambiar "
    "el perfil interno del sitio a B: inspeccioná la página y no cierres sesión "
    "ni confundas A o B con perfiles de Chrome. "
    "Para abrir un resultado de una búsqueda ya hecha (Google, YouTube, una "
    "tienda), usá web_open_result con su número o con parte de su título. "
    "Para contestar una pregunta sobre algo de internet o sobre lo que dice "
    "una página, buscá con browser_search si hace falta y leé la página con "
    "web_read; respondé con lo leído, sin inventar. "
    "Para buscar dentro de un sitio, no uses browser_search: inspeccioná la "
    "página, abrí Buscar/Search solo si aún no hay un textbox, escribí en el "
    "textbox y usá submit sobre ese campo; luego reinspeccioná para verificar. "
    "Si un resultado accesible está marcado fuera de pantalla, podés usar "
    "web_act con scroll_into_view, reinspeccionar y recién entonces activarlo. "
    "En una orden encadenada de sitio (abrir, elegir perfil, buscar contenido, "
    "elegir episodio y pausar), respetá ese orden y verificá cada transición. "
    "Para un episodio o una pausa solicitados, actuá únicamente sobre el "
    "control accesible con ese nombre exacto; no declares el objetivo completo "
    "por haber abierto solo el sitio. "
    "Si el usuario pidió temporada y capítulo, elegí primero la temporada "
    "visible y luego el capítulo visible; reinspeccioná entre ambos. Para "
    "reiniciar o posicionar contenido, inspeccioná controles de reproductor: "
    "un slider puede exponer range con valor, mínimo y máximo. Usá set_range "
    "solo dentro de esos límites y solo traducí minutos o segundos cuando la "
    "unidad publicada por el control sea inequívoca; no adivines una duración. "
    "Para siguiente/anterior usá un botón accesible exacto si existe; como "
    "alternativa global podés enviar control_media next/previous, aclarando "
    "que el sistema aceptó la señal pero sin afirmar el estado final. "
    "Cuando el usuario nombre un autor o canal y además un video o resultado "
    "concreto, el video es el destino y el autor es una restricción: buscá "
    "ambos juntos, no trates al canal ni a un botón Search como resultado. "
    "Si varios resultados cumplen el título solicitado, no elijas por orden: "
    "mostrá las alternativas y la información pública accesible de autor o "
    "fecha para que el usuario pueda precisarlo. Para 'cambia de video a X', "
    "trabajá sobre la pestaña actual: no abras otra pestaña ni uses browser_open. "
    "Para buscar archivos o carpetas locales, no uses browser_search: usá "
    "find_files o find_folders. Antes de abrir un resultado ambiguo, mostrale "
    "las alternativas al usuario; open_file y open_folder ya rechazan "
    "coincidencias dudosas. Para carpetas personales conocidas usá "
    "open_known_folder. Una orden de abrir un archivo, carpeta o descarga "
    "local nunca se resuelve con browser_open ni browser_search. "
    "Leé el portapapeles o capturá la pantalla solo cuando el usuario lo "
    "solicite explícitamente. Copiá texto al portapapeles o mostrale una "
    "notificación solo cuando el usuario haya indicado claramente esa acción. "
    "Para poner, reproducir, escuchar o ver algo nuevo (una canción, música, "
    "un video, una película o una serie) usá play_web_media aunque el pedido "
    "sea vago; si no dijo el sitio, YouTube. "
    "Para pausar, reanudar, pasar, retroceder o detener contenido multimedia "
    "usá control_media: funciona sobre la sesión multimedia activa del sistema, "
    "sin depender de Netflix, YouTube o Twitch. play_pause es una señal de "
    "alternar, así que no afirmes que el reproductor quedó pausado o reanudado "
    "si no existe evidencia adicional. Para subir o bajar un poco el volumen "
    "usá control_media con volume_up o volume_down; para un porcentaje exacto "
    "usá set_system_volume. "
    "Para revisar o averiguar algo del sistema (si un puerto está ocupado, "
    "qué procesos corren, espacio en disco, memoria, red, servicios, "
    "registros, paquetes instalados o el contenido de un archivo) usá "
    "run_command con un comando de solo lectura de este sistema operativo "
    "y respondé con lo que devolvió; no digas que no podés ni le pidas al "
    "usuario que lo haga él. run_command no modifica nada: si el usuario "
    "pide cambiar, instalar o borrar algo por terminal, decile que por "
    "ahora solo podés consultar. "
    "Tenés memoria permanente: si el usuario te pide que recuerdes algo o "
    "te cuenta un dato estable sobre él, guardalo con remember. Lo que "
    "hablaron otros días no está a la vista pero quedó anotado: si pregunta "
    "por algo que te dijo, te pidió o hicieron antes (la otra vez, ayer, "
    "el otro día) y no está en esta conversación, usá siempre search_memory "
    "con palabras clave antes de contestar; nunca digas que no tenés "
    "registro sin haber buscado."
)


# =========================================================
# MENSAJE DEL USUARIO
#
# reply_hint: indicación sobre la respuesta final (por
# ejemplo, para leerla en voz alta). Va al final del mensaje
# del usuario y no en el prompt de sistema, para no romper la
# caché del LLM. La memoria de la conversación guarda la
# orden armada igual, por la misma razón.
# =========================================================

def user_message_content(
    user_command,
    reply_hint=None
):

    return (
        f"{user_command}\n\n({reply_hint})"
        if reply_hint
        else user_command
    )


# =========================================================
# MOCHI AGENT LOOP
# =========================================================

class MochiAgentLoop:

    # These words do not distinguish one named result from another.  Keeping
    # them out of the fuzzy comparison lets a request such as "Fortnite lo
    # hizo de nuevo" tolerate a minor conjugation difference without treating
    # a generic Fortnite result as the requested item.
    _RESULT_STOP_WORDS = frozenset({
        "a", "al", "con", "de", "del", "el", "en", "es", "esta", "este",
        "la", "las", "lo", "los", "mi", "por", "que", "un", "una", "y",
        "an", "of", "the",
    })
    _VERIFIED_PAGE_ACTIONS = frozenset({
        "select_result", "select_channel", "select_content_tab", "select_sort",
        "select_visible_video", "select_season", "select_episode",
        "play_media", "pause_media", "seek_media", "adjust_media_position",
        "next_media", "previous_media",
    })

    def __init__(
        self,
        router=None,
        max_steps=6,
        pending_choice_cache=None,
    ):

        self.router = (
            router
            or brain_router
        )

        self.max_steps = (
            max_steps
        )
        self.pending_choice_cache = pending_choice_cache or PendingPageChoiceCache()
        # Semantic conversation scope only.  It helps interpret pronouns in
        # the long-lived command service, but every action still has to prove
        # the active page through a fresh UIA snapshot.
        self._context_provider = None


    @staticmethod
    def _normalize_for_signature(value):
        """Normalización intencionalmente pequeña para detectar reintentos."""
        text = unicodedata.normalize("NFD", str(value or "").strip().casefold())
        text = "".join(char for char in text if unicodedata.category(char) != "Mn")
        return re.sub(r"\s+", " ", text)

    def _action_signature(self, tool_name, arguments, evidence=None):
        def normalize(value):
            if isinstance(value, dict):
                return {str(key): normalize(item) for key, item in sorted(value.items())}
            if isinstance(value, list):
                return [normalize(item) for item in value]
            if isinstance(value, str):
                return self._normalize_for_signature(value)
            return value
        return json.dumps({"tool": self._normalize_for_signature(tool_name), "arguments": normalize(arguments or {}), "evidence": normalize(evidence)}, ensure_ascii=False, sort_keys=True, default=str)

    # Verbos de "elegí el segundo", también con voseo ("abrí",
    # "poné", "entrá al primero"), y lo que se elige.
    _CHOICE_VERBS = (
        r"reproduce|reproducir|reproduci|pon|poner|pone|abre|abrir|abri|"
        r"elige|elije|elegi|selecciona|seleccionar|entra|entrar|"
        r"anda\s+a|ir\s+a|mira|toca|clickea|cliquea|"
        r"(?:haz|hace|dale|da)\s+(?:click|clic)(?:\s+en)?|dale"
    )
    _CHOICE_NOUNS = (
        r"opcion(?:\s+numero)?|video|resultado|link|enlace|pagina|sitio|"
        r"cancion|tema|numero"
    )

    @classmethod
    def _requested_option_index(cls, command):
        """Return a one-based ordinal from a concise follow-up instruction."""
        text = cls._normalize_for_signature(command)
        words = {
            "primer": 1, "primero": 1, "primera": 1,
            "segundo": 2, "segunda": 2,
            "tercer": 3, "tercero": 3, "tercera": 3,
            "cuarto": 4, "cuarta": 4,
            "quinto": 5, "quinta": 5,
        }
        follow_up = re.fullmatch(
            r"(?:(?:por\s+favor|ahora|y)\s+)?"
            r"(?:(?:" + cls._CHOICE_VERBS + r")\s+)?"
            r"(?:(?:el|la|al|a\s+la)\s+)?(?:(?:" + cls._CHOICE_NOUNS + r")\s+)?"
            r"(?P<choice>primer|primero|primera|segundo|segunda|tercer|tercero|tercera|"
            r"cuarto|cuarta|quinto|quinta|[1-5])"
            r"(?:\s+(?:" + cls._CHOICE_NOUNS + r"))?"
            r"(?:\s+(?:de\s+(?:la\s+|esa\s+)?(?:lista|busqueda|pagina|google|youtube)))?"
            r"(?:\s+por\s+favor)?",
            text,
        )
        if not follow_up:
            # A longer continuation is safe only when it explicitly refers
            # to Mochi's prior list.  This avoids stealing ordinals from
            # unrelated clauses such as "primer episodio de la temporada 2".
            if not re.search(
                r"\b(?:esa|aquella|la)\s+lista\b|"
                r"\b(?:esas|las)\s+opciones\b|\bopciones\s+anteriores\b|"
                r"\bresultados\s+anteriores\b",
                text,
            ):
                return None
            referred = re.search(
                r"\b(?:" + cls._CHOICE_VERBS + r")\w*\s+"
                r"(?:(?:el|la)\s+)?(?P<choice>primer|primero|primera|segundo|segunda|"
                r"tercer|tercero|tercera|cuarto|cuarta|quinto|quinta|[1-5])\b",
                text,
            )
            if not referred:
                return None
            choice = referred.group("choice")
            return int(choice) if choice.isdigit() else words.get(choice)
        choice = follow_up.group("choice")
        return int(choice) if choice.isdigit() else words.get(choice)

    @staticmethod
    def _active_page_is_site(registry, site):
        """Si la pestaña activa es del sitio (YouTube, Netflix)."""
        site = str(site or "").strip().casefold()
        if not site or not registry.has_tool("web_inspect"):
            return True
        observation = registry.execute("web_inspect", {})
        page = observation.get("page") or {} if observation.get("ok") else {}
        if not page:
            return True
        return site in f"{page.get('url') or ''} {page.get('title') or ''}".casefold()

    @staticmethod
    def _pending_choice_on_active_page(record, registry):
        """Una lista de opciones vale solo si sigue abierta su página."""
        expected = (record.get("page") or {}).get("url")
        if not expected or not registry.has_tool("web_inspect"):
            return True
        observation = registry.execute("web_inspect", {})
        current = (observation.get("page") or {}).get("url") if observation.get("ok") else None
        return not current or current == expected

    @classmethod
    def _choice_requests_playback(cls, command):
        return bool(re.search(
            r"\b(?:reproduce|reproducir|pon|poner)\b",
            cls._normalize_for_signature(command),
        ))

    def _goal_from_pending_choice(self, record, index, command, continuation=None):
        """Build a fresh, snapshot-free plan for one prior numbered option."""
        options = list(record.get("options") or [])
        state = GoalState(
            goal="continue_pending_page_choice",
            target=str(record.get("target") or "") or None,
            page_query=str(record.get("query") or "") or None,
            page_result_options=options,
            page_choice_context=dict(record.get("page") or {}),
            pending_choice_operation=str(
                record.get("selection_operation") or "select_result"
            ),
        )
        if not 1 <= index <= len(options):
            state.steps.append(
                GoalStep(
                    "select_result",
                    "web_page",
                    "select_result",
                    status="blocked",
                    reason="choice_index_out_of_range",
                )
            )
            return state
        selected = str(options[index - 1].get("name") or "").strip()
        state.page_result = selected
        selection_operation = state.pending_choice_operation
        state.steps.append(
            GoalStep(
                selection_operation,
                "web_page",
                selection_operation,
                target=state.target,
                value=selected,
            )
        )
        appended_operations = [
            GoalStep(
                f"cached_{position}",
                str(item.get("scope") or "web_page"),
                str(item.get("operation") or ""),
                target=state.target,
                value=item.get("value"),
            )
            for position, item in enumerate(record.get("continuation") or [], 1)
            if isinstance(item, dict) and item.get("operation")
        ]
        if continuation:
            new_operations = [
                step for step in continuation.steps
                if step.operation in {
                    "play_media", "pause_media", "seek_media",
                    "adjust_media_position", "next_media", "previous_media",
                    "adjust_volume",
                }
            ]
            # A new clause may extend the pending task, but repeating the
            # exact original playback tail should not double-toggle it.
            existing = {
                (step.operation, str(step.value or ""))
                for step in appended_operations
            }
            appended_operations.extend(
                step for step in new_operations
                if (step.operation, str(step.value or "")) not in existing
            )
        if not appended_operations and self._choice_requests_playback(command):
            state.steps.append(
                GoalStep(
                    "playback",
                    "web_page",
                    "play_media",
                    target=state.target,
                    value="Reproducir",
                    depends_on=selection_operation,
                )
            )
        else:
            dependency = selection_operation
            counts = {}
            for source in appended_operations:
                counts[source.operation] = counts.get(source.operation, 0) + 1
                step_id = (
                    source.operation
                    if counts[source.operation] == 1
                    else f"{source.operation}_{counts[source.operation]}"
                )
                state.steps.append(
                    GoalStep(
                        step_id,
                        source.scope,
                        source.operation,
                        target=state.target,
                        value=source.value,
                        depends_on=dependency,
                    )
                )
                dependency = step_id
        return state

    def _matches_pending_site_profile(self, element_name, site_profile):
        """Whether an inspected control is the profile requested by the user.

        This is intentionally a small equality/containment check over the
        semantic accessible name, rather than site-specific knowledge.  It
        prevents a generic word such as "profile" from authorizing a click on
        a management/settings control when the pending goal is a named profile.
        """
        element = self._normalize_for_signature(element_name)
        expected = self._normalize_for_signature(site_profile)
        return bool(element and expected and (element == expected or element.startswith(expected + " ") or element.endswith(" " + expected)))

    def _is_site_profile_choice(self, element, site_profile, allow_menu_button=False):
        """Accept controls that represent a choice, not an account indicator.

        A button with the current user's name is often an account menu opener,
        while profile gates expose links, list/menu items, radio options or
        tabs.  Until a provider exposes a reliable selected-state pattern, the
        conservative rule avoids clicking an already-active account badge.
        """
        choice_roles = {"link", "listitem", "menuitem", "radio", "tab"}
        if allow_menu_button:
            choice_roles.add("button")
        return self._matches_pending_site_profile(element.get("name"), site_profile) and element.get("role") in choice_roles

    def _declared_profile_menu_opener(self, observation, goal_state):
        """Find a declared or uniquely evidenced current-profile button.

        This protects a deterministic switch from treating unrelated buttons
        such as Play, Notifications or account administration as a profile
        menu.  When the user did not say "from A", two independent occurrences
        of one short button name in the page's semantic context can establish
        A as the active profile (for example a standalone header name plus
        "Continue watching for A").
        """
        source = goal_state.site_profile_source
        if source:
            return next(
                (
                    item for item in observation.get("elements", [])
                    if item.get("role") == "button"
                    and self._matches_pending_site_profile(item.get("name"), source)
                ),
                None,
            )

        requested = self._normalize_for_signature(goal_state.site_profile)
        contextual_text = [
            self._normalize_for_signature(value)
            for value in observation.get("context", [])
            if value
        ]
        candidates = []
        safe_named_buttons = []
        generic_button_words = {
            "buscar", "search", "reproducir", "play", "pausa", "pause",
            "notificaciones", "notifications", "cuenta", "account",
            "perfil", "profile", "menu", "cerrar", "close", "inicio", "home",
        }
        for item in observation.get("elements", []):
            if item.get("role") != "button":
                continue
            name = self._normalize_for_signature(item.get("name"))
            if (
                not name
                or name == requested
                or len(name.split()) > 3
                or not any(character.isalpha() for character in name)
            ):
                continue
            if not any(word in name.split() for word in generic_button_words):
                safe_named_buttons.append(item)
            evidence = sum(
                1
                for text in contextual_text
                if text == name or re.search(r"(?:^|\s)" + re.escape(name) + r"(?:\s|$)", text)
            )
            if evidence >= 2:
                candidates.append(item)
        if len(candidates) == 1:
            return candidates[0]
        # Some profile headers expose only one short user-named button and no
        # duplicate contextual text.  It is safe only when it is the sole
        # non-generic candidate; a page with multiple possible account-like
        # buttons remains ambiguous and no menu is opened.
        return safe_named_buttons[0] if len(safe_named_buttons) == 1 else None

    def _page_matches_goal_target(self, page, target):
        """Avoid acting on a same-named control in an unrelated Chrome tab."""
        title = self._normalize_for_signature((page or {}).get("title"))
        expected = self._normalize_for_signature(target)
        return bool(title and expected and expected in title)

    def _is_planned_browser_target(self, requested_target, expected_target, require_site_root=False):
        """Accept the planned service name or its own domain, never a search.

        A substring check would let ``google.com/search?q=youtube`` through
        merely because it contains the word "YouTube".  Browser-open plans
        that continue with an in-page search must stay on the requested
        service, whether the router supplies its friendly name or URL.
        """
        requested = self._normalize_for_signature(requested_target)
        expected = self._normalize_for_signature(expected_target)
        if not requested or not expected:
            return False
        if requested == expected:
            return True
        location = re.sub(r"^[a-z]+://", "", requested)
        host_and_path = location.split("/", 1)
        host = host_and_path[0].split("?", 1)[0].split(":", 1)[0]
        labels = [label for label in host.split(".") if label and label != "www"]
        if not (labels and labels[0] == expected):
            return False
        if not require_site_root:
            return True
        # A structured plan will submit through the site's own accessible
        # search field.  Letting an LLM embed the full spoken request in a
        # service URL creates duplicate work and can bypass the guarded
        # result-selection logic.
        if "?" in location or "#" in location:
            return False
        path_and_query = location[len(host_and_path[0]):]
        return path_and_query in {"", "/"}

    def _observation_matches_goal_page(self, observation, goal_state):
        """Validate a named destination, or the user's current tab.

        ``cambia de video`` intentionally has no target: its contract is to
        remain in the active page. It still requires a successful UIA
        observation, but must not invent a destination title requirement.
        """
        return bool(
            observation
            and observation.get("ok")
            and (
                not goal_state.target
                or self._page_matches_goal_target(observation.get("page"), goal_state.target)
            )
        )

    def _search_textbox(self, observation):
        """Return one unambiguous semantic site-search textbox, if present."""
        fields = [
            item for item in observation.get("elements", [])
            if item.get("role") in {"textbox", "combobox"}
        ]
        named = [
            item for item in fields
            if any(word in self._normalize_for_signature(item.get("name")) for word in {"buscar", "search"})
        ]
        candidates = named or fields
        return candidates[0] if len(candidates) == 1 else None

    def _search_opener(self, observation):
        """Return a plainly named search control, never a guessed icon."""
        for item in observation.get("elements", []):
            if item.get("role") not in {"button", "link"}:
                continue
            name = self._normalize_for_signature(item.get("name"))
            if name in {"buscar", "search"}:
                return item
        return None

    def _meaningful_result_tokens(self, value):
        return [
            token
            for token in re.findall(r"\w+", self._normalize_for_signature(value))
            if len(token) > 1 and token not in self._RESULT_STOP_WORDS
        ]

    def _page_result_match_score(self, element_name, requested_name):
        """Return a conservative score for a user-named page result.

        Exact title containment is preferred.  A small token-level fuzzy
        fallback is only for natural phrasing and needs every meaningful word
        to be represented in the accessible name, with at most one minor
        spelling/conjugation variation.  It is deliberately not a general
        search ranking heuristic: ambiguous candidates remain unclickable.
        """
        candidate = self._normalize_for_signature(element_name)
        requested = self._normalize_for_signature(requested_name)
        if not candidate or not requested:
            return None
        if candidate == requested:
            return 1.0
        requested_tokens = self._meaningful_result_tokens(requested)
        candidate_tokens = self._meaningful_result_tokens(candidate)
        contained = re.search(
            r"(?<!\w)" + re.escape(requested) + r"(?!\w)",
            candidate,
        )
        if contained:
            suffix = candidate[contained.end():].lstrip()
            # A one-word semantic core (for example "The Office" after
            # dropping the article) must not turn "The Office Movers" into
            # the requested series. Parenthesized/colon editions and normal
            # multiword titles remain valid public title variants.
            if len(requested_tokens) > 1 or not suffix or suffix[0] in "(:[{-—–·":
                return 0.99
        if len(requested_tokens) < 2 or not candidate_tokens:
            return None

        # Spoken commands commonly use public acronyms while accessibility
        # labels publish the expanded title ("GTA" / "Grand Theft Auto").
        # Add only exact initials of adjacent words; this is deterministic
        # title evidence, not a broad fuzzy synonym map.
        candidate_forms = list(candidate_tokens)
        for width in range(2, min(5, len(candidate_tokens)) + 1):
            candidate_forms.extend(
                "".join(token[0] for token in candidate_tokens[start:start + width] if token)
                for start in range(len(candidate_tokens) - width + 1)
            )

        best_scores = []
        exact_matches = 0
        for requested_token in requested_tokens:
            score = max(
                SequenceMatcher(None, requested_token, candidate_token).ratio()
                for candidate_token in candidate_forms
            )
            best_scores.append(score)
            if score == 1.0:
                exact_matches += 1

        # At least all but one terms must be exact and the remaining term has
        # to still be visibly similar.  This accepts "hizo" / "hice", but
        # rejects an unrelated video that merely shares "Fortnite".
        if (
            exact_matches < len(requested_tokens) - 1
            or min(best_scores) < 0.70
            or sum(best_scores) / len(best_scores) < 0.88
        ):
            return None
        return sum(best_scores) / len(best_scores)

    def _page_result_candidates(self, observation, requested_name):
        selectable_roles = {"link", "button", "listitem", "menuitem", "radio", "tab", "combobox"}
        candidates = []
        for item in observation.get("elements", []):
            if item.get("role") not in selectable_roles:
                continue
            score = self._page_result_match_score(item.get("name"), requested_name)
            if score is not None:
                candidates.append((score, item))
        return sorted(candidates, key=lambda candidate: candidate[0], reverse=True)

    def _result_context_tokens(self, goal_state):
        """Return query words that constrain, but are not, the title.

        ``page_result`` is deliberately a concise title so that a site can
        expose a longer accessible title.  The rest of ``page_query`` often
        carries the requested author.  Treat those words as a constraint when
        choosing media instead of losing them after the initial search.
        """
        query_tokens = self._meaningful_result_tokens(goal_state.page_query)
        title_tokens = set(self._meaningful_result_tokens(goal_state.page_result))
        return [token for token in query_tokens if token not in title_tokens]

    def _candidate_matches_result_context(self, item, goal_state):
        """Check the author/context words against one public UIA label."""
        required = self._result_context_tokens(goal_state)
        if not required:
            return True
        candidate_tokens = self._meaningful_result_tokens(item.get("name"))
        if not candidate_tokens:
            return False
        # A spoken query can contain a small spelling variation ("yanke" /
        # "yankee").  This is only contextual filtering after the title has
        # already passed the strict result matcher above; it is never general
        # fuzzy search or an invitation to select a different title.
        return all(
            max(
                SequenceMatcher(None, requested, actual).ratio()
                for actual in candidate_tokens
            ) >= 0.78
            for requested in required
        )

    def _media_result_card_key(self, item):
        """Return evidence that a UIA control represents one media card.

        A search-page suggestion can have exactly the requested text, but it
        is not a result that can start playback.  Duration text is portable
        accessibility evidence for media.  When a site publishes semantic
        list-card ancestry instead, that card boundary is equally useful.
        """
        duration_key = self._visible_duration_card_key(item.get("name"))
        if duration_key:
            return f"duration:{duration_key}"
        container = self._normalize_for_signature(item.get("container"))
        return f"container:{container}" if container else None

    def _goal_page_result_candidates(self, observation, goal_state):
        """Find candidates compatible with the *kind* of pending goal.

        Normal page tasks retain their existing title-only selection.  A
        playback request is stricter: if the snapshot contains a real media
        card, only that card can satisfy the result step.  This prevents a
        same-named autosuggestion, channel or navigation link from winning
        merely because it happens to be an exact string match.
        """
        matches = self._page_result_candidates(observation, goal_state.page_result)
        play_step = goal_state.step("play_media")
        if not play_step:
            return matches

        media_matches = [
            (score, item)
            for score, item in matches
            if self._media_result_card_key(item) is not None
        ]
        if media_matches:
            # Author/context terms are a restriction, not a weak preference.
            # If no exposed media result satisfies them we stop and report the
            # ambiguity instead of falling back to a same-titled video.
            media_matches = [
                (score, item)
                for score, item in media_matches
                if self._candidate_matches_result_context(item, goal_state)
            ]
            deduplicated = []
            seen_cards = set()
            for score, item in media_matches:
                card_key = self._media_result_card_key(item)
                if card_key in seen_cards:
                    continue
                seen_cards.add(card_key)
                deduplicated.append((score, item))
            return deduplicated

        # Some providers only expose a title link, without duration or a
        # list-card ancestor.  Preserve that capability, but still honour an
        # author/context supplied by the user when it is observable.
        contextual = [
            (score, item)
            for score, item in matches
            if self._candidate_matches_result_context(item, goal_state)
        ]
        # If this provider did not expose the author at all, title matching
        # remains usable only when it is independently unambiguous.  The
        # caller's normal ambiguity guard still prevents choosing between two
        # same-titled controls by visual order.
        return contextual or matches

    def _goal_named_page_result(self, observation, goal_state):
        """Return one unambiguous result suitable for the pending goal."""
        matches = self._goal_page_result_candidates(observation, goal_state)
        if not matches:
            return None
        # Do not resolve two similarly credible titles by order in the UI.
        # Their visual order can change between providers or refreshes.
        if len(matches) > 1 and matches[0][0] - matches[1][0] < 0.08:
            return None
        return matches[0][1]

    def _named_page_result(self, observation, requested_name):
        """Return one accessible result that clearly matches the user's name."""

        matches = self._page_result_candidates(observation, requested_name)
        if not matches:
            return None
        # Do not resolve two similarly credible titles by order in the UI.
        # Their visual order can change between providers or refreshes.
        if len(matches) > 1 and matches[0][0] - matches[1][0] < 0.08:
            return None
        return matches[0][1]

    def _named_channel_result(self, observation, requested_name):
        """Return one control that explicitly denotes channel navigation.

        Search pages can expose chapters, video titles and buttons containing
        the creator's name.  Labels such as "Ir al canal X" or an exact
        channel name carry stronger role evidence than mere text containment,
        so a chapter named after the creator can never tie with the channel.
        """
        requested = self._normalize_for_signature(requested_name)
        if not requested:
            return None
        rich, navigation, exact = [], [], []
        for item in observation.get("elements", []):
            if item.get("role") not in {"link", "button", "listitem"}:
                continue
            name = self._normalize_for_signature(item.get("name"))
            if name == requested:
                exact.append(item)
                continue
            prefixes = ("ir al canal ", "go to channel ", "visitar canal ", "visit channel ")
            if any(name == prefix + requested for prefix in prefixes):
                navigation.append(item)
                continue
            # YouTube and similar search pages may expose one rich channel
            # card as "Name Verified @handle subscribers description" rather
            # than a separate "Go to channel" link.  Require both the leading
            # public name and matching handle so a description that merely
            # mentions the creator cannot qualify.
            if (
                name.startswith(requested + " ")
                and re.search(
                    rf"@{re.escape(requested.replace(' ', ''))}(?![\w])",
                    name,
                )
            ):
                rich.append(item)
        # A rich card with an exact handle identifies the public channel more
        # strongly than duplicate navigation/name links for that same card.
        # Prefix handles such as @VeritasiumES never satisfy @Veritasium.
        for candidates in (rich, navigation, exact):
            if len(candidates) == 1:
                return candidates[0]
            if len(candidates) > 1:
                return None
        return None

    def _exact_named_control(self, observation, name):
        """El único control con exactamente ese nombre, o None."""
        wanted = self._normalize_for_signature(name)
        controls = [
            item for item in (observation or {}).get("elements", [])
            if item.get("role") in {"button", "tab", "link", "radio", "option", "listitem"}
            and self._normalize_for_signature(item.get("name")) == wanted
        ]
        return controls[0] if len(controls) == 1 else None

    def _content_tab(self, observation, name):
        """La única pestaña (role tab) con ese nombre, o None."""
        wanted = self._normalize_for_signature(name)
        tabs = [
            item for item in (observation or {}).get("elements", [])
            if item.get("role") == "tab"
            and self._normalize_for_signature(item.get("name")) == wanted
        ]
        return tabs[0] if len(tabs) == 1 else None

    def _channel_choice_candidates(self, observation, requested_name):
        """Return (option, control) per public channel identity.

        YouTube publica la misma tarjeta de canal dos veces ("Go to channel
        Rubius Z" y "Rubius Z Verified @elrubius ..."): se agrupan por el
        nombre público para no preguntar entre dos entradas del mismo canal.
        """

        requested = self._normalize_for_signature(requested_name)
        if not requested:
            return []
        prefixes = ("ir al canal ", "go to channel ", "visitar canal ", "visit channel ")
        candidates, identities = [], {}
        compact_requested = requested.replace(" ", "")
        for item in observation.get("elements", []):
            if item.get("role") not in {"link", "button", "listitem"}:
                continue
            raw_name = str(item.get("name") or "").strip()
            name = self._normalize_for_signature(raw_name)
            if not raw_name or self._media_result_card_key(item) is not None:
                continue
            prefix = next(
                (
                    prefix for prefix in prefixes
                    if name.startswith(prefix) and requested in name[len(prefix):]
                ),
                None,
            )
            rich_identity = (
                name.startswith(requested + " ")
                and "@" in raw_name
                and (
                    compact_requested in name.replace(" ", "")
                    or compact_requested in raw_name.casefold().replace(" ", "")
                )
            )
            if not (prefix or rich_identity or name == requested):
                continue
            if prefix:
                identity = name[len(prefix):].strip()
            elif rich_identity:
                identity = re.sub(
                    r"\s+(?:verified|verificad[oa])\s*$", "", name.split("@", 1)[0].strip()
                )
            else:
                identity = name
            if identity in identities:
                # Para hacer clic conviene el enlace "Ir al canal".
                if prefix:
                    index = identities[identity]
                    candidates[index] = (candidates[index][0], item)
                continue
            identities[identity] = len(candidates)
            candidates.append(({"name": raw_name}, item))
            if len(candidates) == 5:
                break
        return candidates

    def _channel_choice_options(self, observation, requested_name):
        """Return public channel identities without confusing video cards."""
        return [
            option
            for option, _item in self._channel_choice_candidates(observation, requested_name)
        ]

    def _named_reply_option_index(self, command, options):
        """Índice (1-based) de la opción que nombra una respuesta corta.

        "elrubius z" ante una lista de canales: cada palabra de la respuesta
        tiene que aparecer en una sola de las opciones.
        """
        text = re.sub(r"[^\w\s]", " ", self._normalize_for_signature(command))
        text = re.sub(
            r"^(?:(?:" + self._CHOICE_VERBS + r")\s+)?"
            r"(?:(?:el|la|al|a\s+la)\s+)?(?:(?:canal|opcion|video)\s+)?(?:(?:de|del)\s+)?",
            "",
            text.strip(),
        )
        words = [word for word in text.split() if word not in {"el", "la", "de", "del"}]
        if not words or len(words) > 5:
            return None

        def word_score(word, option_words):
            # 2 = la palabra está entera; 1 = una contiene a la otra.
            if word in option_words:
                return 2
            return int(any(
                min(len(word), len(other)) >= 4 and (word in other or other in word)
                for other in option_words
            ))

        scores = []
        for index, option in enumerate(options, start=1):
            option_text = re.sub(
                r"[^\w\s]", " ", self._normalize_for_signature(option.get("name"))
            )
            option_words = option_text.split()
            word_scores = [word_score(word, option_words) for word in words]
            if all(word_scores):
                scores.append((sum(word_scores), index))
        scores.sort(reverse=True)
        if not scores or (len(scores) > 1 and scores[0][0] == scores[1][0]):
            return None
        return scores[0][1]

    def _observation_is_requested_channel(self, observation, requested_name):
        """Recognize a loaded channel page from title plus channel navigation."""
        if not observation.get("ok") or not self._has_page_content(observation):
            return False
        requested = self._normalize_for_signature(requested_name)
        title = self._normalize_for_signature((observation.get("page") or {}).get("title"))
        if not requested or requested not in title:
            return False
        names = {
            self._normalize_for_signature(item.get("name"))
            for item in observation.get("elements", [])
            if item.get("name")
        }
        # YouTube search-result filters can coexist with sidebar links named
        # Playlists/Videos and otherwise imitate channel navigation.  A
        # selected result filter is explicit counter-evidence that this is a
        # channel page.
        if any(
            item.get("selected") is True
            and self._normalize_for_signature(item.get("name")) in {
                "todo", "all", "sin mirar", "unwatched", "vistos", "watched",
                "subidos recientemente", "recently uploaded",
            }
            for item in observation.get("elements", [])
        ):
            return False
        has_videos = any(name in {"videos", "video"} for name in names)
        has_channel_navigation = any(
            name in {"listas", "playlists", "publicaciones", "community"}
            for name in names
        )
        return has_videos and has_channel_navigation

    def _remember_ambiguous_page_results(self, goal_state, observation):
        """Keep a small, serializable choice list for the final response."""
        matches = self._goal_page_result_candidates(observation, goal_state)
        if goal_state.step("play_media"):
            # A media request may briefly expose only autocomplete entries.
            # They can match the requested words exactly but cannot be played;
            # do not turn them into conversational options.  Returning False
            # lets the bounded same-page wait continue until actual cards are
            # published, or the normal plan reports their absence honestly.
            raw_matches = self._page_result_candidates(observation, goal_state.page_result)
            if raw_matches and len(matches) < 2 and not any(
                self._media_result_card_key(item) is not None
                for _score, item in raw_matches
            ):
                return False
        if len(matches) < 2:
            return False
        options = []
        seen = set()
        for _score, item in matches:
            name = str(item.get("name") or "").strip()
            key = self._normalize_for_signature(name)
            if not name or key in seen:
                continue
            seen.add(key)
            # UIA providers often include public channel/date/duration text in
            # the accessible name.  Preserve exactly that evidence rather
            # than inventing an author or a publication date.
            options.append({"name": name})
        if len(options) < 2:
            return False
        goal_state.record_page_result_options(options)
        self.pending_choice_cache.remember(
            page=observation.get("page"),
            target=goal_state.target,
            query=goal_state.page_query,
            options=options,
            selection_operation="select_result",
            continuation=self._choice_continuation(goal_state, "select_result"),
        )
        return True

    @staticmethod
    def _choice_continuation(goal_state, after_operation):
        """Serialize public pending intent after an ambiguous selection."""
        found = False
        continuation = []
        for step in goal_state.steps:
            if not found:
                if step.operation == after_operation and step.status == "pending":
                    found = True
                continue
            if step.status != "pending":
                continue
            continuation.append({
                "operation": step.operation,
                "scope": step.scope,
                "value": step.value,
            })
        return continuation

    def _fast_pending_choice_selection(self, registry, goal_state):
        """Re-find a persisted numbered option before activating it.

        The cache contains labels only.  A new UIA snapshot must expose the
        whole prior choice set and one exact selected label, preventing a
        follow-up such as "the third" from acting on a different page.
        """
        selection_operation = goal_state.pending_choice_operation or "select_result"
        step = goal_state.step(selection_operation)
        if (
            not step
            or step.status != "pending"
            or not step.value
            or not registry.has_tool("web_inspect")
            or not registry.has_tool("web_act")
        ):
            return []
        observation = registry.execute("web_inspect", {})
        records = [("web_inspect", {}, observation, None)]
        if not self._observation_matches_goal_page(observation, goal_state):
            step.status = "blocked"
            step.reason = "choice_context_page_changed"
            return records
        visible_names = {
            self._normalize_for_signature(item.get("name"))
            for item in observation.get("elements", [])
            if item.get("name")
        }
        expected_names = {
            self._normalize_for_signature(option.get("name"))
            for option in goal_state.page_result_options
            if option.get("name")
        }
        current_title = self._normalize_for_signature(
            (observation.get("page") or {}).get("title")
        )
        cached_title = self._normalize_for_signature(
            (goal_state.page_choice_context or {}).get("title")
        )
        query_terms = self._meaningful_result_tokens(goal_state.page_query)
        same_query_page = bool(
            current_title
            and (
                (cached_title and current_title == cached_title)
                or (
                    query_terms
                    and all(term in current_title for term in query_terms)
                )
                or bool(expected_names & visible_names)
            )
        )
        if not expected_names or not same_query_page:
            step.status = "blocked"
            step.reason = "choice_context_page_changed"
            return records
        selected_observation = observation
        matches = [
            item for item in observation.get("elements", [])
            if item.get("role") in {"link", "button", "listitem", "menuitem", "radio", "tab"}
            and self._normalize_for_signature(item.get("name")) == self._normalize_for_signature(step.value)
        ]
        if len(matches) != 1:
            targeted_arguments = {"query": step.value}
            selected_observation = registry.execute("web_inspect", targeted_arguments)
            records.append(("web_inspect", targeted_arguments, selected_observation, None))
            if not self._observation_matches_goal_page(selected_observation, goal_state):
                step.status = "blocked"
                step.reason = "choice_context_page_changed"
                return records
            matches = [
                item for item in selected_observation.get("elements", [])
                if item.get("role") in {"link", "button", "listitem", "menuitem", "radio", "tab"}
                and self._normalize_for_signature(item.get("name")) == self._normalize_for_signature(step.value)
            ]
        if len(matches) != 1:
            step.status = "blocked"
            step.reason = "choice_context_not_visible" if not matches else "selected_choice_not_unique"
            return records
        selected = matches[0]
        selected = {**selected, "_snapshot_id": selected_observation.get("snapshot_id")}
        if selected.get("offscreen"):
            selected = self._scroll_result_into_view(registry, goal_state, selected, records)
            if selected is None:
                return records
        arguments = {
            "element_id": selected["id"],
            "action": "click",
            "snapshot_id": selected.get("_snapshot_id") or selected_observation.get("snapshot_id"),
        }
        result = registry.execute("web_act", arguments)
        records.append(("web_act", arguments, result, selection_operation))
        if result.get("ok"):
            time.sleep(0.2)
            records.append(("web_inspect", {}, registry.execute("web_inspect", {}), None))
        return records

    _NON_CONTENT_LINKS = frozenset({
        "inicio", "home", "shorts", "suscripciones", "subscriptions", "tu", "you",
        "youtube", "netflix", "series", "peliculas", "movies", "juegos", "games",
        "mi lista", "my list", "explora por idiomas", "notificaciones", "notifications",
    })

    @staticmethod
    def _visible_duration_card_key(name):
        """Return a stable title key only when UIA publishes a duration."""
        text = str(name or "")
        # Player elapsed/total readouts contain durations but are controls,
        # not media cards.  Reject both clock and fully-spoken variants before
        # using duration as evidence for a selectable result.
        if re.search(
            r"^\s*-?(?:\d{1,2}:)?\d{1,2}:\d{2}\s*(?:/|de|of)\s*"
            r"(?:\d{1,2}:)?\d{1,2}:\d{2}\s*$|"
            r"^\s*-?\d+\s+(?:minutos?|minutes?)\s+\d+\s+"
            r"(?:segundos?|seconds?)\s+(?:de|of)\s+\d+\s+(?:minutos?|minutes?)",
            text,
            re.I,
        ):
            return None
        has_duration = bool(
            re.search(
                r"\b\d{1,2}:\d{2}(?::\d{2})?\b|"
                r"\b\d+\s+(?:segundos?|seconds?|minutos?|minutes?|horas?|hours?)\b",
                text,
                re.I,
            )
        )
        if not has_duration:
            return None
        without_duration = re.sub(
            r"\b\d{1,2}:\d{2}(?::\d{2})?\b|"
            r"\b\d+\s+(?:segundos?|seconds?|minutos?|minutes?|horas?|hours?)"
            r"(?:\s+(?:y|and)\s+\d+\s+(?:segundos?|seconds?))?\b",
            " ",
            text,
            flags=re.I,
        )
        normalized = unicodedata.normalize("NFD", without_duration.casefold())
        normalized = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
        return re.sub(r"[^\w]+", "", normalized)

    def _search_field_values(self, observation):
        """Texto escrito en los cuadros de búsqueda de la página."""
        return {
            value
            for value in (
                self._normalize_for_signature(item.get("value"))
                for item in observation.get("elements", [])
                if item.get("role") in {"textbox", "combobox"}
            )
            if value
        }

    def _visible_video_cards(self, observation, scope):
        """Return one semantic control per visible media card in UIA order."""
        cards, seen = [], set()
        has_active_player = bool(
            self._playback_toggle_controls(observation)
            or self._timeline_slider(observation) is not None
        )
        search_values = self._search_field_values(observation)
        for item in observation.get("elements", []):
            if item.get("role") not in {"link", "button", "listitem"} or item.get("offscreen"):
                continue
            name = self._normalize_for_signature(item.get("name"))
            container = self._normalize_for_signature(item.get("container"))
            section = self._normalize_for_signature(item.get("section"))
            if not name or name in self._NON_CONTENT_LINKS:
                continue
            # Una sugerencia del autocompletado (YouTube en Chromium) es un
            # botón dentro de un listitem con su mismo texto, que contiene lo
            # escrito en el buscador y no tiene duración: no es un video.
            if (
                container == name
                and any(value in name for value in search_values)
                and not self._visible_duration_card_key(item.get("name"))
            ):
                continue
            if scope == "recommended":
                section_is_recommended = any(
                    token in section
                    for token in {
                        "recomend", "recommended", "suger", "related",
                        "up next", "a continuacion",
                    }
                )
                # Chromium sometimes omits section ancestry from the compact
                # UIA result. On a proven player page, other visible links
                # carrying a duration are the provider's playable side-list;
                # this remains generic and excludes channel/navigation links.
                if not section_is_recommended and not has_active_player:
                    continue
            duration_key = self._visible_duration_card_key(item.get("name"))
            # Without a semantic card/list-item boundary, adjacent links can
            # be a title, channel, avatar or menu. A visible duration is the
            # one generic fallback that proves a link represents media rather
            # than navigation; everything else remains unselectable.
            if not container and not duration_key:
                continue
            card_key = container or duration_key
            if card_key in seen:
                continue
            seen.add(card_key)
            cards.append(item)
        return cards

    def _fast_visible_video_selection(self, registry, goal_state):
        """Choose a numbered visible or recommended card in the current tab."""
        step = goal_state.step("select_visible_video")
        if (
            not step
            or step.status != "pending"
            or not registry.has_tool("web_inspect")
            or not registry.has_tool("web_act")
        ):
            return []
        try:
            scope, requested_text = str(step.value or "").split(":", 1)
            requested = int(requested_text)
        except (ValueError, TypeError):
            step.status = "blocked"
            step.reason = "invalid_visible_video_request"
            return []
        observation = registry.execute("web_inspect", {})
        records = [("web_inspect", {}, observation, None)]
        cards = self._visible_video_cards(observation, scope) if observation.get("ok") else []
        # Recién llegados a los resultados, las tarjetas tardan en publicarse.
        for _attempt in range(6):
            if requested <= len(cards):
                break
            time.sleep(0.4)
            observation = registry.execute("web_inspect", {})
            records.append(("web_inspect", {}, observation, None))
            cards = self._visible_video_cards(observation, scope) if observation.get("ok") else []
        if not observation.get("ok"):
            return records
        if (
            requested == 1
            and not cards
            and scope == "visible"
            and goal_state.goal in {"search_and_play_any_result", "channel_video"}
        ):
            # Sin ninguna tarjeta a la vista (en una ventana chica, la
            # tarjeta del canal tapa los resultados), el primer video puede
            # estar más abajo: se lo busca fuera de pantalla y se lo acerca.
            hidden = self._first_hidden_video_card(registry, records)
            if hidden is not None:
                return records + self._select_hidden_video_card(registry, hidden)
        if requested > len(cards) and scope == "visible":
            # En una ventana chica entran pocas tarjetas: web_inspect
            # también publica los videos de más abajo, en el orden de la
            # página, y se acerca el pedido antes de abrirlo.
            every_card = self._hidden_media_cards(observation)
            if len(cards) <= len(every_card) and requested <= len(every_card):
                card = {
                    **every_card[requested - 1],
                    "_snapshot_id": observation.get("snapshot_id"),
                    "_query": None,
                }
                return records + self._select_hidden_video_card(registry, card)
        if requested < 1 or requested > len(cards):
            step.status = "blocked"
            step.reason = f"visible_video_index_out_of_range:{len(cards)}"
            return records
        selected = cards[requested - 1]
        arguments = {
            "element_id": selected["id"],
            "action": "click",
            "snapshot_id": observation.get("snapshot_id"),
        }
        result = registry.execute("web_act", arguments)
        records.append(("web_act", arguments, result, "select_visible_video"))
        if result.get("ok"):
            time.sleep(0.2)
            records.append(("web_inspect", {}, registry.execute("web_inspect", {}), None))
        return records

    # Palabras de la duración que publican las tarjetas ("3 minutes, 8
    # seconds", "3 minutos y 8 segundos"). Como consulta, web_inspect
    # devuelve también los controles fuera de pantalla.
    _HIDDEN_CARD_QUERIES = ("minut", "segund", "second")

    def _media_card_names(self, observation):
        """Títulos de las tarjetas con duración a la vista (para comparar)."""
        return frozenset(
            self._visible_duration_card_key(item.get("name"))
            for item in self._hidden_media_cards(observation or {})
            if not item.get("offscreen")
        )

    def _hidden_media_cards(self, observation):
        """Tarjetas con duración, a la vista o no, en el orden de la página."""
        cards, seen = [], set()
        for item in observation.get("elements", []):
            if item.get("role") not in {"link", "button"}:
                continue
            if self._normalize_for_signature(item.get("name")) in self._NON_CONTENT_LINKS:
                continue
            duration_key = self._visible_duration_card_key(item.get("name"))
            if not duration_key or duration_key in seen:
                continue
            seen.add(duration_key)
            cards.append(item)
        return cards

    def _first_hidden_video_card(self, registry, records):
        """Primera tarjeta de video de la página, aunque esté más abajo."""
        for query in self._HIDDEN_CARD_QUERIES:
            arguments = {"query": query}
            observation = registry.execute("web_inspect", arguments)
            records.append(("web_inspect", arguments, observation, None))
            if not observation.get("ok") or not observation.get("query_matched"):
                continue
            cards = self._hidden_media_cards(observation)
            if cards:
                return {**cards[0], "_snapshot_id": observation.get("snapshot_id"), "_query": query}
        return None

    def _select_hidden_video_card(self, registry, card):
        """Acerca la tarjeta con scroll_into_view y la abre."""
        records = []
        if card.get("offscreen"):
            arguments = {"element_id": card["id"], "action": "scroll_into_view", "snapshot_id": card["_snapshot_id"]}
            result = registry.execute("web_act", arguments)
            records.append(("web_act", arguments, result, "scroll_result_into_view"))
            if result.get("ok"):
                # El desplazamiento invalida el snapshot: se vuelve a
                # buscar la misma tarjeta por su nombre.
                time.sleep(0.2)
                arguments = {"query": card["_query"]}
                observation = registry.execute("web_inspect", arguments)
                records.append(("web_inspect", arguments, observation, None))
                refreshed = next(
                    (
                        item for item in self._hidden_media_cards(observation)
                        if item.get("name") == card.get("name")
                    ),
                    None,
                ) if observation.get("ok") else None
                if refreshed is None:
                    return records
                card = {**refreshed, "_snapshot_id": observation.get("snapshot_id")}
        arguments = {"element_id": card["id"], "action": "click", "snapshot_id": card["_snapshot_id"]}
        result = registry.execute("web_act", arguments)
        records.append(("web_act", arguments, result, "select_visible_video"))
        if result.get("ok"):
            time.sleep(0.2)
            records.append(("web_inspect", {}, registry.execute("web_inspect", {}), None))
        return records

    def _fast_collect_results(self, registry, goal_state):
        """Collect an explicitly requested public media list from UIA.

        Names are preserved exactly as exposed by the provider so author,
        date and duration metadata can be shown when available.  Navigation,
        autocomplete and player-timeline controls are excluded by the same
        media-card contract used for ordinal selection.
        """
        step = goal_state.step("collect_results")
        if not step or step.status != "pending" or not registry.has_tool("web_inspect"):
            return []
        try:
            requested = max(1, min(10, int(float(step.value or 5))))
        except (TypeError, ValueError):
            requested = 5
        records = []
        observation = None
        cards = []
        accumulated_cards = []
        accumulated_keys = set()
        for attempt in range(5):
            observation = registry.execute("web_inspect", {})
            records.append(("web_inspect", {}, observation, None))
            if self._observation_matches_goal_page(observation, goal_state):
                cards = self._visible_video_cards(observation, "visible")
                for item in cards:
                    key = self._normalize_for_signature(item.get("name"))
                    if key and key not in accumulated_keys:
                        accumulated_keys.add(key)
                        accumulated_cards.append(item)
                if len(accumulated_cards) >= requested or self._observation_has_terminal_empty_results(observation):
                    break
            if attempt < 4:
                time.sleep(0.3)
        options, seen = [], set()
        for item in accumulated_cards:
            name = str(item.get("name") or "").strip()
            key = self._normalize_for_signature(name)
            if not name or key in seen:
                continue
            seen.add(key)
            options.append({"name": name})
            if len(options) == requested:
                break
        if options:
            goal_state.record_page_result_options(options)
            if len(options) >= 2:
                self.pending_choice_cache.remember(
                    page=(observation or {}).get("page"),
                    target=goal_state.target,
                    query=goal_state.page_query,
                    options=options,
                    selection_operation="select_result",
                )
        if len(options) >= requested:
            goal_state.mark_completed("collect_results")
            step.reason = "requested_result_count_observed"
        else:
            step.status = "blocked"
            step.reason = f"result_count_short:{len(options)}:{requested}"
        return records

    def _fast_adjust_volume(self, registry, goal_state):
        """Apply one requested relative system-volume change with evidence."""
        step = goal_state.pending_step("adjust_volume")
        if not step or step.status != "pending" or not registry.has_tool("control_media"):
            return []
        try:
            amount = float(step.value or 0)
        except (TypeError, ValueError):
            step.status = "blocked"
            step.reason = "invalid_volume_adjustment"
            return []
        if amount == 0:
            goal_state.mark_completed("adjust_volume")
            step.reason = "zero_volume_adjustment"
            return []
        arguments = {
            "action": "volume_up" if amount > 0 else "volume_down",
            "amount": abs(amount),
        }
        result = registry.execute("control_media", arguments)
        records = [("control_media", arguments, result, None)]
        if result.get("ok") and (
            result.get("state_confirmed") is True
            or result.get("percent") is not None
        ):
            goal_state.mark_completed("adjust_volume")
            step.reason = "system_volume_observed"
        elif result.get("ok"):
            step.status = "blocked"
            step.reason = "volume_change_unverified"
        else:
            step.status = "failed"
            step.reason = result.get("error") or "volume_change_failed"
        return records

    def _initial_page_search_query(self, goal_state):
        """Build a direct search for the requested result and its context.

        In "search AUTHOR and open VIDEO", the video is the destination and
        AUTHOR constrains which result the user means.  Searching only the
        author first is slower and can surface a channel or an unrelated
        recent upload, so a named result leads the actual page query.
        """
        initial = str(goal_state.page_query or "").strip()
        result = str(goal_state.page_result or "").strip()
        if not initial or not result:
            return initial or None
        initial_terms = set(self._meaningful_result_tokens(initial))
        result_terms = set(self._meaningful_result_tokens(result))
        if not result_terms or result_terms.issubset(initial_terms):
            return initial
        return f"{result} {initial}"[:240]

    def _refined_page_query(self, goal_state):
        """Try the reverse combined phrase once when a direct lookup misses."""
        initial = str(goal_state.page_query or "").strip()
        result = str(goal_state.page_result or "").strip()
        if not initial or not result:
            return None
        initial_terms = set(self._meaningful_result_tokens(initial))
        result_terms = set(self._meaningful_result_tokens(result))
        if len(result_terms) < 2 or result_terms.issubset(initial_terms):
            return None
        return f"{initial} {result}"[:240]

    @staticmethod
    def _has_page_content(observation):
        """An empty renderer tree means the just-opened page is not ready."""

        return bool(observation.get("elements") or observation.get("context"))

    def _observation_has_terminal_empty_results(self, observation):
        """Recognize explicit provider evidence that a search has no result."""

        texts = [
            str(item.get("name") or "")
            for item in observation.get("elements", [])
        ] + [str(value or "") for value in observation.get("context", [])]
        normalized = " ".join(
            self._normalize_for_signature(value)
            for value in texts
        )
        return any(phrase in normalized for phrase in {
            "intenta con otras palabras",
            "no hay resultados",
            "sin resultados",
            "no results",
            "we couldnt find",
            "we could not find",
        })

    def _find_targeted_page_result(self, registry, goal_state, records):
        """Inspect only the requested result, including a scrollable one."""
        arguments = {"query": goal_state.page_result}
        observation = registry.execute("web_inspect", arguments)
        records.append(("web_inspect", arguments, observation, None))
        selected = (
            self._goal_named_page_result(observation, goal_state)
            if self._observation_matches_goal_page(observation, goal_state)
            else None
        )
        if selected:
            selected = {**selected, "_snapshot_id": observation.get("snapshot_id")}
        return observation, selected

    def _scroll_result_into_view(self, registry, goal_state, selected, records):
        """Expose an otherwise hidden, user-requested result through UIA."""
        if not selected.get("offscreen"):
            return selected
        arguments = {
            "element_id": selected["id"],
            "action": "scroll_into_view",
            "snapshot_id": selected.get("_snapshot_id"),
        }
        result = registry.execute("web_act", arguments)
        records.append(("web_act", arguments, result, "scroll_result_into_view"))
        if not result.get("ok"):
            return None
        time.sleep(0.2)
        _observation, refreshed = self._find_targeted_page_result(registry, goal_state, records)
        return refreshed if refreshed and not refreshed.get("offscreen") else None

    def _fast_page_search(self, registry, goal_state, initial_observation=None):
        """Generic inspect → search-field → type → submit → verify workflow.

        It relies only on browser accessibility roles and localised accessible
        names for the common Search/Buscar control. When the page is
        ambiguous, it returns the observations to the regular agent instead
        of clicking a guessed control.
        """
        search_step = goal_state.step("search_page")
        if (
            not search_step
            or search_step.status != "pending"
            or not goal_state.page_query
            or not registry.has_tool("web_inspect")
            or not registry.has_tool("web_act")
        ):
            return []

        observation = initial_observation
        records = []
        # BrowserController returns as soon as Chrome accepts the navigation;
        # a complex SPA can expose its UIA tree a few moments later.  Retry a
        # bounded number of times only while the matching page is completely
        # empty, never by guessing controls or extending the model loop.
        for attempt in range(6):
            if observation is None:
                observation = registry.execute("web_inspect", {})
                records.append(("web_inspect", {}, observation, None))
            if (
                self._observation_matches_goal_page(observation, goal_state)
                and self._has_page_content(observation)
            ):
                break
            if attempt < 5:
                time.sleep(0.5)
                observation = None
        if observation is None:
            observation = {
                "ok": False,
                "error": "web_inspection_missing",
                "message": "No se pudo observar la página.",
            }
            records.append(("web_inspect", {}, observation, None))
        if not self._observation_matches_goal_page(observation, goal_state):
            return records

        textbox = self._search_textbox(observation)
        if textbox is None:
            opener = self._search_opener(observation)
            if opener is None:
                return records
            opener_arguments = {"element_id": opener["id"], "action": "click", "snapshot_id": observation.get("snapshot_id")}
            opener_result = registry.execute("web_act", opener_arguments)
            records.append(("web_act", opener_arguments, opener_result, "open_search"))
            if not opener_result.get("ok"):
                return records
            textbox = None
            for attempt in range(4):
                time.sleep(0.2 if attempt == 0 else 0.3)
                observation = registry.execute("web_inspect", {})
                records.append(("web_inspect", {}, observation, None))
                textbox = self._search_textbox(observation) if observation.get("ok") else None
                if textbox is not None:
                    break
            if textbox is None:
                return records

        search_query = self._initial_page_search_query(goal_state)
        type_arguments = {
            "element_id": textbox["id"],
            "action": "type",
            "value": search_query,
            "snapshot_id": observation.get("snapshot_id"),
        }
        type_result = registry.execute("web_act", type_arguments)
        records.append(("web_act", type_arguments, type_result, "type_search"))
        if not type_result.get("ok"):
            return records

        # Typing invalidates the snapshot, so obtain a fresh reference before
        # sending Enter to the actual accessible field.
        observation = registry.execute("web_inspect", {})
        records.append(("web_inspect", {}, observation, None))
        textbox = self._search_textbox(observation) if observation.get("ok") else None
        if textbox is None:
            return records

        submit_arguments = {"element_id": textbox["id"], "action": "submit", "snapshot_id": observation.get("snapshot_id")}
        submit_result = registry.execute("web_act", submit_arguments)
        records.append(("web_act", submit_arguments, submit_result, "submit_search"))
        if submit_result.get("ok"):
            # A submit can first expose auto-complete suggestions before the
            # result page replaces them.  This short interval keeps the fast
            # path responsive while giving the provider time to publish the
            # first result tree.
            time.sleep(0.4)
            results_observation = registry.execute("web_inspect", {})
            records.append(("web_inspect", {}, results_observation, None))

            # Si ya había resultados de otra búsqueda (YouTube), la página
            # vieja sigue publicada un rato después del Enter: esperar a que
            # cambien la URL o el título y a que haya contenido, para no
            # elegir un resultado de la búsqueda anterior.
            submitted_from = self._page_identity(observation)
            # Chromium en Linux cambia la URL antes que el contenido: un
            # rato sigue publicando los videos de la portada. Mientras sean
            # los mismos de antes del Enter, la página no cambió todavía.
            cards_before = self._media_card_names(observation)
            for _attempt in range(12):
                if (
                    results_observation.get("ok")
                    and self._page_identity(results_observation) != submitted_from
                    and self._has_page_content(results_observation)
                    and not (cards_before and self._media_card_names(results_observation) == cards_before)
                ):
                    break
                time.sleep(0.4)
                results_observation = registry.execute("web_inspect", {})
                records.append(("web_inspect", {}, results_observation, None))

            # A named follow-up from the user (for example "busca X en
            # YouTube y abre X") is executable from semantic UIA evidence.
            # We only act when precisely one result has that accessible name;
            # otherwise the regular agent receives the observed results.
            selection_step = goal_state.step("select_result")
            if not selection_step:
                # Search-only requests still need evidence that the SPA
                # replaced its pre-submit/autocomplete tree.  Wait only while
                # the provider reports an unchanged snapshot.
                for _attempt in range(4):
                    if results_observation.get("changed"):
                        break
                    time.sleep(0.3)
                    results_observation = registry.execute("web_inspect", {})
                    records.append(("web_inspect", {}, results_observation, None))
            selected = (
                self._goal_named_page_result(results_observation, goal_state)
                if selection_step
                and selection_step.status == "pending"
                and self._observation_matches_goal_page(results_observation, goal_state)
                else None
            )
            if not selected and selection_step and selection_step.status == "pending":
                if self._observation_has_terminal_empty_results(results_observation):
                    selection_step.status = "blocked"
                    selection_step.reason = "content_not_found"
                    return records
                # The normal compact snapshot intentionally excludes offscreen
                # controls.  Probe the requested title alone before assuming
                # it is absent; a UIA ScrollItemPattern can make that exact
                # result visible without guessing or using coordinates.
                _targeted_observation, selected = self._find_targeted_page_result(
                    registry,
                    goal_state,
                    records,
                )
                if (
                    not selected
                    and not goal_state.step("play_media")
                    and self._remember_ambiguous_page_results(goal_state, _targeted_observation)
                ):
                    return records
            if selected:
                # Seeing the uniquely requested result is direct evidence
                # that the in-site search completed, even if Chromium reports
                # the same accessibility digest as the autocomplete view.
                goal_state.mark_completed("search_page")
                if selected.get("offscreen"):
                    selected = self._scroll_result_into_view(registry, goal_state, selected, records)
                    if selected is None:
                        return records
                selection_arguments = {
                    "element_id": selected["id"],
                    "action": "click",
                    "snapshot_id": selected.get("_snapshot_id") or results_observation.get("snapshot_id"),
                }
                selection_result = registry.execute("web_act", selection_arguments)
                records.append(("web_act", selection_arguments, selection_result, "select_result"))
                if selection_result.get("ok"):
                    time.sleep(0.2)
                    records.append(("web_inspect", {}, registry.execute("web_inspect", {}), None))
            elif selection_step and selection_step.status == "pending":
                # "Busca un creador y abre este video" is naturally a broad
                # first search followed by a title lookup.  If that title is
                # not yet in the accessible results, refine the existing
                # page search once rather than guessing a channel or an
                # unrelated visible video.  This is generic to pages with a
                # semantic search field; no provider names are special-cased.
                refined_query = self._refined_page_query(goal_state)
                refined_textbox = self._search_textbox(results_observation) if results_observation.get("ok") else None
                if not refined_query:
                    # Direct title lookups ("pon X en YouTube") do not have
                    # a meaningful alternate query.  YouTube, Netflix and
                    # other SPA pages often expose the results document one
                    # rendering turn before the matching result links. Poll
                    # that *same* in-site search briefly instead of giving up
                    # or asking the model to fall back to Google.
                    for _attempt in range(4):
                        time.sleep(0.3)
                        delayed_results = registry.execute("web_inspect", {})
                        records.append(("web_inspect", {}, delayed_results, None))
                        if self._observation_has_terminal_empty_results(delayed_results):
                            selection_step.status = "blocked"
                            selection_step.reason = "content_not_found"
                            return records
                        selected = (
                            self._goal_named_page_result(delayed_results, goal_state)
                            if self._observation_matches_goal_page(delayed_results, goal_state)
                            else None
                        )
                        if not selected:
                            if self._remember_ambiguous_page_results(goal_state, delayed_results):
                                return records
                            # A targeted query is comparatively expensive and
                            # we already made one immediately after submit.
                            # Reserve one final targeted lookup for an item
                            # that remains offscreen after the bounded wait.
                            if _attempt == 3:
                                _targeted_observation, selected = self._find_targeted_page_result(
                                    registry,
                                    goal_state,
                                    records,
                                )
                        if not selected:
                            continue
                        if selected.get("offscreen"):
                            selected = self._scroll_result_into_view(registry, goal_state, selected, records)
                            if selected is None:
                                return records
                        selection_arguments = {
                            "element_id": selected["id"],
                            "action": "click",
                            "snapshot_id": selected.get("_snapshot_id") or delayed_results.get("snapshot_id"),
                        }
                        selection_result = registry.execute("web_act", selection_arguments)
                        records.append(("web_act", selection_arguments, selection_result, "select_result"))
                        if selection_result.get("ok"):
                            time.sleep(0.2)
                            records.append(("web_inspect", {}, registry.execute("web_inspect", {}), None))
                        break
                    return records
                if refined_textbox is None:
                    return records

                refined_type_arguments = {
                    "element_id": refined_textbox["id"],
                    "action": "type",
                    "value": refined_query,
                    "snapshot_id": results_observation.get("snapshot_id"),
                }
                refined_type_result = registry.execute("web_act", refined_type_arguments)
                records.append(("web_act", refined_type_arguments, refined_type_result, "type_refined_search"))
                if not refined_type_result.get("ok"):
                    return records

                refined_observation = registry.execute("web_inspect", {})
                records.append(("web_inspect", {}, refined_observation, None))
                refined_textbox = self._search_textbox(refined_observation) if refined_observation.get("ok") else None
                if refined_textbox is None:
                    return records

                refined_submit_arguments = {
                    "element_id": refined_textbox["id"],
                    "action": "submit",
                    "snapshot_id": refined_observation.get("snapshot_id"),
                }
                refined_submit_result = registry.execute("web_act", refined_submit_arguments)
                records.append(("web_act", refined_submit_arguments, refined_submit_result, "submit_refined_search"))
                if not refined_submit_result.get("ok"):
                    return records

                # Result pages can publish their document before the result
                # links.  Retry only while the requested item remains absent;
                # the moment one clear semantic match appears we stop.
                for attempt in range(5):
                    time.sleep(0.35)
                    refined_results = registry.execute("web_inspect", {})
                    records.append(("web_inspect", {}, refined_results, None))
                    selected = (
                        self._goal_named_page_result(refined_results, goal_state)
                        if self._observation_matches_goal_page(refined_results, goal_state)
                        else None
                    )
                    if not selected:
                        if self._remember_ambiguous_page_results(goal_state, refined_results):
                            return records
                        _targeted_observation, selected = self._find_targeted_page_result(
                            registry,
                            goal_state,
                            records,
                        )
                    if not selected:
                        continue
                    if selected.get("offscreen"):
                        selected = self._scroll_result_into_view(registry, goal_state, selected, records)
                        if selected is None:
                            return records
                    selection_arguments = {
                        "element_id": selected["id"],
                        "action": "click",
                        "snapshot_id": selected.get("_snapshot_id") or refined_results.get("snapshot_id"),
                    }
                    selection_result = registry.execute("web_act", selection_arguments)
                    records.append(("web_act", selection_arguments, selection_result, "select_result"))
                    if selection_result.get("ok"):
                        time.sleep(0.2)
                        records.append(("web_inspect", {}, registry.execute("web_inspect", {}), None))
                    break
        return records

    @staticmethod
    def _accessible_duration_seconds(item):
        """Read a published media duration from an accessible name/value.

        RangeValue itself does not declare whether its numbers are seconds,
        milliseconds or a percentage.  A player often exposes the duration in
        its accessible text (for example ``0:12 / 23:41``).  We use that
        explicit evidence when present and otherwise decline percent-based
        seeking instead of guessing.
        """
        text = " ".join(str(item.get(field) or "") for field in ("name", "value"))
        matches = re.findall(r"(?<!\d)(?:(\d+):)?(\d{1,2}):(\d{2})(?!\d)", text)
        values = []
        for hours, minutes, seconds in matches:
            minute_value, second_value = int(minutes), int(seconds)
            if second_value < 60:
                values.append(int(hours or 0) * 3600 + minute_value * 60 + second_value)
        return max(values) if values else None

    def _timeline_slider(self, observation):
        """Return one visible non-volume slider suitable for media seeking."""
        candidates = []
        for item in observation.get("elements", []):
            if item.get("role") != "slider" or item.get("offscreen"):
                continue
            bounds = item.get("range") or {}
            try:
                minimum, maximum, current = (
                    float(bounds["minimum"]),
                    float(bounds["maximum"]),
                    float(bounds["value"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if maximum <= minimum:
                continue
            label = self._normalize_for_signature(
                f"{item.get('name', '')} {item.get('value', '')}"
            )
            if any(word in label for word in {"volumen", "volume", "loudness"}):
                continue
            duration = self._accessible_duration_seconds(item)
            # A labelled seek/progress control is clear.  A player may also
            # omit those words yet publish a clock-duration, which is equally
            # clear evidence and remains generic across providers.
            if not (
                duration is not None
                or any(word in label for word in {"progreso", "progress", "seek", "tiempo", "time", "reproduccion", "playback"})
                or maximum - minimum > 100
            ):
                continue
            candidates.append((item, minimum, maximum, current, duration))
        return candidates[0] if len(candidates) == 1 else None

    def _sample_focused_timeline(self, registry, observation, records):
        """Focus a semantic timeline and collect two fresh state samples.

        Chromium may leave passive RangeValue and playback-button labels
        stale. Focusing the UIA slider makes the provider refresh its current
        value, after which progression or stability can be observed without
        coordinates or site-specific keyboard shortcuts.
        """
        slider = self._timeline_slider(observation)
        if slider is None:
            return None, None
        item, _minimum, _maximum, _current, _duration = slider
        arguments = {
            "element_id": item["id"],
            "action": "focus",
            "snapshot_id": observation.get("snapshot_id"),
        }
        result = registry.execute("web_act", arguments)
        records.append(("web_act", arguments, result, "focus_timeline"))
        if not result.get("ok"):
            return None, None
        time.sleep(0.2)
        first = registry.execute("web_inspect", {})
        records.append(("web_inspect", {}, first, None))
        time.sleep(1.1)
        second = registry.execute("web_inspect", {})
        records.append(("web_inspect", {}, second, None))
        return first, second

    def _use_windows_media_session(self, registry, goal_state, desired, observation, records):
        """Use an explicitly observable Windows media state when available.

        Returns True when a matching session handled the request (whether it
        ultimately confirmed or rejected it), and False when the page should
        continue through its UIA fallback.
        """
        if not registry.has_tool("control_media"):
            return False
        title = str((observation.get("page") or {}).get("title") or "")
        query_arguments = {"action": "get_state"}
        if title:
            query_arguments["expected_title"] = title
        state_result = registry.execute("control_media", query_arguments)
        records.append(("control_media", query_arguments, state_result, None))
        if not state_result.get("ok") or not state_result.get("state_confirmed"):
            return False
        status = str(state_result.get("playback_status") or "").casefold()
        already_desired = (
            status == "playing"
            if desired == "play"
            else status in {"paused", "stopped"}
        )
        operation = "play_media" if desired == "play" else "pause_media"
        step = goal_state.pending_step(operation)
        if already_desired:
            goal_state.mark_completed(operation)
            if step:
                step.reason = f"windows_media_session_{status}"
            return True
        if status not in {"playing", "paused", "stopped"}:
            return False
        action_arguments = {"action": desired}
        if title:
            action_arguments["expected_title"] = title
        action_result = registry.execute("control_media", action_arguments)
        records.append(("control_media", action_arguments, action_result, None))
        final_status = str(action_result.get("playback_status") or "").casefold()
        confirmed = bool(action_result.get("ok") and action_result.get("state_confirmed"))
        if desired == "play":
            confirmed = confirmed and final_status == "playing"
        else:
            confirmed = confirmed and final_status in {"paused", "stopped"}
        if confirmed:
            goal_state.mark_completed(operation)
            if step:
                step.reason = f"windows_media_session_{final_status}"
        # A matching session accepted or rejected an explicit state request;
        # never follow it with a blind toggle in the same micro-plan.
        return True

    def _fast_player_navigation(self, registry, goal_state, operation):
        """Execute one verified player operation through generic UIA evidence."""
        step = goal_state.pending_step(operation)
        if (
            not step
            or step.status != "pending"
            or not registry.has_tool("web_inspect")
            or not registry.has_tool("web_act")
        ):
            return []
        observation = None
        readiness_records = []
        for attempt in range(6):
            observation = registry.execute("web_inspect", {})
            readiness_records.append(("web_inspect", {}, observation, None))
            if (
                self._observation_matches_goal_page(observation, goal_state)
                and (
                    self._has_page_content(observation)
                    or (
                        registry.has_tool("control_media")
                        and operation in {
                            "seek_media", "adjust_media_position",
                            "next_media", "previous_media",
                        }
                    )
                )
            ):
                break
            if attempt < 5:
                time.sleep(0.5)
        records = readiness_records
        if not self._observation_matches_goal_page(observation, goal_state):
            return records

        if (
            operation in {"seek_media", "adjust_media_position"}
            and registry.has_tool("control_media")
        ):
            try:
                requested_seconds = float(step.value or 0)
            except (TypeError, ValueError):
                requested_seconds = None
            if requested_seconds is not None:
                title = str((observation.get("page") or {}).get("title") or "")
                media_arguments = {
                    "action": "seek" if operation == "seek_media" else "adjust_position",
                    "amount": requested_seconds,
                }
                if title:
                    media_arguments["expected_title"] = title
                media_result = registry.execute("control_media", media_arguments)
                records.append(("control_media", media_arguments, media_result, None))
                if media_result.get("ok") and media_result.get("state_confirmed"):
                    goal_state.mark_completed(operation)
                    step.reason = "windows_media_session_position"
                    return records
                if media_result.get("error") == "media_position_out_of_range":
                    step.status = "blocked"
                    step.reason = (
                        "media_position_out_of_range:"
                        f"{media_result.get('requested_position_seconds')}:"
                        f"{media_result.get('duration_seconds')}"
                    )
                    return records
                # If Windows accepted the request but could not confirm it,
                # do not apply a second adjustment through UIA.  Unsupported
                # or mismatched sessions may safely fall back to the page.
                if media_result.get("ok"):
                    return records

        if (
            operation in {"next_media", "previous_media"}
            and registry.has_tool("control_media")
        ):
            title = str((observation.get("page") or {}).get("title") or "")
            if title:
                media_arguments = {
                    "action": "next" if operation == "next_media" else "previous",
                    "expected_title": title,
                }
                media_result = registry.execute("control_media", media_arguments)
                records.append(("control_media", media_arguments, media_result, None))
                if media_result.get("ok") and media_result.get("state_confirmed"):
                    goal_state.mark_completed(operation)
                    step.reason = "windows_media_session_item_changed"
                    return records
                if media_result.get("ok"):
                    return records

        if operation in {"next_media", "previous_media"}:
            names = (
                {"siguiente", "siguiente episodio", "siguiente video", "next", "next episode", "next video"}
                if operation == "next_media"
                else {"anterior", "episodio anterior", "video anterior", "previous", "previous episode", "previous video"}
            )
            matches = [
                item for item in observation.get("elements", [])
                if item.get("role") in {"button", "link"}
                and not item.get("offscreen")
                and self._normalize_for_signature(item.get("name")) in names
            ]
            if len(matches) != 1:
                return records
            arguments = {
                "element_id": matches[0]["id"],
                "action": "click",
                "snapshot_id": observation.get("snapshot_id"),
            }
        else:
            slider = self._timeline_slider(observation)
            if slider is None:
                return records
            item, minimum, maximum, current, duration = slider
            try:
                requested = float(step.value or 0)
            except (TypeError, ValueError):
                return records
            span = maximum - minimum
            native_target = None
            if operation == "seek_media" and requested == 0:
                native_target = minimum
            elif duration is not None and duration > 0:
                if operation == "adjust_media_position":
                    requested = ((current - minimum) / span) * duration + requested
                if 0 <= requested <= duration:
                    native_target = minimum + (requested / duration) * span
            elif span > 100:
                # A large native range with an explicitly labelled seek
                # slider is usable as seconds.  Small 0..100 ranges need an
                # accessible duration so we never mistake a percentage.
                native_target = current + requested if operation == "adjust_media_position" else requested
            if native_target is None:
                return records
            native_target = max(minimum, min(maximum, native_target))
            if abs(current - native_target) < 0.5:
                goal_state.mark_completed(operation)
                step.reason = "already_at_requested_position"
                return records
            arguments = {
                "element_id": item["id"],
                "action": "set_range",
                "value": f"{native_target:.6f}",
                "snapshot_id": observation.get("snapshot_id"),
            }

        result = registry.execute("web_act", arguments)
        range_navigation = operation in {"seek_media", "adjust_media_position"}
        # Range navigation has a stronger contract than ordinary page
        # changes: completion requires the requested timeline value, not just
        # any changed snapshot (an overlay or focus change is insufficient).
        records.append(("web_act", arguments, result, None if range_navigation else operation))
        if result.get("ok"):
            time.sleep(0.2)
            verified_observation = registry.execute("web_inspect", {})
            records.append(("web_inspect", {}, verified_observation, None))
            if range_navigation:
                verified_slider = self._timeline_slider(verified_observation)
                if verified_slider is not None:
                    _verified_item, verified_minimum, verified_maximum, verified_current, _verified_duration = verified_slider
                    # A playing timeline keeps advancing while UIA focuses the
                    # slider, calibrates a key step and builds the verifying
                    # snapshot.  Compare in the slider's native units and
                    # allow at most four seconds of measured playback drift;
                    # this still rejects an unrelated focus/overlay change.
                    units_per_second = (
                        (maximum - minimum) / duration
                        if duration is not None and duration > 0
                        else 1.0
                    )
                    target_tolerance = max(0.75, 4.0 * units_per_second)
                    provider_value = result.get("observed_range_value")
                    try:
                        provider_near_target = (
                            provider_value is None
                            or abs(float(provider_value) - native_target) <= target_tolerance
                        )
                    except (TypeError, ValueError):
                        provider_near_target = False
                    if (
                        abs(verified_minimum - minimum) < 0.01
                        and abs(verified_maximum - maximum) < 0.01
                        and provider_near_target
                        and abs(verified_current - native_target) <= target_tolerance
                    ):
                        goal_state.mark_completed(operation)
                        step.reason = "timeline_target_observed"
        return records

    def _fast_named_page_action(self, registry, goal_state, operation):
        """Click one exact, already-requested page control and reinspect.

        This handles follow-up controls such as an episode label or a visible
        Pause button without knowing anything about Netflix, YouTube or the
        page layout.  The action is only attempted when a single UIA control
        exactly matches the user-provided step value.
        """
        step = goal_state.pending_step(operation)
        if (
            not step
            or step.status != "pending"
            or not step.value
            or not registry.has_tool("web_inspect")
            or not registry.has_tool("web_act")
        ):
            return []
        observation = None
        records = []
        for attempt in range(6):
            observation = registry.execute("web_inspect", {})
            records.append(("web_inspect", {}, observation, None))
            if (
                self._observation_matches_goal_page(observation, goal_state)
                and (
                    self._has_page_content(observation)
                    or (
                        operation == "pause_media"
                        and registry.has_tool("control_media")
                    )
                )
            ):
                break
            if attempt < 5:
                time.sleep(0.5)
        if not self._observation_matches_goal_page(observation, goal_state):
            return records
        if operation in {"select_content_tab", "select_sort"}:
            goal_state.confirm_selected_page_control(observation)
            if step.status == "completed":
                return records

        if operation == "pause_media" and self._use_windows_media_session(
            registry, goal_state, "pause", observation, records
        ):
            return records

        generic_pause_target = None
        if (
            operation == "pause_media"
            and self._timeline_slider(observation) is not None
        ):
            # Chromium can leave either its explicit Play/Pause label or one
            # neutral "playback button" label stale.  When a timeline exists,
            # its focused movement is stronger evidence than either label.
            first_sample, state_observation = self._sample_focused_timeline(
                registry, observation, records
            )
            if not first_sample or not state_observation:
                return records
            if not self._observation_matches_goal_page(state_observation, goal_state):
                return records
            if self._media_timeline_stable(first_sample, state_observation):
                goal_state.mark_completed(operation)
                step.reason = "timeline_stable_before_pause"
                return records
            if not self._media_timeline_progressed(first_sample, state_observation):
                return records
            observation = state_observation
            toggle_controls = self._playback_toggle_controls(observation)
            if len(toggle_controls) != 1:
                return records
            generic_pause_target = toggle_controls[0]

        if (
            operation == "pause_media"
            and generic_pause_target is None
            and self._has_play_media_control(observation)
        ):
            # A semantic Play control is direct evidence that the player is
            # already paused. Do not toggle it.
            goal_state.mark_completed(operation)
            step.reason = "visible_play_control_before_action"
            return records

        selected = generic_pause_target
        if selected is None and operation == "select_channel":
            selected = self._named_channel_result(observation, step.value)
            if selected is None:
                candidates = self._channel_choice_candidates(observation, step.value)
                choices = [option for option, _item in candidates]
                if len(candidates) == 1:
                    selected = candidates[0][1]
                elif len(choices) > 1:
                    goal_state.record_page_result_options(choices)
                    step.reason = "ambiguous_channel"
                    self.pending_choice_cache.remember(
                        page=observation.get("page"),
                        target=goal_state.target,
                        query=goal_state.page_query,
                        options=choices,
                        selection_operation="select_channel",
                        continuation=self._choice_continuation(
                            goal_state, "select_channel"
                        ),
                    )
        if selected is None and operation == "select_content_tab":
            # La página del canal tiene la pestaña "Videos" y también una
            # sección "Videos" en Inicio: vale la pestaña. La lectura
            # completa puede no incluirla; se busca por nombre.
            selected = self._content_tab(observation, step.value)
            if selected is None:
                targeted = registry.execute("web_inspect", {"query": step.value})
                records.append(("web_inspect", {"query": step.value}, targeted, None))
                if self._observation_matches_goal_page(targeted, goal_state):
                    selected = self._content_tab(targeted, step.value)
                    if selected is not None:
                        observation = targeted
        if selected is None and operation == "select_sort":
            for alias in page_control_names(step.value):
                selected = self._exact_named_control(observation, alias)
                if selected is not None:
                    break
        if selected is None and operation != "select_channel":
            selected = self._named_page_result(observation, step.value)
        if (
            selected is None
            and operation in {"select_channel", "select_content_tab", "select_sort"}
            and not goal_state.page_result_options
        ):
            # Channel pages publish navigation/sort controls after their page
            # shell.  Content existence alone is not readiness for this exact
            # operation, so wait briefly for the requested semantic control.
            for _attempt in range(4):
                time.sleep(0.35)
                refreshed = registry.execute("web_inspect", {})
                records.append(("web_inspect", {}, refreshed, None))
                if not self._observation_matches_goal_page(refreshed, goal_state):
                    continue
                observation = refreshed
                if operation in {"select_content_tab", "select_sort"}:
                    goal_state.confirm_selected_page_control(observation)
                    if step.status == "completed":
                        return records
                    if operation == "select_content_tab":
                        selected = self._content_tab(observation, step.value)
                    else:
                        selected = next(
                            (
                                control
                                for alias in page_control_names(step.value)
                                for control in [self._exact_named_control(observation, alias)]
                                if control is not None
                            ),
                            None,
                        )
                    selected = selected or self._named_page_result(observation, step.value)
                else:
                    selected = self._named_channel_result(observation, step.value)
                    if selected is None:
                        candidates = self._channel_choice_candidates(observation, step.value)
                        choices = [option for option, _item in candidates]
                        if len(candidates) == 1:
                            selected = candidates[0][1]
                        elif len(choices) > 1:
                            goal_state.record_page_result_options(choices)
                            step.reason = "ambiguous_channel"
                            self.pending_choice_cache.remember(
                                page=observation.get("page"),
                                target=goal_state.target,
                                query=goal_state.page_query,
                                options=choices,
                                selection_operation="select_channel",
                                continuation=self._choice_continuation(
                                    goal_state, "select_channel"
                                ),
                            )
                            return records
                if selected is not None:
                    break
        episode_alias = None
        if not selected and operation == "select_episode":
            for alias in self._episode_name_aliases(step.value):
                targeted = registry.execute("web_inspect", {"query": alias})
                records.append(("web_inspect", {"query": alias}, targeted, None))
                if not self._observation_matches_goal_page(targeted, goal_state):
                    continue
                candidate = self._named_page_result(targeted, alias)
                if candidate:
                    observation = targeted
                    selected = candidate
                    episode_alias = alias
                    break
        if selected and operation == "select_season" and selected.get("role") == "combobox":
            # A combobox exposes its currently selected value as its accessible
            # name.  Seeing the requested season there is evidence of state,
            # not an instruction to toggle the selector again.
            goal_state.mark_completed(operation)
            step.reason = "visible_selected_season"
            return records
        if not selected and operation == "select_season":
            # The requested season is commonly hidden behind the current
            # season's combobox.  Open exactly one semantic season selector,
            # reinspect it, and only then select the exact requested option.
            selectors = [
                item for item in observation.get("elements", [])
                if item.get("role") in {"button", "combobox"}
                and not item.get("offscreen")
                and any(token in self._normalize_for_signature(f"{item.get('name', '')} {item.get('value', '')}") for token in {"temporada", "season"})
            ]
            if len(selectors) != 1:
                return records
            opener = selectors[0]
            opener_arguments = {
                "element_id": opener["id"],
                "action": "click",
                "snapshot_id": observation.get("snapshot_id"),
            }
            opener_result = registry.execute("web_act", opener_arguments)
            records.append(("web_act", opener_arguments, opener_result, "open_season_selector"))
            if not opener_result.get("ok"):
                return records
            time.sleep(0.2)
            observation = registry.execute("web_inspect", {})
            records.append(("web_inspect", {}, observation, None))
            if not self._observation_matches_goal_page(observation, goal_state):
                return records
            selected = self._named_page_result(observation, step.value)
        if selected and selected.get("offscreen") and operation in {
            "select_episode", "select_channel", "select_content_tab", "select_sort",
        }:
            scroll_arguments = {
                "element_id": selected["id"],
                "action": "scroll_into_view",
                "snapshot_id": observation.get("snapshot_id"),
            }
            scroll_result = registry.execute("web_act", scroll_arguments)
            records.append(("web_act", scroll_arguments, scroll_result, "scroll_episode_into_view"))
            if not scroll_result.get("ok"):
                return records
            time.sleep(0.2)
            query = episode_alias or selected.get("name") or step.value
            observation = registry.execute("web_inspect", {"query": query})
            records.append(("web_inspect", {"query": query}, observation, None))
            selected = (
                self._content_tab(observation, query)
                if operation == "select_content_tab" and selected.get("role") == "tab"
                else self._named_page_result(observation, query)
            )
        if not selected or selected.get("offscreen"):
            return records
        arguments = {
            "element_id": selected["id"],
            "action": "keyboard_activate" if operation == "pause_media" else "click",
            "snapshot_id": observation.get("snapshot_id"),
        }
        result = registry.execute("web_act", arguments)
        pause_operation = operation == "pause_media"
        # A generic snapshot change is not enough to prove Pause; focus or a
        # controls overlay can change without stopping playback.
        records.append(("web_act", arguments, result, None if pause_operation else operation))
        if result.get("ok"):
            time.sleep(0.3 if pause_operation else 0.2)
            verified_observation = registry.execute("web_inspect", {})
            records.append(("web_inspect", {}, verified_observation, None))
            if (
                pause_operation
                and self._observation_matches_selected_media(verified_observation, goal_state)
            ):
                if self._timeline_slider(verified_observation) is not None:
                    first_sample, final_observation = self._sample_focused_timeline(
                        registry, verified_observation, records
                    )
                    if (
                        first_sample
                        and final_observation
                        and self._observation_matches_selected_media(final_observation, goal_state)
                        and self._media_timeline_stable(first_sample, final_observation)
                    ):
                        goal_state.mark_completed(operation)
                        step.reason = "timeline_stable_after_pause"
                    elif (
                        first_sample
                        and final_observation
                        and self._media_timeline_progressed(first_sample, final_observation)
                    ):
                        # The provider accepted focus but ignored the first
                        # activation.  Retry the one semantic transport
                        # control once, then require a stable focused timeline.
                        retry_controls = self._playback_toggle_controls(final_observation)
                        if len(retry_controls) == 1:
                            retry_arguments = {
                                "element_id": retry_controls[0]["id"],
                                "action": "keyboard_activate",
                                "snapshot_id": final_observation.get("snapshot_id"),
                            }
                            retry_result = registry.execute("web_act", retry_arguments)
                            records.append(("web_act", retry_arguments, retry_result, None))
                            if retry_result.get("ok"):
                                time.sleep(0.3)
                                retry_observation = registry.execute("web_inspect", {})
                                records.append(("web_inspect", {}, retry_observation, None))
                                retry_first, retry_final = self._sample_focused_timeline(
                                    registry, retry_observation, records
                                )
                                if (
                                    retry_first
                                    and retry_final
                                    and self._observation_matches_selected_media(retry_final, goal_state)
                                    and self._media_timeline_stable(retry_first, retry_final)
                                ):
                                    goal_state.mark_completed(operation)
                                    step.reason = "timeline_stable_after_pause_retry"
                elif self._has_play_media_control(verified_observation):
                    goal_state.mark_completed(operation)
                    step.reason = "visible_play_control"
                else:
                    # If a stale Pause label represented an already-paused
                    # player, the first toggle started playback. Toggle the
                    # one resulting Pause control once more to normalize the
                    # requested final state, then require a visible Play.
                    pause_controls = self._pause_media_controls(verified_observation)
                    if len(pause_controls) == 1:
                        recovery_arguments = {
                            "element_id": pause_controls[0]["id"],
                            "action": "keyboard_activate",
                            "snapshot_id": verified_observation.get("snapshot_id"),
                        }
                        recovery_result = registry.execute("web_act", recovery_arguments)
                        records.append(("web_act", recovery_arguments, recovery_result, None))
                        if recovery_result.get("ok"):
                            time.sleep(0.3)
                            final_observation = registry.execute("web_inspect", {})
                            records.append(("web_inspect", {}, final_observation, None))
                            if (
                                self._observation_matches_selected_media(final_observation, goal_state)
                                and self._has_play_media_control(final_observation)
                            ):
                                goal_state.mark_completed(operation)
                                step.reason = "visible_play_control_after_normalization"
        return records

    @classmethod
    def _episode_name_aliases(cls, requested):
        """Map common localized episode/chapter nouns while preserving ordinal."""
        normalized = cls._normalize_for_signature(requested)
        match = re.search(r"\b(\d+)\b", normalized)
        if not match:
            return []
        number = match.group(1)
        return [
            f"Episodio {number}",
            f"Capitulo {number}",
            f"Episode {number}",
            f"Chapter {number}",
        ]

    def _fast_play_media(self, registry, goal_state):
        """Verify playback or activate one explicit Play/Reproducir control."""
        step = goal_state.pending_step("play_media")
        if (
            not step
            or step.status != "pending"
            or not registry.has_tool("web_inspect")
            or not registry.has_tool("web_act")
        ):
            return []
        observation = None
        records = []
        # A result activation often creates the player document before its
        # controls and Windows media session are published.  Poll the same
        # selected page briefly; never click a control until both provider
        # identity and selected-media identity agree with the plan.
        for attempt in range(6):
            observation = registry.execute("web_inspect", {})
            records.append(("web_inspect", {}, observation, None))
            if (
                self._observation_matches_goal_page(observation, goal_state)
                and self._observation_matches_selected_media(observation, goal_state)
                and self._has_page_content(observation)
            ):
                break
            if attempt < 5:
                time.sleep(0.4)
        if not self._observation_matches_goal_page(observation, goal_state):
            return records
        if not self._observation_matches_selected_media(observation, goal_state):
            return records

        if self._use_windows_media_session(
            registry, goal_state, "play", observation, records
        ):
            return records

        if self._timeline_slider(observation) is not None:
            # A focused timeline is the authoritative state signal whenever
            # the provider exposes one.  Accessible Play/Pause labels in
            # Chromium can remain stale across both transitions.
            first_sample, state_observation = self._sample_focused_timeline(
                registry, observation, records
            )
            if not first_sample or not state_observation:
                return records
            if not self._observation_matches_goal_page(state_observation, goal_state):
                return records
            if not self._observation_matches_selected_media(state_observation, goal_state):
                return records
            if self._media_timeline_progressed(first_sample, state_observation):
                goal_state.confirm_media_timeline_progression()
                return records
            if not self._media_timeline_stable(first_sample, state_observation):
                return records

            controls = self._playback_toggle_controls(state_observation)
            if len(controls) != 1:
                return records
            arguments = {
                "element_id": controls[0]["id"],
                "action": "keyboard_activate",
                "snapshot_id": state_observation.get("snapshot_id"),
            }
            result = registry.execute("web_act", arguments)
            records.append(("web_act", arguments, result, None))
            if not result.get("ok"):
                return records
            time.sleep(0.3)
            confirmed_observation = registry.execute("web_inspect", {})
            records.append(("web_inspect", {}, confirmed_observation, None))
            confirmed_first, confirmed_final = self._sample_focused_timeline(
                registry, confirmed_observation, records
            )
            if (
                confirmed_first
                and confirmed_final
                and self._observation_matches_selected_media(confirmed_final, goal_state)
                and self._media_timeline_progressed(confirmed_first, confirmed_final)
            ):
                goal_state.confirm_media_timeline_progression()
                return records

            if not (
                confirmed_first
                and confirmed_final
                and self._media_timeline_stable(confirmed_first, confirmed_final)
            ):
                return records
            # One bounded retry is justified only after proving that the first
            # semantic activation left the same timeline stopped.
            retry_controls = self._playback_toggle_controls(confirmed_final)
            if len(retry_controls) != 1:
                return records
            retry_arguments = {
                "element_id": retry_controls[0]["id"],
                "action": "keyboard_activate",
                "snapshot_id": confirmed_final.get("snapshot_id"),
            }
            retry_result = registry.execute("web_act", retry_arguments)
            records.append(("web_act", retry_arguments, retry_result, None))
            if not retry_result.get("ok"):
                return records
            time.sleep(0.3)
            retry_observation = registry.execute("web_inspect", {})
            records.append(("web_inspect", {}, retry_observation, None))
            retry_first, retry_final = self._sample_focused_timeline(
                registry, retry_observation, records
            )
            if (
                retry_first
                and retry_final
                and self._observation_matches_selected_media(retry_final, goal_state)
                and self._media_timeline_progressed(retry_first, retry_final)
            ):
                goal_state.confirm_media_timeline_progression()
            return records

        pause_controls = self._pause_media_controls(observation)
        play_controls = [
            item for item in observation.get("elements", [])
            if item.get("role") in {"button", "link"}
            and self._is_play_media_control(item.get("name"))
            and not item.get("offscreen")
        ]
        initial_was_pause = len(pause_controls) == 1

        controls = pause_controls if initial_was_pause else play_controls
        if len(controls) != 1:
            return records
        activation = "click" if controls[0].get("role") == "link" else "keyboard_activate"
        arguments = {
            "element_id": controls[0]["id"],
            "action": activation,
            "snapshot_id": observation.get("snapshot_id"),
        }
        result = registry.execute("web_act", arguments)
        # Playback has its own verification below; a stale Pause label in the
        # following snapshot must not complete the goal by itself.
        records.append(("web_act", arguments, result, None))
        if result.get("ok"):
            time.sleep(0.3)
            confirmed_observation = registry.execute("web_inspect", {})
            records.append(("web_inspect", {}, confirmed_observation, None))
            if self._observation_matches_selected_media(confirmed_observation, goal_state):
                if self._pause_media_controls(confirmed_observation):
                    goal_state.mark_completed("play_media")
                    step.reason = "visible_pause_control_after_activation"
                elif initial_was_pause and self._has_play_media_control(confirmed_observation):
                    # The initial Pause label was current (already playing),
                    # so the first toggle paused it. Activate the now-explicit
                    # Play control once to restore/ensure the requested state.
                    recovery_controls = [
                        item for item in confirmed_observation.get("elements", [])
                        if item.get("role") in {"button", "link"}
                        and self._is_play_media_control(item.get("name"))
                        and not item.get("offscreen")
                    ]
                    if len(recovery_controls) == 1:
                        recovery_arguments = {
                            "element_id": recovery_controls[0]["id"],
                            "action": (
                                "click"
                                if recovery_controls[0].get("role") == "link"
                                else "keyboard_activate"
                            ),
                            "snapshot_id": confirmed_observation.get("snapshot_id"),
                        }
                        recovery_result = registry.execute("web_act", recovery_arguments)
                        records.append(("web_act", recovery_arguments, recovery_result, None))
                        if recovery_result.get("ok"):
                            time.sleep(0.3)
                            final_observation = registry.execute("web_inspect", {})
                            records.append(("web_inspect", {}, final_observation, None))
                            if (
                                self._observation_matches_selected_media(final_observation, goal_state)
                                and self._pause_media_controls(final_observation)
                            ):
                                goal_state.mark_completed("play_media")
                                step.reason = "visible_pause_control_after_normalization"
                elif self._playback_timeline_value(confirmed_observation):
                    # State-agnostic playback controls need measured progress.
                    first_sample, playing_observation = self._sample_focused_timeline(
                        registry, confirmed_observation, records
                    )
                    if (
                        first_sample
                        and playing_observation
                        and self._observation_matches_selected_media(playing_observation, goal_state)
                        and self._media_timeline_progressed(first_sample, playing_observation)
                    ):
                        goal_state.confirm_media_timeline_progression()
                elif (
                    not initial_was_pause
                    and activation == "click"
                    and result.get("activation_pattern") != "legacy_accessible"
                ):
                    # Some Chromium anchors advertise InvokePattern but do
                    # not honor it.  A fresh unchanged observation that still
                    # exposes the same Play/Resume link proves the first
                    # activation did not navigate, so one explicit legacy
                    # default-action retry cannot double-start new content.
                    remaining = [
                        item for item in confirmed_observation.get("elements", [])
                        if item.get("role") == "link"
                        and self._is_play_media_control(item.get("name"))
                        and not item.get("offscreen")
                    ]
                    if len(remaining) == 1:
                        retry_arguments = {
                            "element_id": remaining[0]["id"],
                            "action": "legacy_activate",
                            "snapshot_id": confirmed_observation.get("snapshot_id"),
                        }
                        retry_result = registry.execute("web_act", retry_arguments)
                        records.append(("web_act", retry_arguments, retry_result, None))
                        if retry_result.get("ok"):
                            time.sleep(0.5)
                            retry_observation = registry.execute("web_inspect", {})
                            records.append(("web_inspect", {}, retry_observation, None))
                            if (
                                self._observation_matches_selected_media(retry_observation, goal_state)
                                and self._pause_media_controls(retry_observation)
                            ):
                                goal_state.mark_completed("play_media")
                                step.reason = "visible_pause_control_after_legacy_activation"
                            elif self._use_windows_media_session(
                                registry, goal_state, "play", retry_observation, records
                            ):
                                return records
        return records

    def _observation_matches_selected_media(self, observation, goal_state):
        """Guard playback evidence against an unexpected video transition."""
        selected = goal_state.step("select_result")
        if not selected or selected.status != "completed" or not goal_state.page_result:
            return True
        episode = goal_state.step("select_episode")
        expected_title = (
            episode.value
            if episode and episode.status == "completed" and episode.value
            else goal_state.page_result
        )
        title = str((observation.get("page") or {}).get("title") or "")
        if not title:
            return True
        target_tokens = set(self._meaningful_result_tokens(goal_state.target))
        title_tokens = set(self._meaningful_result_tokens(title))
        # A generic provider title (for example just "Netflix") cannot
        # prove the identity of the current item, but is not contradictory.
        # A title with its own meaningful content must still match the result
        # selected in this plan before playback can be confirmed.
        informative_tokens = title_tokens - target_tokens - {"chrome", "google"}
        if not informative_tokens:
            return True
        if self._page_result_match_score(title, expected_title) is not None:
            return True
        # Video pages often shorten an already-selected card title (for
        # example omitting "Official Video" in the browser title).  Retain a
        # strict core-title overlap for that transition, while a wholly
        # different video still has no usable overlap and is rejected.
        result_tokens = set(self._meaningful_result_tokens(expected_title))
        if not result_tokens:
            return False
        matching = result_tokens & informative_tokens
        required = 1 if len(result_tokens) == 1 else max(2, (len(result_tokens) + 1) // 2)
        return len(matching) >= required

    @staticmethod
    def _is_play_media_control(name):
        """Recognise a provider's accessible Play label and shortcut suffix."""
        text = MochiAgentLoop._normalize_for_signature(name)
        if any(phrase in text for phrase in {
            "en el televisor", "on tv", "cast", "dispositivo",
            "reproduccion automatica", "autoplay",
        }):
            return False
        return bool(
            re.search(
                r"(?:^|\s)(?:reproducir|reproduce|reanudar|reanuda|resume|play)(?:\s|$)",
                text,
            )
            or re.search(r"\b(?:boton|button)\s+(?:de\s+)?(?:reproduccion|playback)\b", text)
        )

    @staticmethod
    def _has_state_agnostic_play_control(observation):
        """Whether the player exposes only a non-stateful playback button."""
        return any(
            item.get("role") in {"button", "link"}
            and bool(re.search(
                r"\b(?:boton|button)\s+(?:de\s+)?(?:reproduccion|playback)\b",
                MochiAgentLoop._normalize_for_signature(item.get("name")),
            ))
            and not item.get("offscreen")
            for item in observation.get("elements", [])
        )

    @staticmethod
    def _playback_timeline_value(observation):
        """Read one published timeline for a read-only playback check.

        Some Chromium players publish a correct spoken clock while leaving
        their RangeValue slider frozen. Prefer that explicit clock and use the
        non-volume slider as a fallback.
        """
        clock_values = set()
        for item in observation.get("elements", []):
            if item.get("offscreen"):
                continue
            text = MochiAgentLoop._normalize_for_signature(
                f"{item.get('name', '')} {item.get('value', '')}"
            )
            colon = re.search(
                r"(?<!\d)(?P<current>(?:\d+:)?\d{1,2}:\d{2})\s*"
                r"(?:/|de|of)\s*"
                r"(?P<duration>(?:\d+:)?\d{1,2}:\d{2})(?!\d)",
                text,
            )
            if colon:
                def colon_seconds(value):
                    parts = [int(part) for part in value.split(":")]
                    if len(parts) == 2:
                        return parts[0] * 60 + parts[1]
                    return parts[0] * 3600 + parts[1] * 60 + parts[2]

                current = colon_seconds(colon.group("current"))
                duration = colon_seconds(colon.group("duration"))
                if 0 <= current <= duration and duration > 0:
                    clock_values.add((0.0, float(duration), float(current)))
                    continue
            spoken = re.search(
                r"(?:(?P<ch>\d+)\s*(?:horas?|hours?)\s*)?"
                r"(?:(?P<cm>\d+)\s*(?:minutos?|minutes?)\s*)?"
                r"(?P<cs>\d+)\s*(?:segundos?|seconds?)\s+"
                r"(?:de|of)\s+"
                r"(?:(?P<dh>\d+)\s*(?:horas?|hours?)\s*)?"
                r"(?:(?P<dm>\d+)\s*(?:minutos?|minutes?)\s*)?"
                r"(?P<ds>\d+)\s*(?:segundos?|seconds?)",
                text,
            )
            if spoken:
                current = (
                    int(spoken.group("ch") or 0) * 3600
                    + int(spoken.group("cm") or 0) * 60
                    + int(spoken.group("cs") or 0)
                )
                duration = (
                    int(spoken.group("dh") or 0) * 3600
                    + int(spoken.group("dm") or 0) * 60
                    + int(spoken.group("ds") or 0)
                )
                if 0 <= current <= duration and duration > 0:
                    clock_values.add((0.0, float(duration), float(current)))
        if len(clock_values) == 1:
            return next(iter(clock_values))

        candidates = []
        for item in observation.get("elements", []):
            if item.get("role") != "slider" or item.get("offscreen"):
                continue
            label = MochiAgentLoop._normalize_for_signature(
                f"{item.get('name', '')} {item.get('value', '')}"
            )
            if any(word in label for word in {"volumen", "volume", "loudness"}):
                continue
            bounds = item.get("range") or {}
            try:
                minimum = float(bounds["minimum"])
                maximum = float(bounds["maximum"])
                value = float(bounds["value"])
            except (KeyError, TypeError, ValueError):
                continue
            if maximum > minimum:
                candidates.append((minimum, maximum, value))
        return candidates[0] if len(candidates) == 1 else None

    @classmethod
    def _media_timeline_progressed(cls, earlier, later):
        """Return true only for an observed forward change in the same range."""
        before = cls._playback_timeline_value(earlier)
        after = cls._playback_timeline_value(later)
        if not before or not after:
            return False
        before_minimum, before_maximum, before_value = before
        after_minimum, after_maximum, after_value = after
        return (
            abs(before_minimum - after_minimum) < 0.01
            and abs(before_maximum - after_maximum) < 0.01
            and after_value > before_value + 0.2
        )

    @classmethod
    def _media_timeline_stable(cls, earlier, later):
        """Return true when the same published timeline did not advance."""
        before = cls._playback_timeline_value(earlier)
        after = cls._playback_timeline_value(later)
        if not before or not after:
            return False
        before_minimum, before_maximum, before_value = before
        after_minimum, after_maximum, after_value = after
        return (
            abs(before_minimum - after_minimum) < 0.01
            and abs(before_maximum - after_maximum) < 0.01
            and abs(after_value - before_value) <= 0.2
        )

    @classmethod
    def _has_play_media_control(cls, observation):
        return any(
            item.get("role") in {"button", "link"}
            and not item.get("offscreen")
            and cls._is_play_media_control(item.get("name"))
            for item in observation.get("elements", [])
        )

    @classmethod
    def _pause_media_controls(cls, observation):
        return [
            item for item in observation.get("elements", [])
            if item.get("role") in {"button", "link"}
            and not item.get("offscreen")
            and re.search(
                r"(?:^|\s)(?:pausa|pausar|pause)(?:\s|$)",
                cls._normalize_for_signature(item.get("name")),
            )
        ]

    @classmethod
    def _playback_toggle_controls(cls, observation):
        """Return the single semantic transport toggle, never a card link."""
        pause_ids = {item.get("id") for item in cls._pause_media_controls(observation)}
        return [
            item for item in observation.get("elements", [])
            if item.get("role") == "button"
            and not item.get("offscreen")
            and (
                item.get("id") in pause_ids
                or cls._is_play_media_control(item.get("name"))
            )
        ]

    @staticmethod
    def _next_named_page_step(goal_state):
        """Return the one page action that is currently allowed to advance.

        The ordered media plan deliberately keeps selecting an episode,
        confirming playback and pausing as distinct operations.  This helper
        prevents a model-supplied ``web_act`` from skipping from an episode
        directly to Pause, and also supports a simple video request without
        an episode step.
        """
        supported = {
            "select_result", "select_channel", "select_content_tab", "select_sort",
            "select_season", "select_episode", "select_visible_video", "collect_results",
            "play_media", "pause_media", "seek_media", "adjust_media_position",
            "next_media", "previous_media", "adjust_volume",
        }
        for step in goal_state.steps:
            if step.status != "pending" or step.operation not in supported:
                continue
            dependency = goal_state.step(step.depends_on) if step.depends_on else None
            if not step.depends_on or (dependency and dependency.status == "completed"):
                return step
            if (
                step.operation == "pause_media"
                and dependency
                and str(dependency.reason or "").startswith("media_position_out_of_range:")
            ):
                return step
        return None

    @staticmethod
    def _next_named_page_operation(goal_state):
        step = MochiAgentLoop._next_named_page_step(goal_state)
        return step.operation if step else None

    def _fast_pending_media_operation(self, registry, goal_state):
        """Advance one already-authorized media step from fresh UIA evidence.

        Search, episode selection, playback and pause are separate verified
        states.  Once the post-action inspection has confirmed one of them,
        asking the model again only burns a turn and can hit the loop limit
        halfway through a valid sequence.  This helper advances exactly one
        dependency-respecting follow-up and returns no records when the next
        control is not yet exposed.  It is page-generic: it relies on the
        semantic controls observed through UI Automation, not a provider.
        """
        operation = self._next_named_page_operation(goal_state)
        if operation == "select_result":
            return operation, self._fast_delayed_result_selection(registry, goal_state)
        if operation in {
            "select_channel", "select_content_tab", "select_sort",
            "select_season", "select_episode", "pause_media",
        }:
            return operation, self._fast_named_page_action(registry, goal_state, operation)
        if operation == "select_visible_video":
            return operation, self._fast_visible_video_selection(registry, goal_state)
        if operation == "collect_results":
            return operation, self._fast_collect_results(registry, goal_state)
        if operation == "play_media":
            return operation, self._fast_play_media(registry, goal_state)
        if operation in {"seek_media", "adjust_media_position", "next_media", "previous_media"}:
            return operation, self._fast_player_navigation(registry, goal_state, operation)
        if operation == "adjust_volume":
            return operation, self._fast_adjust_volume(registry, goal_state)
        return None, []

    @staticmethod
    def _record_structured_records(records, step, mode, actions, messages, web_elements_by_snapshot, verbose):
        """Pasa las tools de un micro-plan a actions/messages; devuelve el último snapshot."""
        latest_snapshot_id = None
        for tool_name, arguments, tool_result, _kind in records:
            actions.append({
                "step": step,
                "mode": mode,
                "tool": tool_name,
                "arguments": arguments,
                "result": tool_result,
            })
            messages.append({
                "role": "tool",
                "tool_name": tool_name,
                "content": json.dumps(tool_result, ensure_ascii=False),
            })
            if tool_name == "web_inspect" and tool_result.get("snapshot_id"):
                latest_snapshot_id = tool_result["snapshot_id"]
                web_elements_by_snapshot[latest_snapshot_id] = {
                    item.get("id"): item
                    for item in tool_result.get("elements", [])
                    if item.get("id")
                }
            if verbose:
                print(f"Tool: {tool_name}")
                print(f"Argumentos: {arguments}")
                print("Resultado:")
                print(json.dumps(tool_result, indent=2, ensure_ascii=False))
        return latest_snapshot_id

    @staticmethod
    def _page_identity(observation):
        page = (observation or {}).get("page") or {}
        return page.get("url"), page.get("title")

    @staticmethod
    def _structured_state_is_terminal(goal_state):
        return (
            goal_state.status == "completed"
            or bool(goal_state.page_result_options)
            or any(
                step.status in {"blocked", "failed"}
                for step in goal_state.steps
            )
        )

    def _run_structured_web_microplan(self, registry, goal_state):
        """Execute a fully structured browser/page graph without an LLM turn.

        The plan still observes every live page transition through UIA.  Only
        the choice of the next *already-authorized* operation is deterministic,
        which removes both latency and the possibility that a model replaces
        an in-site search with Google or stops after merely opening the site.
        """
        records = []
        open_step = goal_state.step("browser_open")
        if open_step and open_step.status == "pending":
            if not registry.has_tool("browser_open"):
                open_step.status = "blocked"
                open_step.reason = "capability_missing"
                return records
            arguments = {"target": open_step.target or goal_state.target}
            if open_step.value:
                arguments["profile"] = open_step.value
            result = registry.execute("browser_open", arguments)
            records.append(("browser_open", arguments, result, "browser_open"))
            if result.get("ok"):
                goal_state.mark_completed("browser_open")
            else:
                goal_state.mark_failed(
                    "browser_open",
                    result.get("message") or result.get("error") or "browser_open_failed",
                )
                return records

        profile_step = goal_state.step("select_site_profile")
        if profile_step and profile_step.status == "pending":
            profile_records = self._fast_named_web_selection(registry, goal_state)
            records.extend(profile_records)
            for tool_name, _arguments, result, kind in profile_records:
                if tool_name == "web_act" and result.get("ok"):
                    if kind == "profile_menu_open":
                        goal_state.note_site_profile_menu_opening()
                    elif kind in {"select_profile", "select_site_profile"}:
                        goal_state.note_web_action("select_site_profile")
                elif tool_name == "web_inspect":
                    if goal_state.site_profile_menu_pending:
                        menu_has_target = any(
                            self._is_site_profile_choice(
                                item,
                                goal_state.site_profile,
                                allow_menu_button=True,
                            )
                            for item in result.get("elements", [])
                        )
                        goal_state.observe_site_profile_menu(menu_has_target)
                    goal_state.confirm_visible_site_profile(result)
                    goal_state.verify_web_observation(result)
            if profile_step.status != "completed":
                return records

        search_step = goal_state.step("search_page")
        page_search_observation = None
        if (
            goal_state.channel_name
            and search_step
            and search_step.status == "pending"
            and registry.has_tool("web_inspect")
        ):
            observation = registry.execute("web_inspect", {})
            records.append(("web_inspect", {}, observation, None))
            if self._observation_is_requested_channel(
                observation,
                goal_state.channel_name,
            ):
                goal_state.mark_completed("search_page")
                goal_state.mark_completed("select_channel")
            else:
                # Reuse this fresh provider snapshot as the starting point of
                # the in-page search.  Inspecting a second time here can land
                # on an autocomplete/result transition and lose the textbox
                # that was already usable in the first snapshot.
                page_search_observation = observation
        result_step = goal_state.step("select_result")
        if (
            search_step
            and search_step.status == "pending"
            and result_step
            and result_step.status == "pending"
            and registry.has_tool("web_inspect")
        ):
            observation = registry.execute("web_inspect", {})
            records.append(("web_inspect", {}, observation, None))
            observed_title = str((observation.get("page") or {}).get("title") or "")
            if (
                self._observation_matches_goal_page(observation, goal_state)
                and self._page_result_match_score(observed_title, goal_state.page_result) is not None
            ):
                goal_state.mark_completed("search_page")
                goal_state.mark_completed("select_result")
            elif page_search_observation is None:
                # Reuse the exact readiness snapshot for typing.  A second
                # eager inspection can already be the post-type snapshot in
                # a fast SPA, causing Mochi to skip the actual search field
                # or consume test/provider state out of order.
                page_search_observation = observation
        if search_step and search_step.status == "pending":
            records.extend(self._fast_page_search(
                registry,
                goal_state,
                initial_observation=page_search_observation,
            ))

        scheduled_step_ids = set()
        # Some helpers return an action followed by the observation that will
        # verify it.  Both records are already materialized, so do not launch
        # a second attempt merely because that later observation has not been
        # processed yet.  Associate each emitted action with the first
        # pending occurrence; repeated operations retain their distinct ids.
        for _tool, _arguments, _result, kind in records:
            if kind not in self._VERIFIED_PAGE_ACTIONS:
                continue
            represented = next(
                (
                    step for step in goal_state.steps
                    if step.operation == kind
                    and step.status == "pending"
                    and step.id not in scheduled_step_ids
                ),
                None,
            )
            if represented:
                scheduled_step_ids.add(represented.id)
        if not records:
            initial_step = self._next_named_page_step(goal_state)
            if initial_step:
                scheduled_step_ids.add(initial_step.id)
                _operation, initial_records = self._fast_pending_media_operation(
                    registry,
                    goal_state,
                )
                records.extend(initial_records)
        index = 0
        while index < len(records):
            tool_name, _arguments, result, kind = records[index]
            if tool_name == "web_act" and result.get("ok"):
                if kind in {"submit_search", "submit_refined_search"}:
                    goal_state.note_web_action("search_page")
                elif kind in self._VERIFIED_PAGE_ACTIONS:
                    goal_state.note_web_action(kind)
            elif tool_name == "web_inspect":
                goal_state.verify_web_observation(result)
            next_step = self._next_named_page_step(goal_state)
            if next_step and next_step.id not in scheduled_step_ids:
                scheduled_step_ids.add(next_step.id)
                _operation, follow_up = self._fast_pending_media_operation(
                    registry,
                    goal_state,
                )
                records.extend(follow_up)
            index += 1
        if (
            goal_state.goal == "search_in_page"
            and goal_state.step("search_page")
            and goal_state.step("search_page").status == "completed"
            and not goal_state.step("collect_results")
        ):
            for tool_name, _arguments, observation, _kind in reversed(records):
                if tool_name != "web_inspect" or not observation.get("ok"):
                    continue
                options = []
                seen = set()
                for item in self._visible_video_cards(observation, "visible"):
                    if self._visible_duration_card_key(item.get("name")) is None:
                        continue
                    name = str(item.get("name") or "").strip()
                    key = self._normalize_for_signature(name)
                    if not name or key in seen:
                        continue
                    seen.add(key)
                    options.append({"name": name})
                    if len(options) == 5:
                        break
                if options:
                    goal_state.record_page_result_options(options)
                break
        return records

    def _fast_delayed_result_selection(self, registry, goal_state):
        """Resume an exact result selection after a slow SPA publishes it."""
        step = goal_state.step("select_result")
        search_step = goal_state.step("search_page")
        if (
            not step
            or step.status != "pending"
            or not search_step
            or search_step.status != "completed"
            or not registry.has_tool("web_inspect")
            or not registry.has_tool("web_act")
        ):
            return []
        observation = registry.execute("web_inspect", {})
        records = [("web_inspect", {}, observation, None)]
        if not self._observation_matches_goal_page(observation, goal_state):
            return records
        selected = self._goal_named_page_result(observation, goal_state)
        if not selected:
            self._remember_ambiguous_page_results(goal_state, observation)
            return records
        selected = {**selected, "_snapshot_id": observation.get("snapshot_id")}
        if selected.get("offscreen"):
            selected = self._scroll_result_into_view(registry, goal_state, selected, records)
            if selected is None:
                return records
        arguments = {
            "element_id": selected["id"],
            "action": "click",
            "snapshot_id": selected.get("_snapshot_id") or observation.get("snapshot_id"),
        }
        result = registry.execute("web_act", arguments)
        records.append(("web_act", arguments, result, "select_result"))
        if result.get("ok"):
            time.sleep(0.25)
            records.append(("web_inspect", {}, registry.execute("web_inspect", {}), None))
        return records

    def _fast_site_profile_switch_from_observation(self, registry, goal_state, observation):
        """Open an evidenced current-profile menu and select the exact target.

        This short micro-plan is generic over semantic UIA roles.  It runs
        It opens only the button whose current-profile identity was supplied
        by the user or independently evidenced by the observed page.
        Each success is followed by a fresh observation; no assumed click is
        treated as goal success.
        """
        opener = self._declared_profile_menu_opener(observation, goal_state)
        if not opener or not registry.has_tool("web_inspect") or not registry.has_tool("web_act"):
            return []

        records = []
        opener_arguments = {
            "element_id": opener["id"],
            "action": "click",
            "snapshot_id": observation.get("snapshot_id"),
        }
        opener_result = registry.execute("web_act", opener_arguments)
        records.append(("web_act", opener_arguments, opener_result, "profile_menu_open"))
        if not opener_result.get("ok"):
            return records

        # Give the provider one short, bounded render interval, then make one
        # fresh observation.  If the requested option is absent we stop
        # rather than repeatedly probing a menu that cannot satisfy the goal.
        time.sleep(0.2)
        menu_observation = registry.execute("web_inspect", {})
        records.append(("web_inspect", {}, menu_observation, None))
        selected = next(
            (
                item for item in menu_observation.get("elements", [])
                if self._is_site_profile_choice(item, goal_state.site_profile, allow_menu_button=True)
            ),
            None,
        ) if menu_observation.get("ok") else None
        # If the old page is still exposed unchanged, the menu may simply be
        # one UIA refresh behind. Retry once before concluding that the target
        # is unavailable; a changed menu without the target stops immediately.
        if not selected and menu_observation.get("ok") and not menu_observation.get("changed"):
            time.sleep(0.25)
            menu_observation = registry.execute("web_inspect", {})
            records.append(("web_inspect", {}, menu_observation, None))
            selected = next(
                (
                    item for item in menu_observation.get("elements", [])
                    if self._is_site_profile_choice(item, goal_state.site_profile, allow_menu_button=True)
                ),
                None,
            ) if menu_observation.get("ok") else None

        if not selected:
            return records

        selection_arguments = {
            "element_id": selected["id"],
            "action": "click",
            "snapshot_id": menu_observation.get("snapshot_id"),
        }
        selection_result = registry.execute("web_act", selection_arguments)
        records.append(("web_act", selection_arguments, selection_result, "select_profile"))
        if selection_result.get("ok"):
            records.append(("web_inspect", {}, registry.execute("web_inspect", {}), None))
        return records

    def _fast_named_web_selection(self, registry, goal_state):
        """Execute the safe inspect → named action → verify micro-plan.

        This is not a site adapter: it only needs the target page name and the
        semantic name explicitly requested by the user.  It avoids two model
        round trips for the common, deterministic operation of selecting a
        named item after opening a site with a browser profile.
        """
        selection_step = goal_state.step("select_site_profile")
        if (
            not selection_step
            or selection_step.status != "pending"
            or not goal_state.target
            or not goal_state.site_profile
            or not registry.has_tool("web_inspect")
            or not registry.has_tool("web_act")
        ):
            return []

        observation = None
        selected = None
        active_profile_visible = False
        # Chrome may need a short moment to bring the new page to the front.
        # This bounded readiness check is still substantially faster than
        # waiting for another full model turn.
        for attempt in range(8):
            observation = registry.execute("web_inspect", {})
            if observation.get("ok") and self._page_matches_goal_target(observation.get("page"), goal_state.target):
                active_profile_visible = any(
                    item.get("role") == "button"
                    and self._matches_pending_site_profile(
                        item.get("name"), goal_state.site_profile
                    )
                    for item in observation.get("elements", [])
                )
                if active_profile_visible:
                    break
                # An explicit A→B transition has already provided the only
                # safe menu opener. Do not consume later menu snapshots while
                # searching for B before opening A.
                if self._declared_profile_menu_opener(observation, goal_state):
                    break
                selected = next(
                    (
                        item for item in observation.get("elements", [])
                        if self._is_site_profile_choice(item, goal_state.site_profile)
                    ),
                    None,
                )
                if selected:
                    break
                # A matching page with an accessibility tree is ready.  If
                # the requested profile is not a visible choice, hand this
                # exact observation to the safe current-profile menu path.
                # Polling again here can consume the menu snapshot before we
                # have actually opened that menu and then click a phantom
                # future state in tests or a stale provider tree in Chrome.
                if self._has_page_content(observation):
                    break
            if attempt < 7:
                time.sleep(0.5)

        records = [("web_inspect", {}, observation or {"ok": False, "error": "web_inspection_missing", "message": "No se pudo observar la página."}, None)]
        if not observation or not observation.get("ok"):
            return records
        if active_profile_visible:
            return records
        if not selected:
            return records + self._fast_site_profile_switch_from_observation(registry, goal_state, observation)

        action_arguments = {
            "element_id": selected["id"],
            "action": "click",
            "snapshot_id": observation.get("snapshot_id"),
        }
        action_result = registry.execute("web_act", action_arguments)
        records.append(("web_act", action_arguments, action_result, "select_profile"))
        if not action_result.get("ok"):
            return records

        # The action itself is not success evidence.  Always observe once more
        # before marking the named web goal complete.
        records.append(("web_inspect", {}, registry.execute("web_inspect", {}), None))
        return records


    # =====================================================
    # CREAR MENSAJE ASSISTANT
    # =====================================================

    def _assistant_message(
        self,
        result
    ):

        message = {

            "role":
                "assistant",

            "content":
                result.text or ""
        }


        if result.tool_calls:

            message[
                "tool_calls"
            ] = result.tool_calls


        return message

    @staticmethod
    def _tool_completion_text(tool_name, tool_result):
        """Render common local results without asking the model a second time."""

        message = str(tool_result.get("message") or "")

        if tool_name == "list_games":
            games = tool_result.get("games") or []
            if not games:
                return "No encontré juegos instalados compatibles."
            return "Juegos instalados:\n" + "\n".join(
                f"{index}. {game}"
                for index, game in enumerate(games, start=1)
            )

        if tool_name == "list_installed_applications":
            applications = tool_result.get("applications") or []
            if not applications:
                return "No encontré aplicaciones instaladas con ese criterio."
            return "Aplicaciones instaladas:\n" + "\n".join(
                f"{index}. {application}"
                for index, application in enumerate(applications, start=1)
            )

        if tool_name == "list_user_folders":
            folders = tool_result.get("folders") or {}
            if not folders:
                return "No encontré carpetas conocidas del usuario."
            return "Carpetas conocidas:\n" + "\n".join(
                f"- {name}: {path}"
                for name, path in folders.items()
            )

        if tool_name in {"find_files", "find_folders"}:
            results = tool_result.get("results") or []
            if not results:
                return message
            return message + "\n" + "\n".join(
                f"{index}. {item.get('name')}"
                for index, item in enumerate(results, start=1)
            )

        if tool_name == "list_open_windows":
            windows = tool_result.get("windows") or []
            if not windows:
                return "No hay ventanas visibles abiertas."
            return "Ventanas abiertas:\n" + "\n".join(
                f"{index}. {item.get('title') or item.get('process')}"
                for index, item in enumerate(windows, start=1)
            )

        if tool_name == "browser_profiles":
            profiles = tool_result.get("profiles") or []
            if profiles:
                return "Perfiles de Chrome:\n" + "\n".join(
                    f"{index}. {item.get('name') or item.get('id')}"
                    for index, item in enumerate(profiles, start=1)
                )
            profile = tool_result.get("profile") or {}
            if profile:
                return f"Perfil activo de Chrome: {profile.get('name') or profile.get('id')}."

        if tool_name == "launch_game":
            game = tool_result.get("game")
            launcher = tool_result.get("launcher")
            if game and launcher:
                ending = "" if str(game).endswith((".", "!", "?")) else "."
                return f"Envié a {launcher.capitalize()} la orden de iniciar {game}{ending}"

        if tool_name == "open_target":
            resolution = tool_result.get("resolution")
            if resolution == "game":
                game = tool_result.get("game")
                launcher = tool_result.get("launcher")
                if game and launcher:
                    ending = "" if str(game).endswith((".", "!", "?")) else "."
                    return f"Envié a {launcher.capitalize()} la orden de iniciar {game}{ending}"
            if resolution == "application" and tool_result.get("application"):
                return f"Envié al sistema la orden de abrir {tool_result['application']}."
            if resolution == "web":
                return f"Listo, abrí {tool_result.get('resolved_target') or 'el destino solicitado'}."

        if tool_name == "read_clipboard":
            return "Portapapeles:\n" + str(tool_result.get("text") or "")

        if tool_name == "capture_screenshot":
            path = tool_result.get("path")
            if path:
                return f"Captura de pantalla guardada en: {path}"

        if tool_name == "get_system_info":
            info = tool_result.get("system") or {}
            os_info = info.get("os") or {}
            cpu = info.get("cpu") or {}
            ram = info.get("ram") or {}
            gpu = info.get("gpu") or []
            if os_info or cpu or ram or gpu:
                lines = []
                if os_info:
                    lines.append(
                        "Sistema: "
                        f"{os_info.get('system', 'desconocido')} "
                        f"{os_info.get('release', '')}".strip()
                    )
                if cpu:
                    lines.append(
                        "CPU: "
                        f"{cpu.get('name', 'desconocida')} "
                        f"({cpu.get('physical_cores', '?')} núcleos / "
                        f"{cpu.get('logical_cores', '?')} hilos)"
                    )
                if ram:
                    lines.append(
                        "RAM: "
                        f"{ram.get('total_gb', '?')} GB total, "
                        f"{ram.get('available_gb', '?')} GB disponible"
                    )
                if gpu:
                    first_gpu = gpu[0] or {}
                    lines.append(
                        "GPU: "
                        f"{first_gpu.get('name', 'desconocida')} "
                        f"({first_gpu.get('vram_gb', '?')} GB VRAM)"
                    )
                return "\n".join(lines)

        return message

    @staticmethod
    def _direct_step_arguments(step):
        """Translate a constrained GoalStep into one public tool call.

        Returning ``None`` means that language parsing did not recover every
        required argument.  In that case the normal agent gets a chance to
        ask for clarification instead of executing an invented value.
        """

        no_arguments = {
            "get_system_volume", "read_clipboard", "capture_screenshot",
            "list_user_folders", "open_latest_download",
            "list_installed_applications", "list_games", "get_system_info",
            "list_open_windows",
        }
        if step.operation in no_arguments:
            return {}

        one_target = {
            "find_files": "query",
            "find_folders": "query",
            "open_known_folder": "folder",
            "open_file": "query",
            "open_folder": "query",
            "launch_game": "game",
            "open_application": "application",
            "browser_open": "target",
            "browser_search": "query",
            "close_window": "window",
            "close_application_windows": "application",
        }
        argument_name = one_target.get(step.operation)
        if argument_name:
            if not step.target:
                return None
            arguments = {argument_name: step.target}
            if step.operation == "browser_open" and step.value:
                arguments["profile"] = step.value
            return arguments

        if step.operation == "set_system_volume":
            if step.target is None:
                return None
            try:
                return {"percent": float(step.target)}
            except (TypeError, ValueError):
                return None
        if step.operation == "control_media":
            if not step.target:
                return None
            arguments = {"action": step.target}
            if step.value is not None:
                try:
                    arguments["amount"] = float(step.value)
                except (TypeError, ValueError):
                    return None
            return arguments
        if step.operation == "write_clipboard":
            return {"text": step.target} if step.target else None
        if step.operation == "show_notification":
            if not step.target or not step.value:
                return None
            return {"title": step.target, "message": step.value}
        if step.operation == "browser_profiles":
            return {"action": step.target or "list"}
        if step.operation == "control_window":
            if not step.target or not step.value:
                return None
            return {"window": step.target, "action": step.value}
        return None

    @classmethod
    def _is_direct_deterministic_plan(cls, goal_state, registry=None):
        """Whether every step can be run without speculative page actions."""

        if not goal_state.steps:
            return False
        # open_target has extra ambiguity/goal-completion handling below.
        if any(step.operation == "open_target" for step in goal_state.steps):
            return False
        if any(step.scope == "web_page" for step in goal_state.steps):
            return False
        if registry is not None and any(
            not registry.has_tool(step.operation)
            for step in goal_state.steps
        ):
            return False
        return all(cls._direct_step_arguments(step) is not None for step in goal_state.steps)

    @staticmethod
    def _goal_summary(goal_state, fallback_text):
        """El resultado final representa el objetivo, no solo la última tool."""
        if not goal_state or not goal_state.steps:
            return fallback_text or ""
        goal_state.finalize()
        open_step = goal_state.step("browser_open")
        site_step = goal_state.step("select_site_profile")
        search_step = goal_state.step("browser_search")
        page_search_step = goal_state.step("search_page")
        result_step = goal_state.step("select_result")
        episode_step = goal_state.step("select_episode")
        play_step = goal_state.step("play_media")
        pause_step = goal_state.step("pause_media")
        season_step = goal_state.step("select_season")
        visible_video_step = goal_state.step("select_visible_video")
        player_step = next(
            (
                step for step in goal_state.steps
                if step.operation in {"seek_media", "adjust_media_position", "next_media", "previous_media"}
            ),
            None,
        )
        if goal_state.goal == "missing_pending_page_choice":
            return "No tengo una lista reciente de opciones para interpretar esa selección. Pedime buscar o mostrar las opciones primero."
        if goal_state.goal == "open_channel":
            channel_step = goal_state.step("select_channel")
            if channel_step and channel_step.status == "completed":
                return f"Listo, abrí el canal de {goal_state.channel_name}."
            if goal_state.page_result_options:
                rendered = "\n".join(
                    f"{number}. {option.get('name')}"
                    for number, option in enumerate(
                        (option for option in goal_state.page_result_options if option.get("name")),
                        start=1,
                    )
                )
                return f"Encontré varios canales posibles para {goal_state.channel_name}:\n{rendered}\n\nIndicame cuál querés."
            return f"Busqué {goal_state.channel_name}, pero no pude confirmar un único canal accesible con ese nombre."
        if goal_state.goal == "channel_video":
            if visible_video_step and visible_video_step.status == "completed" and play_step and play_step.status == "completed":
                order = "más antiguo" if goal_state.result_order == "oldest" else "más reciente"
                rank = str(visible_video_step.value or "").rpartition(":")[2]
                if goal_state.result_order != "oldest" and rank in {"2", "3"}:
                    order = "penúltimo" if rank == "2" else "antepenúltimo"
                elif rank.isdigit() and int(rank) > 1:
                    order = f"{rank}.º {order}"
                return f"Listo, abrí el video {order} del canal {goal_state.channel_name} y confirmé la reproducción."
            if goal_state.page_result_options:
                rendered = "\n".join(
                    f"{number}. {option.get('name')}"
                    for number, option in enumerate(
                        (option for option in goal_state.page_result_options if option.get("name")),
                        start=1,
                    )
                )
                return f"Encontré varios canales posibles para {goal_state.channel_name}:\n{rendered}\n\nIndicame cuál querés."
            return (
                f"Abrí YouTube y busqué el canal {goal_state.channel_name}, pero no pude confirmar "
                "toda la navegación, el orden solicitado y la reproducción."
            )
        if goal_state.goal == "search_and_play_any_result":
            if visible_video_step and visible_video_step.status == "completed" and play_step and play_step.status == "completed":
                return f"Listo, busqué {goal_state.page_query} dentro de {goal_state.target} y confirmé la reproducción del primer resultado multimedia accesible."
            return (
                f"Busqué {goal_state.page_query} dentro de {goal_state.target}, pero no pude confirmar "
                "un resultado multimedia reproducible sin confundirlo con navegación o publicidad."
            )
        if (
            goal_state.goal == "search_in_page"
            and page_search_step
            and page_search_step.status == "completed"
            and goal_state.page_result_options
        ):
            rendered = "\n".join(
                f"- {option.get('name')}"
                for option in goal_state.page_result_options
                if option.get("name")
            )
            return (
                f"Busqué {goal_state.page_query} dentro de {goal_state.target}. "
                "Estos son los primeros resultados multimedia accesibles:\n"
                + rendered
            )
        if goal_state.goal == "select_visible_video" and visible_video_step:
            if visible_video_step.reason and visible_video_step.reason.startswith("visible_video_index_out_of_range:"):
                count = visible_video_step.reason.rsplit(":", 1)[-1]
                return (
                    f"La página solo expone {count} tarjetas de video seleccionables en esa vista; "
                    "no elegí un enlace distinto a ciegas."
                )
            if visible_video_step.status == "completed" and play_step and play_step.status == "completed":
                return "Listo, seleccioné el video solicitado de la vista actual y confirmé que empezó la reproducción."
            if visible_video_step.status == "completed" and pause_step and pause_step.status == "completed":
                return "Listo, seleccioné el video solicitado de la vista actual y confirmé que quedó pausado."
            if visible_video_step.status == "completed":
                return "Seleccioné el video solicitado, pero no pude confirmar la reproducción con los controles accesibles actuales."
            return (
                "No pude identificar tarjetas de video accesibles suficientes en la vista actual; "
                "no conté enlaces de navegación o canales como si fueran videos."
            )
        if goal_state.goal == "continue_pending_page_choice" and result_step:
            if result_step.reason == "choice_index_out_of_range":
                count = len(goal_state.page_result_options)
                return f"La selección anterior solo tiene {count} opciones; indicame un número entre 1 y {count}."
            if result_step.reason in {"choice_context_page_changed", "choice_context_not_visible"}:
                return (
                    "La lista anterior ya no está visible en la pestaña actual. "
                    "Pedime buscar nuevamente para mostrarte opciones actualizadas."
                )
            if result_step.reason == "selected_choice_not_unique":
                return "La opción elegida sigue siendo ambigua en la página actual; indicame autor, fecha o título más preciso."
            if result_step.status == "completed" and play_step and play_step.status == "completed":
                return f"Listo, abrí {result_step.value} y confirmé que empezó la reproducción."
            if result_step.status == "completed":
                return f"Listo, abrí {result_step.value}."
        local_file_search_step = (
            goal_state.step("find_files")
            or goal_state.step("find_folders")
        )
        local_open_step = (
            goal_state.step("open_file")
            or goal_state.step("open_folder")
            or goal_state.step("open_known_folder")
            or goal_state.step("open_latest_download")
        )
        if open_step and open_step.status == "failed":
            return open_step.reason or "No pude abrir el destino solicitado."
        if site_step and site_step.status == "blocked":
            if site_step.reason == "profile_selection_not_available":
                if open_step and open_step.status == "completed":
                    profile = f" con el perfil de navegador {goal_state.browser_profile}" if goal_state.browser_profile else ""
                    return f"Abrí {goal_state.target}{profile}, pero el menú de perfil actual no ofrece una opción seleccionable llamada {goal_state.site_profile}."
                return f"No encontré una opción seleccionable llamada {goal_state.site_profile} en el menú de perfil actual."
            if open_step and open_step.status == "completed":
                profile = f" con el perfil de navegador {goal_state.browser_profile}" if goal_state.browser_profile else ""
                return f"Abrí {goal_state.target}{profile}, pero todavía no puedo seleccionar el perfil {goal_state.site_profile} dentro de {goal_state.target}."
            return f"Entendí que querés usar el perfil {goal_state.site_profile} dentro de {goal_state.target}, pero esa interacción web todavía no está disponible."
        if result_step and result_step.reason == "content_not_found":
            return (
                f"Busqué {goal_state.page_query or result_step.value} dentro de "
                f"{goal_state.target or 'la pestaña actual'}, pero la página indicó que no hay resultados."
            )
        if (
            page_search_step and page_search_step.status == "completed"
            and result_step and result_step.status != "completed"
            and goal_state.page_result_options
        ):
            rendered = "\n".join(
                f"- {option.get('name')}"
                for option in goal_state.page_result_options
                if option.get("name")
            )
            return (
                f"Encontré varias opciones para {result_step.value}. "
                "Indicame cuál querés por autor, fecha, edición o título completo:\n"
                + rendered
            )
        if (
            open_step and open_step.status == "completed"
            and site_step and site_step.status == "completed"
            and not page_search_step and not episode_step and not player_step
        ):
            browser_profile = f" con el perfil de navegador {goal_state.browser_profile}" if goal_state.browser_profile else ""
            return f"Listo, abrí {goal_state.target}{browser_profile} y confirmé que el perfil {goal_state.site_profile} está activo dentro de {goal_state.target}."
        if (
            open_step and open_step.status == "completed"
            and episode_step and episode_step.status == "completed"
            and pause_step and pause_step.status == "completed"
        ):
            season = f" de {season_step.value}" if season_step and season_step.status == "completed" else ""
            return f"Listo, abrí {goal_state.target}, seleccioné {episode_step.value}{season} y lo pausé."
        if (
            player_step and player_step.status == "completed"
            and play_step and play_step.status == "completed"
        ):
            if pause_step and pause_step.status == "completed":
                return "Ajusté la posición solicitada, confirmé la reproducción y dejé el contenido pausado."
            return "Ajusté la posición solicitada y confirmé que el contenido quedó reproduciéndose."
        if player_step and player_step.status == "completed":
            messages = {
                "seek_media": "Ajusté la posición solicitada del reproductor.",
                "adjust_media_position": "Ajusté la posición relativa solicitada del reproductor.",
                "next_media": "Pasé al siguiente elemento del reproductor.",
                "previous_media": "Volví al elemento anterior del reproductor.",
            }
            prefix = f"En {goal_state.target}, " if goal_state.target else ""
            return prefix + messages[player_step.operation]
        if player_step and str(player_step.reason or "").startswith("media_position_out_of_range:"):
            try:
                _code, requested, duration = player_step.reason.split(":", 2)
                requested = float(requested)
                duration = float(duration)
                final_state = (
                    " Lo dejé pausado."
                    if pause_step and pause_step.status == "completed"
                    else ""
                )
                return (
                    f"Abrí el contenido solicitado, pero no pude ir a {requested:g} segundos: "
                    f"su duración publicada es de {duration:g} segundos.{final_state}"
                )
            except (TypeError, ValueError):
                return "La posición solicitada queda fuera de la duración publicada por el contenido; no ejecuté las acciones posteriores."
        if pause_step and pause_step.status == "completed" and not episode_step:
            return "Listo, pausé el contenido del reproductor activo."
        if play_step and play_step.status == "completed" and not result_step and not episode_step:
            return "Listo, reanudé la reproducción del contenido activo."
        if player_step and player_step.status != "completed":
            return (
                "No pude confirmar esa navegación del reproductor con los controles accesibles "
                "actuales; no cambié la posición ni el contenido a ciegas."
            )
        if play_step and play_step.status != "completed" and result_step and result_step.status == "completed":
            return (
                f"Abrí {result_step.value} dentro de {goal_state.target or 'la pestaña actual'}, "
                "pero no pude confirmar que la reproducción haya comenzado con los controles accesibles actuales."
            )
        if play_step and play_step.status == "completed" and result_step and result_step.status == "completed":
            if open_step and open_step.status == "completed":
                return f"Listo, busqué {result_step.value} dentro de {goal_state.target} y confirmé que empezó la reproducción."
            return f"Listo, cambié al video {result_step.value} y confirmé que empezó la reproducción."
        if site_step and site_step.status == "completed":
            return f"Listo, confirmé que el perfil {goal_state.site_profile} está activo dentro de {goal_state.target}."
        if episode_step and episode_step.status != "completed":
            return f"Abrí {goal_state.target}, pero no pude seleccionar {episode_step.value} con los controles accesibles actuales."
        if pause_step and pause_step.status != "completed":
            return f"Seleccioné {episode_step.value if episode_step else 'el contenido solicitado'}, pero no pude confirmar una pausa mediante los controles accesibles actuales."
        if (
            open_step and open_step.status == "completed"
            and page_search_step and page_search_step.status == "completed"
            and result_step and result_step.status == "completed"
        ):
            return f"Listo, busqué {result_step.value} junto con {page_search_step.value} dentro de {goal_state.target} y abrí {result_step.value}."
        if (
            not open_step
            and page_search_step and page_search_step.status == "completed"
            and result_step and result_step.status == "completed"
        ):
            return f"Listo, cambié al video {result_step.value} en la pestaña actual."
        if page_search_step and page_search_step.status == "completed" and result_step and result_step.status != "completed":
            options = goal_state.page_result_options or []
            if options:
                rendered = "\n".join(f"- {option.get('name')}" for option in options if option.get("name"))
                return (
                    f"Encontré varias opciones para {result_step.value}. "
                    "Indicame cuál querés por autor, fecha o título completo:\n"
                    + rendered
                )
            location = goal_state.target or "la pestaña actual"
            return f"Busqué {page_search_step.value} dentro de {location}, pero no pude identificar de forma segura {result_step.value} entre los resultados accesibles."
        if open_step and open_step.status == "completed" and page_search_step and page_search_step.status == "completed":
            return f"Listo, busqué {page_search_step.value} dentro de {goal_state.target}."
        if page_search_step and page_search_step.status == "completed":
            return f"Listo, busqué {page_search_step.value} dentro de {goal_state.target or 'la pestaña actual'}."
        if local_file_search_step and local_file_search_step.status == "completed":
            return fallback_text or f"Completé la búsqueda local de {local_file_search_step.target}."
        if local_open_step and local_open_step.status == "completed":
            return f"Envié al sistema la orden de abrir {local_open_step.target}."
        if open_step and open_step.status == "completed" and page_search_step:
            return (
                f"Abrí {goal_state.target}, pero la página todavía no expuso "
                "controles web accesibles para iniciar la búsqueda."
            )
        if open_step and open_step.status == "completed":
            profile = f" con el perfil {goal_state.browser_profile}" if goal_state.browser_profile else ""
            return f"Listo, abrí {goal_state.target}{profile}."
        if search_step and search_step.status == "completed":
            return f"Listo, busqué {search_step.target}."
        # "Subí el volumen" con una página abierta entra por el plan web:
        # el volumen es el del sistema y ya quedó aplicado (o no).
        volume_step = goal_state.step("adjust_volume")
        if volume_step:
            try:
                louder = float(volume_step.value or 0) > 0
            except (TypeError, ValueError):
                louder = True
            if volume_step.status == "completed":
                return "Listo, subí el volumen." if louder else "Listo, bajé el volumen."
            return "No pude cambiar el volumen."
        return fallback_text or "No pude completar la acción solicitada."

    @staticmethod
    def _final_goal_result(goal_state, fallback_text):
        """Cierra todos los pasos pendientes y entrega resultado de objetivo."""
        goal_state.finalize()
        return {
            "status": goal_state.status,
            "text": MochiAgentLoop._goal_summary(goal_state, fallback_text),
            "goal_state": goal_state.compact(),
        }


    # =====================================================
    # EJECUTAR AGENT LOOP
    # =====================================================

    def run(
        self,
        user_command,
        registry,
        mode="auto",
        verbose=False,
        reply_hint=None,
        history=None
    ):

        # history: la conversación anterior (conversation_memory),
        # como mensajes user/assistant. Va entre el sistema y la
        # orden: así llama-server reusa la caché de todo lo previo.
        user_message = {

            "role":
                "user",

            "content":
                user_message_content(
                    user_command,
                    reply_hint
                )
        }

        messages = [

            {
                "role":
                    "system",

                "content":
                    AGENT_SYSTEM_PROMPT
            },

            *list(
                history
                or []
            ),

            user_message
        ]


        actions = []
        failed_signatures = set()
        latest_web_snapshot_id = None
        web_elements_by_snapshot = {}
        site_profile_recovery_inspection_used = False
        rejected_named_result_action = False
        goal_state = plan_goal(
            user_command,
            context_provider=self._context_provider,
        )
        if str(goal_state.target or "").casefold() in {"youtube", "netflix"}:
            self._context_provider = (
                "YouTube"
                if str(goal_state.target).casefold() == "youtube"
                else "Netflix"
            )
        requested_choice = self._requested_option_index(user_command)
        if (
            requested_choice
            and goal_state.goal == "select_visible_video"
            and not self._active_page_is_site(registry, goal_state.target)
        ):
            # El sitio de la orden anterior (YouTube) no vale si ahora la
            # página activa es otra (una búsqueda de Google).
            goal_state = plan_goal(user_command)
        pending_choice = self.pending_choice_cache.current()
        if pending_choice and not requested_choice:
            # "elrubius z" después de "Encontré varios canales...": la
            # respuesta nombra una de las opciones en vez de numerarla.
            named_choice = self._named_reply_option_index(
                user_command, pending_choice.get("options") or []
            )
            if named_choice and self._pending_choice_on_active_page(pending_choice, registry):
                goal_state = self._goal_from_pending_choice(
                    pending_choice, named_choice, user_command,
                )
        explicit_prior_list_reference = bool(re.search(
            r"\b(?:opcion|opciones|resultado|resultados|lista)\b",
            self._normalize_for_signature(user_command),
        ))
        open_result_index = None
        if (
            requested_choice
            and pending_choice
            and (
                goal_state.goal != "select_visible_video"
                or explicit_prior_list_reference
            )
            and self._pending_choice_on_active_page(pending_choice, registry)
        ):
            goal_state = self._goal_from_pending_choice(
                pending_choice,
                requested_choice,
                user_command,
                continuation=goal_state,
            )
        elif requested_choice and (
            goal_state.goal != "select_visible_video"
            or explicit_prior_list_reference
        ):
            # A bare ordinal must never be misrouted to open_target or a web
            # search.  Sin una lista que Mochi haya mostrado, el referente
            # son los resultados de la página activa (Google, YouTube...).
            open_result_index = requested_choice
            goal_state = GoalState(goal="missing_pending_page_choice")
            goal_state.steps.append(
                GoalStep(
                    "select_result",
                    "web_page",
                    "select_result",
                    status="blocked",
                    reason="choice_context_missing",
                )
            )
        if goal_state.goal == "user_request" and not goal_state.steps:
            # "lo más nuevo del rubius", "poné el penúltimo" (el canal de la
            # orden anterior): el modelo dice canal, orden y puesto, y se
            # arma el mismo plan que con las reglas.
            channel_request = interpret_channel_video(
                self.router,
                user_command,
                messages,
                registry.get_tool_schemas() + [self.router._deep_request_tool()],
                context_provider=self._context_provider,
            ) if hasattr(self.router, "_deep_request_tool") else None
            if channel_request:
                goal_state = _plan_specialized_web_request(channel_request)
                self._context_provider = "YouTube"
                if verbose:
                    print(
                        f"[intención] video del canal {channel_request.channel}: "
                        f"{channel_request.order} #{channel_request.result_index}"
                    )
        if goal_state.goal == "user_request" and not goal_state.steps:
            # Volume/music sentences the strict rules could not explain
            # ("bajale un toque al volumen", "a cero baja") are read by the
            # model as a validated JSON intent, then run like a rule match.
            interpreted = interpret_local_command(self.router, user_command)
            if interpreted:
                operation, target, value = interpreted
                goal_state = GoalState(goal="local_direct_action", target=target)
                goal_state.steps.append(
                    GoalStep(
                        "local_direct",
                        "local_system",
                        operation,
                        target=target,
                        value=value,
                    )
                )
        effective_mode = mode
        router_detects_complexity = getattr(self.router, "should_use_deep_for", lambda _command: False)
        if mode == "auto" and (
            (goal_state.goal == "site_context_action" and goal_state.site_profile_source)
            or goal_state.goal in {"search_in_page", "search_and_open_result", "change_active_video"}
        ):
            # The concrete page transition is guarded by the deterministic
            # UIA micro-plan below; no chain-of-thought model turn is needed
            # just to choose a button whose name was supplied by the user.
            effective_mode = "fast"
        elif mode == "auto" and (goal_state.complex or router_detects_complexity(user_command)):
            effective_mode = "deep"

        # Al final de la orden y no en el sistema: si cambiara el
        # sistema, llama-server tendría que reprocesar todo el
        # historial de la conversación.
        if goal_state.steps:
            user_message["content"] += (
                "\n\nEstado estructurado del objetivo (no alteres roles entre scopes): "
                + json.dumps(goal_state.compact(), ensure_ascii=False)
                + "\nLos datos browser_profile solo son válidos para browser_open.profile. "
                "Los datos site_profile pertenecen a la página y no deben buscarse ni pasarse como perfil de navegador."
            )

        if goal_state.goal == "missing_pending_page_choice" and open_result_index and registry.has_tool("web_open_result"):
            arguments = {"position": open_result_index}
            if verbose:
                print("")
                print("PASO 1")
                print("Modo: deterministic_page_result")
                print("Tool: web_open_result")
                print(f"Argumentos: {arguments}")
            result = registry.execute("web_open_result", arguments)
            if verbose:
                print("Resultado:")
                print(json.dumps(result, indent=2, ensure_ascii=False))
            actions.append({
                "step": 1,
                "mode": "deterministic_page_result",
                "tool": "web_open_result",
                "arguments": arguments,
                "result": result,
            })
            messages.append({
                "role": "tool",
                "tool_name": "web_open_result",
                "content": json.dumps(result, ensure_ascii=False),
            })
            completed = bool(result.get("ok") and result.get("goal_completed"))
            return {
                "ok": True,
                "text": str(result.get("message") or ("Listo." if completed else "No pude abrir ese resultado.")),
                "status": "completed" if completed else "failed",
                "steps": 1,
                "actions": actions,
                "goal_state": {"goal": "open_page_result", "status": "completed" if completed else "failed"},
                "messages": messages,
                "final_mode": "deterministic_page_result",
            }

        if goal_state.goal == "missing_pending_page_choice":
            final_goal = self._final_goal_result(goal_state, "")
            return {
                "ok": True,
                "text": final_goal["text"],
                "status": final_goal["status"],
                "steps": 0,
                "actions": actions,
                "goal_state": final_goal["goal_state"],
                "messages": messages,
                "final_mode": "missing_pending_page_choice",
            }

        # Fully constrained local/browser operations do not need a language
        # model turn.  This is both faster and more reliable: a real runtime
        # check showed that the model could answer "I cannot read volume"
        # even though get_system_volume was registered and working.
        if self._is_direct_deterministic_plan(goal_state, registry):
            if verbose:
                print("")
                print("PASO 1")
                print("Modo: deterministic_capability_plan")

            last_tool = None
            last_result = {}
            for planned_step in goal_state.steps:
                dependency = (
                    next(
                        (
                            candidate for candidate in goal_state.steps
                            if candidate.operation == planned_step.depends_on
                            or candidate.id == planned_step.depends_on
                        ),
                        None,
                    )
                    if planned_step.depends_on
                    else None
                )
                if dependency and dependency.status != "completed":
                    planned_step.status = "blocked"
                    planned_step.reason = "dependency_not_completed"
                    break

                arguments = self._direct_step_arguments(planned_step)
                tool_result = registry.execute(planned_step.operation, arguments)

                # A freshly launched application can need a short moment to
                # create its first top-level window.  Retry only the dependent
                # window operation, with a small bounded wait.
                if (
                    not tool_result.get("ok")
                    and planned_step.operation == "control_window"
                    and dependency
                    and dependency.operation == "open_application"
                ):
                    for _attempt in range(4):
                        time.sleep(0.2)
                        tool_result = registry.execute(planned_step.operation, arguments)
                        if tool_result.get("ok"):
                            break

                actions.append({
                    "step": len(actions) + 1,
                    "mode": "deterministic_capability_plan",
                    "tool": planned_step.operation,
                    "arguments": arguments,
                    "result": tool_result,
                })
                messages.append({
                    "role": "tool",
                    "tool_name": planned_step.operation,
                    "content": json.dumps(tool_result, ensure_ascii=False),
                })
                if verbose:
                    print(f"Tool: {planned_step.operation}")
                    print(f"Argumentos: {arguments}")
                    print("Resultado:")
                    print(json.dumps(tool_result, indent=2, ensure_ascii=False))

                last_tool = planned_step.operation
                last_result = tool_result
                if tool_result.get("ok"):
                    if (
                        planned_step.operation == "control_media"
                        and tool_result.get("state_confirmed") is False
                    ):
                        planned_step.status = "failed"
                        planned_step.reason = "action_dispatched_unverified"
                    else:
                        goal_state.mark_completed(planned_step.operation)
                else:
                    goal_state.mark_failed(
                        planned_step.operation,
                        tool_result.get("message") or tool_result.get("error"),
                    )
                    break

            fallback = (
                self._tool_completion_text(last_tool, last_result)
                if last_tool
                else ""
            )
            if (
                last_tool == "control_media"
                and last_result.get("ok")
                and last_result.get("state_confirmed") is False
            ):
                fallback = (
                    f"{fallback} La orden fue enviada, pero el sistema no expone "
                    "evidencia suficiente para confirmar el estado final."
                ).strip()
            final_goal = self._final_goal_result(goal_state, fallback)
            return {
                "ok": True,
                "text": final_goal["text"],
                "status": final_goal["status"],
                "steps": len(actions),
                "actions": actions,
                "goal_state": final_goal["goal_state"],
                "messages": messages,
                "final_mode": "deterministic_capability_plan",
            }

        structured_web_goal = goal_state.goal in {
            "open_web_service", "site_context_action", "site_content_sequence",
            "search_in_page", "search_and_open_result", "search_and_play_any_result",
            "channel_video", "open_channel",
            "site_player_control", "select_visible_video",
        }
        uia_operations = {
            "select_site_profile", "search_page", "select_result",
            "select_channel", "select_content_tab", "select_sort",
            "select_visible_video", "select_season", "select_episode",
            "collect_results",
        }
        needs_uia = any(
            step.operation in uia_operations and step.status == "pending"
            for step in goal_state.steps
        )
        structured_web_supported = not needs_uia or (
            registry.has_tool("web_inspect") and registry.has_tool("web_act")
        )
        if structured_web_goal and structured_web_supported:
            if verbose:
                print("")
                print("PASO 1")
                print("Modo: deterministic_structured_web_plan")
            deterministic_records = self._run_structured_web_microplan(
                registry,
                goal_state,
            )
            latest_web_snapshot_id = self._record_structured_records(
                deterministic_records,
                1,
                "deterministic_structured_web_plan",
                actions,
                messages,
                web_elements_by_snapshot,
                verbose,
            ) or latest_web_snapshot_id
            if self._structured_state_is_terminal(goal_state):
                final_goal = self._final_goal_result(goal_state, "")
                return {
                    "ok": True,
                    "text": final_goal["text"],
                    "status": final_goal["status"],
                    "steps": 1 if deterministic_records else 0,
                    "actions": actions,
                    "goal_state": final_goal["goal_state"],
                    "messages": messages,
                    "final_mode": "deterministic_structured_web_plan",
                }
            # A mismatched/temporarily incomplete accessibility tree is not a
            # terminal outcome.  Keep the already observed evidence and let
            # the guarded normal loop inspect again; its scope checks still
            # prevent Google fallbacks, wrong profiles and unrelated clicks.

        # A numbered follow-up must continue the exact public list Mochi
        # displayed earlier.  It is intentionally handled before the model:
        # otherwise a fresh process would interpret "the third" as a new
        # app/site name and could open an unrelated search.
        if goal_state.goal == "continue_pending_page_choice":
            if verbose:
                print("")
                print("PASO 1")
                print("Modo: deterministic_pending_page_choice")
            deterministic_records = self._fast_pending_choice_selection(registry, goal_state)
            scheduled_step_ids = set()
            for _tool, _arguments, _result, kind in deterministic_records:
                if kind not in self._VERIFIED_PAGE_ACTIONS:
                    continue
                represented = next(
                    (
                        step for step in goal_state.steps
                        if step.operation == kind
                        and step.status == "pending"
                        and step.id not in scheduled_step_ids
                    ),
                    None,
                )
                if represented:
                    scheduled_step_ids.add(represented.id)
            record_index = 0
            while record_index < len(deterministic_records):
                deterministic_tool, deterministic_arguments, deterministic_result, deterministic_kind = deterministic_records[record_index]
                if deterministic_tool == "web_act" and deterministic_result.get("ok"):
                    if deterministic_kind in self._VERIFIED_PAGE_ACTIONS:
                        goal_state.note_web_action(deterministic_kind)
                elif deterministic_tool == "web_inspect":
                    goal_state.verify_web_observation(deterministic_result)

                actions.append({
                    "step": 1,
                    "mode": "deterministic_pending_page_choice",
                    "tool": deterministic_tool,
                    "arguments": deterministic_arguments,
                    "result": deterministic_result,
                })
                messages.append({
                    "role": "tool",
                    "tool_name": deterministic_tool,
                    "content": json.dumps(deterministic_result, ensure_ascii=False),
                })
                if verbose:
                    print(f"Tool: {deterministic_tool}")
                    print(f"Argumentos: {deterministic_arguments}")
                    print("Resultado:")
                    print(json.dumps(deterministic_result, indent=2, ensure_ascii=False))

                if deterministic_tool == "web_inspect":
                    next_step = self._next_named_page_step(goal_state)
                    if next_step and next_step.id not in scheduled_step_ids:
                        scheduled_step_ids.add(next_step.id)
                        _operation, follow_up_records = self._fast_pending_media_operation(
                            registry, goal_state
                        )
                        deterministic_records.extend(follow_up_records)
                record_index += 1

            selected_choice_step = goal_state.step(
                goal_state.pending_choice_operation or "select_result"
            )
            if selected_choice_step and selected_choice_step.status == "completed":
                self.pending_choice_cache.clear()
            final_goal = self._final_goal_result(goal_state, "")
            return {
                "ok": True,
                "text": final_goal["text"],
                "status": final_goal["status"],
                "steps": 1,
                "actions": actions,
                "goal_state": final_goal["goal_state"],
                "messages": messages,
                "final_mode": "deterministic_pending_page_choice",
            }

        if goal_state.goal == "select_visible_video":
            if verbose:
                print("")
                print("PASO 1")
                print("Modo: deterministic_visible_video")
            deterministic_records = self._fast_visible_video_selection(registry, goal_state)
            scheduled_follow_ups = {"select_visible_video"}
            record_index = 0
            while record_index < len(deterministic_records):
                deterministic_tool, deterministic_arguments, deterministic_result, deterministic_kind = deterministic_records[record_index]
                if deterministic_tool == "web_act" and deterministic_result.get("ok"):
                    if deterministic_kind in {"select_visible_video", "play_media"}:
                        goal_state.note_web_action(deterministic_kind)
                elif deterministic_tool == "web_inspect":
                    goal_state.verify_web_observation(deterministic_result)

                actions.append({
                    "step": 1,
                    "mode": "deterministic_visible_video",
                    "tool": deterministic_tool,
                    "arguments": deterministic_arguments,
                    "result": deterministic_result,
                })
                messages.append({
                    "role": "tool",
                    "tool_name": deterministic_tool,
                    "content": json.dumps(deterministic_result, ensure_ascii=False),
                })
                if verbose:
                    print(f"Tool: {deterministic_tool}")
                    print(f"Argumentos: {deterministic_arguments}")
                    print("Resultado:")
                    print(json.dumps(deterministic_result, indent=2, ensure_ascii=False))

                if deterministic_tool == "web_inspect":
                    operation = self._next_named_page_operation(goal_state)
                    if operation and operation not in scheduled_follow_ups:
                        scheduled_follow_ups.add(operation)
                        _operation, follow_up_records = self._fast_pending_media_operation(
                            registry, goal_state
                        )
                        deterministic_records.extend(follow_up_records)
                record_index += 1

            final_goal = self._final_goal_result(goal_state, "")
            return {
                "ok": True,
                "text": final_goal["text"],
                "status": final_goal["status"],
                "steps": 1,
                "actions": actions,
                "goal_state": final_goal["goal_state"],
                "messages": messages,
                "final_mode": "deterministic_visible_video",
            }

        # A direct player command is already fully constrained to the active
        # tab by plan_goal.  Running it through the model caused repeated
        # inspections and even a Google search for "reanuda el video".  Use
        # the existing provider-neutral UIA microplans directly and verify
        # every action with a fresh snapshot.
        if goal_state.goal in {"player_control", "current_page_episode"}:
            if verbose:
                print("")
                print("PASO 1")
                print("Modo: deterministic_player_control")

            attempted_step_ids = set()
            while goal_state.status in {"pending", "partial"}:
                pending_step = self._next_named_page_step(goal_state)
                operation, deterministic_records = self._fast_pending_media_operation(
                    registry,
                    goal_state,
                )
                if (
                    not operation
                    or not pending_step
                    or pending_step.id in attempted_step_ids
                    or not deterministic_records
                ):
                    break
                attempted_step_ids.add(pending_step.id)

                for deterministic_tool, deterministic_arguments, deterministic_result, deterministic_kind in deterministic_records:
                    if deterministic_tool == "web_act" and deterministic_result.get("ok"):
                        if deterministic_kind in self._VERIFIED_PAGE_ACTIONS:
                            goal_state.note_web_action(deterministic_kind)
                    elif deterministic_tool == "web_inspect":
                        goal_state.verify_web_observation(deterministic_result)

                    actions.append({
                        "step": 1,
                        "mode": "deterministic_player_control",
                        "tool": deterministic_tool,
                        "arguments": deterministic_arguments,
                        "result": deterministic_result,
                    })
                    messages.append({
                        "role": "tool",
                        "tool_name": deterministic_tool,
                        "content": json.dumps(deterministic_result, ensure_ascii=False),
                    })
                    if verbose:
                        print(f"Tool: {deterministic_tool}")
                        print(f"Argumentos: {deterministic_arguments}")
                        print("Resultado:")
                        print(json.dumps(deterministic_result, indent=2, ensure_ascii=False))

            final_goal = self._final_goal_result(goal_state, "")
            return {
                "ok": True,
                "text": final_goal["text"],
                "status": final_goal["status"],
                "steps": 1,
                "actions": actions,
                "goal_state": final_goal["goal_state"],
                "messages": messages,
                "final_mode": "deterministic_player_control",
            }

        # "Cambia de video" is expressly scoped to the active tab.  Run the
        # generic semantic search plan directly so a model cannot decide to
        # launch a second browser tab before interacting with the page the
        # user already has open.
        active_video_step = goal_state.step("search_page")
        if (
            goal_state.goal == "change_active_video"
            and active_video_step
            and active_video_step.status == "pending"
            and registry.has_tool("web_inspect")
            and registry.has_tool("web_act")
        ):
            if verbose:
                print("")
                print("PASO 1")
                print("Modo: deterministic_current_tab_video_change")

            deterministic_records = self._fast_page_search(registry, goal_state)
            for deterministic_tool, deterministic_arguments, deterministic_result, deterministic_kind in deterministic_records:
                if deterministic_tool == "web_act" and deterministic_result.get("ok"):
                    if deterministic_kind in {"submit_search", "submit_refined_search"}:
                        goal_state.note_web_action("search_page")
                    elif deterministic_kind == "select_result":
                        goal_state.note_web_action("select_result")
                elif deterministic_tool == "web_inspect":
                    goal_state.verify_web_observation(deterministic_result)

                actions.append({
                    "step": 1,
                    "mode": "deterministic_current_tab_video_change",
                    "tool": deterministic_tool,
                    "arguments": deterministic_arguments,
                    "result": deterministic_result,
                })
                messages.append({
                    "role": "tool",
                    "tool_name": deterministic_tool,
                    "content": json.dumps(deterministic_result, ensure_ascii=False),
                })
                if verbose:
                    print(f"Tool: {deterministic_tool}")
                    print(f"Argumentos: {deterministic_arguments}")
                    print("Resultado:")
                    print(json.dumps(deterministic_result, indent=2, ensure_ascii=False))

            # Either the action was verified, or live UIA evidence supplied a
            # choice list.  In both cases there is no safe reason to ask the
            # model for another speculative page action.
            if goal_state.status == "completed" or goal_state.page_result_options:
                final_goal = self._final_goal_result(goal_state, "")
                return {
                    "ok": True,
                    "text": final_goal["text"],
                    "status": final_goal["status"],
                    "steps": 1,
                    "actions": actions,
                    "goal_state": final_goal["goal_state"],
                    "messages": messages,
                    "final_mode": "deterministic_current_tab_video_change",
                }

        # A named A→B page-profile transition is fully constrained by the
        # user-provided names and live UIA evidence. Run it before consulting
        # the language model: this removes several expensive model turns for
        # the common case while retaining inspect → act → inspect verification.
        selection_step = goal_state.step("select_site_profile")
        if (
            selection_step
            and selection_step.status == "pending"
            and goal_state.site_profile_source
            and registry.has_tool("web_inspect")
            and registry.has_tool("web_act")
        ):
            if verbose:
                print("")
                print("PASO 1")
                print("Modo: deterministic_site_profile_switch")

            initial_observation = registry.execute("web_inspect", {})
            actions.append({
                "step": 1,
                "mode": "deterministic_site_profile_switch",
                "tool": "web_inspect",
                "arguments": {},
                "result": initial_observation,
            })
            if verbose:
                print("Tool: web_inspect")
                print("Argumentos: {}")
                print("Resultado:")
                print(json.dumps(initial_observation, indent=2, ensure_ascii=False))

            if initial_observation.get("ok") and self._page_matches_goal_target(initial_observation.get("page"), goal_state.target):
                goal_state.confirm_visible_site_profile(initial_observation)
                deterministic_records = (
                    self._fast_site_profile_switch_from_observation(registry, goal_state, initial_observation)
                    if goal_state.status == "pending"
                    else []
                )
                for deterministic_tool, deterministic_arguments, deterministic_result, deterministic_kind in deterministic_records:
                    if deterministic_tool == "web_act" and deterministic_result.get("ok"):
                        if deterministic_kind == "profile_menu_open":
                            goal_state.note_site_profile_menu_opening()
                        else:
                            goal_state.note_web_action()
                    elif deterministic_tool == "web_inspect":
                        if goal_state.site_profile_menu_pending:
                            menu_has_target = any(
                                self._is_site_profile_choice(
                                    item,
                                    goal_state.site_profile,
                                    allow_menu_button=True,
                                )
                                for item in deterministic_result.get("elements", [])
                            )
                            goal_state.observe_site_profile_menu(menu_has_target)
                        goal_state.confirm_visible_site_profile(deterministic_result)
                        goal_state.verify_web_observation(deterministic_result)

                    actions.append({
                        "step": 1,
                        "mode": "deterministic_site_profile_switch",
                        "tool": deterministic_tool,
                        "arguments": deterministic_arguments,
                        "result": deterministic_result,
                    })
                    if verbose:
                        print(f"Tool: {deterministic_tool}")
                        print(f"Argumentos: {deterministic_arguments}")
                        print("Resultado:")
                        print(json.dumps(deterministic_result, indent=2, ensure_ascii=False))

            if goal_state.status in {"completed", "blocked"}:
                final_goal = self._final_goal_result(goal_state, "")
                return {
                    "ok": True,
                    "text": final_goal["text"],
                    "status": final_goal["status"],
                    "steps": 1,
                    "actions": actions,
                    "goal_state": final_goal["goal_state"],
                    "messages": messages,
                    "final_mode": "deterministic_site_profile_switch",
                }


        # A bare, uncategorized "abre X" is resolved by one deterministic
        # tool.  This keeps informal names from being guessed as web searches
        # by the model, while open_target itself still refuses ambiguity.
        direct_open_step = goal_state.step("open_target")
        if direct_open_step and direct_open_step.status == "pending" and registry.has_tool("open_target"):
            if verbose:
                print("")
                print("PASO 1")
                print("Modo: deterministic_target_resolution")
                print("Tool: open_target")
                print(f"Argumentos: {{'target': {direct_open_step.target!r}}}")
            direct_result = registry.execute("open_target", {"target": direct_open_step.target})
            actions.append({
                "step": 1,
                "mode": "deterministic_target_resolution",
                "tool": "open_target",
                "arguments": {"target": direct_open_step.target},
                "result": direct_result,
            })
            messages.append({
                "role": "tool",
                "tool_name": "open_target",
                "content": json.dumps(direct_result, ensure_ascii=False),
            })
            if verbose:
                print("Resultado:")
                print(json.dumps(direct_result, indent=2, ensure_ascii=False))

            if direct_result.get("ok") and direct_result.get("goal_completed", True):
                goal_state.mark_completed("open_target")
            else:
                goal_state.mark_failed("open_target", direct_result.get("message"))
            final_goal = self._final_goal_result(
                goal_state,
                self._tool_completion_text("open_target", direct_result),
            )
            return {
                "ok": True,
                "text": final_goal["text"],
                "status": final_goal["status"],
                "steps": 1,
                "actions": actions,
                "goal_state": final_goal["goal_state"],
                "messages": messages,
                "final_mode": "deterministic_target_resolution",
            }


        # =================================================
        # LOOP
        # =================================================

        for step in range(
            1,
            self.max_steps + 1
        ):

            # =============================================
            # CONSULTAR BRAIN
            # =============================================

            result = (
                self.router.generate(

                    messages=messages,

                    tools=(
                        registry
                        .get_tool_schemas()
                    ),

                    mode=effective_mode
                )
            )

            current_mode = self.router.last_mode


            if not result.success:

                goal_state.finalize()

                return {

                    "ok":
                        False,

                    "error":
                        result.error,

                    "steps":
                        step,

                    "actions":
                        actions,

                    "status": goal_state.status,

                    "goal_state": goal_state.compact(),

                    "messages":
                        messages
                }


            if verbose:

                print("")
                print(
                    f"PASO {step}"
                )

                print(
                    f"Modo: {current_mode}"
                )


            # =============================================
            # GUARDAR RESPUESTA DEL ASSISTANT
            # =============================================

            messages.append(
                self._assistant_message(
                    result
                )
            )


            # =============================================
            # SI NO HAY TOOLS, TERMINAMOS
            # =============================================

            if not result.tool_calls:

                # A structured internal-profile request is actionable with the
                # web tools. Some models nevertheless answer from stale
                # capability assumptions without asking for the first page
                # observation. Give that plan one deterministic inspection
                # before accepting a no-tools response.
                pending_site_profile = goal_state.step("select_site_profile")
                pending_browser_open = goal_state.step("browser_open")
                if (
                    pending_site_profile
                    and pending_site_profile.status == "pending"
                    and (
                        not pending_browser_open
                        or pending_browser_open.status == "completed"
                    )
                    and not site_profile_recovery_inspection_used
                    and registry.has_tool("web_inspect")
                ):
                    site_profile_recovery_inspection_used = True
                    inspection_result = registry.execute("web_inspect", {})
                    goal_state.confirm_visible_site_profile(inspection_result)
                    goal_state.verify_web_observation(inspection_result)
                    latest_web_snapshot_id = inspection_result.get("snapshot_id") or latest_web_snapshot_id
                    if inspection_result.get("snapshot_id"):
                        web_elements_by_snapshot[inspection_result["snapshot_id"]] = {
                            item.get("id"): item for item in inspection_result.get("elements", []) if item.get("id")
                        }
                    actions.append({
                        "step": step,
                        "mode": "deterministic_web_recovery",
                        "tool": "web_inspect",
                        "arguments": {},
                        "result": inspection_result,
                    })
                    messages.append({
                        "role": "tool",
                        "tool_name": "web_inspect",
                        "content": json.dumps(inspection_result, ensure_ascii=False),
                    })
                    if verbose:
                        print("Tool: web_inspect")
                        print("Argumentos: {}")
                        print("Resultado:")
                        print(json.dumps(inspection_result, indent=2, ensure_ascii=False))

                    # For an explicit "from A to B" request we have enough
                    # evidence to perform the safe menu transition ourselves.
                    # This avoids a second model turn merely restating the
                    # visible click instead of issuing web_act.
                    recovery_records = []
                    if goal_state.status == "pending":
                        recovery_records = self._fast_site_profile_switch_from_observation(
                            registry,
                            goal_state,
                            inspection_result,
                        )
                    for recovery_tool, recovery_arguments, recovery_result, recovery_kind in recovery_records:
                        if recovery_tool == "web_act" and recovery_result.get("ok"):
                            if recovery_kind == "profile_menu_open":
                                goal_state.note_site_profile_menu_opening()
                            else:
                                goal_state.note_web_action()
                        elif recovery_tool == "web_inspect":
                            if goal_state.site_profile_menu_pending:
                                menu_has_target = any(
                                    self._is_site_profile_choice(
                                        item,
                                        goal_state.site_profile,
                                        allow_menu_button=True,
                                    )
                                    for item in recovery_result.get("elements", [])
                                )
                                goal_state.observe_site_profile_menu(menu_has_target)
                            goal_state.confirm_visible_site_profile(recovery_result)
                            goal_state.verify_web_observation(recovery_result)
                            latest_web_snapshot_id = recovery_result.get("snapshot_id") or latest_web_snapshot_id
                            if recovery_result.get("snapshot_id"):
                                web_elements_by_snapshot[recovery_result["snapshot_id"]] = {
                                    item.get("id"): item for item in recovery_result.get("elements", []) if item.get("id")
                                }

                        actions.append({
                            "step": step,
                            "mode": "deterministic_site_profile_switch",
                            "tool": recovery_tool,
                            "arguments": recovery_arguments,
                            "result": recovery_result,
                        })
                        messages.append({
                            "role": "tool",
                            "tool_name": recovery_tool,
                            "content": json.dumps(recovery_result, ensure_ascii=False),
                        })
                        if verbose:
                            print(f"Tool: {recovery_tool}")
                            print(f"Argumentos: {recovery_arguments}")
                            print("Resultado:")
                            print(json.dumps(recovery_result, indent=2, ensure_ascii=False))

                    if goal_state.status == "completed":
                        final_goal = self._final_goal_result(goal_state, "")
                        return {
                            "ok": True,
                            "text": final_goal["text"],
                            "status": final_goal["status"],
                            "steps": step,
                            "actions": actions,
                            "goal_state": final_goal["goal_state"],
                            "messages": messages,
                            "final_mode": current_mode,
                        }
                    if goal_state.status == "blocked":
                        final_goal = self._final_goal_result(goal_state, "")
                        return {
                            "ok": True,
                            "text": final_goal["text"],
                            "status": final_goal["status"],
                            "steps": step,
                            "actions": actions,
                            "goal_state": final_goal["goal_state"],
                            "messages": messages,
                            "final_mode": current_mode,
                        }
                    continue

                # A no-tools reply must not terminate an ordered page plan
                # halfway through.  Recover the next deterministic step from
                # fresh UIA evidence.  This is especially important after a
                # browser open: a model may acknowledge the open action even
                # though profile selection and the in-page search are still
                # pending.
                site_step = goal_state.step("select_site_profile")
                open_step = goal_state.step("browser_open")
                page_step = goal_state.step("search_page")
                result_step = goal_state.step("select_result")
                season_step = goal_state.step("select_season")
                episode_step = goal_state.step("select_episode")
                play_step = goal_state.step("play_media")
                pause_step = goal_state.step("pause_media")
                recovery_records = []
                recovery_mode = None
                recovery_operation = None
                if (
                    open_step
                    and open_step.status == "pending"
                    and goal_state.target
                    and registry.has_tool("browser_open")
                ):
                    recovery_mode = "deterministic_browser_open_recovery"
                    open_arguments = {"target": goal_state.target}
                    if goal_state.browser_profile:
                        open_arguments["profile"] = goal_state.browser_profile
                    open_result = registry.execute("browser_open", open_arguments)
                    recovery_records.append(("browser_open", open_arguments, open_result, "browser_open"))
                    if open_result.get("ok"):
                        goal_state.mark_completed("browser_open")
                        if (
                            goal_state.step("select_site_profile")
                            and goal_state.step("select_site_profile").status == "pending"
                        ):
                            recovery_records.extend(self._fast_named_web_selection(registry, goal_state))
                        elif page_step and page_step.status == "pending":
                            recovery_records.extend(self._fast_page_search(registry, goal_state))
                        elif self._next_named_page_operation(goal_state):
                            _operation, player_records = self._fast_pending_media_operation(
                                registry, goal_state
                            )
                            recovery_records.extend(player_records)
                    else:
                        goal_state.mark_failed("browser_open", open_result.get("message"))
                elif (
                    page_step
                    and page_step.status == "pending"
                    and open_step
                    and open_step.status == "completed"
                    and (not site_step or site_step.status == "completed")
                    and registry.has_tool("web_inspect")
                    and registry.has_tool("web_act")
                ):
                    recovery_mode = "deterministic_page_search_recovery"
                    recovery_records = self._fast_page_search(registry, goal_state)
                elif (
                    season_step
                    and season_step.status == "pending"
                    and result_step
                    and result_step.status == "completed"
                ):
                    recovery_mode = "deterministic_season_recovery"
                    recovery_records = self._fast_named_page_action(
                        registry, goal_state, "select_season"
                    )
                elif (
                    episode_step
                    and episode_step.status == "pending"
                    and result_step
                    and result_step.status == "completed"
                    and (not season_step or season_step.status == "completed")
                ):
                    recovery_mode = "deterministic_episode_recovery"
                    recovery_records = self._fast_named_page_action(
                        registry, goal_state, "select_episode"
                    )
                elif (
                    play_step
                    and play_step.status == "pending"
                    and (
                        (episode_step and episode_step.status == "completed")
                        or (not episode_step and result_step and result_step.status == "completed")
                    )
                ):
                    recovery_mode = "deterministic_playback_recovery"
                    recovery_records = self._fast_play_media(registry, goal_state)
                elif (
                    pause_step
                    and pause_step.status == "pending"
                    and play_step
                    and play_step.status == "completed"
                ):
                    recovery_mode = "deterministic_pause_recovery"
                    recovery_records = self._fast_named_page_action(
                        registry, goal_state, "pause_media"
                    )
                elif (
                    not rejected_named_result_action
                    and self._next_named_page_operation(goal_state)
                ):
                    # Direct player commands (seek, advance, next/previous)
                    # have no browser-open or search dependency.  They still
                    # begin with a UIA observation and only act when one
                    # unambiguous semantic control is exposed.
                    recovery_mode = "deterministic_player_control_recovery"
                    recovery_operation, recovery_records = self._fast_pending_media_operation(
                        registry, goal_state
                    )

                if recovery_records:
                    # The first inspection in a helper's own record sequence
                    # is pre-action evidence.  Do not schedule that same
                    # operation again before its following web_act has had a
                    # chance to run; only a different dependency may follow
                    # the verified post-action snapshot.
                    scheduled_follow_ups = {
                        kind for _tool, _arguments, _result, kind in recovery_records
                        if kind in self._VERIFIED_PAGE_ACTIONS
                    }
                    if (
                        goal_state.step("select_result")
                        and any(
                            kind in {"submit_search", "submit_refined_search"}
                            for _tool, _arguments, _result, kind in recovery_records
                        )
                    ):
                        scheduled_follow_ups.add("select_result")
                    recovery_index = 0
                    while recovery_index < len(recovery_records):
                        recovery_tool, recovery_arguments, recovery_result, recovery_kind = recovery_records[recovery_index]
                        if recovery_tool == "web_act" and recovery_result.get("ok"):
                            if recovery_kind in {"submit_search", "submit_refined_search"}:
                                goal_state.note_web_action("search_page")
                            elif recovery_kind in self._VERIFIED_PAGE_ACTIONS:
                                goal_state.note_web_action(recovery_kind)
                        elif recovery_tool == "web_inspect":
                            goal_state.confirm_visible_site_profile(recovery_result)
                            goal_state.verify_web_observation(recovery_result)
                            latest_web_snapshot_id = recovery_result.get("snapshot_id") or latest_web_snapshot_id
                            if recovery_result.get("snapshot_id"):
                                web_elements_by_snapshot[recovery_result["snapshot_id"]] = {
                                    item.get("id"): item for item in recovery_result.get("elements", []) if item.get("id")
                                }

                        actions.append({
                            "step": step,
                            "mode": recovery_mode,
                            "tool": recovery_tool,
                            "arguments": recovery_arguments,
                            "result": recovery_result,
                        })
                        messages.append({
                            "role": "tool",
                            "tool_name": recovery_tool,
                            "content": json.dumps(recovery_result, ensure_ascii=False),
                        })
                        if verbose:
                            print(f"Tool: {recovery_tool}")
                            print(f"Argumentos: {recovery_arguments}")
                            print("Resultado:")
                            print(json.dumps(recovery_result, indent=2, ensure_ascii=False))

                        # A changed post-action snapshot is evidence that the
                        # preceding step completed.  Continue the next
                        # dependency immediately instead of spending another
                        # model turn (and possibly exhausting max_steps) just
                        # to ask for the obvious following UIA operation.
                        if recovery_tool == "web_inspect":
                            operation = self._next_named_page_operation(goal_state)
                            if operation and operation not in scheduled_follow_ups:
                                scheduled_follow_ups.add(operation)
                                _operation, follow_up_records = self._fast_pending_media_operation(
                                    registry, goal_state
                                )
                                recovery_records.extend(follow_up_records)
                        recovery_index += 1

                    if goal_state.status == "completed":
                        final_goal = self._final_goal_result(goal_state, "")
                        return {
                            "ok": True,
                            "text": final_goal["text"],
                            "status": final_goal["status"],
                            "steps": step,
                            "actions": actions,
                            "goal_state": final_goal["goal_state"],
                            "messages": messages,
                            "final_mode": recovery_mode,
                        }
                    if (
                        recovery_mode == "deterministic_player_control_recovery"
                        and recovery_operation
                        and not any(
                            recovery_tool in {"web_act", "control_media"}
                            and recovery_result.get("ok")
                            for recovery_tool, _arguments, recovery_result, _kind in recovery_records
                        )
                    ):
                        # A fresh dependency-gated observation still exposed
                        # no safe action. More identical model turns cannot
                        # improve the evidence; return an honest partial
                        # result with the goal-specific explanation.
                        final_goal = self._final_goal_result(goal_state, "")
                        return {
                            "ok": True,
                            "text": final_goal["text"],
                            "status": final_goal["status"],
                            "steps": step,
                            "actions": actions,
                            "goal_state": final_goal["goal_state"],
                            "messages": messages,
                            "final_mode": recovery_mode,
                        }
                    if (
                        recovery_mode == "deterministic_browser_open_recovery"
                        and open_step
                        and open_step.status != "completed"
                    ):
                        final_goal = self._final_goal_result(goal_state, "")
                        return {
                            "ok": False,
                            "text": final_goal["text"],
                            "status": final_goal["status"],
                            "steps": step,
                            "actions": actions,
                            "goal_state": final_goal["goal_state"],
                            "messages": messages,
                            "final_mode": recovery_mode,
                        }
                    # The browser did open, but there is no page capability
                    # available to continue the remaining structured steps.
                    # Return an honest partial result instead of spending
                    # model turns that can only repeat the same no-tools
                    # response.
                    if (
                        recovery_mode == "deterministic_browser_open_recovery"
                        and len(recovery_records) == 1
                    ):
                        final_goal = self._final_goal_result(goal_state, "")
                        return {
                            "ok": True,
                            "text": final_goal["text"],
                            "status": final_goal["status"],
                            "steps": step,
                            "actions": actions,
                            "goal_state": final_goal["goal_state"],
                            "messages": messages,
                            "final_mode": recovery_mode,
                        }
                    # Give the regular loop the newly verified page evidence
                    # for later steps rather than finalizing from its stale
                    # no-tools response.
                    continue

                final_goal = self._final_goal_result(goal_state, result.text)

                if verbose:

                    print(
                        "No se solicitaron más tools."
                    )


                    if result.text:

                        print(
                            f"Respuesta: {result.text}"
                        )


                return {

                    "ok":
                        True,

                    "text": final_goal["text"],

                    "status": final_goal["status"],

                    "steps":
                        step,

                    "actions":
                        actions,

                    "goal_state": final_goal["goal_state"],

                    "messages":
                        messages,

                    "final_mode":
                        current_mode
                }


            # =============================================
            # EJECUTAR TOOL CALLS
            # =============================================

            for call in result.tool_calls:

                tool_name, arguments = (
                    extract_tool_call(
                        call
                    )
                )

                # play_web_media: el LLM ya interpretó qué poner. El
                # objetivo pasa a ser el plan verificado de "poné X en
                # YouTube" y corre sin más turnos del LLM. Si no llega a
                # un estado final, el loop sigue con ese objetivo.
                media_goal = (
                    plan_web_media(
                        arguments.get("site"),
                        arguments.get("query"),
                        arguments.get("title"),
                    )
                    if tool_name == "play_web_media"
                    and registry.has_tool("web_inspect")
                    and registry.has_tool("web_act")
                    else None
                )
                if media_goal is not None:
                    goal_state = media_goal
                    self._context_provider = goal_state.target
                    if verbose:
                        print(f"Tool: {tool_name}")
                        print(f"Argumentos: {arguments}")
                    media_records = self._run_structured_web_microplan(
                        registry,
                        goal_state,
                    )
                    plan_messages = []
                    latest_web_snapshot_id = self._record_structured_records(
                        media_records,
                        step,
                        current_mode,
                        actions,
                        plan_messages,
                        web_elements_by_snapshot,
                        verbose,
                    ) or latest_web_snapshot_id
                    if self._structured_state_is_terminal(goal_state):
                        final_goal = self._final_goal_result(goal_state, "")
                        return {
                            "ok": True,
                            "text": final_goal["text"],
                            "status": final_goal["status"],
                            "steps": step,
                            "actions": actions,
                            "goal_state": final_goal["goal_state"],
                            "messages": messages + plan_messages,
                            "final_mode": current_mode,
                        }
                    # Al LLM le llega el estado del plan y la última lectura
                    # de la página, no todas: juntas pasan el contexto (16k).
                    last_page = next(
                        (
                            result for name, _arguments, result, _kind in reversed(media_records)
                            if name == "web_inspect"
                        ),
                        None,
                    )
                    messages.append({
                        "role": "tool",
                        "tool_name": tool_name,
                        "content": json.dumps({
                            "ok": False,
                            "message": "El plan no terminó; seguí desde la página actual.",
                            "goal_state": goal_state.compact(),
                            "page": last_page,
                        }, ensure_ascii=False),
                    })
                    continue

                # A profile transition needs the account-menu opener and the
                # target option in the same snapshot. A model-supplied query
                # can accidentally match a generic site link ("Netflix") and
                # hide both, wasting several turns. Use the full semantic
                # page only for this already-scoped operation.
                if (
                    tool_name == "web_inspect"
                    and goal_state.step("select_site_profile")
                    and goal_state.step("select_site_profile").status == "pending"
                    and arguments.get("query")
                ):
                    arguments = {key: value for key, value in arguments.items() if key != "query"}

                # A page snapshot or a click has no useful meaning until the
                # structured browser-open dependency is complete.  Some
                # models start by inspecting the previously active tab (often
                # about:blank) instead of opening the requested site.  Route
                # that premature web action to the explicit, user-authorized
                # browser_open step and let the verified micro-plan continue.
                pending_browser_open = goal_state.step("browser_open")
                if (
                    tool_name in {"web_inspect", "web_act"}
                    and pending_browser_open
                    and pending_browser_open.status == "pending"
                    and goal_state.target
                    and registry.has_tool("browser_open")
                ):
                    tool_name = "browser_open"
                    arguments = {"target": goal_state.target}
                    if goal_state.browser_profile:
                        arguments["profile"] = goal_state.browser_profile

                # Once the requested browser_open succeeded, launching the
                # Chrome application again can surface the profile picker and
                # steal focus from the page that is still loading.  Treat that
                # model proposal as the intended fresh observation; the
                # dependency-gated web micro-plan below will act only if the
                # requested site is now visible.
                open_step = goal_state.step("browser_open")
                pending_web_step = any(
                    step_item.scope == "web_page" and step_item.status == "pending"
                    for step_item in goal_state.steps
                )
                proposed_application = _norm(
                    arguments.get("application") or arguments.get("target")
                )
                if (
                    tool_name == "open_application"
                    and open_step
                    and open_step.status == "completed"
                    and pending_web_step
                    and proposed_application in {"chrome", "google chrome"}
                    and registry.has_tool("web_inspect")
                ):
                    tool_name = "web_inspect"
                    arguments = {}
                if (
                    tool_name in {"close_window", "close_application_windows"}
                    and open_step
                    and open_step.status == "completed"
                    and pending_web_step
                    and registry.has_tool("web_inspect")
                ):
                    # Closing the browser cannot advance a still-pending web
                    # objective. Small models sometimes propose it after a
                    # transient empty renderer snapshot; turn that proposal
                    # into another observation and preserve the live page.
                    tool_name = "web_inspect"
                    arguments = {}


                if verbose:

                    print(
                        f"Tool: {tool_name}"
                    )

                    print(
                        f"Argumentos: {arguments}"
                    )


                # =========================================
                # VALIDAR TOOL
                # =========================================

                if not tool_name:

                    tool_result = {

                        "ok":
                            False,

                        "message":
                            "El modelo solicitó una tool inválida."
                    }


                elif not registry.has_tool(
                    tool_name
                ):

                    tool_result = {

                        "ok":
                            False,

                        "message":
                            (
                                f"La herramienta "
                                f"'{tool_name}' no existe."
                            )
                    }


                else:

                    # e1/e2 are deliberately local to an inspection.  If a
                    # fresh inspection happens to assign e3 again, it is new
                    # evidence and must not be blocked as a duplicate failed
                    # action from the prior page state.
                    action_evidence = None
                    profile_menu_opening = False
                    page_result_selection = False
                    named_page_operation = None
                    pending_named_page_operation = self._next_named_page_operation(goal_state)
                    if tool_name == "web_act":
                        action_evidence = {"snapshot_id": arguments.get("snapshot_id") or latest_web_snapshot_id}
                    signature = self._action_signature(tool_name, arguments, action_evidence)
                    if (
                        goal_state.goal.startswith("local_")
                        and tool_name in {"browser_open", "browser_search"}
                    ):
                        tool_result = {
                            "ok": False,
                            "error": "invalid_scope_action",
                            "message": "La petición es local; usá las herramientas de archivos o carpetas, no Chrome.",
                        }
                    elif tool_name == "browser_search" and goal_state.step("search_page"):
                        tool_result = {
                            "ok": False,
                            "error": "invalid_scope_action",
                            "message": "La búsqueda pendiente es dentro del sitio ya abierto; usá web_inspect y web_act, no una búsqueda externa.",
                        }
                    elif tool_name == "browser_profiles" and goal_state.browser_profile and goal_state.step("browser_open"):
                        tool_result = {
                            "ok": False,
                            "error": "invalid_scope_action",
                            "message": "El perfil de Chrome ya fue definido por el plan; continuá con la página, no vuelvas a consultar perfiles.",
                        }
                    elif (
                        goal_state.goal == "change_active_video"
                        and tool_name in {"browser_open", "browser_search"}
                    ):
                        tool_result = {
                            "ok": False,
                            "error": "invalid_scope_action",
                            "message": "Cambiar de video debe operar en la pestaña actual; no abras otra pestaña ni hagas una búsqueda web externa.",
                        }
                    elif tool_name == "browser_search" and goal_state.steps and not goal_state.explicit_search:
                        tool_result = {
                            "ok": False,
                            "error": "invalid_scope_action",
                            "message": "browser_search solo sirve para una búsqueda solicitada explícitamente o para descubrir un destino; no uses perfiles o datos pendientes como consulta.",
                        }
                    elif (
                        tool_name == "browser_open"
                        and goal_state.step("browser_open")
                        and goal_state.step("browser_open").status == "completed"
                    ):
                        target_matches = (
                            not goal_state.target
                            or self._is_planned_browser_target(
                                arguments.get("target"), goal_state.target,
                                require_site_root=bool(goal_state.step("search_page")),
                            )
                        )
                        profile_matches = (
                            not goal_state.browser_profile
                            or _norm(arguments.get("profile")) == _norm(goal_state.browser_profile)
                        )
                        if not target_matches or not profile_matches:
                            tool_result = {
                                "ok": False,
                                "error": "invalid_scope_argument",
                                "message": "El sitio ya fue abierto con el destino y perfil del plan; no lo reemplaces por otro.",
                            }
                        else:
                            tool_result = {
                                "ok": True,
                                "message": "El destino web del plan ya está abierto; reutilizo la sesión observada.",
                                "session_reused": True,
                            }
                    elif (
                        tool_name == "browser_open"
                        and goal_state.step("browser_open")
                        and goal_state.step("browser_open").status == "pending"
                        and goal_state.target
                        and not self._is_planned_browser_target(
                            arguments.get("target"),
                            goal_state.target,
                            require_site_root=bool(
                                goal_state.step("search_page")
                                and goal_state.step("search_page").status == "pending"
                            ),
                        )
                    ):
                        if self._is_planned_browser_target(
                            arguments.get("target"), goal_state.target
                        ):
                            # The model sometimes constructs the service's
                            # own /search URL.  The structured plan already
                            # has a guarded in-page search step, so send the
                            # same-site detour to the service root instead
                            # rather than spending another model turn or
                            # accepting a duplicate, unverified search.
                            arguments = {**arguments, "target": goal_state.target}
                            tool_result = registry.execute(name=tool_name, arguments=arguments)
                        else:
                            tool_result = {
                                "ok": False,
                                "error": "invalid_scope_argument",
                                "message": f"El destino pendiente es {goal_state.target}; no abras otro sitio ni una búsqueda externa.",
                            }
                    elif tool_name == "browser_open" and goal_state.browser_profile and _norm(arguments.get("profile")) != _norm(goal_state.browser_profile):
                        tool_result = {
                            "ok": False,
                            "error": "invalid_scope_argument",
                            "message": "El perfil de browser_open debe ser el browser_profile del plan; el perfil interno del sitio no es un perfil de Chrome.",
                        }
                    elif (
                        tool_name == "browser_open"
                        and goal_state.site_profile
                        and _norm(arguments.get("profile")) == _norm(goal_state.site_profile)
                        # The same display name can legitimately refer to a
                        # Chrome profile and a site-internal profile when the
                        # user explicitly named both. Reject only when the
                        # argument is being substituted for an absent or a
                        # different planned browser profile.
                        and (
                            not goal_state.browser_profile
                            or _norm(arguments.get("profile")) != _norm(goal_state.browser_profile)
                        )
                    ):
                        tool_result = {
                            "ok": False,
                            "error": "invalid_scope_argument",
                            "message": "El perfil interno del sitio no puede usarse como perfil de Chrome.",
                        }
                    elif (
                        tool_name == "web_act"
                        and goal_state.step("search_page")
                        and goal_state.step("search_page").status == "completed"
                        and goal_state.step("select_result")
                        and goal_state.step("select_result").status == "pending"
                    ):
                        snapshot_id = arguments.get("snapshot_id") or latest_web_snapshot_id
                        inspected_elements = web_elements_by_snapshot.get(snapshot_id, {})
                        inspected = inspected_elements.get(arguments.get("element_id"))
                        expected = self._goal_named_page_result(
                            {"elements": list(inspected_elements.values())},
                            goal_state,
                        )
                        action_name = str(arguments.get("action") or "").strip().lower()
                        if not inspected:
                            tool_result = {
                                "ok": False,
                                "error": "element_not_in_observed_snapshot",
                                "message": "Ese ID no pertenece al snapshot observado para el resultado pendiente; inspeccioná la página nuevamente.",
                            }
                        elif action_name not in {"click", "invoke", "select"}:
                            tool_result = {
                                "ok": False,
                                "error": "invalid_goal_action",
                                "message": "El resultado pendiente solo admite una activación semántica verificable.",
                            }
                        elif not expected:
                            tool_result = {
                                "ok": False,
                                "error": "ambiguous_goal_element",
                                "message": f"Todavía no hay un resultado accesible único que corresponda a '{goal_state.page_result}'.",
                            }
                        elif expected.get("id") != inspected.get("id"):
                            tool_result = {
                                "ok": False,
                                "error": "invalid_goal_element",
                                "message": f"El objetivo pendiente es abrir '{goal_state.page_result}', no '{inspected.get('name')}'.",
                            }
                        else:
                            page_result_selection = True
                            tool_result = registry.execute(name=tool_name, arguments=arguments)
                    elif tool_name == "web_act" and pending_named_page_operation:
                        operation = pending_named_page_operation
                        snapshot_id = arguments.get("snapshot_id") or latest_web_snapshot_id
                        inspected_elements = web_elements_by_snapshot.get(snapshot_id, {})
                        inspected = inspected_elements.get(arguments.get("element_id"))
                        expected = self._goal_named_page_result(
                            {"elements": list(inspected_elements.values())},
                            GoalState(
                                goal=goal_state.goal,
                                target=goal_state.target,
                                page_query=goal_state.page_query,
                                page_result=goal_state.step(operation).value,
                            ),
                        )
                        action_name = str(arguments.get("action") or "").strip().lower()
                        if not inspected:
                            tool_result = {
                                "ok": False,
                                "error": "element_not_in_observed_snapshot",
                                "message": "Ese ID no pertenece al snapshot actual; inspeccioná la página nuevamente.",
                            }
                        elif action_name not in {"click", "invoke", "select"}:
                            tool_result = {
                                "ok": False,
                                "error": "invalid_goal_action",
                                "message": f"{goal_state.step(operation).value} requiere una activación semántica verificable.",
                            }
                        elif not expected or expected.get("id") != inspected.get("id"):
                            tool_result = {
                                "ok": False,
                                "error": "invalid_goal_element",
                                "message": f"El siguiente objetivo es '{goal_state.step(operation).value}', no '{inspected.get('name')}'.",
                            }
                        else:
                            named_page_operation = operation
                            tool_result = registry.execute(name=tool_name, arguments=arguments)
                    elif tool_name == "web_act" and goal_state.site_profile and goal_state.step("select_site_profile") and goal_state.step("select_site_profile").status == "pending":
                        snapshot_id = arguments.get("snapshot_id") or latest_web_snapshot_id
                        inspected = web_elements_by_snapshot.get(snapshot_id, {}).get(arguments.get("element_id"))
                        if not inspected:
                            tool_result = {
                                "ok": False,
                                "error": "element_not_in_observed_snapshot",
                                "message": "Ese ID no pertenece al snapshot observado para el objetivo pendiente; inspeccioná la página nuevamente.",
                            }
                        elif self._is_site_profile_choice(
                            inspected,
                            goal_state.site_profile,
                            allow_menu_button=goal_state.site_profile_menu_pending,
                        ):
                            tool_result = registry.execute(name=tool_name, arguments=arguments)
                        # One reversible button activation may reveal a profile
                        # menu. The current profile's name is normally not the
                        # requested target, so this check must precede the
                        # exact-name guard below.
                        elif (
                            inspected.get("role") == "button"
                            and not goal_state.site_profile_menu_pending
                            and str(arguments.get("action") or "").strip().lower() in {"click", "invoke"}
                            and (
                                not goal_state.site_profile_source
                                or self._matches_pending_site_profile(
                                    inspected.get("name"), goal_state.site_profile_source
                                )
                            )
                        ):
                            profile_menu_opening = True
                            tool_result = registry.execute(name=tool_name, arguments=arguments)
                        elif not self._matches_pending_site_profile(inspected.get("name"), goal_state.site_profile):
                            tool_result = {
                                "ok": False,
                                "error": "invalid_goal_element",
                                "message": f"El objetivo pendiente es seleccionar el perfil {goal_state.site_profile}; no actúes sobre '{inspected.get('name')}'. Buscá el control con ese nombre exacto.",
                            }
                        else:
                            tool_result = {
                                "ok": False,
                                "error": "ambiguous_goal_element",
                                "message": f"'{inspected.get('name')}' parece un indicador o menú de cuenta, no una opción de perfil verificable.",
                            }
                    elif signature in failed_signatures:
                        tool_result = {
                            "ok": False,
                            "error": "duplicate_failed_action",
                            "message": "Esta acción equivalente ya falló sin información nueva. Elegí otra capacidad o explicá la limitación.",
                        }
                    else:

                        # =====================================
                        # EJECUTAR TOOL REAL
                        # =====================================

                        tool_result = (
                            registry.execute(

                                name=tool_name,

                                arguments=arguments
                            )
                        )

                    if not tool_result.get("ok"):
                        if (
                            tool_result.get("error") == "ambiguous_goal_element"
                            and pending_named_page_operation == "select_result"
                        ):
                            rejected_named_result_action = True
                        # Observations are evidence, not an attempted state
                        # change. A renderer can become available moments
                        # after a failed inspection, so never suppress a new
                        # web_inspect as a duplicate failed action.
                        if tool_name != "web_inspect":
                            failed_signatures.add(signature)
                        # A rejected model proposal is not evidence that the
                        # requested operation itself failed.  In particular,
                        # keep browser_open pending after rejecting a Google
                        # detour so the next turn can use the planned site.
                        if not (
                            tool_name == "browser_open"
                            and tool_result.get("error") in {"invalid_scope_action", "invalid_scope_argument"}
                        ):
                            goal_state.mark_failed(tool_name, tool_result.get("message"))
                        if tool_name == "open_application":
                            tool_result.setdefault(
                                "suggested_next_capability",
                                "browser_open",
                            )
                    elif tool_name == "browser_open":
                        goal_state.mark_completed("browser_open")
                    elif tool_name == "browser_search":
                        goal_state.mark_completed("browser_search")
                    elif tool_name == "web_act":
                        if profile_menu_opening:
                            goal_state.note_site_profile_menu_opening()
                        elif page_result_selection:
                            goal_state.note_web_action("select_result")
                        elif named_page_operation:
                            goal_state.note_web_action(named_page_operation)
                        elif (
                            goal_state.step("search_page")
                            and goal_state.step("search_page").status == "pending"
                            and str(arguments.get("action") or "").strip().lower() == "submit"
                        ):
                            goal_state.note_web_action("search_page")
                        elif (
                            goal_state.step("select_result")
                            and goal_state.step("select_result").status == "pending"
                            and str(arguments.get("action") or "").strip().lower() in {"click", "invoke", "select"}
                        ):
                            goal_state.note_web_action("select_result")
                        else:
                            goal_state.note_web_action()
                    elif tool_name == "web_inspect":
                        if goal_state.site_profile_menu_pending:
                            menu_has_target = any(
                                self._is_site_profile_choice(
                                    item,
                                    goal_state.site_profile,
                                    allow_menu_button=True,
                                )
                                for item in tool_result.get("elements", [])
                            )
                            goal_state.observe_site_profile_menu(menu_has_target)
                        goal_state.confirm_visible_site_profile(tool_result)
                        goal_state.verify_web_observation(tool_result)
                        latest_web_snapshot_id = tool_result.get("snapshot_id") or latest_web_snapshot_id
                        if tool_result.get("snapshot_id"):
                            web_elements_by_snapshot[tool_result["snapshot_id"]] = {
                                item.get("id"): item for item in tool_result.get("elements", []) if item.get("id")
                            }
                    else:
                        goal_state.mark_completed(tool_name)


                # =========================================
                # REGISTRAR ACCIÓN
                # =========================================

                action = {

                    "step":
                        step,

                    "mode":
                        current_mode,

                    "tool":
                        tool_name,

                    "arguments":
                        arguments,

                    "result":
                        tool_result
                }


                actions.append(
                    action
                )


                if verbose:

                    print(
                        "Resultado:"
                    )

                    print(
                        json.dumps(
                            tool_result,
                            indent=2,
                            ensure_ascii=False
                        )
                    )


                # =========================================
                # DEVOLVER RESULTADO AL BRAIN
                # =========================================

                messages.append({

                    "role":
                        "tool",

                    "tool_name":
                        tool_name or "unknown",

                    "content":
                        json.dumps(
                            tool_result,
                            ensure_ascii=False
                        )
                })

                # A named page selection is fully determined by the user's
                # requested value and the inspected accessible names.  Run it
                # as a short guarded micro-plan after a successful open rather
                # than spending separate model turns deciding inspect/click/
                # verify.  If the page is not ready or the requested name is
                # absent, the normal agent loop receives the observation and
                # continues without guessing.
                run_open_microplan = (
                    tool_name == "browser_open"
                    and tool_result.get("ok")
                    and (
                        (
                            goal_state.step("select_site_profile")
                            and goal_state.step("select_site_profile").status == "pending"
                        )
                        or (
                            goal_state.step("search_page")
                            and goal_state.step("search_page").status == "pending"
                        )
                        or self._next_named_page_operation(goal_state) is not None
                    )
                )
                run_observation_microplan = (
                    tool_name == "web_inspect"
                    and tool_result.get("ok")
                    and self._observation_matches_goal_page(tool_result, goal_state)
                    and (
                        (
                            goal_state.step("select_site_profile")
                            and goal_state.step("select_site_profile").status == "pending"
                            and bool(goal_state.site_profile_source)
                        )
                        or (
                            goal_state.step("search_page")
                            and goal_state.step("search_page").status == "pending"
                            and (
                                not goal_state.step("select_site_profile")
                                or goal_state.step("select_site_profile").status == "completed"
                            )
                        )
                        or self._next_named_page_operation(goal_state) is not None
                    )
                )
                if run_open_microplan or run_observation_microplan:
                    if run_open_microplan:
                        if goal_state.step("select_site_profile") and goal_state.step("select_site_profile").status == "pending":
                            fast_records = self._fast_named_web_selection(registry, goal_state)
                        elif goal_state.step("search_page") and goal_state.step("search_page").status == "pending":
                            fast_records = self._fast_page_search(registry, goal_state)
                        else:
                            _operation, fast_records = self._fast_pending_media_operation(registry, goal_state)
                    elif goal_state.step("select_site_profile") and goal_state.step("select_site_profile").status == "pending":
                        fast_records = self._fast_site_profile_switch_from_observation(registry, goal_state, tool_result)
                    elif goal_state.step("search_page") and goal_state.step("search_page").status == "pending":
                        fast_records = self._fast_page_search(registry, goal_state)
                    else:
                        _operation, fast_records = self._fast_pending_media_operation(registry, goal_state)
                    scheduled_follow_ups = {
                        kind for _tool, _arguments, _result, kind in fast_records
                        if kind in self._VERIFIED_PAGE_ACTIONS
                    }
                    if (
                        goal_state.step("select_result")
                        and any(
                            kind in {"submit_search", "submit_refined_search"}
                            for _tool, _arguments, _result, kind in fast_records
                        )
                    ):
                        # _fast_page_search already performed its complete,
                        # bounded result wait. Do not launch a second delayed
                        # selector while replaying that helper's own records.
                        scheduled_follow_ups.add("select_result")
                    fast_index = 0
                    while fast_index < len(fast_records):
                        fast_tool_name, fast_arguments, fast_result, fast_action_kind = fast_records[fast_index]
                        if fast_tool_name == "web_inspect":
                            if goal_state.site_profile_menu_pending:
                                menu_has_target = any(
                                    self._is_site_profile_choice(
                                        item,
                                        goal_state.site_profile,
                                        allow_menu_button=True,
                                    )
                                    for item in fast_result.get("elements", [])
                                )
                                goal_state.observe_site_profile_menu(menu_has_target)
                            goal_state.confirm_visible_site_profile(fast_result)
                            goal_state.verify_web_observation(fast_result)
                            latest_web_snapshot_id = fast_result.get("snapshot_id") or latest_web_snapshot_id
                            if fast_result.get("snapshot_id"):
                                web_elements_by_snapshot[fast_result["snapshot_id"]] = {
                                    item.get("id"): item for item in fast_result.get("elements", []) if item.get("id")
                                }
                        elif fast_tool_name == "web_act" and fast_result.get("ok"):
                            if fast_action_kind == "profile_menu_open":
                                goal_state.note_site_profile_menu_opening()
                            elif fast_action_kind in {"submit_search", "submit_refined_search"}:
                                goal_state.note_web_action("search_page")
                            elif fast_action_kind in self._VERIFIED_PAGE_ACTIONS:
                                goal_state.note_web_action(fast_action_kind)
                            else:
                                goal_state.note_web_action()

                        fast_action = {
                            "step": step,
                            "mode": "deterministic_web_plan",
                            "tool": fast_tool_name,
                            "arguments": fast_arguments,
                            "result": fast_result,
                        }
                        actions.append(fast_action)

                        if verbose:
                            print(f"Tool: {fast_tool_name}")
                            print(f"Argumentos: {fast_arguments}")
                            print("Resultado:")
                            print(json.dumps(fast_result, indent=2, ensure_ascii=False))

                        messages.append({
                            "role": "tool",
                            "tool_name": fast_tool_name,
                            "content": json.dumps(fast_result, ensure_ascii=False),
                        })

                        # A structured chain should keep moving while each
                        # fresh UIA snapshot verifies its predecessor.  The
                        # next operation remains bounded and dependency-gated;
                        # this is not an unbounded retry loop.
                        if fast_tool_name == "web_inspect":
                            operation = self._next_named_page_operation(goal_state)
                            if operation and operation not in scheduled_follow_ups:
                                scheduled_follow_ups.add(operation)
                                _operation, follow_up_records = self._fast_pending_media_operation(
                                    registry, goal_state
                                )
                                fast_records.extend(follow_up_records)
                        fast_index += 1

                    # The micro-plan has already performed an action and a
                    # post-action observation.  If that completed every
                    # requested step, producing the deterministic goal summary
                    # is safer and faster than asking the model for one more
                    # conversational turn.
                    if goal_state.status == "completed":
                        final_goal = self._final_goal_result(goal_state, "")
                        return {
                            "ok": True,
                            "text": final_goal["text"],
                            "status": final_goal["status"],
                            "steps": step,
                            "actions": actions,
                            "goal_state": final_goal["goal_state"],
                            "messages": messages,
                            "final_mode": current_mode,
                        }

                # The ordinary tool path can complete a structured plan too
                # (for example inspect → act → inspect).  Do not spend an
                # extra model turn after the post-action observation already
                # verified every requested step.  Unplanned free-form tasks
                # deliberately keep their existing model-controlled behavior.
                if goal_state.steps and goal_state.status == "completed":
                    final_goal = self._final_goal_result(
                        goal_state,
                        self._tool_completion_text(tool_name, tool_result),
                    )
                    return {
                        "ok": True,
                        "text": final_goal["text"],
                        "status": final_goal["status"],
                        "steps": step,
                        "actions": actions,
                        "goal_state": final_goal["goal_state"],
                        "messages": messages,
                        "final_mode": current_mode,
                    }

                if (
                    goal_state.step("select_site_profile")
                    and goal_state.step("select_site_profile").status == "blocked"
                    and goal_state.step("select_site_profile").reason == "profile_selection_not_available"
                ):
                    final_goal = self._final_goal_result(goal_state, "")
                    return {
                        "ok": True,
                        "text": final_goal["text"],
                        "status": final_goal["status"],
                        "steps": step,
                        "actions": actions,
                        "goal_state": final_goal["goal_state"],
                        "messages": messages,
                        "final_mode": current_mode,
                    }

                if run_open_microplan and len(result.tool_calls) > 1:
                    # The remaining calls were proposed against the page
                    # state that existed before Chrome accepted navigation.
                    # Discard them instead of allowing a stale batch to open
                    # Google, act on invented IDs, or inspect the wrong tab.
                    # The next agent turn receives the verified UIA state.
                    break


        # =================================================
        # PROTECCIÓN CONTRA LOOP INFINITO
        # =================================================

        goal_state.finalize()

        return {

            "ok":
                False,

            "error":
                (
                    "Mochi alcanzó el máximo de "
                    f"{self.max_steps} pasos."
                ),

            "steps":
                self.max_steps,

            "actions":
                actions,

            "status": goal_state.status,

            "goal_state": goal_state.compact(),

            "messages":
                messages
        }


# =========================================================
# SINGLETON
# =========================================================

agent_loop = (
    MochiAgentLoop()
)


def _configure_console_output():
    """Keep verbose Windows logs printable when UIA exposes any Unicode."""
    try:
        # Preserve the user's configured console encoding.  Replacing only
        # unrepresentable code points avoids a cp1252 ``UnicodeEncodeError``
        # while retaining normal Spanish output in legacy PowerShell hosts.
        sys.stdout.reconfigure(errors="backslashreplace")
        sys.stderr.reconfigure(errors="backslashreplace")
    except (AttributeError, OSError):
        pass


# =========================================================
# PRUEBA DIRECTA
# =========================================================

if __name__ == "__main__":

    _configure_console_output()

    registry = (
        create_mochi_tool_registry()
    )


    command = " ".join(sys.argv[1:]).strip()
    if not command:
        command = input("USUARIO: ").strip()
    if not command:
        raise SystemExit("Indicá una orden, por ejemplo: Busca Rick y Morty en Netflix.")


    print("")
    print(
        "======================================"
    )

    print(
        "         MOCHI AGENT LOOP"
    )

    print(
        "======================================"
    )

    print(
        f"USUARIO: {command}"
    )


    result = (
        agent_loop.run(

            user_command=command,

            registry=registry,

            mode="auto",

            verbose=True
        )
    )


    print("")
    print(
        "======================================"
    )

    print(
        "RESULTADO FINAL:"
    )

    print(
        json.dumps(
            {
                "ok":
                    result.get(
                        "ok"
                    ),

                "steps":
                    result.get(
                        "steps"
                    ),

                "actions":
                    result.get(
                        "actions"
                    ),

                "text":
                    result.get(
                        "text",
                        ""
                    ),

                "error":
                    result.get(
                        "error"
                    )
                ,
                "status": result.get("status")
            },
            indent=2,
            ensure_ascii=False
        )
    )

    print(
        "======================================"
    )
