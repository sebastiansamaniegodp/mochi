"""Adquisición y reducción del árbol Windows UI Automation de Chrome."""
import unicodedata

from web_interaction.element_store import ElementStore


def _normal(value):
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    return "".join(char for char in text if unicodedata.category(char) != "Mn")


class WebInspector:
    MAX_ELEMENTS = 60
    # Chromium pages can put a large, non-interactive subtree (navigation,
    # badges, thumbnails) before a visible result in RawView order.  A modest
    # increase plus breadth-first collection below keeps result links within
    # the bounded scan without walking an unbounded page.
    MAX_NODES = 1200
    MAX_CONTENT_DEPTH = 32

    def __init__(self, store=None):
        self.store = store or ElementStore()
        self._uia = None
        self._api = None
        self._preferred_profile_directory = None
        self._preferred_profile_aliases = set()

    def prefer_profile_directory(self, directory, aliases=None):
        """Prefer Chrome windows owned by one explicitly requested profile."""

        self._preferred_profile_directory = _normal(directory) or None
        self._preferred_profile_aliases = {
            _normal(alias) for alias in (aliases or []) if _normal(alias)
        }

    def _window_matches_preferred_profile(self, hwnd, process_directory):
        if (
            self._preferred_profile_directory
            and process_directory == self._preferred_profile_directory
        ):
            return True
        if not self._uia or not self._preferred_profile_aliases:
            return False
        try:
            root = self._uia.ElementFromHandle(hwnd)
            walker = self._uia.RawViewWalker
            queue, index, nodes = [(root, 0)], 0, 0
            while index < len(queue) and nodes < 350:
                node, depth = queue[index]
                index += 1
                if depth > 8:
                    continue
                child = walker.GetFirstChildElement(node)
                while child and nodes < 350:
                    nodes += 1
                    class_name = str(self._safe(child, "CurrentClassName", "") or "")
                    name = _normal(self._safe(child, "CurrentName", ""))
                    if class_name == "AvatarToolbarButton" and any(
                        alias == name or alias in name
                        for alias in self._preferred_profile_aliases
                    ):
                        return True
                    queue.append((child, depth + 1))
                    child = walker.GetNextSiblingElement(child)
        except Exception:
            return False
        return False

    @staticmethod
    def _window_profile_directory(hwnd):
        """Read a Chrome top-level window's public process launch argument."""

        try:
            import psutil
            import re
            import win32process

            _thread_id, process_id = win32process.GetWindowThreadProcessId(hwnd)
            command = " ".join(psutil.Process(process_id).cmdline())
            match = re.search(
                r"--profile-directory(?:=|\s+)(?:\"([^\"]+)\"|'([^']+)'|([^\s]+))",
                command,
                re.I,
            )
            return _normal(next((value for value in match.groups() if value), "")) if match else None
        except Exception:
            return None

    def _load_uia(self):
        if self._uia:
            return True, None
        try:
            from comtypes.client import CreateObject, GetModule
            GetModule(r"C:\Windows\System32\UIAutomationCore.dll")
            import comtypes.gen.UIAutomationClient as api
            self._api = api
            self._uia = CreateObject(api.CUIAutomation, interface=api.IUIAutomation)
            return True, None
        except Exception as error:
            return False, str(error)

    def _chrome_windows(self):
        import win32gui
        windows = []
        foreground = win32gui.GetForegroundWindow()
        def collect(hwnd, _):
            title = win32gui.GetWindowText(hwnd)
            if win32gui.IsWindowVisible(hwnd) and "chrome" in title.casefold():
                profile_directory = self._window_profile_directory(hwnd)
                profile_match = self._window_matches_preferred_profile(
                    hwnd,
                    profile_directory,
                )
                windows.append((hwnd, title, hwnd == foreground, profile_match))
        win32gui.EnumWindows(collect, None)
        if self._preferred_profile_directory and not any(item[3] for item in windows):
            # Falling back to another Chrome profile would make a page title
            # look valid while operating on the wrong signed-in account.
            return []
        ordered = sorted(
            windows,
            key=lambda item: (
                not item[3] if self._preferred_profile_directory else False,
                not item[2],
            ),
        )
        # Preserve the historical public tuple shape used by callers.
        return [(hwnd, title, foreground) for hwnd, title, foreground, _match in ordered]

    def _active_window(self):
        windows = self._chrome_windows()
        return windows[0] if windows else None

    def activate_matching_tab(self, target, preferred_title=None):
        """Activate one existing Chrome tab using UIA selection semantics.

        A browser command normally opens a new tab.  When a short-lived,
        profile-verified session cache identifies a prior page, this lets the
        caller bring that tab forward without coordinates, DevTools or a new
        navigation.  A tie is deliberately reported rather than resolved by
        tab order.
        """
        ok, error = self._load_uia()
        if not ok:
            return {"ok": False, "error": "uia_unavailable", "message": str(error or "UI Automation no está disponible.")}
        active = self._active_window()
        if not active:
            return {"ok": False, "error": "chrome_window_not_found", "message": "No encontré una ventana visible de Chrome."}

        expected = _normal(target)
        preferred = _normal(preferred_title)
        if not expected:
            return {"ok": False, "error": "tab_target_missing", "message": "No se indicó la pestaña a reutilizar."}
        try:
            root = self._uia.ElementFromHandle(active[0])
            walker = self._uia.RawViewWalker
        except Exception as error:
            return {"ok": False, "error": "uia_connection_failed", "message": str(error)}

        candidates = []
        queue, index, nodes = [(root, 0)], 0, 0
        try:
            while index < len(queue) and nodes < 600:
                node, depth = queue[index]
                index += 1
                if depth > 10:
                    continue
                child = walker.GetFirstChildElement(node)
                while child and nodes < 600:
                    nodes += 1
                    name = str(self._safe(child, "CurrentName", "") or "").strip()
                    role = self._role(self._safe(child, "CurrentControlType"))
                    normalized_name = _normal(name)
                    if role == "tab" and expected in normalized_name:
                        score = 1
                        if preferred and normalized_name == preferred:
                            score = 3
                        elif preferred and (preferred in normalized_name or normalized_name in preferred):
                            score = 2
                        candidates.append((score, name, child))
                    queue.append((child, depth + 1))
                    child = walker.GetNextSiblingElement(child)
        except Exception as error:
            return {"ok": False, "error": "tab_enumeration_failed", "message": str(error)}

        if not candidates:
            return {"ok": False, "error": "existing_tab_not_found", "message": "No encontré una pestaña abierta que corresponda al destino."}
        candidates.sort(key=lambda candidate: candidate[0], reverse=True)
        if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
            return {"ok": False, "error": "existing_tab_ambiguous", "message": "Hay varias pestañas abiertas que podrían corresponder al destino."}

        _score, name, tab = candidates[0]
        try:
            raw_pattern = tab.GetCurrentPattern(self._api.UIA_SelectionItemPatternId)
            if raw_pattern is None:
                return {"ok": False, "error": "tab_selection_unsupported", "message": "La pestaña existente no admite selección accesible."}
            pattern = raw_pattern.QueryInterface(self._api.IUIAutomationSelectionItemPattern)
            pattern.Select()
        except Exception as error:
            return {"ok": False, "error": "tab_activation_failed", "message": str(error)}
        return {"ok": True, "message": "Pestaña existente activada.", "tab_title": name}

    def _role(self, control_type):
        api = self._api
        mapping = {
            api.UIA_ButtonControlTypeId: "button", api.UIA_HyperlinkControlTypeId: "link",
            api.UIA_EditControlTypeId: "textbox", api.UIA_CheckBoxControlTypeId: "checkbox",
            api.UIA_RadioButtonControlTypeId: "radio", api.UIA_ComboBoxControlTypeId: "combobox",
            api.UIA_MenuItemControlTypeId: "menuitem", api.UIA_TabItemControlTypeId: "tab",
            api.UIA_ListItemControlTypeId: "listitem", api.UIA_DocumentControlTypeId: "document",
        }
        slider_control_type = getattr(api, "UIA_SliderControlTypeId", None)
        if slider_control_type is not None:
            mapping[slider_control_type] = "slider"
        return mapping.get(control_type)

    @staticmethod
    def _safe(element, attribute, default=None):
        try:
            return getattr(element, attribute)
        except Exception:
            return default

    def _range_metadata(self, element):
        """Return a serializable UIA range when a control exposes one.

        A range is the accessible contract used by web-player scrubbers.  We
        expose its native bounds rather than guessing a site's duration or
        using screen coordinates.  Callers can therefore seek only when the
        provider publishes enough evidence to do it safely.
        """
        pattern_id = getattr(self._api, "UIA_RangeValuePatternId", None)
        interface = getattr(self._api, "IUIAutomationRangeValuePattern", None)
        if pattern_id is None or interface is None:
            return None
        try:
            raw_pattern = element.GetCurrentPattern(pattern_id)
            if raw_pattern is None:
                return None
            pattern = raw_pattern.QueryInterface(interface)
            return {
                "value": float(pattern.CurrentValue),
                "minimum": float(pattern.CurrentMinimum),
                "maximum": float(pattern.CurrentMaximum),
                "small_change": float(pattern.CurrentSmallChange),
                "large_change": float(pattern.CurrentLargeChange),
            }
        except Exception:
            return None

    def _selection_metadata(self, element):
        """Expose the current SelectionItem state without leaking COM refs."""
        pattern_id = getattr(self._api, "UIA_SelectionItemPatternId", None)
        interface = getattr(self._api, "IUIAutomationSelectionItemPattern", None)
        if pattern_id is None or interface is None:
            return None
        try:
            raw_pattern = element.GetCurrentPattern(pattern_id)
            if raw_pattern is None:
                return None
            pattern = raw_pattern.QueryInterface(interface)
            return bool(pattern.CurrentIsSelected)
        except Exception:
            return None

    def inspect(self, query=None):
        ok, error = self._load_uia()
        if not ok:
            return {"ok": False, "error": "uia_unavailable", "message": f"UI Automation no está disponible: {error}"}
        active = self._active_window()
        if not active:
            return {"ok": False, "error": "chrome_window_not_found", "message": "No encontré una ventana visible de Chrome."}
        hwnd, title, _ = active
        try:
            root = self._uia.ElementFromHandle(hwnd)
            walker = self._uia.RawViewWalker
        except Exception as error:
            return {"ok": False, "error": "uia_connection_failed", "message": str(error)}

        wanted = _normal(query)
        previous = self.store.get()
        # Primero localizamos documentos candidatos. Chrome actual expone la
        # página bajo Chrome_RenderWidgetHostHWND, no necesariamente WebView.
        candidates, nodes = [], 0
        def find_documents(parent, ancestors=(), depth=0):
            nonlocal nodes
            if depth > 14 or nodes >= self.MAX_NODES:
                return
            child = walker.GetFirstChildElement(parent)
            while child and nodes < self.MAX_NODES:
                nodes += 1
                class_name = str(self._safe(child, "CurrentClassName", "") or "")
                if self._role(self._safe(child, "CurrentControlType")) == "document" and not self._safe(child, "CurrentIsOffscreen", False):
                    candidates.append((child, ancestors + (class_name,)))
                find_documents(child, ancestors + (class_name,), depth + 1)
                child = walker.GetNextSiblingElement(child)
        try:
            find_documents(root)
        except Exception:
            pass

        def descendants(parent):
            result = []
            # Do not let one deep decorative branch consume the entire node
            # budget before sibling result cards are examined.  Browser pages
            # commonly place those cards alongside chrome-heavy widgets.
            # Preserve a compact semantic ancestry for each interactive
            # child.  Video sites frequently expose both a title link and a
            # channel link inside one list card; the ancestry lets callers
            # choose cards in visible order rather than blindly counting all
            # links.  It contains public accessible names only, never DOM or
            # UIA references.
            queue = [(parent, 0, ())]
            index = 0
            while index < len(queue) and len(result) < self.MAX_NODES:
                node, depth, ancestry = queue[index]
                index += 1
                if depth > self.MAX_CONTENT_DEPTH:
                    continue
                child = walker.GetFirstChildElement(node)
                while child and len(result) < self.MAX_NODES:
                    child_role = self._role(self._safe(child, "CurrentControlType"))
                    child_name = str(self._safe(child, "CurrentName", "") or "").strip()
                    result.append((child, ancestry))
                    child_context = ancestry + ((child_role, child_name),) if child_name else ancestry
                    queue.append((child, depth + 1, child_context[-8:]))
                    child = walker.GetNextSiblingElement(child)
            return result

        # Score: documento visible, bajo renderer Chrome y con contenido útil.
        ranked = []
        for document, ancestors in candidates:
            subtree = descendants(document)
            semantic = sum(1 for item, _ancestry in subtree if self._role(self._safe(item, "CurrentControlType")))
            name = str(self._safe(document, "CurrentName", "") or "")
            score = semantic * 10 + (100 if "Chrome_RenderWidgetHostHWND" in ancestors else 0) + (5 if name and _normal(name) in _normal(title) else 0)
            ranked.append((score, document, subtree))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if not ranked or ranked[0][0] <= 0:
            return {"ok": False, "error": "web_content_unavailable", "message": "Chrome no está exponiendo un documento web accesible utilizable."}

        document, subtree = ranked[0][1], ranked[0][2]
        page_title = str(self._safe(document, "CurrentName", "") or "").strip() or title
        # Build an unfiltered semantic view first.  A natural-language query
        # may be in a different language than the accessible name (for
        # example, "profile settings" vs. visible profile names), and must not
        # turn an otherwise usable page into an apparently empty one.
        all_candidates, visible_candidates, all_text_context = [], [], []
        # Conservamos el control más interactivo de cada nombre: link/button
        # gana frente a ListItem contenedor con el mismo nombre.
        priority = {"link": 5, "button": 5, "textbox": 4, "combobox": 4, "slider": 4, "checkbox": 4, "radio": 4, "menuitem": 4, "tab": 4, "listitem": 2}
        candidates_by_name = []
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

        for child, ancestry in subtree:
            role = self._role(self._safe(child, "CurrentControlType"))
            name = str(self._safe(child, "CurrentName", "") or "").strip()
            value = str(self._safe(child, "CurrentValue", "") or "").strip()
            if role and name:
                range_metadata = self._range_metadata(child)
                selected = self._selection_metadata(child)
                container = semantic_container(ancestry)
                section = semantic_section(ancestry)
                candidate = (
                    priority.get(role, 1),
                    role,
                    name,
                    value,
                    bool(self._safe(child, "CurrentIsOffscreen", False)),
                    range_metadata,
                    selected,
                    container,
                    section,
                    child,
                )
                # The ordinary snapshot is intentionally limited to controls
                # exposed as visible.  A targeted query may include an
                # offscreen result so it can be scrolled into view through
                # UIA, rather than being silently invisible to Mochi.
                all_candidates.append(candidate)
                if not candidate[4]:
                    visible_candidates.append(candidate)
            elif name and len(all_text_context) < 12:
                all_text_context.append(name[:180])

        filtered_candidates = [item for item in all_candidates if wanted in _normal(f"{item[2]} {item[3]}")] if wanted else visible_candidates
        filtered_context = [item for item in all_text_context if wanted in _normal(item)] if wanted else all_text_context
        query_matched = not wanted or bool(filtered_candidates or filtered_context)
        candidates_by_name = filtered_candidates if query_matched else visible_candidates
        text_context = filtered_context if query_matched else all_text_context
        elements, dedupe = {}, set()
        for _, role, name, value, offscreen, range_metadata, selected, container, section, child in sorted(candidates_by_name, reverse=True, key=lambda item: item[0]):
            key = (_normal(name), value)
            if key in dedupe or len(elements) >= self.MAX_ELEMENTS:
                continue
            dedupe.add(key)
            element_id = f"e{len(elements) + 1}"
            elements[element_id] = {
                "element": child,
                "runtime_id": tuple(self._safe(child, "GetRuntimeId", lambda: [])() or []),
                "role": role,
                "name": name,
                "value": value,
                "offscreen": offscreen,
                "range": range_metadata,
                "selected": selected,
                "container": container,
                "section": section,
            }
        url = None
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
