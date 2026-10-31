import json
import os
import re
import subprocess
import unicodedata
from pathlib import Path

try:
    import winreg
except ImportError:
    winreg = None


# =========================================================
# NORMALIZAR NOMBRES
# =========================================================

def normalize_name(text: str) -> str:

    text = text.lower().strip()

    text = unicodedata.normalize(
        "NFD",
        text
    )

    text = "".join(
        character
        for character in text
        if unicodedata.category(character) != "Mn"
    )

    text = re.sub(
        r"[^a-z0-9]+",
        " ",
        text
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# =========================================================
# MENÚ INICIO
# =========================================================

def discover_start_menu_shortcuts():

    applications = []

    locations = []

    appdata = os.environ.get(
        "APPDATA"
    )

    programdata = os.environ.get(
        "PROGRAMDATA"
    )

    userprofile = os.environ.get(
        "USERPROFILE"
    )

    public = os.environ.get(
        "PUBLIC"
    )


    if appdata:

        locations.append(
            Path(appdata)
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
        )


    if programdata:

        locations.append(
            Path(programdata)
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
        )


    # También accesos directos del escritorio

    if userprofile:

        locations.append(
            Path(userprofile)
            / "Desktop"
        )


    if public:

        locations.append(
            Path(public)
            / "Desktop"
        )


    for location in locations:

        if not location.exists():
            continue


        try:

            for file in location.rglob("*"):

                if not file.is_file():
                    continue


                extension = (
                    file.suffix.lower()
                )


                if extension not in {
                    ".lnk",
                    ".url"
                }:

                    continue


                name = file.stem.strip()


                if not name:
                    continue


                applications.append({

                    "name":
                        name,

                    "normalized_name":
                        normalize_name(name),

                    "type":
                        "shortcut",

                    "target":
                        str(file),

                    "source":
                        "start_menu"
                })


        except Exception as e:

            print(
                f"Error leyendo {location}: {e}"
            )


    return applications


# =========================================================
# APPS DEL MENÚ INICIO / MICROSOFT STORE
# =========================================================

def discover_windows_start_apps():

    applications = []


    powershell_command = r"""
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
Get-StartApps |
Select-Object Name, AppID |
ConvertTo-Json -Compress
"""


    try:

        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                powershell_command
            ],
            capture_output=True,
            encoding="utf-8",
            errors="ignore",
            timeout=15,
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if os.name == "nt"
                else 0
            )
        )


        output = (
            result.stdout.strip()
        )


        if not output:
            return applications


        data = json.loads(output)


        # PowerShell devuelve objeto si existe uno
        # y lista cuando existen varios.

        if isinstance(data, dict):
            data = [data]


        for item in data:

            name = (
                item.get("Name")
                or ""
            ).strip()

            app_id = (
                item.get("AppID")
                or ""
            ).strip()


            if not name or not app_id:
                continue


            applications.append({

                "name":
                    name,

                "normalized_name":
                    normalize_name(name),

                "type":
                    "start_app",

                "target":
                    app_id,

                "source":
                    "windows_start_apps"
            })


    except Exception as e:

        print(
            "No pude obtener Get-StartApps:"
        )

        print(e)


    return applications


# =========================================================
# APP PATHS DEL REGISTRO DE WINDOWS
# =========================================================

def discover_registry_app_paths():

    applications = []


    if winreg is None:
        return applications


    locations = [

        (
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
        ),

        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
        ),

        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"
        )
    ]


    for hive, path in locations:

        try:

            with winreg.OpenKey(
                hive,
                path
            ) as root:

                index = 0


                while True:

                    try:

                        subkey_name = (
                            winreg.EnumKey(
                                root,
                                index
                            )
                        )

                        index += 1


                    except OSError:
                        break


                    try:

                        with winreg.OpenKey(
                            root,
                            subkey_name
                        ) as subkey:

                            executable, _ = (
                                winreg.QueryValueEx(
                                    subkey,
                                    None
                                )
                            )


                            executable = (
                                str(executable)
                                .strip()
                                .strip('"')
                            )


                            if not executable:
                                continue


                            executable_path = (
                                Path(executable)
                            )


                            # Nombre derivado del ejecutable
                            name = (
                                Path(subkey_name)
                                .stem
                            )


                            if executable_path.exists():

                                applications.append({

                                    "name":
                                        name,

                                    "normalized_name":
                                        normalize_name(name),

                                    "type":
                                        "executable",

                                    "target":
                                        str(executable_path),

                                    "source":
                                        "registry"
                                })


                    except OSError:
                        continue


        except OSError:
            continue


    return applications


# =========================================================
# DESCUBRIMIENTO GENERAL
# =========================================================

def discover_applications():

    print("")
    print(
        "Buscando aplicaciones instaladas..."
    )


    applications = []


    # Linux: entradas .desktop del menú de aplicaciones.
    if os.name != "nt":
        from linux_platform.applications import (
            discover_desktop_entries
        )

        applications.extend(
            discover_desktop_entries(
                normalize_name
            )
        )

        print(
            f"Se detectaron {len(applications)} entradas."
        )

        return applications


    applications.extend(
        discover_start_menu_shortcuts()
    )


    applications.extend(
        discover_windows_start_apps()
    )


    applications.extend(
        discover_registry_app_paths()
    )


    print(
        f"Se detectaron {len(applications)} entradas."
    )


    return applications