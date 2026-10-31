import json
import os
import tempfile
import threading
import time

from chrome_profiles import (
    discover_chrome
)

from mochi_environment import (
    environment
)

from hardware_profiler import (
    hardware_profiler
)


# =========================================================
# VERSIÓN ACTUAL DE CONFIGURACIÓN
# =========================================================

CONFIG_VERSION = 4


# =========================================================
# UBICACIÓN DE CONFIGURACIÓN
#
# Ya NO calculamos APPDATA acá.
# MochiEnvironment es la única fuente de rutas.
# =========================================================

CONFIG_DIR = (
    environment.data_dir
)

CONFIG_PATH = (
    environment.config_file
)


_CONFIG_REPLACE_LOCK = (
    threading.Lock()
)


# =========================================================
# SERVICIOS CONOCIDOS
#
# IMPORTANTE:
#
# Son solamente accesos rápidos.
# No significa que Mochi solamente pueda abrir estos sitios.
# =========================================================

DEFAULT_SERVICES = {

    "youtube": {
        "url":
            "https://www.youtube.com"
    },

    "youtube_music": {
        "url":
            "https://music.youtube.com"
    },

    "google": {
        "url":
            "https://www.google.com"
    },

    "gmail": {
        "url":
            "https://mail.google.com"
    },

    "drive": {
        "url":
            "https://drive.google.com"
    },

    "outlook": {
        "url":
            "https://outlook.office.com"
    },

    "whatsapp": {
        "url":
            "https://web.whatsapp.com"
    },

    "sheets": {
        "url":
            "https://docs.google.com/spreadsheets"
    },

    "netflix": {
        "url":
            "https://www.netflix.com"
    },

    "github": {
        "url":
            "https://github.com"
    },

    "chatgpt": {
        "url":
            "https://chatgpt.com"
    }
}


# =========================================================
# CARGAR CONFIGURACIÓN
# =========================================================

def load_config():

    if not CONFIG_PATH.exists():

        return None


    try:

        with open(
            CONFIG_PATH,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(
                file
            )


        if not isinstance(
            data,
            dict
        ):

            print(
                "La configuración no tiene "
                "un formato válido."
            )

            return None


        return data


    except Exception as e:

        print("")
        print(
            "Error leyendo configuración:"
        )
        print(
            repr(e)
        )

        return None


# =========================================================
# GUARDAR CONFIGURACIÓN
# =========================================================

def save_config(
    config
):

    CONFIG_DIR.mkdir(
        parents=True,
        exist_ok=True
    )


    temporary_path = None


    try:

        # Serializar antes de tocar el archivo vigente. Si el objeto no es
        # válido, la configuración anterior permanece intacta.
        payload = json.dumps(
            config,
            indent=4,
            ensure_ascii=False
        )


        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".config-",
            suffix=".tmp",
            dir=CONFIG_DIR
        )

        temporary_path = temporary_name


        with os.fdopen(
            descriptor,
            "w",
            encoding="utf-8"
        ) as file:

            file.write(
                payload
            )

            file.flush()

            # Garantiza que el contenido temporal llegue al disco antes de
            # sustituir atómicamente al archivo visible por otros procesos.
            os.fsync(
                file.fileno()
            )


        # En Windows dos procesos pueden intentar reemplazar el mismo
        # destino en el mismo instante. El lock cubre hilos del proceso y
        # los reintentos breves absorben esa colisión entre procesos.
        with _CONFIG_REPLACE_LOCK:

            for attempt in range(20):

                try:

                    os.replace(
                        temporary_path,
                        CONFIG_PATH
                    )

                    break

                except PermissionError:

                    if attempt == 19:

                        raise

                    time.sleep(
                        0.01
                    )

        temporary_path = None


        return True


    except Exception as e:

        print("")
        print(
            "Error guardando configuración:"
        )
        print(
            repr(e)
        )

        return False


    finally:

        if temporary_path:

            try:

                os.unlink(
                    temporary_path
                )

            except FileNotFoundError:

                pass


# =========================================================
# PERFIL INICIAL DE CHROME
# =========================================================

def _select_initial_profile(
    profiles
):

    if not profiles:

        return None


    # =====================================================
    # Preferimos el perfil Default si existe
    # =====================================================

    if "Default" in profiles:

        return "Default"


    # =====================================================
    # Si no existe, utilizar el primero detectado
    # =====================================================

    return next(
        iter(profiles)
    )


# =========================================================
# INFORMACIÓN DE HARDWARE
#
# Esta función NO se ejecutará en cada comando.
# Solamente cuando se crea la configuración o cuando
# explícitamente pidamos actualizar el hardware.
# =========================================================

def _get_hardware_snapshot():

    try:

        return (
            hardware_profiler
            .analyze()
        )


    except Exception as e:

        print("")
        print(
            "No pude analizar completamente "
            "el hardware:"
        )

        print(
            repr(e)
        )


        return {
            "os": {},
            "cpu": {},
            "ram": {},
            "gpu": [],
            "mochi": {
                "profile":
                    "unknown",

                "recommended":
                    False,

                "description":
                    "Hardware no analizado"
            }
        }


# =========================================================
# CREAR DEVICE CONFIG
# =========================================================

def _create_device_config():

    hardware = (
        _get_hardware_snapshot()
    )


    return {

        "name":
            environment.machine_name,

        "hardware":
            hardware,

        "mochi_profile":
            hardware
            .get(
                "mochi",
                {}
            )
            .get(
                "profile",
                "unknown"
            )
    }


# =========================================================
# CREAR CONFIGURACIÓN DE BRAIN
#
# Todavía NO elegimos runtime ni modelo.
# Dejamos preparada la estructura.
# =========================================================

def _create_brain_config():

    return {

        "enabled":
            False,

        "runtime":
            None,

        "model":
            None,

        "model_profile":
            None,

        "context_size":
            None,

        "vision_enabled":
            False,

        # Solo para runtime "llama_cpp":
        # URL de llama-server en la PC del LLM.
        "base_url":
            None,

        "api_key":
            None,

        # Mochi usa el modelo sin razonamiento.
        "thinking_enabled":
            False
    }


# =========================================================
# CREAR PREFERENCIAS
# =========================================================

def _create_preferences():

    return {

        "language":
            "es-PY",

        "confirm_sensitive_actions":
            True,

        "default_music_service":
            "youtube_music",

        "default_video_service":
            "youtube",

        # Contexto web efímero, en segundos. No almacena cookies, credenciales
        # ni referencias UI Automation; solo evita reabrir una página activa
        # que todavía puede verificarse mediante UIA.
        "web_session_cache_ttl_seconds": 900,

        # Lista de alternativas web que Mochi mostró al usuario. Permite que
        # una orden posterior como "reproduce el tercero" continúe la misma
        # elección tras una nueva inspección accesible de la pestaña.
        "pending_page_choice_ttl_seconds": 900,

        # Plantilla intercambiable para la capability de búsqueda web.
        # {query} se sustituye con texto URL-encoded.
        "search_url_template":
            "https://www.google.com/search?q={query}"
    }


# =========================================================
# CREAR CONFIGURACIÓN DE SEGURIDAD
#
# Más adelante acá vivirán:
#
# pairing
# tokens
# PCs autorizadas
# permisos
# confirmaciones
# =========================================================

def _create_security_config():

    return {

        "pairing_enabled":
            False,

        "require_authentication":
            False
    }


# =========================================================
# CREAR CONFIGURACIÓN NUEVA
# =========================================================

def create_config():

    print("")
    print(
        "Creando configuración inicial "
        "de Mochi..."
    )


    # =====================================================
    # DETECTAR CHROME
    # =====================================================

    chrome = (
        discover_chrome()
    )


    profiles = (
        chrome.get(
            "profiles",
            {}
        )
        or {}
    )


    default_profile = (
        _select_initial_profile(
            profiles
        )
    )


    # =====================================================
    # SERVICIOS
    # =====================================================

    services = {}


    for name, info in (
        DEFAULT_SERVICES.items()
    ):

        services[name] = {

            "url":
                info["url"],

            "profile":
                default_profile
        }


    # =====================================================
    # CONFIG COMPLETA
    # =====================================================

    config = {

        "version":
            CONFIG_VERSION,

        "device":
            _create_device_config(),

        "brain":
            _create_brain_config(),

        "browser": {

            "type":
                "chrome",

            "executable":
                chrome.get(
                    "executable"
                ),

            "user_data_dir":
                chrome.get(
                    "user_data_dir"
                ),

            "default_profile":
                default_profile,

            "profiles":
                profiles,

            "force_renderer_accessibility": True
        },

        "services":
            services,

        "preferences":
            _create_preferences(),

        "security":
            _create_security_config()
    }


    save_config(
        config
    )


    return config


# =========================================================
# SINCRONIZAR DEVICE
# =========================================================

def _sync_device(
    config
):

    if "device" not in config:

        config["device"] = (
            _create_device_config()
        )

        return


    device = (
        config["device"]
    )


    # =====================================================
    # Nombre actual de la máquina
    # =====================================================

    device["name"] = (
        environment.machine_name
    )


    # =====================================================
    # Si esta config viene de una versión vieja,
    # generar hardware.
    #
    # NO analizamos hardware en cada ejecución.
    # =====================================================

    if (
        "hardware"
        not in device
        or
        not isinstance(
            device["hardware"],
            dict
        )
    ):

        hardware = (
            _get_hardware_snapshot()
        )


        device["hardware"] = (
            hardware
        )


        device[
            "mochi_profile"
        ] = (
            hardware
            .get(
                "mochi",
                {}
            )
            .get(
                "profile",
                "unknown"
            )
        )


    # =====================================================
    # Compatibilidad si hardware ya existe,
    # pero mochi_profile todavía no.
    # =====================================================

    elif (
        "mochi_profile"
        not in device
    ):

        device[
            "mochi_profile"
        ] = (
            device[
                "hardware"
            ]
            .get(
                "mochi",
                {}
            )
            .get(
                "profile",
                "unknown"
            )
        )


# =========================================================
# SINCRONIZAR BRAIN
# =========================================================

def _sync_brain(
    config
):

    defaults = (
        _create_brain_config()
    )


    if "brain" not in config:

        config["brain"] = (
            defaults
        )

        return


    brain = (
        config["brain"]
    )


    for key, value in (
        defaults.items()
    ):

        brain.setdefault(
            key,
            value
        )


# =========================================================
# SINCRONIZAR PREFERENCIAS
# =========================================================

def _sync_preferences(
    config
):

    defaults = (
        _create_preferences()
    )


    if "preferences" not in config:

        config[
            "preferences"
        ] = defaults

        return


    preferences = (
        config[
            "preferences"
        ]
    )


    for key, value in (
        defaults.items()
    ):

        preferences.setdefault(
            key,
            value
        )


# =========================================================
# SINCRONIZAR SEGURIDAD
# =========================================================

def _sync_security(
    config
):

    defaults = (
        _create_security_config()
    )


    if "security" not in config:

        config["security"] = (
            defaults
        )

        return


    security = (
        config["security"]
    )


    for key, value in (
        defaults.items()
    ):

        security.setdefault(
            key,
            value
        )


# =========================================================
# SINCRONIZAR CHROME
# =========================================================

def _sync_browser(
    config
):

    chrome = (
        discover_chrome()
    )


    if "browser" not in config:

        config["browser"] = {}


    browser = (
        config["browser"]
    )


    browser["type"] = (
        "chrome"
    )


    browser["executable"] = (
        chrome.get(
            "executable"
        )
    )


    browser["user_data_dir"] = (
        chrome.get(
            "user_data_dir"
        )
    )

    browser.setdefault("force_renderer_accessibility", True)


    detected_profiles = (
        chrome.get(
            "profiles",
            {}
        )
        or {}
    )


    # =====================================================
    # Siempre sincronizamos perfiles detectados
    # =====================================================

    browser["profiles"] = (
        detected_profiles
    )


    # =====================================================
    # Revisar perfil predeterminado
    # =====================================================

    default_profile = (
        browser.get(
            "default_profile"
        )
    )


    if (
        default_profile
        not in detected_profiles
    ):

        browser[
            "default_profile"
        ] = (
            _select_initial_profile(
                detected_profiles
            )
        )


# =========================================================
# SINCRONIZAR SERVICIOS
# =========================================================

def _sync_services(
    config
):

    if "services" not in config:

        config["services"] = {}


    services = (
        config["services"]
    )


    browser = (
        config.get(
            "browser",
            {}
        )
    )


    profiles = (
        browser.get(
            "profiles",
            {}
        )
        or {}
    )


    default_profile = (
        browser.get(
            "default_profile"
        )
    )


    for name, info in (
        DEFAULT_SERVICES.items()
    ):

        # =================================================
        # Servicio nuevo
        # =================================================

        if name not in services:

            services[name] = {

                "url":
                    info["url"],

                "profile":
                    default_profile
            }


            continue


        service = (
            services[name]
        )


        # =================================================
        # Actualizar URL oficial
        # =================================================

        service["url"] = (
            info["url"]
        )


        # =================================================
        # MUY IMPORTANTE:
        #
        # No sobrescribir un perfil personalizado
        # si sigue existiendo.
        #
        # Ejemplo:
        # Netflix → Marian
        # =================================================

        selected_profile = (
            service.get(
                "profile"
            )
        )


        if (
            selected_profile
            and
            selected_profile
            in profiles
        ):

            continue


        service["profile"] = (
            default_profile
        )


# =========================================================
# SINCRONIZAR CONFIGURACIÓN COMPLETA
# =========================================================

def sync_config(
    config
):

    # =====================================================
    # MIGRAR A VERSIÓN ACTUAL
    # =====================================================

    config["version"] = (
        CONFIG_VERSION
    )


    # =====================================================
    # DEVICE
    # =====================================================

    _sync_device(
        config
    )


    # =====================================================
    # BRAIN
    # =====================================================

    _sync_brain(
        config
    )


    # =====================================================
    # BROWSER
    # =====================================================

    _sync_browser(
        config
    )


    # =====================================================
    # SERVICES
    # =====================================================

    _sync_services(
        config
    )


    # =====================================================
    # PREFERENCES
    # =====================================================

    _sync_preferences(
        config
    )


    # =====================================================
    # SECURITY
    # =====================================================

    _sync_security(
        config
    )


    save_config(
        config
    )


    return config


# =========================================================
# OBTENER CONFIGURACIÓN
# =========================================================

def get_config():

    config = (
        load_config()
    )


    if config is None:

        config = (
            create_config()
        )


    else:

        config = (
            sync_config(
                config
            )
        )


    return config


# =========================================================
# ACTUALIZAR HARDWARE MANUALMENTE
#
# Esto será útil para:
#
# - instalador
# - diagnóstico
# - cambio de GPU
# - cambio de RAM
# - selección de modelo
# =========================================================

def refresh_hardware():

    config = (
        get_config()
    )


    hardware = (
        _get_hardware_snapshot()
    )


    config.setdefault(
        "device",
        {}
    )


    config["device"][
        "name"
    ] = (
        environment.machine_name
    )


    config["device"][
        "hardware"
    ] = (
        hardware
    )


    config["device"][
        "mochi_profile"
    ] = (
        hardware
        .get(
            "mochi",
            {}
        )
        .get(
            "profile",
            "unknown"
        )
    )


    save_config(
        config
    )


    return config


# =========================================================
# MOSTRAR CONFIGURACIÓN
# =========================================================

def print_configuration():

    config = (
        get_config()
    )


    device = (
        config.get(
            "device",
            {}
        )
    )


    hardware = (
        device.get(
            "hardware",
            {}
        )
    )


    mochi_hardware = (
        hardware.get(
            "mochi",
            {}
        )
    )


    browser = (
        config.get(
            "browser",
            {}
        )
    )


    # =====================================================
    # CABECERA
    # =====================================================

    print("")
    print(
        "======================================"
    )
    print(
        "        MOCHI - ENTORNO DETECTADO"
    )
    print(
        "======================================"
    )


    # =====================================================
    # DISPOSITIVO
    # =====================================================

    print("")
    print(
        "Dispositivo:"
    )


    print(
        f"  PC: "
        f"{device.get('name', 'Desconocida')}"
    )


    print(
        f"  Perfil Mochi: "
        f"{device.get('mochi_profile', 'unknown')}"
    )


    if mochi_hardware:

        print(
            f"  Recomendado: "
            f"{mochi_hardware.get('recommended')}"
        )


    # =====================================================
    # RAM
    # =====================================================

    ram = (
        hardware.get(
            "ram",
            {}
        )
    )


    if ram:

        print("")
        print(
            "RAM:"
        )


        print(
            f"  Detectada: "
            f"{ram.get('total_gb')} GB"
        )


        print(
            f"  Clase instalada: "
            f"{ram.get('installed_class_gb')} GB"
        )


    # =====================================================
    # GPU
    # =====================================================

    gpus = (
        hardware.get(
            "gpu",
            []
        )
    )


    if gpus:

        print("")
        print(
            "GPU:"
        )


        for gpu in gpus:

            print(
                f"  {gpu.get('name', 'Desconocida')}"
            )


            if (
                gpu.get(
                    "vram_gb"
                )
                is not None
            ):

                print(
                    f"    VRAM: "
                    f"{gpu['vram_gb']} GB"
                )


    # =====================================================
    # CHROME
    # =====================================================

    print("")
    print(
        "Chrome:"
    )


    print(
        browser.get(
            "executable"
        )
        or
        "NO ENCONTRADO"
    )


    # =====================================================
    # PERFILES DE CHROME
    # =====================================================

    print("")
    print(
        "Perfiles encontrados:"
    )


    profiles = (
        browser.get(
            "profiles",
            {}
        )
        or {}
    )


    if not profiles:

        print(
            "  No encontré perfiles."
        )


    else:

        for directory, profile in (
            profiles.items()
        ):

            print("")

            print(
                f"  [{directory}]"
            )


            print(
                f"     Nombre: "
                f"{profile.get('name', '')}"
            )


            if profile.get(
                "email"
            ):

                print(
                    f"     Cuenta: "
                    f"{profile['email']}"
                )


    # =====================================================
    # PERFIL DEFAULT
    # =====================================================

    print("")
    print(
        "Perfil predeterminado:"
    )


    print(
        browser.get(
            "default_profile"
        )
    )


    # =====================================================
    # CONFIG PATH
    # =====================================================

    print("")
    print(
        "Configuración guardada en:"
    )


    print(
        CONFIG_PATH
    )


    print("")
    print(
        f"Versión config: "
        f"{config.get('version')}"
    )


    print(
        "======================================"
    )

    print("")


# =========================================================
# EJECUCIÓN DIRECTA
# =========================================================

if __name__ == "__main__":

    print_configuration()
