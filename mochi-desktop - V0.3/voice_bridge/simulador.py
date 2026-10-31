"""
ESP32 simulado: prueba el puente sin la placa.

Crea un puerto serie falso (pty) que habla como el firmware Mochi 3.5,
arranca el puente (con su ventana) apuntando a ese puerto, "aprieta"
BOOT, "dice" una frase sintetizada con Piper, suelta BOOT y muestra lo
que el puente le devuelve al ESP32 (estados, texto, voz).

Solo Linux (usa os.openpty). Desde la carpeta de mochi-desktop:

    python -m voice_bridge.simulador "qué ventanas tengo abiertas"
    python -m voice_bridge.simulador "hola" --echo
    python -m voice_bridge.simulador "hola" --echo --stt whisper

Lo que va después de la frase se le pasa al puente tal cual.
"""

import binascii
import os
import select
import subprocess
import sys
import time
import tty
from pathlib import Path

import numpy as np

from voice_bridge.esp32_bridge import DEFAULT_PIPER_VOICE
from voice_bridge.mochi_pc import protocol
from voice_bridge.speech import TextToSpeech


MOCHI_DIR = Path(__file__).resolve().parent.parent

STATES = ["IDLE", "LISTENING", "THINKING", "SPEAKING", "DONE", "ERROR"]

# Lo que contesta el firmware 3.5 a "?".
HELLO = (
    b'{"device":"Mochi","firmware":"3.5","llm":1,"protocol":1,"pc_rx_protocol":2,'
    b'"sample_rate":16000,"pcm":"s16le","mic":true,"amp":true,"screen":true,'
    b'"stream":false,"dropped":0,"record_seconds":6.00}'
)

# El firmware manda el micrófono en bloques de 128 muestras (8 ms).
MIC_BLOCK = 256


def synthesize_16k(text):
    """Frase de Piper pasada a PCM 16 kHz, con silencio alrededor."""

    tts = TextToSpeech(DEFAULT_PIPER_VOICE)
    pcm = np.frombuffer(tts.synthesize(text), dtype=np.int16).astype(np.float32)

    positions = np.linspace(0, len(pcm) - 1, int(len(pcm) * 16000 / tts.sample_rate))
    pcm = np.interp(positions, np.arange(len(pcm)), pcm).astype(np.int16)

    silence = np.zeros(4000, dtype=np.int16)

    return np.concatenate([silence, pcm, silence]).tobytes()


class FakeEsp32:

    def __init__(self, master, bridge):

        self.master = master
        self.bridge = bridge
        self.encoded = bytearray()
        self.sequence = 0
        self.streaming_mic = False
        self.speaker_on = False
        self.speech_packets = 0
        self.states = []
        self.mic = b""          # audio pendiente de "decir"
        self.next_mic = 0.0


    def _send(self, kind, payload):

        os.write(self.master, protocol.encode(kind, payload, self.sequence))
        self.sequence += 1


    def _status(self, data):

        self._send(protocol.STATUS, data)


    def _show_bridge_output(self):

        try:
            output = self.bridge.stdout.read()
        except TypeError:
            return

        for line in (output or "").splitlines():
            if line.strip():
                print(f"  [puente] {line}")


    # =====================================================
    # PC -> ESP32: COBS([A5 5A | tipo | largo | datos | CRC16]) + 00
    # =====================================================

    @staticmethod
    def _cobs_decode(data):

        out = bytearray()
        i = 0
        while i < len(data):
            code = data[i]
            if code == 0:
                return None
            out += data[i + 1:i + code]
            i += code
            if code < 0xFF and i < len(data):
                out.append(0)
        return bytes(out)


    def _packets(self, data):

        for byte in data:
            if byte:
                self.encoded.append(byte)
                continue
            raw = self._cobs_decode(bytes(self.encoded))
            self.encoded.clear()
            if not raw or len(raw) < 7 or raw[:2] != b"\xA5\x5A":
                continue
            kind = raw[2:3]
            length = int.from_bytes(raw[3:5], "little")
            payload = raw[5:5 + length]
            crc = int.from_bytes(raw[5 + length:7 + length], "little")
            if crc != binascii.crc_hqx(raw[2:5 + length], 0xFFFF):
                print("  !! CRC incorrecto")
                continue
            yield kind, payload


    def _on_packet(self, kind, payload):

        if kind == b"C":
            command = payload.decode("ascii")
            if command == "?":
                self._send(protocol.HELLO, HELLO)
            elif command == "L":
                self.streaming_mic = True
            elif command == "Q":
                self.streaming_mic = False
            elif command == "K":
                self.speaker_on = True
                self._status(b'{"audio":"streaming"}')
            elif command == "J":
                self.speaker_on = False
                self._status(b'{"audio":"idle"}')
                if self.speech_packets:
                    print(f"  <- voz {self.speech_packets * 0.02:.1f} s")
                    self.speech_packets = 0
            elif command in "012345":
                state = STATES[int(command)]
                self.states.append(state)
                print(f"  <- estado {state}")
            elif command == "#":
                # Como el firmware: sin micrófono, el latido se contesta igual.
                if self.speaker_on:
                    self._status(b'{"audio":"streaming"}')
            else:
                print(f"  <- orden {command}")

        elif kind == b"T":
            print(f"  <- pantalla {payload.decode('ascii', 'replace')!r}")

        elif kind in (b"A", b"B", b"S") and self.speaker_on:
            self.speech_packets += 1


    def pump(self, seconds, until=None):
        """Atiende el puerto hasta until() o hasta el tiempo."""

        end = time.monotonic() + seconds

        while time.monotonic() < end:

            self._show_bridge_output()

            if self.bridge.poll() is not None:
                self._show_bridge_output()
                raise SystemExit("El puente terminó.")

            # Micrófono: silencio o la frase, a ritmo real (como el firmware).
            now = time.monotonic()
            while self.streaming_mic and not self.speaker_on and now >= self.next_mic:
                block, self.mic = self.mic[:MIC_BLOCK], self.mic[MIC_BLOCK:]
                self._send(protocol.PCM, block.ljust(MIC_BLOCK, b"\x00"))
                self.next_mic = max(self.next_mic + 0.008, now - 0.05)

            ready, _, _ = select.select([self.master], [], [], 0.004)

            if ready:
                for kind, payload in self._packets(os.read(self.master, 65536)):
                    self._on_packet(kind, payload)

            if until and until():
                return True

        return False


    def say(self, pcm):
        """Aprieta BOOT, dice la frase y suelta BOOT."""

        self._status(b'{"button":"down"}')
        self.mic = pcm
        self.pump(len(pcm) / 32000 + 0.1, until=lambda: not self.mic)
        self._status(b'{"button":"up"}')


def main():

    if len(sys.argv) < 2:
        raise SystemExit(__doc__)

    phrase = sys.argv[1]
    bridge_arguments = sys.argv[2:]

    pcm = synthesize_16k(phrase)

    master, slave = os.openpty()
    tty.setraw(master)
    tty.setraw(slave)

    bridge = subprocess.Popen(
        [sys.executable, "-m", "voice_bridge.esp32_bridge", "--port", os.ttyname(slave), *bridge_arguments],
        cwd=MOCHI_DIR,
        env=dict(os.environ, PYTHONUNBUFFERED="1"),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    os.set_blocking(bridge.stdout.fileno(), False)

    esp32 = FakeEsp32(master, bridge)

    try:
        # La ventana se conecta sola y deja el estado en reposo.
        if not esp32.pump(120, until=lambda: "IDLE" in esp32.states):
            raise SystemExit("El puente no arrancó.")

        esp32.pump(1.0)
        print(f"-> BOOT + digo: {phrase!r} ({len(pcm) / 32000:.1f} s)")
        esp32.say(pcm)

        started = time.monotonic()
        esp32.pump(300, until=lambda: esp32.states[-1] in ("DONE", "ERROR"))
        print(f"-> respuesta completa en {time.monotonic() - started:.1f} s")

        esp32.pump(1.0)

    finally:
        bridge.terminate()


if __name__ == "__main__":
    main()
