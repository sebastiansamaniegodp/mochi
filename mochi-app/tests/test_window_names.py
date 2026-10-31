import unittest

from mochi_app import paths

paths.enable_agent_imports()

from discovery.windows import normalize_window_name
from window_manager import WindowManager


# =========================================================
# VENTANAS FALSAS, con los datos del .desktop como en KDE
# =========================================================

def _window(hwnd, title, process, generic_name="", keywords=()):
    return {
        "hwnd": hwnd,
        "title": title,
        "normalized_title": normalize_window_name(title),
        "process": process,
        "normalized_process": normalize_window_name(process),
        "pid": hwnd,
        "app_name": "",
        "generic_name": generic_name,
        "keywords": list(keywords),
    }


WINDOWS = [
    _window(
        1, "Descargas — Dolphin", "dolphin", "Gestor de archivos",
        # Keywords[es] de org.kde.dolphin.desktop (Debian 13).
        [
            "archivos", "gestión de archivos", "administración de archivos",
            "exploración de archivos", "gestor de archivos",
            "administrador de archivos", "explorador", "buscador",
        ],
    ),
    _window(2, "naruto - Chromium", "chromium", "Navegador web", ["browser"]),
    _window(3, "~ — Konsole", "konsole", "Emulador de terminal", ["terminal"]),
]


class WindowNamesTest(unittest.TestCase):

    def setUp(self):
        self.manager = WindowManager()
        self.manager.get_windows = lambda: WINDOWS

    def test_nombre_comun_de_la_aplicacion(self):
        for query in (
            "el explorador de archivos",
            "explorador",
            "el administrador de archivos",
            "el gestor de archivos",
        ):
            with self.subTest(query=query):
                self.assertEqual(self.manager.find(query)["process"], "dolphin")
        self.assertEqual(self.manager.find("el navegador")["process"], "chromium")
        self.assertEqual(self.manager.find("la terminal")["process"], "konsole")

    def test_cerrar_todas_por_nombre_comun(self):
        windows = self.manager.find_all("el explorador de archivos")
        self.assertEqual([w["process"] for w in windows], ["dolphin"])

    def test_sin_coincidencia_no_elige_cualquiera(self):
        self.assertIsNone(self.manager.find("la calculadora"))
        self.assertEqual(
            self.manager.last_match_message("el bloc de notas"),
            "No encontré una ventana del bloc de notas",
        )


if __name__ == "__main__":
    unittest.main()
