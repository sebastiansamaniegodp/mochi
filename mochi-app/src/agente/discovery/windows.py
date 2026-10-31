import re
import unicodedata

import os

import psutil

if os.name == "nt":
    import win32gui
    import win32process


def normalize_window_name(text: str) -> str:

    text = text.lower().strip()

    text = unicodedata.normalize(
        "NFD",
        text
    )

    text = "".join(
        char
        for char in text
        if unicodedata.category(char) != "Mn"
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


def discover_windows():

    # Linux (KDE Plasma): las ventanas se piden a KWin.
    if os.name != "nt":
        from linux_platform.windows import (
            discover_windows as discover_linux_windows
        )

        return discover_linux_windows(
            normalize_window_name
        )


    windows = []


    def callback(hwnd, _):

        try:

            # Ignorar ventanas invisibles
            if not win32gui.IsWindowVisible(hwnd):
                return


            title = (
                win32gui.GetWindowText(hwnd)
                .strip()
            )


            # Ignorar ventanas sin título
            if not title:
                return


            _, pid = (
                win32process
                .GetWindowThreadProcessId(hwnd)
            )


            process_name = ""

            executable = ""


            try:

                process = psutil.Process(pid)

                process_name = (
                    process.name()
                    .strip()
                )

                try:

                    executable = (
                        process.exe()
                    )

                except Exception:
                    executable = ""


            except Exception:
                pass


            process_without_extension = (
                process_name
                .lower()
                .removesuffix(".exe")
            )


            windows.append({

                "hwnd":
                    hwnd,

                "title":
                    title,

                "normalized_title":
                    normalize_window_name(
                        title
                    ),

                "pid":
                    pid,

                "process":
                    process_name,

                "normalized_process":
                    normalize_window_name(
                        process_without_extension
                    ),

                "executable":
                    executable
            })


        except Exception:
            pass


    win32gui.EnumWindows(
        callback,
        None
    )


    return windows