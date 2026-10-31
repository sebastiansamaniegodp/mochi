import json
import os
import re
from pathlib import Path


# Steam installs this shared runtime alongside games. It has a manifest and a
# launch URI, but it is not a title a person can play.
STEAM_NON_GAME_APP_IDS = {"228980"}
STEAM_NON_GAME_NAMES = {"steamworks common redistributables"}


def normalize_game_name(text: str) -> str:
    # Keep dotted initials together: "R.E.P.O." and "REPO" should resolve
    # to the same installed title, while ordinary punctuation remains a word
    # separator below.
    text = re.sub(
        r"\b(?:[A-Za-z]\.){2,}[A-Za-z]\.?(?=\s|$)",
        lambda match: match.group(0).replace(".", ""),
        str(text or ""),
    )
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9áéíóúñü]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def is_playable_game(game):
    """Exclude known launcher support packages from a user-facing game list."""

    if not isinstance(game, dict):
        return False
    if game.get("launcher") != "steam":
        return True
    return (
        str(game.get("id") or "") not in STEAM_NON_GAME_APP_IDS
        and normalize_game_name(game.get("name")) not in STEAM_NON_GAME_NAMES
    )


# =========================================================
# STEAM
# =========================================================

def find_steam_path():

    candidates = []

    program_files_x86 = os.environ.get("PROGRAMFILES(X86)")
    program_files = os.environ.get("PROGRAMFILES")

    if program_files_x86:
        candidates.append(
            Path(program_files_x86) / "Steam"
        )

    if program_files:
        candidates.append(
            Path(program_files) / "Steam"
        )

    # Linux: Steam nativo y Flatpak.
    if os.name != "nt":
        home = Path.home()
        candidates.extend([
            home / ".local" / "share" / "Steam",
            home / ".steam" / "steam",
            home / ".var" / "app" / "com.valvesoftware.Steam" / "data" / "Steam",
        ])

    for path in candidates:
        if path.exists():
            return path

    return None


def parse_steam_libraryfolders(file_path: Path):

    libraries = []

    if not file_path.exists():
        return libraries

    try:
        content = file_path.read_text(
            encoding="utf-8",
            errors="ignore"
        )

        matches = re.findall(
            r'"path"\s+"([^"]+)"',
            content
        )

        for match in matches:

            path = Path(
                match.replace("\\\\", "\\")
            )

            if path.exists():
                libraries.append(path)

    except Exception as e:
        print("Error leyendo Steam libraries:", e)

    return libraries


def parse_steam_manifest(manifest: Path):

    try:
        content = manifest.read_text(
            encoding="utf-8",
            errors="ignore"
        )

        app_id_match = re.search(
            r'"appid"\s+"([^"]+)"',
            content
        )

        name_match = re.search(
            r'"name"\s+"([^"]+)"',
            content
        )

        if not app_id_match or not name_match:
            return None

        app_id = app_id_match.group(1)
        name = name_match.group(1)

        game = {
            "name": name,
            "normalized_name": normalize_game_name(name),
            "launcher": "steam",
            "id": app_id,
            "launch_uri": f"steam://rungameid/{app_id}"
        }
        return game if is_playable_game(game) else None

    except Exception:
        return None


def discover_steam_games():

    games = []

    steam_path = find_steam_path()

    if not steam_path:
        return games

    libraries = [steam_path]

    library_file = (
        steam_path
        / "steamapps"
        / "libraryfolders.vdf"
    )

    libraries.extend(
        parse_steam_libraryfolders(
            library_file
        )
    )

    # Eliminar duplicados
    unique_libraries = []

    for library in libraries:
        if library not in unique_libraries:
            unique_libraries.append(library)

    for library in unique_libraries:

        steamapps = (
            library
            / "steamapps"
        )

        if not steamapps.exists():
            continue

        for manifest in steamapps.glob(
            "appmanifest_*.acf"
        ):

            game = parse_steam_manifest(
                manifest
            )

            if game:
                games.append(game)

    return games


# =========================================================
# EPIC GAMES
# =========================================================

def discover_epic_games():

    games = []

    program_data = os.environ.get(
        "PROGRAMDATA"
    )

    if not program_data:
        return games

    manifests_path = (
        Path(program_data)
        / "Epic"
        / "EpicGamesLauncher"
        / "Data"
        / "Manifests"
    )

    if not manifests_path.exists():
        return games

    for manifest in manifests_path.glob(
        "*.item"
    ):

        try:

            with open(
                manifest,
                "r",
                encoding="utf-8",
                errors="ignore"
            ) as file:

                data = json.load(file)

            display_name = data.get(
                "DisplayName"
            )

            app_name = data.get(
                "AppName"
            )

            catalog_namespace = data.get(
                "CatalogNamespace"
            )

            catalog_item_id = data.get(
                "CatalogItemId"
            )

            if not display_name:
                continue

            launch_uri = None

            if (
                catalog_namespace
                and catalog_item_id
                and app_name
            ):

                launch_uri = (
                    "com.epicgames.launcher://apps/"
                    f"{catalog_namespace}%3A"
                    f"{catalog_item_id}%3A"
                    f"{app_name}"
                    "?action=launch&silent=true"
                )

            games.append({
                "name": display_name,
                "normalized_name":
                    normalize_game_name(
                        display_name
                    ),
                "launcher": "epic",
                "id": app_name,
                "launch_uri": launch_uri
            })

        except Exception as e:

            print(
                f"Error leyendo {manifest.name}:",
                e
            )

    return games


# =========================================================
# TODOS
# =========================================================

def discover_games():

    print("")
    print("Buscando juegos instalados...")

    games = []

    steam_games = discover_steam_games()
    epic_games = discover_epic_games()

    games.extend(steam_games)
    games.extend(epic_games)

    print(
        f"Steam: {len(steam_games)} juegos"
    )

    print(
        f"Epic: {len(epic_games)} juegos"
    )

    print(
        f"Total: {len(games)} juegos"
    )

    return games
