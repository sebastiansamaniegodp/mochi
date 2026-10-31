import os
import subprocess

from config_manager import get_config
from linux_platform import system_open


def get_chrome_path():

    config = get_config()

    return (
        config
        .get("browser", {})
        .get("executable")
    )


def _resolve_profile_directory(browser, profile_reference):
    """Resolve a configured profile ID to Chrome's on-disk directory.

    Configuration commonly names a profile ``personal`` while Chrome expects
    ``Default`` in ``--profile-directory``.  Callers may still pass an actual
    directory such as ``Profile 5`` directly, so unknown references retain
    their literal value.
    """
    reference = str(profile_reference or "").strip()
    if not reference:
        return None
    configured = (browser.get("profiles", {}) or {}).get(reference)
    if isinstance(configured, dict):
        return configured.get("directory") or reference
    return reference


def open_chrome(
    profile_directory=None
):

    chrome = get_chrome_path()

    if not chrome:

        return False


    config = get_config()

    browser = config.get(
        "browser",
        {}
    )


    if profile_directory is None:
        profile_directory = browser.get("default_profile")
    profile_directory = _resolve_profile_directory(browser, profile_directory)


    arguments = [
        chrome
    ]


    if profile_directory:

        arguments.append(
            f"--profile-directory={profile_directory}"
        )

    if browser.get("force_renderer_accessibility", True):
        arguments.append("--force-renderer-accessibility")


    try:

        subprocess.Popen(
            arguments
        )

        return True

    except Exception as e:

        print(
            "Error abriendo Chrome:",
            e
        )

        return False


def open_url(
    url,
    profile_directory=None
):

    chrome = get_chrome_path()

    if not chrome:

        return False


    config = get_config()

    browser = config.get(
        "browser",
        {}
    )


    if profile_directory is None:
        profile_directory = browser.get("default_profile")
    profile_directory = _resolve_profile_directory(browser, profile_directory)


    arguments = [
        chrome
    ]


    if profile_directory:

        arguments.append(
            f"--profile-directory={profile_directory}"
        )

    if browser.get("force_renderer_accessibility", True):
        arguments.append("--force-renderer-accessibility")


    arguments.append(
        url
    )


    try:

        subprocess.Popen(
            arguments
        )

        return True

    except Exception as e:

        print(
            "Error abriendo URL:",
            e
        )

        return False


def open_spotify():

    try:

        system_open(
            "spotify:"
        )

        return True

    except Exception as e:

        print(
            "Error Spotify:",
            e
        )

        return False
