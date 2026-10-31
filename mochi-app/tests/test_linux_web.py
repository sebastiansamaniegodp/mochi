"""
web_inspect / web_act de Linux (linux_platform/web.py) contra un
bus AT-SPI falso en memoria: no necesitan sesión gráfica, KWin,
Chromium ni el portal de teclado.
"""

import os
import unittest
from unittest.mock import patch

if os.name == "nt":
    raise unittest.SkipTest("linux_platform/web.py es solo para Linux")

from linux_platform import atspi as at
from linux_platform import keyboard
from linux_platform import web


APP_BUS = ":1.50"
PID = 4242
WINDOW_TITLE = "Prueba - Chromium"

SHOWING = (1 << at.STATE_SHOWING) | (1 << at.STATE_VISIBLE) | (1 << at.STATE_ENABLED)


# =========================================================
# BUS AT-SPI FALSO
# =========================================================

class Node:

    def __init__(self, role, name="", states=SHOWING, children=(), actions=(), **extra):
        self.role = role
        self.name = name
        self.states = states
        self.children = list(children)
        self.actions = list(actions)
        self.attributes = extra.pop("attributes", {})
        self.text = extra.pop("text", None)
        self.value = extra.pop("value", None)
        self.on_action = extra.pop("on_action", None)
        self.parent = None
        self.path = None
        self.selection = extra.pop("selection", None)


class FakeAtspi(at.Atspi):
    """Atspi con call_many respondido desde un árbol de Node."""

    def __init__(self, app):
        super().__init__()
        self.nodes = {}
        self.focused = None
        self.scrolled = []
        self.actions_done = []
        self.app = app
        self._register(app, "/org/a11y/atspi/accessible/root", None)

    def _register(self, node, path, parent):
        node.path = path
        node.parent = parent
        self.nodes[path] = node
        for index, child in enumerate(node.children):
            self._register(child, f"/org/a11y/atspi/accessible/{len(self.nodes) + 1}", node)

    def ref(self, node):
        return (APP_BUS, node.path)

    def remove(self, node):
        del self.nodes[node.path]

    def by_name(self, name):
        return next(node for node in self.nodes.values() if node.name == name)

    # -----------------------------------------------------

    def call_many(self, requests):
        return [self._answer(*request) for request in requests]

    def _answer(self, ref, interface, method, signature, body):

        if ref == at.REGISTRY_ROOT and method == "GetChildren":
            return True, ([(APP_BUS, self.app.path)],)

        if method == "GetConnectionUnixProcessID":
            return True, (PID,)

        node = self.nodes.get(ref[1])
        if node is None:
            return False, f"object {ref[1]} does not exist"

        if interface == at.PROPERTIES and method == "Get":
            return self._property(node, *body)

        if interface == at.PROPERTIES and method == "Set":
            _iface, name, (_sig, value) = body
            if name == "CurrentValue" and node.value is not None and node.value.get("accepts_set", True):
                node.value["value"] = value
            return True, ()

        if interface == at.ACCESSIBLE:
            if method == "GetRole":
                return True, (node.role,)
            if method == "GetState":
                return True, ([node.states & 0xFFFFFFFF, node.states >> 32],)
            if method == "GetChildren":
                return True, ([self.ref(child) for child in node.children if child.path in self.nodes],)
            if method == "GetAttributes":
                return True, (node.attributes,)
            if method == "GetIndexInParent":
                return True, (node.parent.children.index(node),)

        if interface == at.ACTION and node.actions:
            if method == "GetName":
                return True, (node.actions[body[0]],)
            if method == "DoAction":
                self.actions_done.append((node.name, node.actions[body[0]]))
                if node.on_action:
                    node.on_action(self, node.actions[body[0]])
                return True, (True,)

        if interface == at.COMPONENT:
            if method == "GrabFocus":
                self.focused = node
                return True, (True,)
            if method == "ScrollTo":
                self.scrolled.append(node.name)
                return True, (True,)

        if interface == at.TEXT and node.text is not None:
            if method == "GetText":
                return True, (node.text,)
            if method in {"GetNSelections"}:
                return True, (0,)
            if method in {"AddSelection", "SetSelection"}:
                return True, (True,)

        if interface == at.SELECTION and node.selection is not None and method == "SelectChild":
            return True, (node.selection(body[0]),)

        if interface == at.DOCUMENT and method == "GetAttributes" and node.role == at.ROLE_DOCUMENT_WEB:
            return True, ({"URI": "https://example.com/"},)

        return False, f"Method {method} doesn't exist"

    def _property(self, node, interface, name):

        if interface == at.ACCESSIBLE and name == "Name":
            return True, (("s", node.name),)
        if interface == at.ACCESSIBLE and name == "Parent":
            parent = node.parent
            return True, (("(so)", self.ref(parent) if parent else (APP_BUS, at.NULL_PATH)),)
        if interface == at.ACTION and name == "NActions":
            return True, (("i", len(node.actions)),)
        if interface == at.TEXT and name == "CharacterCount" and node.text is not None:
            return True, (("i", len(node.text)),)
        if interface == at.VALUE and node.value is not None:
            keys = {"CurrentValue": "value", "MinimumValue": "minimum", "MaximumValue": "maximum", "MinimumIncrement": "step"}
            return True, (("d", float(node.value[keys[name]])),)

        return False, f"No property {name}"


# =========================================================
# PÁGINA DE PRUEBA
# =========================================================

def build_page(extra_tabs=(), slider_accepts_set=True):

    def counter(bus, _action):
        output = bus.by_name("Sin acción") if any(n.name == "Sin acción" for n in bus.nodes.values()) else None
        if output:
            output.name = "Clic"

    def check(bus, _action):
        node = bus.by_name("Recordarme")
        node.states ^= 1 << at.STATE_CHECKED

    def step(bus, action):
        node = bus.by_name("Volumen")
        node.value["value"] += 5 if action == "increment" else -5

    tabs = [Node(at.ROLE_PAGE_TAB, "Prueba - Memory usage - 20 MB", actions=["doDefault", "showContextMenu"])]
    tabs += [Node(at.ROLE_PAGE_TAB, title, actions=["doDefault", "showContextMenu"]) for title in extra_tabs]

    document = Node(at.ROLE_DOCUMENT_WEB, "Prueba", children=[
        Node(83, "Título de la página"),
        Node(at.ROLE_LINK, "Ir a uno", actions=["jump", "showContextMenu"]),
        Node(at.ROLE_PUSH_BUTTON, "Botón contador", actions=["press", "showContextMenu"], on_action=counter),
        Node(at.ROLE_ENTRY, "Buscar", actions=["activate", "showContextMenu"], text="abc", attributes={"tag": "input"}),
        Node(at.ROLE_ENTRY, "Clave", actions=["activate", "showContextMenu"], text="secreta", attributes={"text-input-type": "password"}),
        Node(at.ROLE_CHECK_BOX, "Recordarme", actions=["check", "showContextMenu"], on_action=check),
        Node(at.ROLE_RADIO_BUTTON, "Opción A", states=SHOWING | (1 << at.STATE_CHECKED), actions=["check"]),
        Node(at.ROLE_SLIDER, "Volumen", actions=["doDefault", "showContextMenu", "decrement", "increment"],
             value={"value": 30, "minimum": 0, "maximum": 100, "step": 0, "accepts_set": slider_accepts_set},
             on_action=step),
        Node(32, "", children=[
            Node(at.ROLE_LINK, "Video uno", actions=["jump"]),
        ]),
        Node(32, "Recomendados", children=[
            Node(at.ROLE_LINK, "Video dos", actions=["jump"]),
        ]),
        Node(116, "Sin acción"),
        Node(116, "•"),
        Node(at.ROLE_LINK, "Enlace del fondo", states=(1 << at.STATE_VISIBLE) | (1 << at.STATE_ENABLED), actions=["jump"]),
    ])

    frame = Node(at.ROLE_FRAME, WINDOW_TITLE, children=[
        Node(39, "", children=[
            Node(at.ROLE_PUSH_BUTTON, "Sebastián", attributes={"class": "AvatarToolbarButton"}, actions=["press"]),
            Node(at.ROLE_PAGE_TAB_LIST, "", children=tabs, selection=lambda _index: False),
            document,
        ]),
    ])

    menu = Node(at.ROLE_FRAME, "", children=[
        Node(33, "", children=[
            Node(at.ROLE_MENU_ITEM, "Cortar Ctrl+X", actions=["doDefault"], on_action=lambda bus, _a: bus.paste("")),
            Node(at.ROLE_MENU_ITEM, "Pegar Ctrl+V", actions=["doDefault"], on_action=lambda bus, _a: bus.paste(bus.clipboard)),
            Node(at.ROLE_MENU_ITEM, "Pegar como texto sin formato Ctrl+Mayús+V", actions=["doDefault"],
                 on_action=lambda bus, _a: bus.paste(bus.clipboard)),
        ]),
    ])

    app = Node(75, "Chromium", children=[frame, menu])
    bus = FakeAtspi(app)
    bus.clipboard = ""

    def paste(text):
        bus.focused.text = text
    bus.paste = paste

    return bus


KWIN_WINDOWS = [
    {"id": "{otra}", "title": "Dolphin", "pid": 1, "resource_class": "org.kde.dolphin", "active": False, "minimized": False},
    {"id": "{chromium}", "title": WINDOW_TITLE, "pid": PID, "resource_class": "chromium", "active": True, "minimized": False},
]


class LinuxWebTestCase(unittest.TestCase):

    def setUp(self):
        self.bus = build_page()
        self.inspector = web.WebInspector(bus=self.bus)
        self.interactor = web.WebInteractor(self.inspector)
        patcher = patch.object(web.kwin, "list_windows", return_value=KWIN_WINDOWS)
        patcher.start()
        self.addCleanup(patcher.stop)
        focus = patch.object(web.kwin, "window_action", return_value=True)
        self.window_action = focus.start()
        self.addCleanup(focus.stop)
        sleep = patch.object(web.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def element(self, observation, name):
        return next(item for item in observation["elements"] if item["name"] == name)


# =========================================================
# INSPECCIÓN
# =========================================================

class InspectTests(LinuxWebTestCase):

    def test_same_public_format_as_windows(self):
        result = self.inspector.inspect()

        self.assertTrue(result["ok"])
        self.assertEqual(set(result), {"ok", "snapshot_id", "page", "elements", "context", "changed", "element_count", "query_matched"})
        self.assertEqual(result["page"], {"title": "Prueba", "url": "https://example.com/"})
        self.assertEqual(result["element_count"], len(result["elements"]))
        for item in result["elements"]:
            self.assertRegex(item["id"], r"^e\d+$")
            self.assertTrue({"id", "role", "name", "value", "offscreen"} <= set(item))

    def test_roles_values_and_metadata(self):
        result = self.inspector.inspect()
        roles = {item["name"]: item["role"] for item in result["elements"]}

        self.assertEqual(roles["Botón contador"], "button")
        self.assertEqual(roles["Ir a uno"], "link")
        self.assertEqual(roles["Buscar"], "textbox")
        self.assertEqual(roles["Recordarme"], "checkbox")
        self.assertEqual(roles["Volumen"], "slider")
        self.assertEqual(self.element(result, "Buscar")["value"], "abc")
        # Nunca se lee el contenido de una contraseña.
        self.assertEqual(self.element(result, "Clave")["value"], "")
        self.assertTrue(self.element(result, "Opción A")["selected"])
        self.assertEqual(self.element(result, "Volumen")["range"]["value"], 30.0)
        self.assertEqual(self.element(result, "Video dos")["container"], "Recomendados")
        self.assertEqual(self.element(result, "Video dos")["section"], "Recomendados")
        self.assertIn("Sin acción", result["context"])
        # Los botones del navegador no son parte de la página.
        self.assertNotIn("Sebastián", roles)

    def test_search_box_named_like_its_button_is_kept(self):
        # YouTube: el cuadro de búsqueda y el botón se llaman "Search".
        document = self.bus.by_name("Prueba")
        button = Node(at.ROLE_PUSH_BUTTON, "Buscar", actions=["press"])
        document.children.append(button)
        self.bus._register(button, "/org/a11y/atspi/accessible/boton-buscar", document)

        result = self.inspector.inspect()
        roles = sorted(item["role"] for item in result["elements"] if item["name"] == "Buscar")
        self.assertEqual(roles, ["button", "textbox"])

    def test_offscreen_only_with_query(self):
        names = [item["name"] for item in self.inspector.inspect()["elements"]]
        self.assertNotIn("Enlace del fondo", names)

        result = self.inspector.inspect("fondo")
        self.assertEqual([(item["name"], item["offscreen"]) for item in result["elements"]], [("Enlace del fondo", True)])

    def test_unmatched_query_keeps_visible_page(self):
        result = self.inspector.inspect("no existe")
        self.assertFalse(result["query_matched"])
        self.assertTrue(result["elements"])

    def test_changed_after_page_update(self):
        self.inspector.inspect()
        self.bus.by_name("Buscar").text = "otro"
        self.assertTrue(self.inspector.inspect()["changed"])

    def test_no_browser_window(self):
        with patch.object(web.kwin, "list_windows", return_value=KWIN_WINDOWS[:1]):
            result = self.inspector.inspect()
        self.assertEqual(result["error"], "chrome_window_not_found")

    def test_page_without_accessible_content(self):
        document = next(node for node in self.bus.nodes.values() if node.role == at.ROLE_DOCUMENT_WEB)
        document.children = []
        result = self.inspector.inspect()
        self.assertEqual(result["error"], "web_content_unavailable")
        self.assertIn("--force-renderer-accessibility", result["message"])

    def test_bus_unavailable(self):
        with patch.object(self.bus, "applications", side_effect=at.AtspiError("sin bus")):
            result = self.inspector.inspect()
        self.assertEqual(result["error"], "uia_unavailable")

    def test_preferred_profile_by_avatar_or_rejects(self):
        self.inspector.prefer_profile_directory("Profile 9", aliases=["Sebastián"])
        self.assertTrue(self.inspector.inspect()["ok"])

        self.inspector.prefer_profile_directory("Profile 9", aliases=["Otra persona"])
        self.assertEqual(self.inspector.inspect()["error"], "chrome_window_not_found")


# =========================================================
# PESTAÑAS
# =========================================================

class TabTests(LinuxWebTestCase):

    def test_activates_tab_without_memory_suffix(self):
        result = self.inspector.activate_matching_tab("prueba", preferred_title="Prueba")
        self.assertEqual(result, {"ok": True, "message": "Pestaña existente activada.", "tab_title": "Prueba"})
        self.assertIn(("Prueba - Memory usage - 20 MB", "doDefault"), self.bus.actions_done)

    def test_ambiguous_and_missing_tabs(self):
        self.bus = build_page(extra_tabs=["Prueba"])
        self.inspector = web.WebInspector(bus=self.bus)
        self.assertEqual(self.inspector.activate_matching_tab("prueba")["error"], "existing_tab_ambiguous")
        self.assertEqual(self.inspector.activate_matching_tab("youtube")["error"], "existing_tab_not_found")


# =========================================================
# ACCIONES
# =========================================================

class ActTests(LinuxWebTestCase):

    def act(self, name, action, value=None, query=None):
        observation = self.inspector.inspect(query)
        item = self.element(observation, name)
        return self.interactor.act(item["id"], action, value=value, snapshot_id=observation["snapshot_id"])

    def test_click_uses_default_action(self):
        result = self.act("Botón contador", "click")
        self.assertTrue(result["ok"])
        self.assertEqual(result["activation_pattern"], "invoke")
        self.assertIn("Clic", self.inspector.inspect()["context"])

    def test_snapshot_goes_stale_after_action_and_removed_element(self):
        observation = self.inspector.inspect()
        button = self.element(observation, "Botón contador")
        self.interactor.act(button["id"], "click", snapshot_id=observation["snapshot_id"])
        self.assertEqual(self.interactor.act(button["id"], "click", snapshot_id=observation["snapshot_id"])["error"], "stale_element")

        observation = self.inspector.inspect()
        link = self.element(observation, "Ir a uno")
        self.bus.remove(self.bus.by_name("Ir a uno"))
        result = self.interactor.act(link["id"], "click", snapshot_id=observation["snapshot_id"])
        self.assertEqual(result["error"], "stale_element")
        self.assertTrue(result["stale_element"])

    def test_toggle_select_and_invalid(self):
        self.assertTrue(self.act("Recordarme", "toggle")["ok"])
        self.assertTrue(self.bus.by_name("Recordarme").states & (1 << at.STATE_CHECKED))
        self.assertTrue(self.act("Opción A", "select")["ok"])
        self.assertEqual(self.act("Ir a uno", "toggle")["error"], "pattern_unsupported")
        self.assertEqual(self.act("Ir a uno", "volar")["error"], "invalid_action")
        self.assertEqual(self.act("Buscar", "keyboard_activate")["error"], "invalid_keyboard_target")
        self.assertEqual(self.act("Ir a uno", "submit")["error"], "invalid_submit_target")

    def test_type_pastes_plain_text_and_restores_clipboard(self):
        copied = []

        def set_clipboard(kind, data):
            copied.append(data)
            self.bus.clipboard = data.decode("utf-8")

        with (
            patch.object(web, "_save_clipboard", return_value=("text/plain", b"anterior")),
            patch.object(web, "_set_clipboard", side_effect=set_clipboard),
            patch.object(web, "_restore_clipboard") as restore,
        ):
            result = self.act("Buscar", "type", value="hola ñandú")

        self.assertTrue(result["ok"])
        self.assertEqual(result["risk"], "input")
        self.assertEqual(self.bus.by_name("Buscar").text, "hola ñandú")
        self.assertIn(("Pegar como texto sin formato Ctrl+Mayús+V", "doDefault"), self.bus.actions_done)
        restore.assert_called_once_with(("text/plain", b"anterior"))
        self.window_action.assert_called_with("{chromium}", "focus")

    def test_type_requires_value_and_text_field(self):
        self.assertEqual(self.act("Buscar", "type")["error"], "value_required")
        with patch.object(web, "_save_clipboard", return_value=None):
            self.assertEqual(self.act("Ir a uno", "type", value="x")["error"], "pattern_unsupported")

    def test_set_range_direct_and_with_step_actions(self):
        result = self.act("Volumen", "set_range", value=70)
        self.assertEqual(result["range_strategy"], "range_value")
        self.assertEqual(result["observed_range_value"], 70.0)
        self.assertEqual(self.act("Volumen", "set_range", value=150)["error"], "range_value_out_of_bounds")
        self.assertEqual(self.act("Volumen", "set_range", value="mucho")["error"], "invalid_range_value")

        self.bus = build_page(slider_accepts_set=False)
        self.inspector = web.WebInspector(bus=self.bus)
        self.interactor = web.WebInteractor(self.inspector)
        result = self.act("Volumen", "set_range", value=50)
        self.assertEqual(result["range_strategy"], "range_step_actions")
        self.assertEqual(result["observed_range_value"], 50.0)

    def test_submit_and_keyboard_activate_use_portal_keys(self):
        class Keys:
            def __init__(self):
                self.pressed = []
            def press(self, keysym, count=1):
                self.pressed.append(keysym)

        keys = Keys()
        with patch.object(web.keyboard, "get_keyboard", return_value=keys):
            self.assertTrue(self.act("Buscar", "submit")["ok"])
            result = self.act("Botón contador", "keyboard_activate")

        self.assertEqual(keys.pressed, [keyboard.KEY_RETURN, keyboard.KEY_SPACE])
        self.assertEqual(result["activation_pattern"], "keyboard_space")
        self.assertEqual(self.bus.focused.name, "Botón contador")

    def test_without_keyboard_permission(self):
        class NoKeys:
            def press(self, keysym, count=1):
                raise keyboard.KeyboardUnavailable("sin permiso")

        with patch.object(web.keyboard, "get_keyboard", return_value=NoKeys()):
            submit = self.act("Buscar", "submit")
            activate = self.act("Botón contador", "keyboard_activate")

        self.assertEqual(submit["error"], "uia_action_failed")
        # La activación cae en la acción predeterminada del control.
        self.assertTrue(activate["ok"])
        self.assertEqual(activate["activation_pattern"], "invoke")
        self.assertEqual(activate["keyboard_fallback"], "sin permiso")

    def test_scroll_and_focus(self):
        self.assertTrue(self.act("Enlace del fondo", "scroll_into_view", query="fondo")["ok"])
        self.assertEqual(self.bus.scrolled, ["Enlace del fondo"])
        result = self.act("Buscar", "focus")
        self.assertFalse(result["requires_observation"])
        self.assertEqual(self.bus.focused.name, "Buscar")


# =========================================================
# EN LINUX, BROWSER_TOOLS USA ESTE INSPECTOR
# =========================================================

class BrowserToolsImportTests(unittest.TestCase):

    def test_browser_tools_uses_linux_inspector(self):
        from brain import browser_tools
        self.assertIsInstance(browser_tools._web_inspector, web.WebInspector)
        self.assertIsInstance(browser_tools._web_interactor, web.WebInteractor)


if __name__ == "__main__":
    unittest.main()
