"""Comandos de terminal de solo lectura para Mochi (Linux).

Mochi puede consultar el sistema (puertos, procesos, disco, red,
servicios, archivos) pero no modificarlo. No se usa un shell: el
texto se separa en palabras, solo se acepta `|` entre comandos y
cada comando tiene que estar en la lista de permitidos, sin las
opciones que escriben, borran o ejecutan otra cosa.
"""

import glob
import os
import re
import shlex
import subprocess
import tempfile
import time


# =========================================================
# LÍMITES
# =========================================================

TIMEOUT_SECONDS = 15

# La RX 580 lee ~210 tokens/s: una salida larga se paga cara.
MAX_OUTPUT_CHARS = 2500

MAX_PIPELINE = 5


# =========================================================
# COMANDOS PERMITIDOS
#
# Valor: opciones largas prohibidas (por prefijo) y letras
# prohibidas dentro de las opciones cortas (-abc).
# =========================================================

def _rule(long=(), short="", subcommands=None, words=()):

    return {
        "long": tuple(long),
        "short": short,
        "subcommands": subcommands,
        "words": frozenset(words),
    }


_PLAIN = _rule()

ALLOWED_COMMANDS = {

    # Red y puertos
    "ss": _rule(long=("--kill", "--diag"), short="KD"),
    "ip": _rule(
        long=("-batch", "--batch"),
        words=(
            "add", "del", "delete", "set", "flush", "change",
            "replace", "append", "prepend", "exec", "save",
            "restore", "-b",
        ),
    ),
    "ping": _rule(long=("--flood",), short="f"),
    "getent": _PLAIN,
    "hostname": _rule(long=("--file",), short="Fb"),

    # Procesos
    "ps": _PLAIN,
    "pgrep": _PLAIN,
    "pidof": _PLAIN,
    "lsof": _PLAIN,
    "uptime": _PLAIN,
    "w": _PLAIN,
    "who": _PLAIN,
    "last": _PLAIN,

    # Disco y memoria
    "df": _PLAIN,
    "du": _PLAIN,
    "free": _PLAIN,
    "lsblk": _PLAIN,
    "findmnt": _PLAIN,

    # Hardware y sistema
    "uname": _PLAIN,
    "lscpu": _PLAIN,
    "lsusb": _PLAIN,
    "lspci": _PLAIN,
    "lsmem": _PLAIN,
    "nproc": _PLAIN,
    "sensors": _rule(long=("--set",), short="s"),
    "upower": _PLAIN,
    "whoami": _PLAIN,
    "id": _PLAIN,
    "date": _rule(long=("--set",), short="s"),
    "dmesg": _rule(
        long=(
            "--clear", "--read-clear", "--console",
            "--follow", "--follow-new",
        ),
        short="cCDEnwW",
    ),

    # Servicios y registros
    "systemctl": _rule(
        subcommands=(
            "status", "is-active", "is-enabled", "is-failed",
            "list-units", "list-unit-files", "list-timers",
            "list-sockets", "show", "cat",
        ),
    ),
    "journalctl": _rule(
        long=(
            "--rotate", "--vacuum", "--flush", "--sync",
            "--relinquish", "--smart-relinquish", "--setup-keys",
            "--update-catalog", "--follow",
        ),
        short="f",
    ),
    "timedatectl": _rule(
        subcommands=(
            "", "status", "show", "list-timezones",
            "timesync-status", "show-timesync",
        ),
    ),
    "loginctl": _rule(
        subcommands=(
            "", "list-sessions", "list-users", "list-seats",
            "session-status", "user-status", "show-session",
            "show-user",
        ),
    ),

    # Paquetes
    "dpkg": _rule(
        subcommands=(
            "-l", "--list", "-s", "--status", "-L",
            "--listfiles", "-S", "--search", "-p",
            "--print-avail", "--version",
            "--print-architecture",
        ),
    ),
    "apt": _rule(
        subcommands=("list", "show", "policy", "search"),
    ),
    "apt-cache": _PLAIN,

    # Audio
    "pactl": _rule(
        subcommands=(
            "list", "info", "stat", "get-default-sink",
            "get-default-source", "get-sink-volume",
            "get-sink-mute", "get-source-volume",
            "get-source-mute",
        ),
    ),
    "wpctl": _rule(
        subcommands=("status", "get-volume", "inspect"),
    ),

    # Archivos (leer)
    "ls": _PLAIN,
    "cat": _PLAIN,
    "head": _PLAIN,
    "tail": _rule(long=("--follow", "--retry"), short="fF"),
    "wc": _PLAIN,
    "stat": _PLAIN,
    "file": _rule(long=("--compile",), short="C"),
    "find": _rule(
        long=(
            "-delete", "-exec", "-ok", "-fprint", "-fls",
        ),
    ),
    "which": _PLAIN,
    "readlink": _PLAIN,
    "realpath": _PLAIN,
    "basename": _PLAIN,
    "dirname": _PLAIN,
    "pwd": _PLAIN,
    "md5sum": _PLAIN,
    "sha256sum": _PLAIN,

    # Filtros de texto
    "grep": _PLAIN,
    "sort": _rule(
        long=("--output", "--compress-program"),
        short="o",
    ),
    "uniq": _PLAIN,
    "cut": _PLAIN,
    "tr": _PLAIN,
    "echo": _PLAIN,
}


# Reciben patrones propios: no se expanden comodines.
_NO_GLOB = {"grep", "find", "pgrep", "tr", "echo", "apt", "apt-cache", "dpkg"}


# =========================================================
# RUTAS PRIVADAS
#
# Claves y sesiones que no hace falta que lea el modelo.
# =========================================================

_PRIVATE_MARKERS = (
    ".ssh", ".gnupg", ".mochi/config", ".mochi_chrome",
    "api_key", "/etc/shadow", "/etc/gshadow", "/etc/sudoers",
    ".netrc", "id_rsa", "id_ed25519", "login data", "cookies",
    "keyring", "kwallet", ".password-store", "/proc/kcore",
)


# =========================================================
# ERRORES
# =========================================================

class CommandRejected(Exception):
    pass


# =========================================================
# SEPARAR EN COMANDOS
# =========================================================

_STDERR_REDIRECT = re.compile(r"\s*2>\s*(?:/dev/null|&1)")

_OPERATOR = re.compile(r"^[();<>|&]+$")


def _split_pipeline(command):

    command = _STDERR_REDIRECT.sub(" ", command)

    if "`" in command or "$(" in command or "\n" in command:

        raise CommandRejected(
            "No se permite ejecutar un comando dentro de otro."
        )

    lexer = shlex.shlex(
        command,
        posix=True,
        punctuation_chars=True
    )

    lexer.whitespace_split = True

    try:

        tokens = list(lexer)

    except ValueError as error:

        raise CommandRejected(
            f"Comando mal escrito: {error}"
        )

    stages = [[]]

    for token in tokens:

        if token == "|":

            stages.append([])

        elif _OPERATOR.match(token):

            raise CommandRejected(
                f"No se permite '{token}': solo comandos de "
                "lectura unidos con '|', sin redirecciones "
                "ni ';' ni '&&'."
            )

        else:

            stages[-1].append(token)

    if any(not stage for stage in stages):

        raise CommandRejected("Comando vacío.")

    if len(stages) > MAX_PIPELINE:

        raise CommandRejected(
            f"Demasiados comandos encadenados (máximo {MAX_PIPELINE})."
        )

    return stages


# =========================================================
# VALIDAR UN COMANDO
# =========================================================

def _check_stage(stage):

    name = stage[0]

    arguments = stage[1:]

    rule = ALLOWED_COMMANDS.get(name)

    if rule is None:

        raise CommandRejected(
            f"'{name}' no está permitido. Solo comandos de "
            "lectura: "
            + ", ".join(sorted(ALLOWED_COMMANDS))
            + "."
        )

    for argument in arguments:

        lowered = argument.lower()

        if any(marker in lowered for marker in _PRIVATE_MARKERS):

            raise CommandRejected(
                f"'{argument}' es una ruta privada (claves o "
                "sesiones): no se lee."
            )

        if argument in rule["words"]:

            raise CommandRejected(
                f"'{name} ... {argument}' modifica el sistema: "
                "solo se permite consultar."
            )

        if not argument.startswith("-") or argument == "-":

            continue

        if any(argument.startswith(bad) for bad in rule["long"]):

            raise CommandRejected(
                f"La opción '{argument}' de {name} no es de "
                "solo lectura."
            )

        if (
            not argument.startswith("--")
            and any(letter in argument[1:] for letter in rule["short"])
        ):

            raise CommandRejected(
                f"La opción '{argument}' de {name} no es de "
                "solo lectura."
            )

    if rule["subcommands"] is not None:

        if name == "dpkg":

            first = arguments[0] if arguments else ""

        else:

            first = next(
                (a for a in arguments if not a.startswith("-")),
                ""
            )

        if first not in rule["subcommands"]:

            raise CommandRejected(
                f"'{name} {first}' no está permitido. Solo: "
                + ", ".join(s for s in rule["subcommands"] if s)
                + "."
            )

    if name == "uniq":

        positional = [a for a in arguments if not a.startswith("-")]

        if len(positional) > 1:

            raise CommandRejected(
                "uniq con archivo de salida no es de solo lectura."
            )

    if name == "hostname":

        if any(not a.startswith("-") for a in arguments):

            raise CommandRejected(
                "hostname solo se permite para consultar."
            )


# =========================================================
# PREPARAR ARGUMENTOS (~, $HOME, comodines)
# =========================================================

def _expand(stage):

    name = stage[0]

    expanded = [name]

    for argument in stage[1:]:

        if not argument.startswith("-"):

            argument = os.path.expanduser(argument)

        if (
            name not in _NO_GLOB
            and not argument.startswith("-")
            and any(char in argument for char in "*?[")
        ):

            matches = sorted(glob.glob(argument))[:200]

            if matches:

                expanded.extend(matches)

                continue

        expanded.append(argument)

    # Un ping sin límite no termina nunca.
    if name == "ping" and not any(a.startswith("-c") for a in expanded[1:]):

        expanded[1:1] = ["-c", "4"]

    return expanded


# =========================================================
# EJECUTAR
# =========================================================

def _read_limited(handle):

    handle.seek(0)

    data = handle.read(MAX_OUTPUT_CHARS * 4 + 1)

    text = data.decode("utf-8", errors="replace")

    truncated = len(text) > MAX_OUTPUT_CHARS

    return text[:MAX_OUTPUT_CHARS].rstrip(), truncated


def run_readonly_command(command):

    command = str(command or "").strip()

    if not command:

        return {
            "ok": False,
            "message": "No se indicó ningún comando.",
        }

    try:

        stages = _split_pipeline(command)

        for stage in stages:

            _check_stage(stage)

        stages = [_expand(stage) for stage in stages]

        # Los comodines expandidos también se revisan.
        for stage in stages:

            _check_stage(stage)

    except CommandRejected as error:

        return {
            "ok": False,
            "rejected": True,
            "command": command,
            "message": str(error),
        }

    processes = []

    timed_out = False

    with tempfile.TemporaryFile() as stdout_file, \
            tempfile.TemporaryFile() as stderr_file:

        try:

            previous = None

            for index, stage in enumerate(stages):

                last = index == len(stages) - 1

                process = subprocess.Popen(
                    stage,
                    stdin=(
                        previous.stdout
                        if previous is not None
                        else subprocess.DEVNULL
                    ),
                    stdout=stdout_file if last else subprocess.PIPE,
                    stderr=stderr_file,
                    cwd=os.path.expanduser("~"),
                    start_new_session=True,
                )

                # El padre suelta su copia para que llegue
                # SIGPIPE cuando el siguiente termina.
                if previous is not None:

                    previous.stdout.close()

                processes.append(process)

                previous = process

        except FileNotFoundError:

            for process in processes:

                process.kill()

            return {
                "ok": False,
                "command": command,
                "message": (
                    f"'{stage[0]}' no está instalado en este equipo."
                ),
            }

        except OSError as error:

            for process in processes:

                process.kill()

            return {
                "ok": False,
                "command": command,
                "message": f"No pude ejecutar '{stage[0]}': {error}",
            }

        deadline = time.monotonic() + TIMEOUT_SECONDS

        for process in processes:

            try:

                process.wait(
                    timeout=max(0.1, deadline - time.monotonic())
                )

            except subprocess.TimeoutExpired:

                timed_out = True

                break

        if timed_out:

            for process in processes:

                if process.poll() is None:

                    process.kill()

                    process.wait()

        output, truncated = _read_limited(stdout_file)

        errors, _ = _read_limited(stderr_file)

    exit_code = processes[-1].returncode

    result = {
        "ok": not timed_out,
        "command": command,
        "exit_code": exit_code,
        "output": output,
    }

    if errors:

        result["stderr"] = errors[:600]

    if truncated:

        result["truncated"] = True

    if timed_out:

        result["message"] = (
            f"El comando tardó más de {TIMEOUT_SECONDS} s y se cortó."
        )

    elif not output and not errors:

        # grep sin coincidencias, puerto libre, etc.
        result["message"] = (
            "El comando terminó sin mostrar nada "
            f"(código {exit_code}): no hubo resultados."
        )

    elif truncated:

        result["message"] = (
            "Salida recortada: acotá el comando con grep, head "
            "o tail si falta algo."
        )

    else:

        result["message"] = "Comando ejecutado."

    return result
