from config_manager import (
    get_config
)

from brain.ollama_provider import (
    OllamaProvider
)

from brain.llama_cpp_provider import (
    LlamaCppProvider
)


# =========================================================
# CREAR EL BRAIN SEGÚN LA CONFIGURACIÓN DE ESTA PC
# =========================================================

def create_brain_provider():

    config = (
        get_config()
    )

    brain_config = (
        config.get(
            "brain",
            {}
        )
    )


    # =====================================================
    # BRAIN DESACTIVADO
    # =====================================================

    if not brain_config.get(
        "enabled",
        False
    ):

        print(
            "Mochi Brain está desactivado."
        )

        return None


    # =====================================================
    # DATOS DEL BRAIN
    # =====================================================

    runtime = (
        brain_config.get(
            "runtime"
        )
    )

    model = (
        brain_config.get(
            "model"
        )
    )


    # =====================================================
    # VALIDAR MODELO
    # =====================================================

    if not model:

        print(
            "No hay ningún modelo configurado."
        )

        return None


    # =====================================================
    # OLLAMA
    # =====================================================

    if runtime == "ollama":

        provider = (
            OllamaProvider(
                model_name=model
            )
        )


        return provider


    # =====================================================
    # LLAMA.CPP (llama-server en la PC con la RX 580)
    # =====================================================

    if runtime == "llama_cpp":

        provider = (
            LlamaCppProvider(

                model_name=model,

                base_url=(
                    brain_config.get(
                        "base_url"
                    )
                    or "http://localhost:8080"
                ),

                allow_thinking=(
                    brain_config.get(
                        "thinking_enabled",
                        False
                    )
                ),

                api_key=(
                    brain_config.get(
                        "api_key"
                    )
                )
            )
        )


        return provider


    # =====================================================
    # FUTUROS RUNTIMES
    #
    # Después podremos agregar:
    #
    # embedded
    # etc.
    # =====================================================

    print(
        f"Runtime de Brain no soportado: {runtime}"
    )

    return None


# =========================================================
# MOSTRAR ESTADO DEL BRAIN
# =========================================================

def print_brain_status():

    config = (
        get_config()
    )


    brain_config = (
        config.get(
            "brain",
            {}
        )
    )


    print("")
    print(
        "======================================"
    )
    print(
        "          MOCHI BRAIN STATUS"
    )
    print(
        "======================================"
    )


    print(
        f"Activado: "
        f"{brain_config.get('enabled')}"
    )


    print(
        f"Runtime: "
        f"{brain_config.get('runtime')}"
    )


    print(
        f"Modelo: "
        f"{brain_config.get('model')}"
    )


    print(
        f"Perfil: "
        f"{brain_config.get('model_profile')}"
    )


    print(
        f"Contexto: "
        f"{brain_config.get('context_size')}"
    )


    print(
        f"Visión: "
        f"{brain_config.get('vision_enabled')}"
    )


    provider = (
        create_brain_provider()
    )


    if provider is None:

        print("")
        print(
            "Provider: NO DISPONIBLE"
        )

        print(
            "======================================"
        )

        return


    print("")
    print(
        f"Provider: "
        f"{provider.provider_name}"
    )


    print(
        f"Disponible: "
        f"{provider.is_available()}"
    )


    print(
        f"Modelo instalado: "
        f"{provider.model_exists()}"
        if hasattr(
            provider,
            "model_exists"
        )
        else
        "Modelo instalado: desconocido"
    )


    print(
        "======================================"
    )


# =========================================================
# PRUEBA DIRECTA
# =========================================================

if __name__ == "__main__":

    print_brain_status()