"""Escucha siempre activa: «Mochi» abre la escucha de la orden para el LLM."""

import queue
import threading
import unittest
from unittest import mock

import numpy as np

from mochi_app import paths
from mochi_app.device import commands
from mochi_app.device.protocol import LLM_IDLE
from mochi_app.voice import pc_audio


def tone(seconds, level):
    """PCM s16le de una onda cuadrada de ese RMS."""

    samples = np.full(int(seconds * 16000), level * 32767, dtype=np.float32)
    samples[1::2] *= -1
    return samples.astype("<i2").tobytes()


# =========================================================
# DETECTOR (device/commands.py)
# =========================================================

class WakeHelpersTests(unittest.TestCase):

    def test_decision_counts_call_words(self):
        router = commands.CommandRouter()
        self.assertEqual(router.process("mochi").wake_words, 1)
        self.assertEqual(router.process("hola mochi feliz").wake_words, 2)
        self.assertEqual(router.process("feliz", now=10 ** 6).wake_words, 0)

    def test_wake_call_end_and_confidence(self):
        decision = commands.CommandRouter().process("hola mochi")
        result = {"result": [
            {"word": "hola", "start": 0.2, "end": 0.4, "conf": 1.0},
            {"word": "mochi", "start": 0.4, "end": 0.8, "conf": 0.6},
        ]}
        end, confidence = commands.wake_call(result, decision)
        self.assertEqual(end, 0.8)
        self.assertAlmostEqual(confidence, 0.8)
        self.assertEqual(commands.wake_call(result, commands.Decision()), (None, 0.0))

    def test_tail_keeps_positions_after_trimming(self):
        tail = commands.AudioTail(seconds=1.0)
        for second in range(3):
            tail.extend(bytes([second + 1]) * 32000)
        # Guarda solo el último segundo, pero ubicado desde el principio.
        self.assertEqual(len(tail.since(0)), 32000)
        self.assertEqual(tail.since(2.5), bytes([3]) * 16000)
        tail.reset()
        self.assertEqual(tail.since(0), b"")

    def test_voice_in(self):
        heard, quiet = commands.voice_in(tone(0.5, 0.2) + tone(0.4, 0.0), 0.012)
        self.assertTrue(heard)
        self.assertAlmostEqual(quiet, 0.4, delta=0.03)
        self.assertEqual(commands.voice_in(tone(1, 0.001), 0.012)[0], False)

    def test_fixed_command_with_or_without_call(self):
        self.assertEqual(commands.fixed_command("duerme")[0], "D")
        self.assertEqual(commands.fixed_command("mochi feliz por favor")[0], "F")
        self.assertIsNone(commands.fixed_command("qué hora es"))

    def test_llm_words_keep_questions_out_of_fixed_commands(self):
        from mochi_app.dev.simulador import synthesize_16k
        from vosk import KaldiRecognizer, Model, SetLogLevel
        import json

        SetLogLevel(-1)
        model = Model(str(paths.VOSK_MODEL))
        grammar = commands.recognition_grammar(model, commands.DEFAULT_WAKE, commands.DEFAULT_WAKE_PHRASES, llm_words=True)

        def heard(phrase):
            recognizer = KaldiRecognizer(model, 16000, json.dumps(grammar, ensure_ascii=False))
            recognizer.AcceptWaveform(synthesize_16k(phrase) + bytes(32000))
            return commands.CommandRouter().process(json.loads(recognizer.FinalResult())["text"])

        # Sin las palabras para el LLM, salía «mochi duerme».
        question = heard("Mochí, sube el volumen")
        self.assertTrue(question.awakened)
        self.assertIsNone(question.command)


# =========================================================
# SIN PLACA (voice/pc_audio.py)
# =========================================================

class FakeRecorder:
    """Devuelve el PCM de a bloques; el reloj avanza 20 ms por bloque."""

    def __init__(self, pcm, on_end=None):
        self.samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        self.position = 0
        self.on_end = on_end

    @property
    def now(self):
        return self.position / 16000

    def record(self, numframes):
        block = self.samples[self.position:self.position + numframes]
        self.position += numframes
        if len(block) < numframes:
            if self.on_end:
                self.on_end()
            block = np.zeros(numframes, dtype=np.float32)
        return block.reshape(-1, 1)


class FakeAssistant:

    busy = False

    def __init__(self, on_submit=None):
        self.submitted = []
        self.on_submit = on_submit

    def submit(self, session, pcm, rate):
        self.submitted.append(pcm)
        if self.on_submit:
            self.on_submit()


class PcWakeTests(unittest.TestCase):

    def test_record_uses_audio_said_after_the_call(self):
        session = pc_audio.PcAudioSession(queue.Queue(), FakeAssistant())
        recorder = FakeRecorder(tone(3, 0.0))
        finish = threading.Event()
        pre = tone(0.8, 0.2) + tone(1.3, 0.0)

        with mock.patch.object(pc_audio.time, "monotonic", lambda: recorder.now):
            pcm, heard = session._record(finish, recorder, pre, noise=0.001)

        # Ya terminó de hablar antes de la llamada: no espera otro silencio.
        self.assertTrue(heard)
        self.assertTrue(pcm.startswith(pre))
        self.assertLess(len(pcm) - len(pre), 3 * 640)

    def test_call_and_question_reach_the_assistant(self):
        from mochi_app.dev.simulador import synthesize_16k

        stop = threading.Event()
        assistant = FakeAssistant(on_submit=stop.set)
        session = pc_audio.PcAudioSession(queue.Queue(), assistant)

        from vosk import KaldiRecognizer, Model, SetLogLevel
        SetLogLevel(-1)
        model = Model(str(paths.VOSK_MODEL))
        grammar = commands.recognition_grammar(model, commands.DEFAULT_WAKE, commands.DEFAULT_WAKE_PHRASES)
        import json
        recognizer = KaldiRecognizer(model, 16000, json.dumps(grammar, ensure_ascii=False))
        recognizer.SetWords(True)

        speech = synthesize_16k("Mochí, ¿qué hora es?")
        recorder = FakeRecorder(tone(1, 0.0) + speech + tone(4, 0.0), on_end=stop.set)

        # La voz de Piper cambia en cada corrida y a veces «Mochi» sale con
        # poca confianza: acá se prueba el camino, no el umbral.
        with mock.patch.object(pc_audio.time, "monotonic", lambda: recorder.now), \
             mock.patch.object(pc_audio, "WAKE_LLM_CONFIDENCE", 0.0):
            session._wake_listen(stop, recorder, recognizer, commands.CommandRouter(), lambda: False)

        self.assertEqual(len(assistant.submitted), 1)

        # Llega lo dicho después de «Mochi», no la llamada.
        free = KaldiRecognizer(model, 16000)
        free.AcceptWaveform(assistant.submitted[0])
        text = json.loads(free.FinalResult())["text"]
        self.assertTrue(text)
        self.assertNotIn("mochi", text)
        # Desde el final de «Mochi» (~0,3 s después del comienzo de la frase).
        said = len(speech) - 8000
        self.assertLess(len(assistant.submitted[0]) - pc_audio.END_SILENCE_SECONDS * 32000, said - 0.2 * 32000)

    def test_ready_text_mentions_the_call_only_when_listening(self):
        session = pc_audio.PcAudioSession(queue.Queue(), FakeAssistant())
        self.assertNotIn("«Mochi»", session.ready_text.replace("Hablarle a Mochi", ""))
        with mock.patch.object(pc_audio.PcAudioSession, "wake_active", True):
            self.assertIn("decí «Mochi»", session.ready_text)


# =========================================================
# ASISTENTE: «Mochi» ... «duerme» en dos veces
# =========================================================

class FixedCommandAfterCallTests(unittest.TestCase):

    def test_two_step_fixed_command_skips_the_llm(self):
        from mochi_app.assistant import Assistant

        assistant = Assistant(queue.Queue())
        assistant.stt = mock.Mock()
        assistant.stt.transcribe.return_value = "duerme"
        assistant.execute = mock.Mock()

        session = mock.Mock()
        session.fixed_command.return_value = True

        assistant._handle_utterance(session, b"\x00" * 32000, 16000)

        session.fixed_command.assert_called_once_with("duerme")
        assistant.execute.assert_not_called()
        session.show_state.assert_called_with(LLM_IDLE)


if __name__ == "__main__":
    unittest.main()
