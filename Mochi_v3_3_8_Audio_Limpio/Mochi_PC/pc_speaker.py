"""Envia audio real de SALIDA Windows (WASAPI loopback), nunca el microfono.
Mantiene un unico propietario del COM en app.py. Incluye tono de autodiagnostico.
"""
import queue
import binascii
import threading
import time

from audio_dsp import AudioControls, AudioProcessor, FIRDownsampleFilter

SAMPLE_RATE=16000
FRAME_SAMPLES=320


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
    if kind not in (b'C',b'A',b'B'):
        raise ValueError('Tipo USB invalido')
    expected={b'C':1,b'A':640,b'B':480}[kind]
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
    """PCM16 -> PCM12 empaquetado, con redondeo y dither TPDF solo si hay audio."""
    import numpy as np
    global _PCM12_RNG
    if len(pcm)!=640:
        raise ValueError('PCM16 debe tener 320 muestras')
    signed=np.frombuffer(pcm,dtype='<i2').astype(np.int32)
    if np.any(signed):
        if _PCM12_RNG is None: _PCM12_RNG=np.random.default_rng()
        # 1 LSB PCM12 = 16 unidades PCM16. Se preservan los silencios exactos.
        random_dither=_PCM12_RNG.random(320)-_PCM12_RNG.random(320)
        quantized=np.clip(np.rint(signed/16.0+random_dither),-2048,2047).astype(np.int32)
    else:
        quantized=np.zeros(320,dtype=np.int32)
    packed=quantized & 0x0FFF
    out=np.empty(480,dtype=np.uint8)
    out[0::3]=(packed[0::2]&0xFF).astype(np.uint8)
    out[1::3]=(((packed[0::2]>>8)&0x0F)|((packed[1::2]&0x0F)<<4)).astype(np.uint8)
    out[2::3]=((packed[1::2]>>4)&0xFF).astype(np.uint8)
    return out.tobytes()


def frame_audio(pcm):
    return frame_packet(b'B',pack_pcm12(pcm))


def audio_outputs():
    """Enumera ambas rutas, priorizando WASAPI WPatch para HDMI.

    Los IDs prefijados evitan confundir los indices WASAPI de PortAudio
    con los identificadores de SoundCard (son espacios de nombres distintos).
    """
    items = []
    default_id = None
    problems = []
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
        for output in sc.all_speakers():
            items.append(('sc:'+output.id, output.name+'  (SoundCard original)'))
        if default_id is None and old_default is not None:
            default_id = 'sc:'+old_default.id
    except (ImportError, OSError, RuntimeError) as exc:
        problems.append('SoundCard: '+str(exc))
    if not items:
        raise RuntimeError('No se detectaron salidas de Windows. '+' / '.join(problems))
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

    def _tone(self):
        import numpy as np
        self.events.put(('log','PRUEBA USB: enviando tono de 880 Hz durante 2 segundos (sin microfono ni WASAPI).'))
        for i in range(100):
            if self.stop.is_set(): break
            t=(np.arange(FRAME_SAMPLES,dtype=np.float64)+i*FRAME_SAMPLES)/SAMPLE_RATE
            # Rampa de entrada/salida para evitar clics al arrancar y al terminar.
            env=min(1.0,(i+1)/6.0,(100-i)/6.0)
            pcm=(np.sin(t*2*np.pi*880.0)*0.24*env*32767).astype('<i2')
            self._put(frame_audio(pcm.tobytes()))
            self.stop.wait(0.020)
        self.events.put(('log','PRUEBA USB: tono enviado. Si no se escucho, el problema esta en USB/firmware/I2S.'))

    def _windows(self):
        if self.device_id and self.device_id.startswith('sc:'):
            self._windows_soundcard()
        else:
            self._windows_wpatch()

    def _windows_wpatch(self):
        """Lee la mezcla que sale por HDMI/auriculares, NO del microfono.

        La salida tiene una frecuencia nativa variable (por ejemplo 48000),
        que convertimos de forma continua a mono PCM16 de 16 kHz para Mochi.
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
            resampler=_LinearResampler(rate,SAMPLE_RATE)
            processor=AudioProcessor(self.controls)
            pcm_pending=np.empty(0,dtype=np.float32)
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
                    if channels>=2:
                        # Mezcla de canales activa. En 5.1 / 7.1 tambien se
                        # capturan centro y laterales (evita silencio HDMI).
                        mono=values.mean(axis=1)
                    else:
                        mono=values[:,0]
                    converted=resampler.feed(mono)
                    if converted.size:
                        pcm_pending=np.concatenate((pcm_pending,converted))
                    while len(pcm_pending)>=FRAME_SAMPLES:
                        segment=pcm_pending[:FRAME_SAMPLES]
                        pcm_pending=pcm_pending[FRAME_SAMPLES:]
                        rms=float(np.sqrt(np.mean(segment*segment)))
                        max_this_block=float(np.max(np.abs(segment)))
                        peak_observed=max(peak_observed,max_this_block)
                        near_max+=int(np.count_nonzero(np.abs(segment)>=0.995))
                        if rms>0.003:
                            empty_since=time.monotonic();warned=False
                            if not ever_sound:
                                ever_sound=True
                                self.events.put(('log','WASAPI alternativo: audio real detectado desde la salida Windows.'))
                        pcm=processor.to_pcm16(segment)
                        self._put(frame_audio(pcm))
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
            raise RuntimeError('No hay salida de audio Windows disponible.')
        # SoundCard incluye microfonos fisicos cuando include_loopback=True.
        # Exigimos el dispositivo de captura propio de la SALIDA, no el microfono.
        source=sc.get_microphone(speaker.id,include_loopback=True)
        if source is None or not getattr(source,'isloopback',False):
            raise RuntimeError('La salida elegida no tiene WASAPI loopback compatible. Selecciona otra salida.')
        self.events.put(('log','Fuente: SALIDA de Windows «'+speaker.name+'» (loopback; NO microfono).'))
        self.events.put(('log','Reproduce en esa misma salida de Windows. Si el medidor sigue en cero, elige otra salida (por ejemplo Altavoces o Auriculares).'))
        empty_since=time.monotonic()
        warned=False
        last_meter=0.0
        pending=np.empty(0,dtype=np.float32)
        processor=AudioProcessor(self.controls)
        resampler=_LinearResampler(48000,SAMPLE_RATE)
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
                if arr.ndim==2: arr=arr.mean(axis=1)
                arr=np.nan_to_num(arr,nan=0.0,posinf=0.0,neginf=0.0)
                # El filtro FIR evita plegamiento de frecuencias al remuestrear.
                converted=resampler.feed(arr)
                if converted.size:
                    pending=np.concatenate((pending,converted))
                while len(pending)>=FRAME_SAMPLES:
                    mono=pending[:FRAME_SAMPLES]
                    pending=pending[FRAME_SAMPLES:]
                    rms=float(np.sqrt(np.mean(mono*mono)))
                    if rms>0.003: empty_since=time.monotonic();warned=False
                    pcm=processor.to_pcm16(mono)
                    self._put(frame_audio(pcm))
                    now=time.monotonic()
                    if now-last_meter>0.3:
                        self.events.put(('pc_meter',min(100.0,rms*250)))
                        last_meter=now
                    if not warned and now-empty_since>5:
                        warned=True
                        self.events.put(('log','No entra audio desde esta salida de Windows. Comprueba que Spotify/YouTube usen ESA salida, que no este silenciada y que haya reproduccion.'))

    def _worker(self):
        try:
            if self.mode=='tone': self._tone()
            else: self._windows()
        except Exception as exc:
            self.failed=str(exc)
            self.events.put(('log','ERROR de captura: '+self.failed))
        finally:
            self.finished_at=time.monotonic()

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
