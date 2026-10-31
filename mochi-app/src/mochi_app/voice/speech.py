"""
Voz a texto (Vosk) y texto a voz (Piper), ambos locales.

Modelos (descargar una vez):

  Vosk:  https://alphacephei.com/vosk/models
         vosk-model-small-es-0.42  -> descomprimir en models/

  Piper: https://huggingface.co/rhasspy/piper-voices
         es/es_AR/daniela/high/es_AR-daniela-high.onnx (+ .onnx.json)
         -> models/
"""

import json
import re
import unicodedata
from pathlib import Path


# =========================================================
# VOZ A TEXTO
# =========================================================

class SpeechToText:

    def __init__(self, model_path):

        from vosk import KaldiRecognizer, Model, SetLogLevel

        SetLogLevel(-1)

        if not Path(model_path).exists():
            raise FileNotFoundError(
                f"No encuentro el modelo de Vosk en {model_path}"
            )

        self._model = Model(str(model_path))
        self._recognizer_class = KaldiRecognizer


    def transcribe(self, pcm, sample_rate=16000):

        recognizer = self._recognizer_class(self._model, sample_rate)
        recognizer.AcceptWaveform(pcm)
        result = json.loads(recognizer.FinalResult())

        return (result.get("text") or "").strip()


# =========================================================
# VOZ A TEXTO: WHISPER (alternativa a Vosk)
#
# Más preciso que vosk-model-small, pero más lento: con "small"
# tarda ~2 s por frase en la i3 de la notebook. El modelo se
# descarga solo la primera vez (pip install faster-whisper).
# =========================================================

# Palabras que se usan seguido con Mochi: ayudan a Whisper a
# escribirlas bien.
WHISPER_VOCABULARY = (
    "Mochi, abrí, cerrá, minimizá, maximizá, pausá, volumen, ventana, "
    "Firefox, Chrome, YouTube, Netflix, Spotify, carpeta, descargas."
)


class WhisperSpeechToText:

    def __init__(self, model_size="small"):

        from faster_whisper import WhisperModel

        self._model = WhisperModel(
            model_size,
            device="cpu",
            compute_type="int8",
        )


    def transcribe(self, pcm, sample_rate=16000):

        import numpy as np

        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768

        if sample_rate != 16000:
            positions = np.linspace(0, len(audio) - 1, int(len(audio) * 16000 / sample_rate))
            audio = np.interp(positions, np.arange(len(audio)), audio).astype(np.float32)

        segments, _info = self._model.transcribe(
            audio,
            language="es",
            beam_size=1,
            initial_prompt=WHISPER_VOCABULARY,
            condition_on_previous_text=False,
        )

        return " ".join(segment.text.strip() for segment in segments).strip()


# =========================================================
# TEXTO A VOZ
# =========================================================

class TextToSpeech:

    def __init__(self, voice_path):

        from piper import PiperVoice

        if not Path(voice_path).exists():
            raise FileNotFoundError(
                f"No encuentro la voz de Piper en {voice_path}"
            )

        self._voice = PiperVoice.load(str(voice_path))
        self.sample_rate = self._voice.config.sample_rate


    def synthesize(self, text):
        """Devuelve PCM 16 bits mono a self.sample_rate."""

        return b"".join(
            chunk.audio_int16_bytes
            for chunk in self._voice.synthesize(text)
        )


# =========================================================
# TEXTO PARA HABLAR
#
# El LLM a veces contesta con Markdown, listas o PID. Piper los
# leería tal cual ("asterisco asterisco..."), y una respuesta larga
# tarda decenas de segundos en decirse.
# =========================================================

MAX_SPEECH_CHARS = 280


def to_speech_text(text, limit=MAX_SPEECH_CHARS):

    text = str(text or "")

    # Detalles técnicos entre paréntesis: (PID: 1234), (ID 5)...
    text = re.sub(r"\((?:PID|pid|ID|id)\b[^)]*\)", "", text)

    # Markdown: negrita, cursiva, código, títulos, citas. El "_"
    # solo como énfasis: dentro de una palabra (media_controller) queda.
    text = re.sub(r"[*`]+", "", text)
    text = re.sub(r"(?<!\w)_+|_+(?!\w)", "", text)
    text = re.sub(r"(?m)^\s*[#>]+\s*", "", text)

    # Viñetas y numeración al principio de cada línea.
    text = re.sub(r"(?m)^\s*(?:[-•]|\d+[.)])\s+", "", text)

    # Cada línea es una frase: termina con punto si no tenía.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    text = " ".join(
        line if line[-1] in ".!?:;," else f"{line}."
        for line in lines
    )
    text = " ".join(text.split())

    if len(text) <= limit:
        return text

    # Corta en la última oración completa que entra.
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))

    if end > limit // 3:
        return cut[:end + 1]

    return cut.rsplit(" ", 1)[0] + "..."


# =========================================================
# TEXTO PARA LA PANTALLA
#
# La fuente del ESP32 es ASCII: se sacan tildes y eñes.
# =========================================================

def to_display_text(text, limit=200):

    normalized = unicodedata.normalize("NFKD", str(text or ""))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_text = " ".join(ascii_text.split())

    return ascii_text[:limit]
