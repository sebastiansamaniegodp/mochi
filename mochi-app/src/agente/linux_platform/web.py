"""
web_inspect / web_act en Linux: la página activa de Chromium o
Chrome, por accesibilidad (AT-SPI) en lugar de UI Automation.

Devuelve exactamente el mismo formato que web_interaction/ en
Windows (snapshot_id, IDs e1, e2..., roles, códigos de error),
así el prompt y las tools del LLM no cambian.

Equivalencias con los patrones de UIA:

- Invoke / LegacyIAccessible  →  Action (acción predeterminada)
- SelectionItem               →  Selection del padre
- Toggle                      →  Action de una casilla
- RangeValue                  →  Value (y las acciones
                                 increment / decrement)
- ScrollItem                  →  Component.ScrollTo
- SetFocus                    →  activar la ventana (KWin)
                                 + Component.GrabFocus
- Value.SetValue (escribir)   →  "Pegar como texto sin formato"
                                 del menú contextual de Chromium

Chromium no implementa EditableText, y en Wayland no se pueden
mandar teclas a otra ventana. Para escribir se copia el texto al
portapapeles y se usa el "Pegar" del menú contextual (todo por
AT-SPI); después se restaura el portapapeles. Enter y Espacio
(submit, keyboard_activate) van por el portal RemoteDesktop
(ver keyboard.py).

Chromium solo publica el contenido de las páginas si se abrió
con --force-renderer-accessibility (Mochi lo agrega al abrirlo).
"""

import re
import subprocess
import time
import unicodedata

import psutil

from linux_platform import atspi as at
from linux_platform import kwin
from linux_platform import keyboard
from web_interaction.element_store import ElementStore


def _normal(value):
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    return "".join(char for char in text if unicodedata.category(char) != "Mn")


# Roles de AT-SPI con los nombres que usa Mochi (los de UIA).
ROLES = {
    at.ROLE_PUSH_BUTTON: "button",
    at.ROLE_TOGGLE_BUTTON: "button",
    at.ROLE_PUSH_BUTTON_MENU: "button",
    at.ROLE_LINK: "link",
    at.ROLE_ENTRY: "textbox",
    at.ROLE_PASSWORD_TEXT: "textbox",
    at.ROLE_CHECK_BOX: "checkbox",
    at.ROLE_RADIO_BUTTON: "radio",
    at.ROLE_COMBO_BOX: "combobox",
    at.ROLE_MENU_ITEM: "menuitem",
    at.ROLE_CHECK_MENU_ITEM: "menuitem",
    at.ROLE_RADIO_MENU_ITEM: "menuitem",
    at.ROLE_PAGE_TAB: "tab",
    at.ROLE_LIST_ITEM: "listitem",
    at.ROLE_DOCUMENT_WEB: "document",
    at.ROLE_DOCUMENT_FRAME: "document",
    at.ROLE_SLIDER: "slider",
}

DOCUMENT_ROLES = {at.ROLE_DOCUMENT_WEB}

# Acciones de Chromium que no son la "predeterminada" del control.
SECONDARY_ACTIONS = {
    "showcontextmenu", "increment", "decrement", "focus", "blur",
    "scrollbackward", "scrollforward", "scrollup", "scrolldown",
    "scrollleft", "scrollright",
}

# Nombre de la acción predeterminada de Chromium → patrón de UIA
# equivalente (es lo que se informa en activation_pattern).
ACTION_PATTERNS = {
    "press": "invoke",
    "click": "invoke",
    "jump": "invoke",
    "activate": "invoke",
    "clickancestor": "invoke",
    "select": "selection_item",
    "switch": "selection_item",
    "open": "expand_collapse",
    "expand": "expand_collapse",
    "collapse": "expand_collapse",
}

# Ítems del menú contextual: "Pegar como texto sin formato Ctrl+Mayús+V"
# y "Pegar Ctrl+V" (el atajo no depende del idioma).
PASTE_PLAIN = re.compile(r"Ctrl\+[^+\s]+\+V$")
PASTE = re.compile(r"Ctrl\+V$")

BROWSER_CLASSES = ("chrom", "google-chrome")

# Roles de AT-SPI con texto para leer, y los que arman un renglón.
TEXT_ROLES = {at.ROLE_STATIC, at.ROLE_HEADING, at.ROLE_LABEL}
TEXT_BLOCK_ROLES = {
    at.ROLE_PARAGRAPH, at.ROLE_HEADING, at.ROLE_LIST_ITEM,
    at.ROLE_TABLE_ROW, at.ROLE_ARTICLE, at.ROLE_BLOCK_QUOTE,
}

# Un resultado de búsqueda muestra su dirección; los anuncios dicen
# que lo son.
RESULT_ADDRESS = re.compile(r"https?://|www\.|\s›\s")
MEDIA_DURATION = re.compile(
    r"\b\d{1,2}:\d{2}(?::\d{2})?\b|"
    r"\b\d+\s+(?:segundos?|seconds?|minutos?|minutes?|horas?|hours?)\b",
    re.I,
)
SPONSORED = re.compile(r"\b(?:patrocinad[oa]s?|sponsored|anuncio|publicidad)\b")

CHROMIUM_A11Y_HINT = (
    " En Linux, Chromium/Chrome tiene que estar abierto con "
    "--force-renderer-accessibility (cerrarlo del todo y abrirlo "
    "desde Mochi, o agregar ese flag al lanzador)."
)


# Chromium agrega al nombre de la pestaña el uso de memoria
# ("Título - Memory usage - 23.6 MB"); para comparar se saca.
TAB_MEMORY_SUFFIX = re.compile(r"\s+-\s+[^-]+\s+-\s+[\d.,]+\s*[KMG]i?B$")


def tab_title(name):

    return TAB_MEMORY_SUFFIX.sub("", str(name or "")).strip()


def result_title(name):
    """Título de un resultado de Google ("Título Sitio https://...")."""

    title = re.split(r"\s+(?:https?://|www\.)", str(name or ""), maxsplit=1)[0].strip()

    # Los videos de YouTube traen canal, vistas y duración en el nombre.
    if len(title) > 90:
        title = title[:90].rsplit(" ", 1)[0] + "…"

    return title


def role_name(node):
    """Rol de Mochi para un nodo de AT-SPI, o None."""

    role = node["role"]

    # Chromium usa "text" para algunos campos editables.
    if role == at.ROLE_TEXT and at.has_state(node["states"], at.STATE_EDITABLE):
        return "textbox"

    return ROLES.get(role)


def offscreen(node):

    return not at.has_state(node["states"], at.STATE_SHOWING)


# =========================================================
# INSPECTOR
# =========================================================

class WebInspector:

    MAX_ELEMENTS = 60
    MAX_NODES = 1200
    MAX_CONTENT_DEPTH = 32
    MAX_WINDOW_NODES = 1200
    MAX_READ_NODES = 4000

    def __init__(self, store=None, bus=None):

        self.store = store or ElementStore()
        self.bus = bus or at.Atspi()
        self._preferred_profile_directory = None
        self._preferred_profile_aliases = set()


    # -----------------------------------------------------
    # PERFIL PREFERIDO
    # -----------------------------------------------------

    def prefer_profile_directory(self, directory, aliases=None):
        """Preferir ventanas de un perfil pedido explícitamente."""

        self._preferred_profile_directory = _normal(directory) or None
        self._preferred_profile_aliases = {
            _normal(alias) for alias in (aliases or []) if _normal(alias)
        }


    @staticmethod
    def _window_profile_directory(pid):
        """--profile-directory de la línea de comandos del proceso."""

        try:
            command = " ".join(psutil.Process(int(pid)).cmdline())
            match = re.search(
                r"--profile-directory(?:=|\s+)(?:\"([^\"]+)\"|'([^']+)'|([^\s]+))",
                command,
                re.I,
            )
            return _normal(next((value for value in match.groups() if value), "")) if match else None
        except Exception:
            return None


    def _frame_matches_preferred_profile(self, frame, process_directory):

        if (
            self._preferred_profile_directory
            and process_directory == self._preferred_profile_directory
        ):
            return True

        if not frame or not self._preferred_profile_aliases:
            return False

        # El botón del avatar se llama como el perfil, igual que en
        # Windows (clase AvatarToolbarButton).
        try:
            nodes = self._walk_ui(frame, limit=350, max_depth=8)
            buttons = [node for node in nodes if node["role"] == at.ROLE_PUSH_BUTTON and node["name"]]
            replies = self.bus.call_many(
                (node["ref"], at.ACCESSIBLE, "GetAttributes", None, ()) for node in buttons
            )
        except at.AtspiError:
            return False

        for node, (ok, body) in zip(buttons, replies):
            if not ok or (body[0] or {}).get("class") != "AvatarToolbarButton":
                continue
            name = _normal(node["name"])
            if any(alias == name or alias in name for alias in self._preferred_profile_aliases):
                return True

        return False


    # -----------------------------------------------------
    # VENTANAS DEL NAVEGADOR
    # -----------------------------------------------------

    def _browser_frames(self):
        """
        Ventanas (frames) de las aplicaciones Chromium/Chrome en
        AT-SPI, con el pid de cada aplicación.
        """

        frames = []

        for ref, name, pid in self.bus.applications():

            if not any(token in name.casefold() for token in ("chrom", "google chrome")):
                continue

            children = self.bus.call(ref, at.ACCESSIBLE, "GetChildren")[0]
            refs = [tuple(child) for child in children if child[1] != at.NULL_PATH]

            for node in self.bus.describe(refs, with_children=False):
                if node and node["role"] == at.ROLE_FRAME:
                    frames.append((node, pid))

        return frames


    def _chrome_windows(self):
        """
        Lista de (ventana, título, activa), con la ventana activa y
        el perfil preferido primero. La ventana es un diccionario
        con el id de KWin, el pid y el frame de AT-SPI.
        """

        frames = self._browser_frames()

        try:
            kwin_windows = kwin.list_windows()
        except kwin.KWinError:
            kwin_windows = None

        windows = []

        if kwin_windows is not None:

            for item in kwin_windows:

                title = str(item.get("title") or "")
                window_class = str(item.get("resource_class") or "").casefold()

                if not (
                    any(token in window_class for token in BROWSER_CLASSES)
                    or "chrome" in title.casefold()
                    or "chromium" in title.casefold()
                ):
                    continue

                same_process = [node for node, pid in frames if pid == item.get("pid")]
                frame = next((node for node in same_process if node["name"] == title), None)
                if frame is None:
                    named = [node for node in same_process if node["name"]]
                    frame = named[0] if len(named) == 1 else None

                windows.append(({
                    "window_id": item.get("id"),
                    "pid": item.get("pid"),
                    "frame": frame,
                    "minimized": bool(item.get("minimized")),
                }, title, bool(item.get("active"))))

        else:

            # Sin KWin (otro escritorio): se confía en el estado
            # "activa" que publica el propio Chromium.
            for node, pid in frames:
                if node["name"]:
                    windows.append(({
                        "window_id": None,
                        "pid": pid,
                        "frame": node,
                        "minimized": False,
                    }, node["name"], at.has_state(node["states"], at.STATE_ACTIVE)))

        marked = []

        for window, title, active in windows:
            profile_match = self._frame_matches_preferred_profile(
                window["frame"],
                self._window_profile_directory(window["pid"]),
            ) if self._preferred_profile_directory or self._preferred_profile_aliases else False
            marked.append((window, title, active, profile_match))

        if self._preferred_profile_directory and not any(item[3] for item in marked):
            # Caer en otro perfil haría parecer válida una página de
            # otra cuenta.
            return []

        ordered = sorted(
            marked,
            key=lambda item: (
                not item[3] if self._preferred_profile_directory else False,
                not item[2],
            ),
        )

        return [(window, title, active) for window, title, active, _match in ordered]


    def _active_window(self):

        windows = self._chrome_windows()

        return windows[0] if windows else None


    def focus_window(self, window_id):
        """Activa la ventana del navegador (para foco y teclas)."""

        if not window_id:
            return False

        try:
            focused = kwin.window_action(window_id, "focus")
        except kwin.KWinError:
            return False

        # Wayland tarda un instante en pasarle el teclado.
        time.sleep(0.15)

        return focused


    # -----------------------------------------------------
    # RECORRIDOS
    # -----------------------------------------------------

    def _walk_ui(self, frame, limit, max_depth):
        """
        Interfaz del navegador (barras, pestañas, menús) sin entrar
        en el contenido de las páginas.
        """

        result = []
        level = [frame["ref"]]
        depth = 0

        while level and len(result) < limit and depth <= max_depth:

            next_level = []

            for node in self.bus.describe(level):

                if node is None:
                    continue

                result.append(node)

                if node["role"] not in DOCUMENT_ROLES:
                    next_level.extend(node["children"])

                if len(result) >= limit:
                    break

            level = next_level
            depth += 1

        return result


    def _find_documents(self, frame):

        documents = []
        level = [frame["ref"]]
        depth = 0
        nodes = 0

        while level and nodes < self.MAX_WINDOW_NODES and depth <= 14:

            next_level = []

            for node in self.bus.describe(level):

                if node is None:
                    continue

                nodes += 1

                if node["role"] in DOCUMENT_ROLES:
                    if not offscreen(node):
                        documents.append(node)
                    continue

                next_level.extend(node["children"])

            level = next_level
            depth += 1

        return documents


    def _descendants(self, document, max_nodes=None):
        """
        Recorrido a lo ancho, con la ascendencia semántica de cada
        nodo (rol y nombre de hasta 8 ancestros con nombre), igual
        que en Windows.

        Devuelve (nodo, ascendencia, orden): el orden es el camino
        de índices desde el documento, así que ordenar por él da el
        orden de la página (el recorrido a lo ancho lo mezcla: en
        Google, un video del cuarto resultado salía antes que el
        primer resultado).
        """

        max_nodes = max_nodes or self.MAX_NODES
        result = []
        level = [(child, (), (index,)) for index, child in enumerate(document["children"])]
        depth = 0

        while level and len(result) < max_nodes and depth <= self.MAX_CONTENT_DEPTH:

            nodes = self.bus.describe(ref for ref, _ancestry, _order in level)
            next_level = []

            for node, (_ref, ancestry, order) in zip(nodes, level):

                if node is None:
                    continue

                if len(result) >= max_nodes:
                    break

                result.append((node, ancestry, order))

                role = role_name(node)
                context = ancestry + ((role, node["name"]),) if node["name"] else ancestry
                next_level.extend(
                    (child, context[-8:], order + (index,))
                    for index, child in enumerate(node["children"])
                )

            level = next_level
            depth += 1

        return result


    # -----------------------------------------------------
    # DATOS EXTRA DE LOS CONTROLES
    # -----------------------------------------------------

    def _range_metadata_many(self, nodes):

        requests = []

        for node in nodes:
            for name in ("CurrentValue", "MinimumValue", "MaximumValue", "MinimumIncrement"):
                requests.append(self.bus.property_request(node["ref"], at.VALUE, name))

        replies = self.bus.call_many(requests)
        result = []

        for index, _node in enumerate(nodes):

            values = replies[index * 4:index * 4 + 4]

            if not all(ok for ok, _body in values):
                result.append(None)
                continue

            current, minimum, maximum, increment = (float(body[0][1]) for _ok, body in values)

            # AT-SPI tiene un solo paso; UIA distingue chico y grande.
            result.append({
                "value": current,
                "minimum": minimum,
                "maximum": maximum,
                "small_change": increment,
                "large_change": increment,
            })

        return result


    def _text_values_many(self, nodes):
        """Texto de campos y cuadros combinados (nunca contraseñas)."""

        replies = self.bus.call_many(
            (node["ref"], at.TEXT, "GetText", "ii", (0, -1)) for node in nodes
        )
        attributes = self.bus.call_many(
            (node["ref"], at.ACCESSIBLE, "GetAttributes", None, ()) for node in nodes
        )
        values = []

        for node, (ok, body), (attributes_ok, attributes_body) in zip(nodes, replies, attributes):

            password = node["role"] == at.ROLE_PASSWORD_TEXT or (
                attributes_ok and (attributes_body[0] or {}).get("text-input-type") == "password"
            )

            if not ok or password:
                values.append("")
                continue

            text = str(body[0] or "").replace("￼", "").strip()
            values.append(text[:240])

        return values


    @staticmethod
    def _selected(node, role):
        """Equivalente de SelectionItem.IsSelected, o None."""

        if role == "radio":
            return at.has_state(node["states"], at.STATE_CHECKED)

        if at.has_state(node["states"], at.STATE_SELECTABLE):
            return at.has_state(node["states"], at.STATE_SELECTED)

        return None


    def _document_url(self, document):

        try:
            attributes = self.bus.call(document["ref"], at.DOCUMENT, "GetAttributes")[0]
        except at.AtspiError:
            return None

        url = str((attributes or {}).get("URI") or "").strip()

        return url or None


    # -----------------------------------------------------
    # PESTAÑAS
    # -----------------------------------------------------

    def activate_matching_tab(self, target, preferred_title=None):
        """Activa una pestaña ya abierta, con la semántica de Selection."""

        try:
            active = self._active_window()
        except at.AtspiError as error:
            return {"ok": False, "error": "uia_unavailable", "message": str(error)}

        if not active:
            return {"ok": False, "error": "chrome_window_not_found", "message": "No encontré una ventana visible de Chrome."}

        expected = _normal(target)
        preferred = _normal(preferred_title)
        if not expected:
            return {"ok": False, "error": "tab_target_missing", "message": "No se indicó la pestaña a reutilizar."}

        frame = active[0]["frame"]
        if not frame:
            return {"ok": False, "error": "uia_connection_failed", "message": "La ventana de Chrome no aparece en el bus de accesibilidad."}

        try:
            nodes = self._walk_ui(frame, limit=600, max_depth=10)
        except at.AtspiError as error:
            return {"ok": False, "error": "tab_enumeration_failed", "message": str(error)}

        candidates = []

        for node in nodes:
            if node["role"] != at.ROLE_PAGE_TAB:
                continue
            normalized_name = _normal(tab_title(node["name"]))
            if expected not in normalized_name:
                continue
            score = 1
            if preferred and normalized_name == preferred:
                score = 3
            elif preferred and (preferred in normalized_name or normalized_name in preferred):
                score = 2
            candidates.append((score, tab_title(node["name"]), node))

        if not candidates:
            return {"ok": False, "error": "existing_tab_not_found", "message": "No encontré una pestaña abierta que corresponda al destino."}

        candidates.sort(key=lambda candidate: candidate[0], reverse=True)
        if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
            return {"ok": False, "error": "existing_tab_ambiguous", "message": "Hay varias pestañas abiertas que podrían corresponder al destino."}

        _score, name, tab = candidates[0]

        try:
            # Chromium no acepta SelectChild en la lista de pestañas;
            # la acción predeterminada de la pestaña la activa.
            if not select_in_parent(self.bus, tab["ref"]):
                action = default_action(self.bus, tab["ref"])
                if action is None:
                    return {"ok": False, "error": "tab_selection_unsupported", "message": "La pestaña existente no admite selección accesible."}
                self.bus.do_action(tab["ref"], action[0])
        except at.AtspiError as error:
            return {"ok": False, "error": "tab_activation_failed", "message": str(error)}

        return {"ok": True, "message": "Pestaña existente activada.", "tab_title": name}


    # -----------------------------------------------------
    # INSPECCIÓN
    # -----------------------------------------------------

    def _active_page(self, max_nodes=None):
        """
        Ventana, documento y recorrido de la página activa, o el
        error con el mismo formato que inspect.
        """

        try:
            active = self._active_window()
        except at.AtspiError as error:
            return {"ok": False, "error": "uia_unavailable", "message": f"La accesibilidad (AT-SPI) no está disponible: {error}"}

        if not active:
            return {"ok": False, "error": "chrome_window_not_found", "message": "No encontré una ventana visible de Chrome."}

        window, title, _ = active

        if not window["frame"]:
            return {"ok": False, "error": "uia_connection_failed", "message": "La ventana de Chrome no aparece en el bus de accesibilidad." + CHROMIUM_A11Y_HINT}

        try:
            documents = self._find_documents(window["frame"])
            ranked = []
            for document in documents:
                subtree = self._descendants(document, max_nodes=max_nodes)
                semantic = sum(1 for node, _ancestry, _order in subtree if role_name(node))
                name = document["name"]
                score = semantic * 10 + (5 if name and _normal(name) in _normal(title) else 0)
                ranked.append((score, document, subtree))
        except at.AtspiError as error:
            return {"ok": False, "error": "uia_connection_failed", "message": str(error)}

        ranked.sort(key=lambda item: item[0], reverse=True)
        # Un documento vacío es Chromium sin accesibilidad de páginas.
        if not ranked or ranked[0][0] <= 0 or not ranked[0][2]:
            hint = " La ventana de Chrome está minimizada." if window["minimized"] else CHROMIUM_A11Y_HINT
            return {"ok": False, "error": "web_content_unavailable", "message": "Chrome no está exponiendo un documento web accesible utilizable." + hint}

        document, subtree = ranked[0][1], ranked[0][2]

        return {"ok": True, "window": window, "title": title, "document": document, "subtree": subtree}


    def inspect(self, query=None):

        wanted = _normal(query)
        previous = self.store.get()

        page = self._active_page()
        if not page["ok"]:
            return page

        window, document, subtree = page["window"], page["document"], page["subtree"]
        page_title = document["name"] or page["title"]

        all_candidates, visible_candidates, all_text_context = [], [], []
        priority = {"link": 5, "button": 5, "textbox": 4, "combobox": 4, "slider": 4, "checkbox": 4, "radio": 4, "menuitem": 4, "tab": 4, "listitem": 2}

        def semantic_container(ancestry):
            for role, ancestor_name in reversed(ancestry):
                if role in {"listitem", "menuitem", "tab"} and ancestor_name:
                    return ancestor_name[:240]
            return ""

        def semantic_section(ancestry):
            for _role, ancestor_name in reversed(ancestry):
                normalized = _normal(ancestor_name)
                if any(token in normalized for token in ("recomend", "recommended", "suger", "related", "a continuacion", "up next")):
                    return ancestor_name[:240]
            return ""

        semantic_nodes = []

        for node, ancestry, order in subtree:
            role = role_name(node)
            if role and node["name"]:
                semantic_nodes.append((node, ancestry, role, order))
            elif node["name"]:
                all_text_context.append((order, node["name"][:180]))

        try:
            sliders = [node for node, _ancestry, role, _order in semantic_nodes if role == "slider"]
            ranges = dict(zip((id(node) for node in sliders), self._range_metadata_many(sliders)))
            fields = [node for node, _ancestry, role, _order in semantic_nodes if role in {"textbox", "combobox"}]
            texts = dict(zip((id(node) for node in fields), self._text_values_many(fields)))
        except at.AtspiError as error:
            return {"ok": False, "error": "uia_connection_failed", "message": str(error)}

        for node, ancestry, role, order in semantic_nodes:
            candidate = (
                priority.get(role, 1),
                role,
                node["name"],
                texts.get(id(node), ""),
                offscreen(node),
                ranges.get(id(node)),
                self._selected(node, role),
                semantic_container(ancestry),
                semantic_section(ancestry),
                node["ref"],
                order,
            )
            all_candidates.append(candidate)
            # Sin filtro van los controles a la vista y, además, los
            # enlaces de más abajo (marcados offscreen): si no, en una
            # ventana chica no se ve ni un resultado de Google.
            if not candidate[4] or role == "link":
                visible_candidates.append(candidate)

        filtered_candidates = [item for item in all_candidates if wanted in _normal(f"{item[2]} {item[3]}")] if wanted else visible_candidates
        all_text_context = [name for _order, name in sorted(all_text_context)]
        filtered_context = [item for item in all_text_context if wanted in _normal(item)][:12] if wanted else all_text_context[:12]
        all_text_context = all_text_context[:12]
        query_matched = not wanted or bool(filtered_candidates or filtered_context)
        candidates_by_name = filtered_candidates if query_matched else visible_candidates
        text_context = filtered_context if query_matched else all_text_context

        elements, dedupe, chosen = {}, set(), []

        # Primero se eligen los que entran (a la vista antes que los de
        # más abajo, y por prioridad del rol) y después se numeran en
        # el orden de la página.
        for candidate in sorted(candidates_by_name, key=lambda item: (bool(item[4]) and not wanted, -item[0], item[10])):
            # Con el rol: en YouTube el cuadro y el botón se llaman "Search".
            key = (candidate[1], _normal(candidate[2]), candidate[3])
            if key in dedupe or len(chosen) >= self.MAX_ELEMENTS:
                continue
            dedupe.add(key)
            chosen.append(candidate)

        for _, role, name, value, is_offscreen, range_metadata, selected, container, section, ref, _order in sorted(chosen, key=lambda item: item[10]):
            element_id = f"e{len(elements) + 1}"
            elements[element_id] = {
                "element": ref,
                "runtime_id": ref,
                "window_id": window["window_id"],
                "role": role,
                "name": name,
                "value": value,
                "offscreen": is_offscreen,
                "range": range_metadata,
                "selected": selected,
                "container": container,
                "section": section,
            }

        url = self._document_url(document)
        fingerprint = tuple((item["role"], item["name"], item["value"], item["offscreen"], item["selected"], item["container"], item["section"], tuple(sorted((item["range"] or {}).items()))) for item in elements.values())
        snapshot_id = self.store.create(page_title, url, fingerprint, elements)
        changed = bool(previous and previous.fingerprint != fingerprint)
        public = [
            {
                "id": key,
                "role": item["role"],
                "name": item["name"],
                "value": item["value"],
                "offscreen": item["offscreen"],
                **({"selected": item["selected"]} if item["selected"] is not None else {}),
                **({"range": item["range"]} if item["range"] else {}),
                **({"container": item["container"]} if item["container"] else {}),
                **({"section": item["section"]} if item["section"] else {}),
            }
            for key, item in elements.items()
        ]

        return {"ok": True, "snapshot_id": snapshot_id, "page": {"title": page_title, "url": url}, "elements": public, "context": text_context, "changed": changed, "element_count": len(public), "query_matched": query_matched}


    # -----------------------------------------------------
    # REGIONES DE LA PÁGINA
    # -----------------------------------------------------

    def _content_filter(self, subtree):
        """
        Dice si un nodo (por su orden) es contenido: adentro de la
        región principal (<main>, si la página tiene) y afuera de
        los menús, la cabecera, el pie y el cuadro de búsqueda.
        """

        landmarks = [(node, order) for node, _ancestry, order in subtree if node["role"] == at.ROLE_LANDMARK]
        replies = self.bus.call_many(
            (node["ref"], at.ACCESSIBLE, "GetAttributes", None, ())
            for node, _order in landmarks
        )

        mains, skipped = [], []
        for (_node, order), (ok, body) in zip(landmarks, replies):
            kind = str(dict(body[0]).get("xml-roles", "")) if ok else ""
            if kind == "main":
                mains.append(order)
            elif kind in {"navigation", "banner", "contentinfo", "search"}:
                skipped.append(order)

        def inside(order, prefix):
            return order[:len(prefix)] == prefix

        def is_content(order):
            if mains and not any(inside(order, main) for main in mains):
                return False
            return not any(inside(order, region) for region in skipped)

        return is_content


    # -----------------------------------------------------
    # RESULTADOS DE UNA BÚSQUEDA
    # -----------------------------------------------------

    def page_results(self, limit=10):
        """
        Resultados de la página activa en el orden en que se ven
        (Google, Bing, DuckDuckGo...), como un snapshot de inspect:
        sus IDs sirven para web_act.

        Un resultado es un enlace del contenido que muestra su
        dirección ("Título  Sitio https://sitio › ruta"); si la
        página no tiene ninguno así (una tienda, un foro), se usan
        los enlaces del contenido con nombre de varias palabras.
        """

        page = self._active_page()
        if not page["ok"]:
            return page

        window, document, subtree = page["window"], page["document"], page["subtree"]

        try:
            is_content = self._content_filter(subtree)
        except at.AtspiError as error:
            return {"ok": False, "error": "uia_connection_failed", "message": str(error)}

        links = sorted(
            (
                (order, node) for node, _ancestry, order in subtree
                if role_name(node) == "link" and node["name"] and is_content(order)
                and not SPONSORED.search(_normal(node["name"]))
            ),
            key=lambda item: item[0],
        )
        with_address = [item for item in links if RESULT_ADDRESS.search(item[1]["name"])]
        # En YouTube, un video dice su duración ("... 3 minutos y 8
        # segundos"); la miniatura sola (solo la duración) no cuenta.
        with_duration = [
            item for item in links
            if MEDIA_DURATION.search(item[1]["name"])
            and len(MEDIA_DURATION.sub(" ", item[1]["name"]).split()) >= 3
        ]
        pool = with_address or with_duration or [item for item in links if len(item[1]["name"].split()) >= 3]

        elements, seen = {}, set()
        for _order, node in pool:
            # Google repite el resultado en la miniatura del video.
            key = _normal(result_title(node["name"]))[:60]
            if key in seen:
                continue
            seen.add(key)
            elements[f"e{len(elements) + 1}"] = {
                "element": node["ref"],
                "runtime_id": node["ref"],
                "window_id": window["window_id"],
                "role": "link",
                "name": node["name"],
                "value": "",
                "offscreen": offscreen(node),
                "range": None,
                "selected": None,
                "container": "",
                "section": "",
            }
            if len(elements) >= limit:
                break

        page_title = document["name"] or page["title"]
        url = self._document_url(document)
        fingerprint = tuple(("link", item["name"]) for item in elements.values())
        snapshot_id = self.store.create(page_title, url, fingerprint, elements)
        results = [
            {"id": key, "title": result_title(item["name"]), "name": item["name"], "offscreen": item["offscreen"]}
            for key, item in elements.items()
        ]

        return {"ok": True, "snapshot_id": snapshot_id, "page": {"title": page_title, "url": url}, "results": results, "result_count": len(results)}


    # -----------------------------------------------------
    # TEXTO DE LA PÁGINA
    # -----------------------------------------------------

    def read_text(self, max_chars=2400):
        """
        Texto legible de la página activa, en orden: títulos y
        párrafos de la región principal, sin menús ni pie. Un
        renglón por título, párrafo, ítem de lista o fila.
        """

        page = self._active_page(max_nodes=self.MAX_READ_NODES)
        if not page["ok"]:
            return page

        document, subtree = page["document"], page["subtree"]

        try:
            is_content = self._content_filter(subtree)
        except at.AtspiError as error:
            return {"ok": False, "error": "uia_connection_failed", "message": str(error)}

        roles = {order: node["role"] for node, _ancestry, order in subtree}

        def block(order):
            for size in range(len(order), 0, -1):
                if roles.get(order[:size]) in TEXT_BLOCK_ROLES:
                    return order[:size]
            return None

        lines, current, current_block, heading = [], [], None, None
        total, truncated = 0, False

        for node, _ancestry, order in sorted(subtree, key=lambda item: item[2]):

            if node["role"] not in TEXT_ROLES or not node["name"] or not is_content(order):
                continue

            # El texto adentro de un título repite el nombre del título.
            if heading and order[:len(heading)] == heading and order != heading:
                continue
            heading = order if node["role"] == at.ROLE_HEADING else None

            text = " ".join(node["name"].split())
            node_block = block(order)

            if current and (heading or node_block != current_block):
                lines.append(" ".join(current))
                current = []
            current_block = node_block

            if current and current[-1] == text:
                continue

            current.append(text)
            total += len(text) + 1
            if total >= max_chars:
                truncated = True
                break

        if current:
            lines.append(" ".join(current))

        text = "\n".join(line for line in lines if line.strip())
        if truncated:
            text = text[:max_chars].rsplit(" ", 1)[0] + " …"

        return {
            "ok": bool(text),
            "page": {"title": document["name"] or page["title"], "url": self._document_url(document)},
            "text": text,
            "truncated": truncated,
            **({} if text else {"error": "no_text", "message": "La página no tiene texto legible por accesibilidad."}),
        }


# =========================================================
# AYUDAS COMUNES
# =========================================================

def select_in_parent(bus, ref):
    """
    Selecciona un hijo con la interfaz Selection de su padre
    (pestañas, opciones de una lista). Devuelve False si el padre
    no la tiene.
    """

    parent = tuple(bus.get_property(ref, at.ACCESSIBLE, "Parent"))
    if not parent or parent[1] == at.NULL_PATH:
        return False

    index = int(bus.call(ref, at.ACCESSIBLE, "GetIndexInParent")[0])

    ok, body = bus.call_many([(parent, at.SELECTION, "SelectChild", "i", (index,))])[0]

    return bool(ok and body and body[0])


def default_action(bus, ref):
    """(índice, nombre) de la acción predeterminada, o None."""

    for index, name in enumerate(bus.action_names(ref)):
        if name and name.casefold() not in SECONDARY_ACTIONS:
            return index, name

    return None


def named_action(bus, ref, wanted):

    for index, name in enumerate(bus.action_names(ref)):
        if name.casefold() == wanted:
            return index

    return None


# =========================================================
# PORTAPAPELES (para escribir)
# =========================================================

def _save_clipboard():
    """(tipo, bytes) de lo que hay en el portapapeles, o None."""

    try:
        types = subprocess.run(
            ["wl-paste", "--list-types"],
            capture_output=True,
            timeout=3,
        )
        available = types.stdout.decode("utf-8", "replace").split()

        if types.returncode != 0 or not available:
            return None

        preferred = next(
            (kind for kind in ("text/plain;charset=utf-8", "text/plain", "UTF8_STRING") if kind in available),
            available[0],
        )

        content = subprocess.run(
            ["wl-paste", "--no-newline", "--type", preferred],
            capture_output=True,
            timeout=3,
        )
        if content.returncode != 0:
            return None

        return preferred, content.stdout

    except (OSError, subprocess.SubprocessError):
        return None


def _set_clipboard(kind, data):

    process = subprocess.Popen(
        ["wl-copy", "--type", kind],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    process.stdin.write(data)
    process.stdin.close()


def _restore_clipboard(saved):

    try:
        if saved is None:
            subprocess.run(["wl-copy", "--clear"], timeout=3)
        else:
            _set_clipboard(*saved)
    except (OSError, subprocess.SubprocessError):
        pass


# =========================================================
# ACCIONES
# =========================================================

class WebInteractor:

    def __init__(self, inspector=None):

        self.inspector = inspector or WebInspector()


    @property
    def bus(self):

        return self.inspector.bus


    @staticmethod
    def _risk(action, name):
        text = str(name).casefold()
        if any(word in text for word in ("enviar", "publicar", "comprar", "pagar", "eliminar", "borrar", "confirmar")):
            return "commit"
        return "input" if action == "type" else "selection" if action in {"select", "toggle"} else "navigation"


    # -----------------------------------------------------

    def _focus(self, item):
        """Ventana al frente y foco en el control (como SetFocus)."""

        self.inspector.focus_window(item.get("window_id"))
        self.bus.call(item["element"], at.COMPONENT, "GrabFocus")
        time.sleep(0.05)


    def _activate(self, ref):
        """
        Activación semántica, sin coordenadas: la acción
        predeterminada que publica Chromium; si no tiene, la
        selección del padre. Devuelve el patrón de UIA equivalente.
        """

        action = default_action(self.bus, ref)

        if action is not None:
            index, name = action
            self.bus.do_action(ref, index)
            return ACTION_PATTERNS.get(name.casefold(), "legacy_accessible")

        if select_in_parent(self.bus, ref):
            return "selection_item"

        return None


    def _press_key(self, item, keysym, count=1):

        self._focus(item)
        keyboard.get_keyboard().press(keysym, count)


    # -----------------------------------------------------

    def _type(self, item, value):
        """
        Reemplaza el texto del campo: selecciona todo y pega con
        "Pegar como texto sin formato" del menú contextual.
        """

        ref = item["element"]

        if not self._editable(ref):
            return {"ok": False, "error": "pattern_unsupported", "message": "Ese campo no permite escritura accesible."}

        menu_action = named_action(self.bus, ref, "showcontextmenu")
        if menu_action is None:
            return {"ok": False, "error": "pattern_unsupported", "message": "Ese campo no permite escritura accesible."}

        saved = _save_clipboard()

        try:
            self._focus(item)
            self._select_all_text(ref)

            if value == "":
                # Sin nada que pegar: "Cortar" vacía la selección.
                pasted = self._context_menu_item(ref, menu_action, re.compile(r"Ctrl\+X$"))
            else:
                _set_clipboard("text/plain;charset=utf-8", value.encode("utf-8"))
                time.sleep(0.1)
                pasted = self._context_menu_item(ref, menu_action, PASTE_PLAIN, PASTE)

            if not pasted:
                return {"ok": False, "error": "uia_action_failed", "message": "No encontré la opción Pegar en el menú contextual de Chrome."}

            # Chromium lee el portapapeles después: esperar a que el
            # texto aparezca antes de restaurarlo.
            expected = value.strip()
            for _attempt in range(20):
                time.sleep(0.05)
                ok, body = self.bus.call_many([(ref, at.TEXT, "GetText", "ii", (0, -1))])[0]
                current = str(body[0] if ok and body else "").replace("￼", "").strip()
                if (expected and expected in current) or (not expected and not current):
                    return None

            return {"ok": False, "error": "uia_action_failed", "message": "Chrome no pegó el texto (¿la ventana perdió el foco?)."}

        finally:
            time.sleep(0.1)
            _restore_clipboard(saved)


    def _editable(self, ref):
        """
        Chromium no marca EDITABLE los campos de una línea: vale el
        rol (campo de texto, o combobox que es un <input>).
        """

        states = self.bus.states(ref)

        if at.has_state(states, at.STATE_READ_ONLY):
            return False

        if at.has_state(states, at.STATE_EDITABLE):
            return True

        role = int(self.bus.call(ref, at.ACCESSIBLE, "GetRole")[0])

        if role in {at.ROLE_ENTRY, at.ROLE_PASSWORD_TEXT}:
            return True

        if role == at.ROLE_COMBO_BOX:
            attributes = self.bus.call(ref, at.ACCESSIBLE, "GetAttributes")[0] or {}
            return attributes.get("tag") in {"input", "textarea"}

        return False


    def _select_all_text(self, ref):

        count = int(self.bus.get_property(ref, at.TEXT, "CharacterCount"))
        if count <= 0:
            return

        if int(self.bus.call(ref, at.TEXT, "GetNSelections")[0]) > 0:
            self.bus.call(ref, at.TEXT, "SetSelection", "iii", (0, 0, count))
        else:
            self.bus.call(ref, at.TEXT, "AddSelection", "ii", (0, count))


    def _context_menu_item(self, ref, menu_action, *patterns):
        """
        Abre el menú contextual del campo y activa el ítem pedido.
        A veces el menú no se abre al primer intento (página recién
        cargada): se vuelve a pedir una vez.
        """

        application = (ref[0], "/org/a11y/atspi/accessible/root")

        for _opening in range(2):

            self.bus.do_action(ref, menu_action)

            for _attempt in range(10):

                time.sleep(0.1)
                items = self._menu_items(application)

                for pattern in patterns:
                    for node in items:
                        if pattern.search(node["name"]) and at.has_state(node["states"], at.STATE_ENABLED):
                            self.bus.do_action(node["ref"], 0)
                            return True

        return False


    def _menu_items(self, application):
        """Ítems de menú visibles de la aplicación (sin entrar en páginas)."""

        items = []
        level = [application]
        nodes = 0

        while level and nodes < 2000:

            next_level = []

            for node in self.bus.describe(level):

                if node is None:
                    continue

                nodes += 1

                if node["role"] == at.ROLE_MENU_ITEM and not offscreen(node):
                    items.append(node)

                if node["role"] not in DOCUMENT_ROLES:
                    next_level.extend(node["children"])

            level = next_level

        return items


    # -----------------------------------------------------

    def _set_range(self, item, target_value):
        """
        Value.CurrentValue; si Chromium lo ignora, se calibra un
        paso con la acción increment/decrement y se repite.
        """

        ref = item["element"]
        minimum = float(self.bus.get_property(ref, at.VALUE, "MinimumValue"))
        maximum = float(self.bus.get_property(ref, at.VALUE, "MaximumValue"))

        if not minimum <= target_value <= maximum:
            return {"ok": False, "error": "range_value_out_of_bounds", "message": f"El valor debe estar entre {minimum:g} y {maximum:g}."}, None, None

        def current():
            return float(self.bus.get_property(ref, at.VALUE, "CurrentValue"))

        try:
            before_value = current()
        except (at.AtspiError, TypeError, ValueError):
            before_value = None

        self.bus.set_property(ref, at.VALUE, "CurrentValue", "d", float(target_value))
        strategy = "range_value"
        observed = None

        if before_value is not None and abs(target_value - before_value) > 0.05:

            time.sleep(0.06)
            after_set = current()
            observed = after_set

            if abs(after_set - before_value) <= 0.05:

                direction = "increment" if target_value > after_set else "decrement"
                step_action = named_action(self.bus, ref, direction)

                if step_action is None:
                    return {"ok": False, "error": "range_value_not_applied", "message": "El proveedor no aplicó el valor ni respondió al ajuste accesible del control."}, None, None

                self.bus.do_action(ref, step_action)
                time.sleep(0.08)
                calibrated_value = current()
                calibrated_step = calibrated_value - after_set
                requested_direction = 1 if target_value > after_set else -1

                if calibrated_step * requested_direction <= 0.05:
                    return {"ok": False, "error": "range_value_not_applied", "message": "El proveedor no aplicó el valor ni respondió al ajuste accesible del control."}, None, None

                remaining = target_value - calibrated_value
                additional = min(int(round(abs(remaining) / abs(calibrated_step))), 2000)

                if additional:
                    remaining_action = named_action(self.bus, ref, "increment" if remaining > 0 else "decrement")
                    for _index in range(additional):
                        self.bus.do_action(ref, remaining_action)
                    time.sleep(0.08)

                observed = current()
                strategy = "range_step_actions"

        return None, strategy, observed


    # -----------------------------------------------------

    def act(self, element_id, action, value=None, snapshot_id=None):

        action = str(action or "").strip().lower()
        snapshot, item, error = self.inspector.store.resolve(element_id, snapshot_id)
        if error:
            return {"ok": False, "error": error, "stale_element": error == "stale_element", "message": "El elemento ya no es válido; inspeccioná la página nuevamente." if error == "stale_element" else "No encontré el elemento solicitado."}

        ref = item["element"]
        activation_pattern = None
        range_strategy = None
        observed_range_value = None
        keyboard_fallback = None

        try:
            if not self.bus.exists(ref):
                self.inspector.store.mark_stale(snapshot.id)
                return {"ok": False, "error": "stale_element", "stale_element": True, "message": "El elemento cambió; inspeccioná nuevamente."}

            if action == "focus":
                self._focus(item)

            elif action in {"click", "invoke"}:
                activation_pattern = self._activate(ref)
                if activation_pattern is None:
                    return {"ok": False, "error": "pattern_unsupported", "message": "Ese control no admite una acción invocable segura."}

            elif action == "keyboard_activate":
                if item["role"] not in {"button", "link", "menuitem"}:
                    return {"ok": False, "error": "invalid_keyboard_target", "message": "La activación por teclado solo admite controles accionables."}
                keysym = keyboard.KEY_SPACE if item["role"] == "button" else keyboard.KEY_RETURN
                try:
                    self._press_key(item, keysym)
                    activation_pattern = "keyboard_space" if keysym == keyboard.KEY_SPACE else "keyboard_enter"
                except keyboard.KeyboardUnavailable as keyboard_error:
                    # Sin permiso de teclado: la acción predeterminada
                    # del control hace lo mismo en casi todos los sitios.
                    activation_pattern = self._activate(ref)
                    if activation_pattern is None:
                        return {"ok": False, "error": "uia_action_failed", "message": str(keyboard_error)}
                    keyboard_fallback = str(keyboard_error)

            elif action == "legacy_activate":
                if item["role"] not in {"button", "link", "menuitem"}:
                    return {"ok": False, "error": "invalid_legacy_target", "message": "La acción predeterminada solo admite controles accionables."}
                default = default_action(self.bus, ref)
                if default is None:
                    return {"ok": False, "error": "pattern_unsupported", "message": "Ese control no publica una acción predeterminada accesible."}
                self.bus.do_action(ref, default[0])
                activation_pattern = "legacy_accessible"

            elif action == "select":
                if not select_in_parent(self.bus, ref):
                    named = named_action(self.bus, ref, "select")
                    if named is None and item["role"] == "radio":
                        default = default_action(self.bus, ref)
                        named = default[0] if default else None
                    if named is None:
                        return {"ok": False, "error": "pattern_unsupported", "message": "Ese control no admite selección."}
                    self.bus.do_action(ref, named)

            elif action == "toggle":
                states = self.bus.states(ref)
                default = default_action(self.bus, ref)
                if default is None or not (
                    item["role"] == "checkbox"
                    or at.has_state(states, at.STATE_CHECKABLE)
                    or default[1].casefold() in {"check", "uncheck", "toggle"}
                ):
                    return {"ok": False, "error": "pattern_unsupported", "message": "Ese control no admite alternar."}
                self.bus.do_action(ref, default[0])

            elif action == "type":
                if value is None:
                    return {"ok": False, "error": "value_required", "message": "Falta el texto a escribir."}
                failure = self._type(item, str(value))
                if failure:
                    return failure

            elif action == "set_range":
                if value is None:
                    return {"ok": False, "error": "value_required", "message": "Falta el valor numérico del control deslizante."}
                try:
                    target_value = float(value)
                except (TypeError, ValueError):
                    return {"ok": False, "error": "invalid_range_value", "message": "El valor del control deslizante debe ser numérico."}
                if item["role"] != "slider" and not item.get("range"):
                    return {"ok": False, "error": "pattern_unsupported", "message": "Ese control no permite ajuste accesible por rango."}
                failure, range_strategy, observed_range_value = self._set_range(item, target_value)
                if failure:
                    return failure

            elif action == "submit":
                if item["role"] not in {"textbox", "combobox"}:
                    return {"ok": False, "error": "invalid_submit_target", "message": "Submit solo puede ejecutarse sobre un campo de texto o cuadro combinado."}
                self._press_key(item, keyboard.KEY_RETURN)

            elif action == "scroll_into_view":
                ok, body = self.bus.call_many([(ref, at.COMPONENT, "ScrollTo", "u", (at.SCROLL_ANYWHERE,))])[0]
                if not ok or not (body and body[0]):
                    return {"ok": False, "error": "pattern_unsupported", "message": "Ese resultado no admite desplazamiento accesible."}

            else:
                return {"ok": False, "error": "invalid_action", "message": "Acción web no válida."}

        except (at.AtspiError, keyboard.KeyboardUnavailable, kwin.KWinError, OSError) as error:
            # Igual que en Windows: un fallo no prueba que la página
            # cambió, así que el snapshot sigue valiendo.
            return {"ok": False, "error": "uia_action_failed", "message": str(error)}

        if action in {"click", "invoke", "keyboard_activate", "legacy_activate", "select", "toggle", "type", "set_range", "submit", "scroll_into_view"}:
            self.inspector.store.mark_stale(snapshot.id)

        result = {"ok": True, "message": "Acción web realizada; inspeccioná nuevamente para verificar el resultado.", "action": action, "element_name": item["name"], "element_role": item["role"], "risk": self._risk(action, item["name"]), "requires_observation": action != "focus"}
        if activation_pattern:
            result["activation_pattern"] = activation_pattern
        if keyboard_fallback:
            result["keyboard_fallback"] = keyboard_fallback
        if range_strategy:
            result["range_strategy"] = range_strategy
        if observed_range_value is not None:
            result["observed_range_value"] = observed_range_value
        return result
