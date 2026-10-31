"""
Aplicaciones instaladas en Linux: entradas .desktop del menú
(sistema, usuario y Flatpak). Devuelve la misma forma que
discovery/applications.py en Windows, con type "desktop_entry".
"""

import configparser
import os
import subprocess
from pathlib import Path


def _application_dirs():

    home = Path.home()

    data_home = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")

    data_dirs = [
        Path(item)
        for item in (
            os.environ.get("XDG_DATA_DIRS")
            or "/usr/local/share:/usr/share"
        ).split(":")
        if item
    ]

    candidates = [
        data_home,
        *data_dirs,
        data_home / "flatpak" / "exports" / "share",
        Path("/var/lib/flatpak/exports/share"),
    ]

    seen = set()

    for base in candidates:
        directory = base / "applications"
        if directory.is_dir() and directory not in seen:
            seen.add(directory)
            yield directory


def _localized(section, key):
    """Name[es_AR] > Name[es] > Name."""

    language = (os.environ.get("LANG") or "es").split(".")[0]

    for variant in (f"{key}[{language}]", f"{key}[{language.split('_')[0]}]", key):
        value = section.get(variant)
        if value:
            return value.strip()

    return ""


def discover_desktop_entries(normalize_name):

    named = {}
    generic = []

    for directory in _application_dirs():

        for file in sorted(directory.rglob("*.desktop")):

            # El id es la ruta relativa con "-" en lugar de "/".
            desktop_id = (
                str(file.relative_to(directory))
                .removesuffix(".desktop")
                .replace("/", "-")
            )

            # El primero que aparece (usuario antes que sistema) manda.
            if desktop_id in named:
                continue

            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.optionxform = str

            try:
                parser.read(file, encoding="utf-8")
                section = parser["Desktop Entry"]

            except Exception:
                continue

            hidden = (
                section.get("Type") != "Application"
                or section.get("NoDisplay", "").lower() == "true"
                or section.get("Hidden", "").lower() == "true"
            )

            name = _localized(section, "Name")

            if hidden or not name:
                named[desktop_id] = None
                continue

            entry = {
                "name": name,
                "normalized_name": normalize_name(name),
                "type": "desktop_entry",
                "target": desktop_id,
                "source": "desktop_entry",
            }
            named[desktop_id] = entry

            # "Calculadora", "Navegador web": también sirve el nombre
            # genérico, pero tiene menos prioridad que los nombres propios.
            generic_name = _localized(section, "GenericName")

            if generic_name and normalize_name(generic_name) != entry["normalized_name"]:
                generic.append({
                    **entry,
                    "name": generic_name,
                    "normalized_name": normalize_name(generic_name),
                    "source": "desktop_entry_generic",
                })

    applications = [entry for entry in named.values() if entry]

    names = {entry["normalized_name"] for entry in applications}

    applications.extend(
        entry
        for entry in generic
        if entry["normalized_name"] not in names
    )

    return applications


def launch_desktop_entry(desktop_id):

    subprocess.Popen(
        ["gtk-launch", desktop_id],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
