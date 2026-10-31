import json
import os
from pathlib import Path

try:
    import winreg
except ImportError:
    winreg = None


def find_chrome_executable():
    """
    Busca Google Chrome sin depender del nombre del usuario
    ni de una ruta fija.
    """

    # Linux: Google Chrome o Chromium desde el PATH.
    if os.name != "nt":
        from linux_platform import chrome

        return chrome.find_chrome_executable()

    # 1. Buscar en registro de Windows
    if winreg is not None:

        registry_locations = [
            (
                winreg.HKEY_CURRENT_USER,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
            ),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
            ),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
            ),
        ]

        for hive, key_path in registry_locations:

            try:

                with winreg.OpenKey(
                    hive,
                    key_path
                ) as key:

                    chrome_path, _ = (
                        winreg.QueryValueEx(
                            key,
                            None
                        )
                    )

                    chrome_path = (
                        chrome_path
                        .strip('"')
                    )

                    if Path(chrome_path).exists():

                        return str(
                            Path(chrome_path)
                        )

            except OSError:
                pass

    # 2. Rutas comunes como fallback

    possible_paths = [

        Path(
            os.environ.get(
                "PROGRAMFILES",
                ""
            )
        )
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",

        Path(
            os.environ.get(
                "PROGRAMFILES(X86)",
                ""
            )
        )
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",

        Path(
            os.environ.get(
                "LOCALAPPDATA",
                ""
            )
        )
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
    ]


    for path in possible_paths:

        if path.exists():
            return str(path)


    return None


def get_chrome_user_data_dir():

    if os.name != "nt":
        from linux_platform import chrome

        return chrome.get_chrome_user_data_dir()

    local_app_data = os.environ.get(
        "LOCALAPPDATA"
    )

    if not local_app_data:
        return None


    path = (
        Path(local_app_data)
        / "Google"
        / "Chrome"
        / "User Data"
    )


    if path.exists():
        return path


    return None


def discover_chrome_profiles():

    user_data_dir = (
        get_chrome_user_data_dir()
    )


    if user_data_dir is None:

        return {}


    profiles = {}


    # =====================================================
    # Leer Local State de Chrome
    # =====================================================

    local_state_path = (
        user_data_dir
        / "Local State"
    )


    if local_state_path.exists():

        try:

            with open(
                local_state_path,
                "r",
                encoding="utf-8"
            ) as file:

                data = json.load(file)


            info_cache = (
                data
                .get("profile", {})
                .get("info_cache", {})
            )


            for directory, info in info_cache.items():

                profiles[directory] = {

                    "directory":
                        directory,

                    "name":
                        info.get(
                            "name",
                            directory
                        ),

                    "email":
                        info.get(
                            "user_name",
                            ""
                        ),

                    "gaia_name":
                        info.get(
                            "gaia_name",
                            ""
                        ),
                }

        except Exception as e:

            print(
                "No pude leer Local State:"
            )

            print(e)


    # =====================================================
    # Fallback:
    # buscar carpetas físicamente
    # =====================================================

    if not profiles:

        try:

            for item in user_data_dir.iterdir():

                if not item.is_dir():
                    continue


                if (
                    item.name == "Default"
                    or
                    item.name.startswith(
                        "Profile "
                    )
                ):

                    profiles[item.name] = {

                        "directory":
                            item.name,

                        "name":
                            item.name,

                        "email":
                            "",

                        "gaia_name":
                            "",
                    }

        except Exception as e:

            print(
                "Error buscando perfiles:"
            )

            print(e)


    return profiles


def discover_chrome():

    executable = (
        find_chrome_executable()
    )

    user_data_dir = (
        get_chrome_user_data_dir()
    )

    profiles = (
        discover_chrome_profiles()
    )


    return {

        "executable":
            executable,

        "user_data_dir":
            (
                str(user_data_dir)
                if user_data_dir
                else None
            ),

        "profiles":
            profiles
    }