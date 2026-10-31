import queue
import unittest
from unittest import mock

import numpy as np

from mochi_app.voice import pc_audio


# =========================================================
# MICRÓFONO FALSO: devuelve bloques de 20 ms de una lista
# =========================================================

class FakeRecorder:

    def __init__(self, levels):
        self.levels = list(levels)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def record(self, numframes):
        level = self.levels.pop(0) if self.levels else 0.0
        return np.full((numframes, 1), level, dtype=np.float32)


class FakeMicrophone:

    name = "falso"

    def __init__(self, levels):
        self.levels = levels

    def recorder(self, **kwargs):
        return FakeRecorder(self.levels)


class FakeAssistant:

    busy = False

    def __init__(self):
        self.submitted = []

    def submit(self, session, pcm, rate):
        self.submitted.append((pcm, rate))


def blocks(seconds, level):
    return [level] * int(seconds / 0.02)


class PcAudioTests(unittest.TestCase):

    def record(self, levels):
        session = pc_audio.PcAudioSession(queue.Queue(), FakeAssistant())
        finish = mock.Mock()
        finish.is_set.return_value = False
        # Reloj falso: cada bloque son 20 ms.
        clock = iter(i * 0.02 for i in range(100000))
        with mock.patch.object(pc_audio, "_open_microphone", return_value=FakeMicrophone(levels)), \
             mock.patch.object(pc_audio.time, "monotonic", lambda: next(clock)):
            pcm, heard = session._record(finish)
        events = []
        while not session.events.empty():
            events.append(session.events.get())
        return pcm, heard, events

    def test_stops_after_silence_following_voice(self):
        pcm, heard, _ = self.record(blocks(1, 0.002) + blocks(1, 0.2) + blocks(3, 0.002))
        self.assertTrue(heard)
        # 1 s de silencio + 1 s de voz + el silencio final.
        self.assertAlmostEqual(len(pcm) / 2 / 16000, 2 + pc_audio.END_SILENCE_SECONDS, delta=0.1)

    def test_gives_up_without_voice(self):
        _, heard, _ = self.record(blocks(10, 0.002))
        self.assertFalse(heard)

    def test_threshold_follows_background_noise(self):
        # Ruido de 0,02 (más que VOICE_RMS): no es voz; 0,2 sí.
        _, heard, _ = self.record(blocks(1, 0.02) + blocks(6, 0.03))
        self.assertFalse(heard)
        _, heard, _ = self.record(blocks(1, 0.02) + blocks(1, 0.2) + blocks(2, 0.02))
        self.assertTrue(heard)

    def test_far_sounds_do_not_keep_listening(self):
        # Voz de cerca (0,2) y después gente hablando lejos: ruido de fondo
        # bajo con picos de 0,03. Con sensibilidad normal termina sola.
        far = [0.03 if i % 3 else 0.004 for i in range(int(10 / 0.02))]
        pcm, heard, _ = self.record(blocks(1, 0.002) + blocks(1, 0.2) + far)
        self.assertTrue(heard)
        self.assertLess(len(pcm) / 2 / 16000, 3.0)

    def test_warns_when_microphone_is_saturated(self):
        _, _, events = self.record(blocks(7, 0.5))
        notices = [value for kind, value in events if kind == "chat"]
        self.assertEqual(len(notices), 1)
        self.assertIn("saturado", notices[0][1])


if __name__ == "__main__":
    unittest.main()
