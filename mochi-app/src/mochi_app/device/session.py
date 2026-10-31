"""Sesión USB con el ESP32 (firmware Mochi 3.5).

Es la VoiceSession de la ventana Tk del Mochi limpio (voice_bridge/mochi_pc/
app.py), copiada sin cambios de lógica: órdenes fijas por voz con Vosk
(«Mochi, duerme»), botón BOOT para el LLM, Parlante PC, voz de Piper por el
parlante de Mochi y el mismo protocolo serie. No tocar sin probar con la
placa: tiene que seguir andando igual que la versión Tk.

Agregado de la app: con wake_llm, «Mochi» sin orden fija abre la escucha
del LLM (como «Hablarle a Mochi»), con lo dicho justo después de la llamada.

Habla con la ventana por la cola `events`: (tipo, valor).
"""
from collections import deque
import array
import json
import math
import queue
import sys
import threading
import time

from .commands import (CommandRouter,DEFAULT_WAKE,DEFAULT_WAKE_PHRASES,recognition_grammar,
  WAKE_LLM_CONFIDENCE,AudioTail,wake_call,voice_in,fixed_command,mic_sensitivity)
from .model_setup import ensure_model
from .protocol import Parser,PCM,HELLO,STATUS,TEXT,LLM_IDLE
from .pc_speaker import PCSpeaker,frame_command,frame_text,speech_packets
from .audio_dsp import AudioControls

USB_WRITE_TIMEOUT_SECONDS=2.0
USB_OPEN_SETTLE_SECONDS=1.0
USB_HANDSHAKE_SECONDS=10.0
# Boton BOOT (LLM): grabacion maxima y voz enviada por adelantado al firmware
# (su cola mide 24 bloques de 20 ms; empieza a sonar con 8).
PTT_MAX_SECONDS=15.0
# Boton "Hablarle a Mochi" de la ventana: termina solo tras este silencio.
# Si no se oye nada en NO_SPEECH_SECONDS, se cancela.
VOICE_RMS=0.012
END_SILENCE_SECONDS=0.8
# El umbral de voz sube con el ruido de fondo: el minimo de los ultimos ~2 s
# (bloques de 8 ms del firmware) por NOISE_FACTOR. Con un umbral fijo, un
# ventilador o la PC sonando no dejaban terminar y se grababan los 15 s.
NOISE_FACTOR=3.0
NOISE_HISTORY_BLOCKS=250
NO_SPEECH_SECONDS=6.0
SPEECH_LEAD_SECONDS=0.2

READY_TEXT='Listo: pulsa «Hablarle a Mochi», escribile, o mantene BOOT'
WAKE_READY_TEXT='Listo: decí «Mochi», pulsa «Hablarle a Mochi», escribile, o mantene BOOT'

def usb_timeout_message(port,connected):
    phase='Se interrumpió el envío a Mochi' if connected else 'No se pudo enviar la consulta inicial a Mochi'
    return (phase+' por '+port+'. Cierra el programa, pulsa RESET/EN sin mantener BOOT y vuelve a conectar. '
      'Si continúa, revisa el puerto USB nativo y carga el firmware con USB CDC On Boot: Enabled '
      'y USB Mode: Hardware CDC and JTAG.')

class VoiceSession:
    def __init__(self,port,events,config,model_path=None,audio_controls=None,assistant=None):
        self.port=port; self.events=events; self.config=config; self.model_path=model_path
        self.audio_controls=audio_controls or AudioControls()
        self.assistant=assistant
        self.stop=threading.Event(); self.outgoing=queue.Queue()
        self.pc_audio_queue=queue.Queue(maxsize=12)
        self.pc_speaker=None
        # LLM: audio del boton BOOT, pausa del Parlante PC y voz pendiente.
        self.ptt=None; self.ptt_started=0.0; self.llm_hold=False
        self.ptt_auto=False; self.ptt_voice=False; self.ptt_last_voice=0.0
        self.ptt_peak=0.0   # voz mas fuerte de la escucha (sensibilidad)
        self.mic_levels=deque(maxlen=NOISE_HISTORY_BLOCKS)   # RMS de los ultimos bloques
        self.speech=deque(); self.speech_current=None; self.button_warned=False
        self.wake_llm=False   # «Mochi» abre la escucha del LLM (lo pone la ventana)
        self.thread=threading.Thread(target=self.run,daemon=True)
    def post(self,kind,value): self.events.put((kind,value))
    @property
    def ready_text(self): return WAKE_READY_TEXT if self.wake_llm and self.assistant is not None else READY_TEXT
    def start(self): self.thread.start()
    def voice_threshold(self):
        factor,fraction=mic_sensitivity(self.config)
        noise=max(VOICE_RMS,NOISE_FACTOR*min(self.mic_levels)) if self.mic_levels else VOICE_RMS
        return max(factor*noise,fraction*self.ptt_peak)
    def send(self,command): self.outgoing.put(command.encode('ascii'))
    def set_pc_audio(self,active,device_id=None): self.outgoing.put(('pc_audio',bool(active),device_id))
    def test_usb_audio(self): self.outgoing.put(('pc_test',))
    def close(self):
        self.stop.set()
        if self.pc_speaker: self.pc_speaker.close()

    # ---- Para el asistente del LLM (se llaman desde su hilo) ----
    def show_state(self,state): self.outgoing.put(('state',int(state)))
    def show_text(self,text): self.outgoing.put(('text',str(text)))
    def hold_music(self,active): self.outgoing.put(('hold',bool(active)))
    # ---- Desde la ventana, sin tocar la placa ----
    def listen_toggle(self): self.outgoing.put(('listen',))
    def cancel(self):
        """Descarta lo que se esta grabando y corta la voz que sale (el asistente lo cancela aparte)."""
        self.outgoing.put(('cancel',))
    def ask_text(self,text): self.outgoing.put(('ask',str(text)))
    def speak(self,pcm,rate):
        """Manda la voz al parlante de Mochi y espera a que termine de sonar."""
        packets=speech_packets(pcm,rate,self.audio_controls)
        if not packets: return True
        done=threading.Event()
        self.outgoing.put(('speech',packets,done))
        return done.wait(len(packets)*0.02+10)

    def _on_button(self,pressed,auto=False):
        if self.assistant is None:
            if pressed and not self.button_warned:
                self.button_warned=True
                self.post('log','Boton BOOT: para hablar con el LLM abri Mochi con el puente (python -m voice_bridge.esp32_bridge).')
            return
        if pressed:
            if self.assistant.busy:
                # BOOT con Mochi ocupado cancela la orden en curso.
                self.assistant.cancel(); self._stop_speech()
                self.post('log','Orden cancelada con BOOT: pulsalo de nuevo para repetirla.')
                return
            self.ptt=bytearray(); self.ptt_started=time.monotonic()
            self.ptt_auto=auto; self.ptt_voice=False; self.ptt_last_voice=self.ptt_started; self.ptt_peak=0.0
            self.post('status','Te escucho: habla ahora' if auto else 'Te escucho: habla y solta BOOT al terminar')
            self.post('llm','Escuchando… (se envia solo cuando dejas de hablar)' if auto else 'Escuchando…')
            self.post('listening',True)
        elif self.ptt is not None:
            pcm=bytes(self.ptt); self.ptt=None
            self.post('listening',False)
            self.assistant.submit(self,pcm,16000)

    def _stop_speech(self):
        """Corta la voz que suena y la que espera (solo desde el hilo de la sesion)."""
        if self.speech_current: self.speech_current[2].set()
        self.speech_current=None
        while self.speech: self.speech.popleft()[1].set()

    def _cancel_listening(self,connection):
        if self.ptt is None: return
        self.ptt=None; self.post('listening',False)
        connection.write(frame_command('0'))
        self.post('face',LLM_IDLE); self.post('llm',self.ready_text)

    def fixed_command(self,text):
        """Desde el asistente: «Mochi» ... «duerme» dicho en dos veces es la orden fija."""
        found=fixed_command(text,self.config.get('wake_words',DEFAULT_WAKE),self.config.get('wake_phrases',DEFAULT_WAKE_PHRASES))
        if not found: return False
        self.send(found[0]); self.post('log','Orden: '+found[1]); self.post('action',found[1])
        return True

    def _wake_to_llm(self,connection,result,decision,tail):
        """«Mochi» sin orden fija: escucha para el LLM. False si no corresponde."""
        if not self.wake_llm or self.assistant is None or self.ptt is not None: return False
        end,confidence=wake_call(result,decision)
        if end is None: return False
        if confidence<WAKE_LLM_CONFIDENCE:
            print('[placa] llamada dudosa: «'+result.get('text','')+'» ('+format(confidence,'.2f')+')')
            return False
        # Ocupado (pensando): se ignora; BOOT con Mochi ocupado cancela, la llamada no.
        if self.assistant.busy: return True
        print('[placa] oí «'+result.get('text','')+'» ('+format(confidence,'.2f')+'): escucho la orden')
        pre=tail.since(end)
        connection.write(frame_command('1'))
        self._on_button(True,auto=True)
        if self.ptt is not None:
            self.ptt.extend(pre)
            heard,quiet=voice_in(pre,self.voice_threshold())
            if heard: self.ptt_voice=True; self.ptt_last_voice=time.monotonic()-quiet
        return True

    def _release_speech(self):
        if self.speech_current: self.speech_current[2].set()
        self.speech_current=None
        while self.speech: self.speech.popleft()[1].set()
        while True:
            try: item=self.outgoing.get_nowait()
            except queue.Empty: break
            if isinstance(item,tuple) and item[0]=='speech': item[2].set()

    def run(self):
        connection=None; connected=False; link_failed=False; serial=None
        try:
            import serial
            from vosk import Model,KaldiRecognizer,SetLogLevel
            path=ensure_model(lambda text:self.post('status',text),self.stop,self.model_path)
            if self.stop.is_set(): return
            self.post('status','Cargando reconocimiento en español…')
            SetLogLevel(-1); model=Model(str(path))
            words=self.config.get('wake_words',DEFAULT_WAKE)
            phrases=self.config.get('wake_phrases',DEFAULT_WAKE_PHRASES)
            router=CommandRouter(words,phrases,self.config.get('wake_window_seconds',5))
            # Con wake_llm, la gramática suma palabras de órdenes para el LLM
            # (ver LLM_WORDS); si se cambia la opción, se rearma.
            def make_recognizer(llm_words):
                grammar=recognition_grammar(model,words,phrases,llm_words)
                recognizer=KaldiRecognizer(model,16000,json.dumps(grammar,ensure_ascii=False))
                recognizer.SetWords(True)
                return recognizer
            grammar_llm=self.wake_llm; recognizer=make_recognizer(grammar_llm)
            if self.stop.is_set(): return
            self.post('status','Conectando a '+self.port+'…')
            connection=serial.Serial(port=None,baudrate=921600,timeout=0.05,
              write_timeout=USB_WRITE_TIMEOUT_SECONDS,xonxoff=False,rtscts=False,dsrdtr=False)
            connection.dtr=True; connection.rts=False; connection.port=self.port
            connection.open()
            self.post('status','Esperando que el USB de Mochi esté listo…')
            if self.stop.wait(USB_OPEN_SETTLE_SECONDS): return
            parser=Parser(); started=time.monotonic(); next_probe=0.0
            next_heartbeat=0.0; busy=False; block=bytearray(); last_level=0.0
            last_partial=''; last_packet=started
            stream_on=False; stream_on_since=0.0; speech_next=None
            tail=AudioTail()   # lo que oyó Vosk, para no perder lo dicho tras «Mochi»
            while not self.stop.is_set():
                now=time.monotonic()
                if not connected and now-started>USB_HANDSHAKE_SECONDS:
                    raise RuntimeError('No respondió el firmware de voz. Carga la nueva versión, activa USB CDC y revisa el puerto.')
                if not connected and now>=next_probe:
                    connection.write(frame_command('?')); next_probe=now+0.6
                if connected and now>=next_heartbeat:
                    connection.write(frame_command('#')); next_heartbeat=now+2
                if connected and now-last_packet>8:
                    raise RuntimeError('Se perdió la comunicación con Mochi. Revisa el USB y vuelve a conectar.')
                while connected:
                    try: command=self.outgoing.get_nowait()
                    except queue.Empty: break
                    if isinstance(command,tuple) and command[0]=='pc_test':
                        if self.pc_speaker is not None:
                            self.post('log','Deten el parlante PC antes de ejecutar la prueba USB.')
                        else:
                            while not self.pc_audio_queue.empty():
                                try: self.pc_audio_queue.get_nowait()
                                except queue.Empty: break
                            self.pc_speaker=PCSpeaker(self.pc_audio_queue,self.events,mode='tone',controls=self.audio_controls)
                            self.pc_speaker.start()
                            self.post('pc_state',True)
                    elif isinstance(command,tuple) and command[0]=='pc_audio':
                        enabled=command[1]
                        if enabled and self.pc_speaker is None:
                            while not self.pc_audio_queue.empty():
                                try: self.pc_audio_queue.get_nowait()
                                except queue.Empty: break
                            self.pc_speaker=PCSpeaker(self.pc_audio_queue,self.events,
                                device_id=command[2] if len(command)>2 else None,
                                controls=self.audio_controls)
                            self.pc_speaker.start()
                            self.post('pc_state',True)
                        elif enabled and self.pc_speaker is not None:
                            self.post('log','El parlante PC ya esta activo. Detenlo antes de cambiar la salida.')
                        elif not enabled and self.pc_speaker is not None:
                            self.pc_speaker.close(); self.pc_speaker=None
                            while not self.pc_audio_queue.empty():
                                try: self.pc_audio_queue.get_nowait()
                                except queue.Empty: break
                            self.post('pc_state',False)
                    elif isinstance(command,tuple) and command[0]=='state':
                        connection.write(frame_command(str(command[1])))
                    elif isinstance(command,tuple) and command[0]=='text':
                        connection.write(frame_text(command[1]))
                    elif isinstance(command,tuple) and command[0]=='hold':
                        self.llm_hold=command[1]
                    elif isinstance(command,tuple) and command[0]=='speech':
                        self.speech.append((command[1],command[2]))
                    elif isinstance(command,tuple) and command[0]=='listen' and self.assistant is not None:
                        # Mismo camino que BOOT; la cara la pone la PC.
                        if self.ptt is None:
                            if not self.assistant.busy: connection.write(frame_command('1'))
                            self._on_button(True,auto=True)
                        else: self._on_button(False)
                    elif isinstance(command,tuple) and command[0]=='cancel':
                        self._stop_speech(); self._cancel_listening(connection)
                    elif isinstance(command,tuple) and command[0]=='ask' and self.assistant is not None:
                        if not self.assistant.submit_text(self,command[1]):
                            self.post('log','Mochi todavia esta respondiendo la orden anterior.')
                    else: connection.write(frame_command(command))
                # K/J en un solo lugar: suena el Parlante PC (salvo mientras el
                # LLM escucha o responde) o la voz del LLM.
                speaking=bool(self.speech) or self.speech_current is not None
                want_stream=connected and (speaking or (self.pc_speaker is not None
                    and not self.llm_hold and self.ptt is None))
                if want_stream!=stream_on:
                    connection.write(frame_command('K' if want_stream else 'J'))
                    stream_on=want_stream; stream_on_since=time.monotonic()
                if self.ptt is not None and time.monotonic()-self.ptt_started>PTT_MAX_SECONDS:
                    self.post('log','Hablar con el LLM: maximo '+format(PTT_MAX_SECONDS,'g')+' segundos.')
                    self._on_button(False)
                if self.ptt is not None and self.ptt_auto:
                    now=time.monotonic()
                    if self.ptt_voice and now-self.ptt_last_voice>END_SILENCE_SECONDS:
                        print('[placa] fin de la voz: ruido '+format(min(self.mic_levels,default=0.0),'.4f')
                          +', umbral '+format(self.voice_threshold(),'.4f'))
                        self._on_button(False)
                    elif not self.ptt_voice and now-self.ptt_started>NO_SPEECH_SECONDS:
                        self.ptt=None; self.post('listening',False)
                        connection.write(frame_command('0'))
                        self.post('llm','No escuche nada. Deci «Mochi» o pulsa «Hablarle a Mochi» y habla cerca del microfono.'
                          if self.wake_llm else 'No escuche nada. Pulsa «Hablarle a Mochi» y habla cerca del microfono.')
                if self.pc_speaker is not None:
                    if not self.pc_speaker.alive() and self.pc_speaker.failed:
                        self.post('log','Captura detenida: '+self.pc_speaker.failed)
                        self.pc_speaker=None
                        self.post('pc_state',False)
                    elif (self.pc_speaker.mode=='tone' and not self.pc_speaker.alive()
                          and self.pc_audio_queue.empty()
                          and time.monotonic()-(self.pc_speaker.finished_at or 0)>0.5):
                        self.pc_speaker=None
                        self.post('pc_state',False)
                        self.post('log','Prueba USB finalizada. Ahora prueba captura de audio de la PC.')
                    elif speaking or not stream_on:
                        # Mientras habla el LLM (o la musica esta en pausa) se descarta.
                        while True:
                            try: self.pc_audio_queue.get_nowait()
                            except queue.Empty: break
                    else:
                        # Priorizar entrada del microfono y comandos; a lo sumo
                        # dos paquetes por vuelta, 40 ms de sonido.
                        for _ in range(2):
                            try: pcm=self.pc_audio_queue.get_nowait()
                            except queue.Empty: break
                            connection.write(pcm)
                # Voz del LLM: bloques de 20 ms a ritmo real, con 0,2 s de adelanto.
                # El primero espera a que el firmware procese K (vacia su cola).
                if self.speech_current is None and self.speech and stream_on:
                    packets,done=self.speech.popleft()
                    self.speech_current=[packets,0,done]
                    speech_next=None
                if self.speech_current is not None and time.monotonic()>=stream_on_since+0.12:
                    packets,index,done=self.speech_current
                    now=time.monotonic()
                    if speech_next is None: speech_next=now
                    while index<len(packets) and speech_next<=now+SPEECH_LEAD_SECONDS:
                        connection.write(packets[index]); index+=1; speech_next+=0.02
                    self.speech_current[1]=index
                    if index>=len(packets) and now>speech_next+0.3:
                        done.set(); self.speech_current=None
                # En modo audio no esperar 50 ms: llegan bloques cada 20 ms.
                connection.timeout=0.004 if (self.pc_speaker is not None or speaking) else 0.05
                data=connection.read(min(max(connection.in_waiting,1),4096))
                for packet in parser.feed(data):
                    last_packet=time.monotonic()
                    if packet.kind==HELLO:
                        info=json.loads(packet.payload.decode('utf-8'))
                        if info.get('device')!='Mochi' or info.get('protocol')!=1 or info.get('sample_rate')!=16000 or info.get('pcm')!='s16le':
                            raise RuntimeError('Este puerto no corresponde al protocolo de Mochi Voz.')
                        if info.get('pc_rx_protocol')!=2:
                            raise RuntimeError('Firmware anterior: carga Mochi v3.3.2 en Arduino para usar el protocolo USB protegido.')
                        if not info.get('mic'):
                            raise RuntimeError('El controlador del micrófono no inició. Revisa el firmware y la RAM.')
                        if not connected:
                            connected=True; connection.write(frame_command('L')); self.post('connected',True)
                            self.post('status','Escuchando a través del micrófono de Mochi')
                            self.post('log','Firmware '+str(info.get('firmware','3.3.x'))+': PCM12 USB estable + filtro de graves en la PC. Revisa BAD/under.')
                            if self.assistant is not None:
                                if info.get('llm'):
                                    connection.write(frame_command('0'))
                                    self.post('log','LLM listo: pulsa «Hablarle a Mochi» (o mantene BOOT en la placa) y habla.')
                                    self.post('llm',self.ready_text)
                                else:
                                    self.post('log','Aviso: este firmware no tiene el boton del LLM. Carga firmware/Mochi (3.5).')
                                    self.post('llm','Firmware sin modo LLM: carga Mochi 3.5')
                            if 'record_seconds' in info:
                                self.post('log','Grabación disponible: '+format(info['record_seconds'],'g')+' segundos.')
                    elif not connected:
                        continue
                    elif packet.kind==TEXT:
                        text=packet.payload.decode('utf-8',errors='replace').strip()
                        if not text.startswith('Mic RMS='): self.post('log',text)
                    elif packet.kind==STATUS:
                        status_info=json.loads(packet.payload.decode('utf-8'))
                        if 'button' in status_info:
                            self._on_button(status_info['button']=='down')
                            continue
                        state=status_info.get('audio','idle')
                        busy=state in ('playing','recording','streaming')
                        block.clear(); recognizer.Reset(); router.reset(); tail.reset(); last_partial=''
                        record_seconds=status_info.get('record_seconds',1.5)
                        self.post('status',{'playing':'Reproduciendo: escucharé al terminar',
                          'recording':'GRABANDO '+format(record_seconds,'g')+' segundos: habla ahora',
                          'streaming':'Parlante PC activo: musica, videos y sonidos de la PC',
                          'idle':'Escuchando a través del micrófono de Mochi'}.get(state,state))
                    elif packet.kind==PCM and not busy:
                        if len(packet.payload)%2: continue
                        samples=array.array('h'); samples.frombytes(packet.payload)
                        if sys.byteorder!='little': samples.byteswap()
                        rms=math.sqrt(sum(float(v)*v for v in samples)/len(samples))/32768 if samples else 0.0
                        if samples: self.mic_levels.append(rms)
                        if samples and time.monotonic()-last_level>0.1:
                            self.post('level',min(100.0,rms*1500)); last_level=time.monotonic()
                        # Con BOOT apretado el audio es para el LLM, no para las ordenes fijas.
                        if self.ptt is not None:
                            self.ptt.extend(packet.payload); block.clear()
                            if self.ptt_auto and samples and rms>self.voice_threshold():
                                self.ptt_voice=True; self.ptt_last_voice=time.monotonic()
                                self.ptt_peak=max(self.ptt_peak,rms)
                            continue
                        block.extend(packet.payload)
                        if len(block)<2560: continue
                        if grammar_llm!=self.wake_llm:
                            grammar_llm=self.wake_llm; recognizer=make_recognizer(grammar_llm)
                            router.reset(); tail.reset(); block.clear(); continue
                        chunk=bytes(block); block.clear(); tail.extend(chunk)
                        if recognizer.AcceptWaveform(chunk):
                            result=json.loads(recognizer.Result()); text=result.get('text','')
                            last_partial=''
                            if not text: continue
                            self.post('transcript',text)
                            confidences=[w.get('conf',0) for w in result.get('result',[])]
                            # Con poca confianza no hay orden fija, pero «Mochi» puede
                            # haberse oído bien (lo que sigue no está en la gramática).
                            doubtful=bool(confidences) and sum(confidences)/len(confidences)<0.55
                            if doubtful and not self.wake_llm: continue
                            decision=router.process(text)
                            if decision.command and not doubtful:
                                connection.write(frame_command(decision.command))
                                self.post('log','Orden: '+decision.label)
                                self.post('action',decision.label)
                            elif decision.awakened and not decision.command and self._wake_to_llm(connection,result,decision,tail):
                                recognizer.Reset(); router.reset(); tail.reset()
                            elif decision.awakened and not doubtful:
                                self.post('status','Te escucho: di una orden en los próximos 5 segundos')
                        else:
                            partial=json.loads(recognizer.PartialResult()).get('partial','')
                            if partial and partial!=last_partial:
                                self.post('transcript',partial); last_partial=partial
        except InterruptedError:
            pass
        except Exception as error:
            link_failed=True
            # Un envío agotado no confirma que el ESP32 haya recibido la orden.
            # No repetimos acciones ni otro envío durante el cierre de ese puerto.
            if serial is not None and isinstance(error,serial.SerialTimeoutException):
                self.post('error',usb_timeout_message(self.port,connected))
            else:
                self.post('error',str(error))
        finally:
            if self.pc_speaker: self.pc_speaker.close()
            self._release_speech(); self.ptt=None
            self.post('pc_state',False)
            if connection is not None:
                try:
                    if connection.is_open and connected and not link_failed:
                        connection.write(frame_command('J')); connection.write(frame_command('0'))
                        connection.write(frame_command('Q'))
                except Exception: pass
                connection.close()
            self.post('connected',False)
            self.post('finished',self.stop.is_set())
