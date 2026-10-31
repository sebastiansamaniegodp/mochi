"""Envia audio real de SALIDA del sistema, nunca el microfono.
Windows: WASAPI loopback. Linux: monitor de PulseAudio/PipeWire (SoundCard).
Mantiene un unico propietario del COM en app.py. Incluye tono de autodiagnostico.
Mono (paquete B) o estereo L/R (paquete S, firmware 3.4+, dos MAX98357A).
"""
import os
import queue
import binascii
import threading
import time

from audio_dsp import AudioControls, AudioProcessor, FIRDownsampleFilter

SAMPLE_RATE=16000
FRAME_SAMPLES=320

IS_WINDOWS=os.name=='nt'
SISTEMA='Windows' if IS_WINDOWS else 'Linux'


def _cobs_encode(raw):
    encoded=bytearray([0])
    code_position=0
    code=1
    for b in raw:
        if b==0:
            encoded[code_position]=code
            code_position=len(encoded)
            encoded.append(0)
            code=1
        else:
            encoded.append(b)
            code+=1
            if code==0xFF:
                encoded[code_position]=code
                code_position=len(encoded)
                encoded.append(0)
                code=1
    encoded[code_position]=code
    encoded.append(0)
    return bytes(encoded)


def frame_packet(kind,payload):
    if kind not in (b'C',b'A',b'B',b'S'):
        raise ValueError('Tipo USB invalido')
    expected={b'C':1,b'A':640,b'B':480,b'S':960}[kind]
    if len(payload)!=expected:
        raise ValueError('Longitud USB invalida')
    contents=kind+len(payload).to_bytes(2,'little')+payload
    crc=binascii.crc_hqx(contents,0xFFFF)
    return _cobs_encode(b'\xA5\x5A'+contents+crc.to_bytes(2,'little'))


def frame_command(value):
    if isinstance(value,str): value=value.encode('ascii')
    return frame_packet(b'C',value)


# Generator persistente: ruido triangular de muy baja amplitud (TPDF) antes
# de reducir a 12 bits. Reduce la distorsion por cuantizacion de PCM12 en
# pasajes suaves; NO aumenta los 480 bytes ni los 50 paquetes por segundo.
_PCM12_RNG=None

def pack_pcm12(pcm):
    """PCM16 -> PCM12 empaquetado, con redondeo y dither TPDF solo si hay audio.

    Mono: 320 muestras (640 bytes) -> 480 bytes.
    Estereo: 320 cuadros L,R intercalados (1280 bytes) -> 960 bytes.
    """
    import numpy as np
    global _PCM12_RNG
    if len(pcm) not in (640,1280):
        raise ValueError('PCM16 debe tener 320 cuadros mono o estereo')
    signed=np.frombuffer(pcm,dtype='<i2').astype(np.int32)
    count=len(signed)
    if np.any(signed):
        if _PCM12_RNG is None: _PCM12_RNG=np.random.default_rng()
        # 1 LSB PCM12 = 16 unidades PCM16. Se preservan los silencios exactos.
        random_dither=_PCM12_RNG.random(count)-_PCM12_RNG.random(count)
        quantized=np.clip(np.rint(signed/16.0+random_dither),-2048,2047).astype(np.int32)
    else:
        quantized=np.zeros(count,dtype=np.int32)
    packed=quantized & 0x0FFF
    out=np.empty(count*3//2,dtype=np.uint8)
    out[0::3]=(packed[0::2]&0xFF).astype(np.uint8)
    out[1::3]=(((packed[0::2]>>8)&0x0F)|((packed[1::2]&0x0F)<<4)).astype(np.uint8)
    out[2::3]=((packed[1::2]>>4)&0xFF).astype(np.uint8)
    return out.tobytes()


def frame_audio(pcm):
    return frame_packet(b'B',pack_pcm12(pcm))


def frame_audio_stereo(left,right):
    """Dos bloques PCM16 de 320 muestras -> paquete S (L,R intercalados)."""
    import numpy as np
    pair=np.empty(640,dtype='<i2')
    pair[0::2]=np.frombuffer(left,dtype='<i2')
    pair[1::2]=np.frombuffer(right,dtype='<i2')
    return frame_packet(b'S',pack_pcm12(pair.tobytes()))


def split_channels(values):
    """Matriz (cuadros, canales) -> (izquierdo, derecho).

    Estereo: canales 0 y 1. En 5.1 / 7.1 el centro (2) va a ambos lados, los
    laterales/traseros pares a la izquierda y los impares a la derecha; el LFE
    (3) se omite (el parlante pequeno no lo reproduce).
    """
    channels=values.shape[1]
    if channels==1:
        return values[:,0],values[:,0]
    left=[0]; right=[1]
    if channels>=3:
        left.append(2); right.append(2)
    for index in range(4,channels):
        (left if index%2==0 else right).append(index)
    return values[:,left].mean(axis=1),values[:,right].mean(axis=1)


def audio_outputs():
    """Enumera ambas rutas, priorizando WASAPI WPatch para HDMI.

    Los IDs prefijados evitan confundir los indices WASAPI de PortAudio
    con los identificadores de SoundCard (son espacios de nombres distintos).
    """
    items = []
    default_id = None
    problems = []
    # PyAudioWPatch (WASAPI) solo existe en Windows.
    if IS_WINDOWS:
        try:
            import pyaudiowpatch as pa
            with pa.PyAudio() as manager:
                for device in manager.get_loopback_device_info_generator():
                    idx = int(device['index'])
                    items.append(('pawp:'+str(idx), device['name']+'  (WASAPI alternativo)'))
                try:
                    default = manager.get_default_wasapi_loopback()
                    default_id = 'pawp:'+str(int(default['index']))
                except (OSError, LookupError, KeyError):
                    pass
        except (ImportError, OSError, RuntimeError) as exc:
            problems.append('PyAudioWPatch: '+str(exc))
    try:
        import soundcard as sc
        old_default = sc.default_speaker()
        sufijo = '  (SoundCard original)' if IS_WINDOWS else '  (monitor PulseAudio/PipeWire)'
        for output in sc.all_speakers():
            items.append(('sc:'+output.id, output.name+sufijo))
        if default_id is None and old_default is not None:
            default_id = 'sc:'+old_default.id
    except (ImportError, OSError, RuntimeError) as exc:
        problems.append('SoundCard: '+str(exc))
    if not items:
        raise RuntimeError('No se detectaron salidas de '+SISTEMA+'. '+' / '.join(problems))
    return items, default_id


class PCSpeaker:
    def __init__(self,packets,events,device_id=None,mode='windows',controls=None):
        self.packets=packets
        self.events=events
        self.device_id=device_id
        self.mode=mode
        self.controls=controls or AudioControls()
        self.stop=threading.Event()
        self.thread=threading.Thread(target=self._worker,daemon=True)
        self.failed=None
        self.sent=0
        self.queue_overflows=0
        self.last_queue_warning=0.0
        self.finished_at=None
        # Un procesador por canal (estado de filtros independiente) y otro
        # para mono. El modo se lee en cada bloque: se puede cambiar en vivo.
        self.processor_left=AudioProcessor(self.controls)
        self.processor_right=AudioProcessor(self.controls)
        self.processor_mono=AudioProcessor(self.controls)

    def start(self): self.thread.start()
    def close(self): self.stop.set()
    def alive(self): return self.thread.is_alive()

    def _put(self,packet):
        try: self.packets.put_nowait(packet)
        except queue.Full:
            self.queue_overflows+=1
            now=time.monotonic()
            if now-self.last_queue_warning>5.0:
                self.last_queue_warning=now
                self.events.put(('log',f'AVISO PC: cola de envio llena, descartados={self.queue_overflows}. Comprueba uso de CPU/USB.'))
            # Si la cola se llena, descartamos el bloque mas antiguo COMPLETO.
            try: self.packets.get_nowait()
            except queue.Empty: pass
            try: self.packets.put_nowait(packet)
            except queue.Full: return
        self.sent+=1

    def _send_block(self,left,right):
        """Procesa 320 muestras por canal y encola un paquete S (estereo) o B (mono)."""
        if self.controls.stereo():
            self._put(frame_audio_stereo(self.processor_left.to_pcm16(left),
                                         self.processor_right.to_pcm16(right)))
        else:
            self._put(frame_audio(self.processor_mono.to_pcm16((left+right)*0.5)))

    def _tone(self):
        import numpy as np
        stereo=self.controls.stereo()
        if stereo:
            self.events.put(('log','PRUEBA USB ESTEREO: 1 s de 880 Hz en el parlante IZQUIERDO y luego 1 s de 660 Hz en el DERECHO.'))
        else:
            self.events.put(('log','PRUEBA USB: enviando tono de 880 Hz durante 2 segundos (sin microfono ni WASAPI).'))
        silence=np.zeros(FRAME_SAMPLES,dtype='<i2').tobytes()
        for i in range(100):
            if self.stop.is_set(): break
            t=(np.arange(FRAME_SAMPLES,dtype=np.float64)+i*FRAME_SAMPLES)/SAMPLE_RATE
            if stereo:
                # Rampa en cada mitad: el cambio de lado tampoco debe hacer clic.
                half=i%50
                env=min(1.0,(half+1)/6.0,(50-half)/6.0)
                freq=880.0 if i<50 else 660.0
                pcm=(np.sin(t*2*np.pi*freq)*0.24*env*32767).astype('<i2').tobytes()
                left,right=(pcm,silence) if i<50 else (silence,pcm)
                self._put(frame_audio_stereo(left,right))
            else:
                # Rampa de entrada/salida para evitar clics al arrancar y al terminar.
                env=min(1.0,(i+1)/6.0,(100-i)/6.0)
                pcm=(np.sin(t*2*np.pi*880.0)*0.24*env*32767).astype('<i2')
                self._put(frame_audio(pcm.tobytes()))
            self.stop.wait(0.020)
        self.events.put(('log','PRUEBA USB: tono enviado. Si no se escucho, el problema esta en USB/firmware/I2S.'))

    def _windows(self):
        # En Linux no hay WASAPI: siempre se usa SoundCard (monitor de la salida).
        if not IS_WINDOWS or (self.device_id and self.device_id.startswith('sc:')):
            self._windows_soundcard()
        else:
            self._windows_wpatch()

    def _windows_wpatch(self):
        """Lee la mezcla que sale por HDMI/auriculares, NO del microfono.

        La salida tiene una frecuencia nativa variable (por ejemplo 48000),
        que convertimos de forma continua a PCM16 de 16 kHz (mono o L/R) para Mochi.
        """
        import numpy as np
        import pyaudiowpatch as pa

        with pa.PyAudio() as manager:
            if self.device_id and self.device_id.startswith('pawp:'):
                info=manager.get_device_info_by_index(int(self.device_id.split(':',1)[1]))
            else:
                info=manager.get_default_wasapi_loopback()
            if not info.get('isLoopbackDevice',False):
                raise RuntimeError('Selecciona una salida (WASAPI alternativo) con loopback, no una entrada de microfono.')
            channels=int(info['maxInputChannels'])
            rate=int(info['defaultSampleRate'])
            if channels<1 or rate<8000:
                raise RuntimeError('Formato WASAPI invalido: '+str(info))
            self.events.put(('log',f'Captura WASAPI alternativo: {info["name"]} | {rate} Hz | {channels} canales.'))
            self.events.put(('log','Este metodo copia la musica HDMI al parlante de Mochi. La TV seguira sonando.'))
            empty_since=time.monotonic()
            warned=False; ever_sound=False; last_meter=0
            last_peak_report=time.monotonic(); peak_observed=0.0; near_max=0
            resampler_left=_LinearResampler(rate,SAMPLE_RATE)
            resampler_right=_LinearResampler(rate,SAMPLE_RATE)
            pending_left=np.empty(0,dtype=np.float32)
            pending_right=np.empty(0,dtype=np.float32)
            # Frames por lectura limitados: las muestras se agrupan despues en
            # paquetes de 20 ms; no asumimos que la salida HDMI sea de 48 kHz.
            buffer_frames=max(256,int(round(rate*0.02)))
            with manager.open(format=pa.paInt16,channels=channels,rate=rate,
                       input=True,input_device_index=int(info['index']),
                       frames_per_buffer=buffer_frames) as stream:
                while not self.stop.is_set():
                    raw=stream.read(buffer_frames,exception_on_overflow=False)
                    if not raw: continue
                    captured=np.frombuffer(raw,dtype='<i2')
                    captured=captured[:(len(captured)//channels)*channels]
                    if len(captured)==0: continue
                    values=captured.reshape(-1,channels).astype(np.float32)/32768.0
                    # Izquierdo y derecho por separado. En 5.1 / 7.1 tambien se
                    # capturan centro y laterales (evita silencio HDMI).
                    left,right=split_channels(values)
                    converted_left=resampler_left.feed(left)
                    converted_right=resampler_right.feed(right)
                    if converted_left.size:
                        pending_left=np.concatenate((pending_left,converted_left))
                        pending_right=np.concatenate((pending_right,converted_right))
                    while len(pending_left)>=FRAME_SAMPLES:
                        segment_left=pending_left[:FRAME_SAMPLES]
                        segment_right=pending_right[:FRAME_SAMPLES]
                        pending_left=pending_left[FRAME_SAMPLES:]
                        pending_right=pending_right[FRAME_SAMPLES:]
                        segment=np.concatenate((segment_left,segment_right))
                        rms=float(np.sqrt(np.mean(segment*segment)))
                        max_this_block=float(np.max(np.abs(segment)))
                        peak_observed=max(peak_observed,max_this_block)
                        near_max+=int(np.count_nonzero(np.abs(segment)>=0.995))
                        if rms>0.003:
                            empty_since=time.monotonic();warned=False
                            if not ever_sound:
                                ever_sound=True
                                self.events.put(('log','WASAPI alternativo: audio real detectado desde la salida Windows.'))
                        self._send_block(segment_left,segment_right)
                        now=time.monotonic()
                        if now-last_peak_report>=15.0:
                            self.events.put(('log',f'Calidad HDMI: pico={peak_observed:.2f} FS; muestras casi al maximo={near_max} (no confirma clipping).'))
                            last_peak_report=now;peak_observed=0.0;near_max=0
                        if now-last_meter>0.3:
                            self.events.put(('pc_meter',min(100.0,rms*250)))
                            last_meter=now
                        if not warned and now-empty_since>5.0:
                            warned=True
                            self.events.put(('log','WASAPI alternativo: la salida elegida entrega SILENCIO. Verifica barras verdes y salida del navegador en Mezclador de volumen.'))

    def _windows_soundcard(self):
        import numpy as np
        import soundcard as sc
        speaker=sc.get_speaker(self.device_id[3:]) if self.device_id and self.device_id.startswith("sc:") else sc.default_speaker()
        if speaker is None:
            raise RuntimeError('No hay salida de audio '+SISTEMA+' disponible.')
        # SoundCard incluye microfonos fisicos cuando include_loopback=True.
        # Exigimos el dispositivo de captura propio de la SALIDA, no el microfono.
        if IS_WINDOWS:
            source=sc.get_microphone(speaker.id,include_loopback=True)
        else:
            source=_linux_monitor(sc,speaker)
        if source is None or not getattr(source,'isloopback',False):
            raise RuntimeError('La salida elegida no tiene loopback compatible. Selecciona otra salida.')
        self.events.put(('log','Fuente: SALIDA de '+SISTEMA+' «'+speaker.name+'» (loopback; NO microfono).'))
        self.events.put(('log','Reproduce en esa misma salida de '+SISTEMA+'. Si el medidor sigue en cero, elige otra salida (por ejemplo Altavoces o Auriculares).'))
        empty_since=time.monotonic()
        warned=False
        last_meter=0.0
        pending_left=np.empty(0,dtype=np.float32)
        pending_right=np.empty(0,dtype=np.float32)
        resampler_left=_LinearResampler(48000,SAMPLE_RATE)
        resampler_right=_LinearResampler(48000,SAMPLE_RATE)
        # Dos canales evitan la limitacion de WASAPI con captura de un solo canal.
        # Para dispositivos mono usamos su unico canal.
        channels=[0,1] if speaker.channels>=2 else [0]
        with source.recorder(samplerate=48000,channels=channels,blocksize=960) as recorder:
            while not self.stop.is_set():
                samples=recorder.record(numframes=960)
                if samples is None or len(samples)==0:
                    self.stop.wait(0.01)
                    continue
                arr=np.asarray(samples,dtype=np.float32)
                if arr.ndim==1: arr=arr.reshape(-1,1)
                arr=np.nan_to_num(arr,nan=0.0,posinf=0.0,neginf=0.0)
                left,right=split_channels(arr)
                # El filtro FIR evita plegamiento de frecuencias al remuestrear.
                converted_left=resampler_left.feed(left)
                converted_right=resampler_right.feed(right)
                if converted_left.size:
                    pending_left=np.concatenate((pending_left,converted_left))
                    pending_right=np.concatenate((pending_right,converted_right))
                while len(pending_left)>=FRAME_SAMPLES:
                    segment_left=pending_left[:FRAME_SAMPLES]
                    segment_right=pending_right[:FRAME_SAMPLES]
                    pending_left=pending_left[FRAME_SAMPLES:]
                    pending_right=pending_right[FRAME_SAMPLES:]
                    segment=np.concatenate((segment_left,segment_right))
                    rms=float(np.sqrt(np.mean(segment*segment)))
                    if rms>0.003: empty_since=time.monotonic();warned=False
                    self._send_block(segment_left,segment_right)
                    now=time.monotonic()
                    if now-last_meter>0.3:
                        self.events.put(('pc_meter',min(100.0,rms*250)))
                        last_meter=now
                    if not warned and now-empty_since>5:
                        warned=True
                        self.events.put(('log','No entra audio desde esta salida de '+SISTEMA+'. Comprueba que Spotify/YouTube usen ESA salida, que no este silenciada y que haya reproduccion.'))

    def _worker(self):
        try:
            if self.mode=='tone': self._tone()
            else: self._windows()
        except Exception as exc:
            self.failed=str(exc)
            self.events.put(('log','ERROR de captura: '+self.failed))
        finally:
            self.finished_at=time.monotonic()

def _linux_monitor(sc,speaker):
    """Devuelve el monitor PulseAudio/PipeWire de la salida elegida.

    En Linux, el loopback de una salida (sink) es la fuente "<sink>.monitor".
    No se usa get_microphone(speaker.id) porque SoundCard busca por nombre
    aproximado y podria devolver otra fuente (incluso un microfono).
    """
    fuentes=sc.all_microphones(include_loopback=True)
    for fuente in fuentes:
        if fuente.id==speaker.id+'.monitor':
            return fuente
    for fuente in fuentes:
        if getattr(fuente,'isloopback',False) and speaker.id in fuente.id:
            return fuente
    raise RuntimeError('No encontre el monitor de la salida «'+speaker.name+'». Revisa que PipeWire o PulseAudio esten activos.')


class _LinearResampler:
    """Remuestreo por interpolacion lineal, con fase continua entre lecturas.

    Solo conserva la ultima muestra, sin acumular buffers de larga duracion.
    """
    def __init__(self,in_rate,out_rate):
        self.step=float(in_rate)/float(out_rate)
        self.next_position=0.0
        self.total_input=0
        self.last_sample=None
        self.antialias=FIRDownsampleFilter(in_rate,out_rate)

    def feed(self,chunk):
        import numpy as np
        arr=self.antialias.feed(chunk)
        if arr.size==0:
            return np.empty(0,dtype=np.float32)
        if self.last_sample is not None:
            first=self.total_input-1
            data=np.concatenate((np.asarray([self.last_sample],dtype=np.float32),arr))
        else:
            first=self.total_input
            data=arr
        last=self.total_input+arr.size-1
        self.last_sample=arr[-1]
        self.total_input+=arr.size
        # Conservar la ultima muestra para interpolarla con la siguiente lectura.
        if self.next_position>=last:
            return np.empty(0,dtype=np.float32)
        positions=np.arange(self.next_position,last,self.step,dtype=np.float64)
        if positions.size==0:
            return np.empty(0,dtype=np.float32)
        out=np.interp(positions,np.arange(first,first+data.size),data).astype(np.float32)
        self.next_position=positions[-1]+self.step
        return out
