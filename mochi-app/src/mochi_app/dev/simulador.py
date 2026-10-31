"""
ESP32 simulado: prueba Mochi sin la placa (solo Linux).

Crea un puerto serie falso (pty) que habla como el firmware Mochi 3.5,
abre Mochi apuntando a ese puerto y muestra lo que el ESP32 recibiría
(estados de la cara, texto de la pantalla, órdenes, voz).

    python -m mochi_app.dev.simulador "qué ventanas tengo abiertas"
        aprieta BOOT, dice la frase (Piper), suelta BOOT y espera la respuesta

    python -m mochi_app.dev.simulador "mochi duerme" --orden
        dice la frase sin BOOT: una orden fija por voz (Vosk)

    python -m mochi_app.dev.simulador "mochí, qué hora es" --llamada
        dice la frase sin BOOT: «Mochi» abre la escucha del LLM y espera la
        respuesta (Vosk oye mejor «Mochí» con tilde en la voz de Piper)

    python -m mochi_app.dev.simulador --interactivo
        deja la placa falsa andando para probar la ventana a mano

    --ruido 0.02
        suma ruido de fondo al micrófono (RMS), por ejemplo un ventilador

Lo que va después de "--" se le pasa a Mochi tal cual (por ejemplo --echo).
"""

import argparse
import binascii
import os
import select
import shlex
import subprocess
import sys
import time
import tty

import numpy as np

from mochi_app import paths
from mochi_app.device import protocol
from mochi_app.voice.speech import TextToSpeech


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

    tts = TextToSpeech(paths.PIPER_VOICE)
    pcm = np.frombuffer(tts.synthesize(text), dtype=np.int16).astype(np.float32)

    positions = np.linspace(0, len(pcm) - 1, int(len(pcm) * 16000 / tts.sample_rate))
    pcm = np.interp(positions, np.arange(len(pcm)), pcm).astype(np.int16)

    silence = np.zeros(4000, dtype=np.int16)

    return np.concatenate([silence, pcm, silence]).tobytes()


class FakeEsp32:

    def __init__(self, master, app, noise=0.0):

        self.master = master
        self.app = app
        self.noise = noise
        self.encoded = bytearray()
        self.sequence = 0
        self.streaming_mic = False
        self.speaker_on = False
        self.speech_packets = 0
        self.states = []
        self.commands = []
        self.mic = b""          # audio pendiente de "decir"
        self.next_mic = 0.0


    def _send(self, kind, payload):

        # Si Mochi no lee el puerto, se pierde (como en el USB de verdad).
        try:
            os.write(self.master, protocol.encode(kind, payload, self.sequence))
        except BlockingIOError:
            pass
        self.sequence += 1


    def _status(self, data):

        self._send(protocol.STATUS, data)


    def _show_app_output(self):

        try:
            output = self.app.stdout.read()
        except TypeError:
            return

        for line in (output or "").splitlines():
            if line.strip():
                print(f"  [mochi] {line}")


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
                self.commands.append(command)
                print(f"  <- orden {command}")

        elif kind == b"T":
            print(f"  <- pantalla {payload.decode('ascii', 'replace')!r}")

        elif kind in (b"A", b"B", b"S") and self.speaker_on:
            self.speech_packets += 1


    def pump(self, seconds, until=None):
        """Atiende el puerto hasta until() o hasta el tiempo."""

        end = time.monotonic() + seconds

        while time.monotonic() < end:

            self._show_app_output()

            if self.app.poll() is not None:
                self._show_app_output()
                raise SystemExit("Mochi se cerró.")

            # Micrófono: silencio o la frase, a ritmo real (como el firmware).
            now = time.monotonic()
            while self.streaming_mic and not self.speaker_on and now >= self.next_mic:
                block, self.mic = self.mic[:MIC_BLOCK], self.mic[MIC_BLOCK:]
                block = block.ljust(MIC_BLOCK, b"\x00")
                if self.noise:
                    samples = np.frombuffer(block, dtype="<i2").astype(np.float32)
                    samples += np.random.normal(0.0, self.noise * 32768, len(samples))
                    block = np.clip(samples, -32768, 32767).astype("<i2").tobytes()
                self._send(protocol.PCM, block)
                self.next_mic = max(self.next_mic + 0.008, now - 0.05)

            ready, _, _ = select.select([self.master], [], [], 0.004)

            if ready:
                try:
                    data = os.read(self.master, 65536)
                except OSError:
                    data = b""
                for kind, payload in self._packets(data):
                    self._on_packet(kind, payload)

            if until and until():
                return True

        return False


    def say(self, pcm, button=True):
        """Dice la frase; con button, apretando BOOT mientras tanto."""

        if button:
            self._status(b'{"button":"down"}')
        self.mic = pcm
        self.pump(len(pcm) / 32000 + 0.1, until=lambda: not self.mic)
        if button:
            self._status(b'{"button":"up"}')


def main():

    argv = sys.argv[1:]
    app_arguments = []
    if "--" in argv:
        cut = argv.index("--")
        argv, app_arguments = argv[:cut], argv[cut + 1:]

    parser = argparse.ArgumentParser(description="ESP32 simulado para probar Mochi")
    parser.add_argument("frase", nargs="?", default="")
    parser.add_argument("--orden", action="store_true", help="sin BOOT: orden fija por voz")
    parser.add_argument("--llamada", action="store_true", help="sin BOOT: «Mochi» y la orden para el LLM")
    parser.add_argument("--interactivo", action="store_true", help="dejar la placa falsa andando")
    parser.add_argument("--programa", help="otro programa en vez de Mochi (para comparar)")
    parser.add_argument("--carpeta", help="carpeta donde correr --programa")
    parser.add_argument("--ruido", type=float, default=0.0, help="ruido de fondo del micrófono (RMS)")
    args = parser.parse_args(argv)

    if not args.frase and not args.interactivo:
        raise SystemExit(__doc__)

    pcm = synthesize_16k(args.frase) if args.frase else b""

    master, slave = os.openpty()
    tty.setraw(master)
    tty.setraw(slave)
    os.set_blocking(master, False)
    port = os.ttyname(slave)

    if args.programa:
        command = [*shlex.split(args.programa), "--port", port, *app_arguments]
        cwd = args.carpeta
    else:
        command = [sys.executable, "-m", "mochi_app", "--port", port, *app_arguments]
        cwd = None

    app = subprocess.Popen(
        command,
        cwd=cwd,
        env=dict(os.environ, PYTHONUNBUFFERED="1", PYTHONPATH=str(paths.SRC_DIR)),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    os.set_blocking(app.stdout.fileno(), False)

    esp32 = FakeEsp32(master, app, args.ruido)

    try:
        # Se conecta sola y deja el estado en reposo.
        if not esp32.pump(120, until=lambda: "IDLE" in esp32.states):
            raise SystemExit("Mochi no se conectó a la placa falsa.")

        if args.interactivo:
            print("-> placa falsa andando: cerrá la ventana de Mochi para terminar")
            esp32.pump(10 ** 9)

        esp32.pump(1.5)

        if args.orden:
            print(f"-> digo (sin BOOT): {args.frase!r} ({len(pcm) / 32000:.1f} s)")
            before = len(esp32.commands)
            esp32.say(pcm, button=False)
            if esp32.pump(6, until=lambda: len(esp32.commands) > before):
                print(f"-> orden recibida: {esp32.commands[-1]}")
            else:
                print("-> no llegó ninguna orden")
        elif args.llamada:
            print(f"-> digo (sin BOOT): {args.frase!r} ({len(pcm) / 32000:.1f} s)")
            before = len(esp32.states)
            esp32.say(pcm, button=False)
            # Lo que sigue: silencio (con el ruido mínimo de un micrófono).
            if not esp32.pump(8, until=lambda: "LISTENING" in esp32.states[before:]):
                print(f"-> no abrió la escucha (órdenes: {esp32.commands})")
            else:
                started = time.monotonic()
                esp32.pump(300, until=lambda: esp32.states[-1] in ("DONE", "ERROR", "IDLE"))
                print(f"-> terminó en {esp32.states[-1]} ({time.monotonic() - started:.1f} s)")
        else:
            print(f"-> BOOT + digo: {args.frase!r} ({len(pcm) / 32000:.1f} s)")
            esp32.say(pcm)

            started = time.monotonic()
            esp32.pump(300, until=lambda: esp32.states[-1] in ("DONE", "ERROR"))
            print(f"-> respuesta completa en {time.monotonic() - started:.1f} s")

        esp32.pump(1.0)

    finally:
        app.terminate()
        try:
            app.wait(10)
        except subprocess.TimeoutExpired:
            app.kill()
        esp32._show_app_output()


if __name__ == "__main__":
    main()
