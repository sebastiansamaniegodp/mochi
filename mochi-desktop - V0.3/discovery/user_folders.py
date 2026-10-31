import os
from pathlib import Path

try:
    import winreg
except ImportError:
    winreg = None


USER_SHELL_FOLDERS = (
    r"Software\Microsoft\Windows"
    r"\CurrentVersion\Explorer\User Shell Folders"
)


FOLDER_REGISTRY_VALUES = {

    "desktop":
        "Desktop",

    "documents":
        "Personal",

    "downloads":
        "{374DE290-123F-4565-9164-39C4925E467B}",

    "pictures":
        "My Pictures",

    "music":
        "My Music",

    "videos":
        "My Video",
}


def _expand_path(value):

    if not value:
        return None

    value = os.path.expandvars(
        str(value)
    )

    path = Path(value)

    try:
        return path.resolve()
    except Exception:
        return path


def _read_windows_folder(
    value_name
):

    if winreg is None:
        return None

    try:

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            USER_SHELL_FOLDERS
        ) as key:

            value, _ = (
                winreg.QueryValueEx(
                    key,
                    value_name
                )
            )

            path = _expand_path(
                value
            )

            if (
                path is not None
                and path.exists()
            ):

                return path

    except OSError:
        pass

    return None


def discover_user_folders():

    home = Path.home()

    fallback = {

        "desktop":
            home / "Desktop",

        "documents":
            home / "Documents",

        "downloads":
            home / "Downloads",

        "pictures":
            home / "Pictures",

        "music":
            home / "Music",

        "videos":
            home / "Videos",
    }


    folders = {}


    for name, registry_name in (
        FOLDER_REGISTRY_VALUES.items()
    ):

        detected = (
            _read_windows_folder(
                registry_name
            )
        )


        # Linux: xdg-user-dirs (Escritorio, Documentos...).
        if detected is None and os.name != "nt":

            from linux_platform.folders import (
                read_xdg_folder
            )

            detected = read_xdg_folder(
                name
            )

        if (
            detected is None
            and fallback[name].exists()
        ):

            detected = fallback[name]


        if detected is not None:

            folders[name] = detected


    # =====================================================
    # ONEDRIVE
    # =====================================================

    one_drive_candidates = [

        os.environ.get(
            "OneDrive"
        ),

        os.environ.get(
            "OneDriveConsumer"
        ),

        os.environ.get(
            "OneDriveCommercial"
        ),
    ]


    for candidate in one_drive_candidates:

        if not candidate:
            continue

        path = Path(candidate)

        if path.exists():

            folders.setdefault(
                "onedrive",
                path
            )

            break


    folders["home"] = home

    return folders


def print_user_folders():

    folders = (
        discover_user_folders()
    )

    print("")
    print(
        "======================================"
    )
    print(
        "        CARPETAS DEL USUARIO"
    )
    print(
        "======================================"
    )

    for name, path in folders.items():

        print(
            f"{name}: {path}"
        )

    print(
        "======================================"
    )