from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


# =========================================================
# RESULTADO DEL BRAIN
# =========================================================

@dataclass
class BrainResult:

    success: bool

    text: str = ""

    raw: Any = None

    tool_calls: list = field(
        default_factory=list
    )

    error: Optional[str] = None


# =========================================================
# CAPACIDADES DEL BRAIN
# =========================================================

@dataclass
class BrainCapabilities:

    tools: bool = False

    vision: bool = False

    streaming: bool = False

    conversations: bool = True


# =========================================================
# INTERFAZ BASE
#
# Cualquier motor que use Mochi deberá respetar
# esta interfaz:
#
# Ollama durante desarrollo
# llama.cpp / GGUF en distribución
# otro runtime futuro
# =========================================================

class BrainProvider(
    ABC
):

    def __init__(
        self,
        model_name=None
    ):

        self.model_name = (
            model_name
        )

        self.loaded = False


    # =====================================================
    # IDENTIFICADOR DEL PROVIDER
    #
    # Ej:
    # ollama
    # llama_cpp
    # embedded
    # =====================================================

    @property
    @abstractmethod
    def provider_name(
        self
    ):

        raise NotImplementedError


    # =====================================================
    # CAPACIDADES
    # =====================================================

    @property
    @abstractmethod
    def capabilities(
        self
    ) -> BrainCapabilities:

        raise NotImplementedError


    # =====================================================
    # COMPROBAR SI EL RUNTIME ESTÁ DISPONIBLE
    # =====================================================

    @abstractmethod
    def is_available(
        self
    ) -> bool:

        raise NotImplementedError


    # =====================================================
    # CARGAR MODELO
    #
    # Algunos runtimes cargan explícitamente.
    # Otros lo hacen al primer prompt.
    # =====================================================

    @abstractmethod
    def load(
        self
    ) -> bool:

        raise NotImplementedError


    # =====================================================
    # DESCARGAR MODELO DE MEMORIA
    #
    # Esto será importante para no tener varios GB
    # ocupados permanentemente.
    # =====================================================

    @abstractmethod
    def unload(
        self
    ) -> bool:

        raise NotImplementedError


    # =====================================================
    # GENERAR RESPUESTA
    #
    # messages:
    #
    # [
    #   {
    #       "role": "system",
    #       "content": "..."
    #   },
    #   {
    #       "role": "user",
    #       "content": "..."
    #   }
    # ]
    #
    # tools:
    # capacidades estructuradas de Mochi.
    # =====================================================

    @abstractmethod
    def generate(
        self,
        messages,
        tools=None,
        options=None
    ) -> BrainResult:

        raise NotImplementedError


    # =====================================================
    # HEALTH CHECK
    # =====================================================

    def health_check(
        self
    ):

        try:

            available = (
                self.is_available()
            )


            return {

                "provider":
                    self.provider_name,

                "model":
                    self.model_name,

                "available":
                    available,

                "loaded":
                    self.loaded,

                "capabilities": {

                    "tools":
                        self.capabilities.tools,

                    "vision":
                        self.capabilities.vision,

                    "streaming":
                        self.capabilities.streaming,

                    "conversations":
                        self.capabilities.conversations
                }
            }


        except Exception as e:

            return {

                "provider":
                    self.provider_name,

                "model":
                    self.model_name,

                "available":
                    False,

                "loaded":
                    False,

                "error":
                    str(e)
            }