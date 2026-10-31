"""Mochi Voz: usa exclusivamente el audio que recibe del ESP32 por USB.

Sola (python app.py) es la ventana de prueba: ordenes fijas y Parlante PC.
Con el puente del LLM (python -m voice_bridge.esp32_bridge) ademas recibe
el boton BOOT del ESP32 y le pasa lo dicho al asistente. Firmware 3.5.
"""
from collections import deque
from pathlib import Path
import argparse
import array
import os
import json
import math
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

# Paquete (voice_bridge.mochi_pc) o script suelto (python app.py).
try:
    from .commands import CommandRouter,DEFAULT_WAKE,DEFAULT_WAKE_PHRASES,recognition_grammar
    from .model_setup import ensure_model
    from .protocol import Parser,PCM,HELLO,STATUS,TEXT
    from .pc_speaker import (PCSpeaker,audio_outputs,frame_command,frame_text,speech_packets,
        ensure_mochi_sink,remove_mochi_sink,MOCHI_SINK)
    from .audio_dsp import AudioControls, PROFILES
except ImportError:
    from commands import CommandRouter,DEFAULT_WAKE,DEFAULT_WAKE_PHRASES,recognition_grammar
    from model_setup import ensure_model
    from protocol import Parser,PCM,HELLO,STATUS,TEXT
    from pc_speaker import (PCSpeaker,audio_outputs,frame_command,frame_text,speech_packets,
        ensure_mochi_sink,remove_mochi_sink,MOCHI_SINK)
    from audio_dsp import AudioControls, PROFILES

ROOT=Path(__file__).resolve().parent
USB_WRITE_TIMEOUT_SECONDS=2.0
USB_OPEN_SETTLE_SECONDS=1.0
USB_HANDSHAKE_SECONDS=10.0
# Boton BOOT (LLM): grabacion maxima y voz enviada por adelantado al firmware
# (su cola mide 24 bloques de 20 ms; empieza a sonar con 8).
PTT_MAX_SECONDS=15.0
# Boton "Hablarle a Mochi" de la ventana: termina solo tras este silencio.
# Si no se oye nada en NO_SPEECH_SECONDS, se cancela.
VOICE_RMS=0.012
END_SILENCE_SECONDS=1.2
NO_SPEECH_SECONDS=6.0
SPEECH_LEAD_SECONDS=0.2

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
        self.speech=deque(); self.speech_current=None; self.button_warned=False
        self.thread=threading.Thread(target=self.run,daemon=True)
    def post(self,kind,value): self.events.put((kind,value))
    def start(self): self.thread.start()
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
                self.post('log','Mochi todavia esta respondiendo la orden anterior.')
                return
            self.ptt=bytearray(); self.ptt_started=time.monotonic()
            self.ptt_auto=auto; self.ptt_voice=False; self.ptt_last_voice=self.ptt_started
            self.post('status','Te escucho: habla ahora' if auto else 'Te escucho: habla y solta BOOT al terminar')
            self.post('llm','Escuchando… (se envia solo cuando dejas de hablar)' if auto else 'Escuchando…')
            self.post('listening',True)
        elif self.ptt is not None:
            pcm=bytes(self.ptt); self.ptt=None
            self.post('listening',False)
            self.assistant.submit(self,pcm,16000)

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
            grammar=recognition_grammar(model,words,phrases)
            recognizer=KaldiRecognizer(model,16000,json.dumps(grammar,ensure_ascii=False))
            recognizer.SetWords(True)
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
                        self._on_button(False)
                    elif not self.ptt_voice and now-self.ptt_started>NO_SPEECH_SECONDS:
                        self.ptt=None; self.post('listening',False)
                        connection.write(frame_command('0'))
                        self.post('llm','No escuche nada. Pulsa «Hablarle a Mochi» y habla cerca del microfono.')
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
                                    self.post('llm','Listo: pulsa «Hablarle a Mochi», escribile, o mantene BOOT')
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
                        block.clear(); recognizer.Reset(); router.reset(); last_partial=''
                        record_seconds=status_info.get('record_seconds',1.5)
                        self.post('status',{'playing':'Reproduciendo: escucharé al terminar',
                          'recording':'GRABANDO '+format(record_seconds,'g')+' segundos: habla ahora',
                          'streaming':'Parlante PC activo: musica, videos y sonidos de la PC',
                          'idle':'Escuchando a través del micrófono de Mochi'}.get(state,state))
                    elif packet.kind==PCM and not busy:
                        if len(packet.payload)%2: continue
                        samples=array.array('h'); samples.frombytes(packet.payload)
                        if sys.byteorder!='little': samples.byteswap()
                        if samples and time.monotonic()-last_level>0.1:
                            rms=math.sqrt(sum(float(v)*v for v in samples)/len(samples))/32768
                            self.post('level',min(100.0,rms*1500)); last_level=time.monotonic()
                        # Con BOOT apretado el audio es para el LLM, no para las ordenes fijas.
                        if self.ptt is not None:
                            self.ptt.extend(packet.payload); block.clear()
                            if self.ptt_auto and samples:
                                rms=math.sqrt(sum(float(v)*v for v in samples)/len(samples))/32768
                                if rms>VOICE_RMS: self.ptt_voice=True; self.ptt_last_voice=time.monotonic()
                            continue
                        block.extend(packet.payload)
                        if len(block)<2560: continue
                        chunk=bytes(block); block.clear()
                        if recognizer.AcceptWaveform(chunk):
                            result=json.loads(recognizer.Result()); text=result.get('text','')
                            last_partial=''
                            if not text: continue
                            self.post('transcript',text)
                            confidences=[w.get('conf',0) for w in result.get('result',[])]
                            if confidences and sum(confidences)/len(confidences)<0.55: continue
                            decision=router.process(text)
                            if decision.command:
                                connection.write(frame_command(decision.command))
                                self.post('log','Orden: '+decision.label)
                                self.post('action',decision.label)
                            elif decision.awakened:
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

class App:
    def __init__(self,root,preferred=None,model_path=None,assistant=None,autoconnect=False):
        self.root=root; self.model_path=model_path; self.session=None
        self.assistant=assistant; self.sink_module=None
        self.audio_controls=AudioControls(percent=70,profile='Protegido (limpio)')
        self.events=queue.Queue(); self.config=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
        root.title('Mochi · voz + LLM (firmware 3.5)' if assistant else 'Mochi · Audio limpio (firmware 3.5)')
        root.geometry('800x760'); root.minsize(700,660)
        root.configure(bg='#101b24')
        style=ttk.Style(root); style.theme_use('clam')
        style.configure('TFrame',background='#101b24')
        style.configure('TLabel',background='#101b24',foreground='#edf6f4',font=('Segoe UI',11))
        style.configure('TCheckbutton',background='#101b24',foreground='#edf6f4',font=('Segoe UI',10))
        style.configure('TButton',font=('Segoe UI',10),padding=7)
        style.configure('TProgressbar',background='#82ead6',troughcolor='#243742')
        main=ttk.Frame(root,padding=22); main.pack(fill='both',expand=True)
        ttk.Label(main,text='Mochi escucha',font=('Segoe UI',22,'bold')).pack(anchor='w')
        ttk.Label(main,text='Habla cerca del INMP441: «Mochi, duerme»').pack(anchor='w',pady=(4,4))
        # Solo con el puente: estado del LLM (boton BOOT para hablar).
        self.llm_text=tk.StringVar(value='Conectando…')
        if assistant is not None:
            llmrow=ttk.Frame(main); llmrow.pack(fill='x',pady=(0,8))
            ttk.Label(llmrow,text='LLM:',font=('Segoe UI',11,'bold')).pack(side='left',padx=(0,8))
            ttk.Label(llmrow,textvariable=self.llm_text,wraplength=600).pack(side='left')
            # Hablarle o escribirle a Mochi sin tocar la placa.
            askrow=ttk.Frame(main); askrow.pack(fill='x',pady=(0,8))
            self.listen_button=ttk.Button(askrow,text='🎤 Hablarle a Mochi',command=self.listen,state='disabled')
            self.listen_button.pack(side='left')
            self.ask_entry=ttk.Entry(askrow,font=('Segoe UI',11))
            self.ask_entry.pack(side='left',fill='x',expand=True,padx=8)
            self.ask_entry.bind('<Return>',lambda event:self.ask())
            self.ask_button=ttk.Button(askrow,text='Enviar texto',command=self.ask,state='disabled')
            self.ask_button.pack(side='left')
        ttk.Frame(main).pack(pady=(0,8))
        row=ttk.Frame(main); row.pack(fill='x')
        ttk.Label(row,text='Puerto USB:').pack(side='left',padx=(0,8))
        self.port=ttk.Combobox(row,state='readonly',width=14); self.port.pack(side='left')
        ttk.Button(row,text='Actualizar',command=self.refresh).pack(side='left',padx=6)
        self.connect_button=ttk.Button(row,text='Conectar',command=self.connect); self.connect_button.pack(side='right')
        self.status=tk.StringVar(value='Conecta el USB de Mochi y selecciona su puerto.')
        ttk.Label(main,textvariable=self.status,wraplength=645).pack(anchor='w',pady=(18,8))
        self.meter=ttk.Progressbar(main,maximum=100); self.meter.pack(fill='x')
        ttk.Label(main,text='Nivel del micrófono de Mochi').pack(anchor='w',pady=(3,13))
        self.transcript=tk.StringVar(value='Aquí verás lo que entendí…')
        ttk.Label(main,textvariable=self.transcript,wraplength=645,font=('Segoe UI',13)).pack(anchor='w',pady=(0,10))
        row=ttk.Frame(main); row.pack(fill='x',pady=(0,10))
        self.test_button=ttk.Button(row,text='Probar sonido',command=lambda:self.send('T'),state='disabled')
        self.test_button.pack(side='left')
        self.usb_button=ttk.Button(row,text='Prueba tono USB',command=self.test_pc_usb,state='disabled')
        self.usb_button.pack(side='left',padx=6)
        self.pc_button=ttk.Button(row,text='Activar parlante PC',command=self.toggle_pc_audio,state='disabled')
        self.pc_button.pack(side='left',padx=7)
        self.pc_audio_active=False
        outrow=ttk.Frame(main); outrow.pack(fill='x',pady=(3,8))
        ttk.Label(outrow,text='Audio que sale de la PC:').pack(side='left',padx=(0,8))
        self.audio_combo=ttk.Combobox(outrow,state='readonly',width=48)
        self.audio_combo.pack(side='left',fill='x',expand=True)
        ttk.Button(outrow,text='Actualizar salidas',command=self.refresh_audio).pack(side='left',padx=6)
        self.audio_device_map={}
        self.pc_level=ttk.Progressbar(main,maximum=100)
        self.pc_level.pack(fill='x',pady=(0,2))
        ttk.Label(main,text='Nivel de musica/video de la PC (NO microfono)').pack(anchor='w',pady=(0,6))
        audrow=ttk.Frame(main); audrow.pack(fill='x',pady=(0,5))
        ttk.Label(audrow,text='Volumen de Mochi:').pack(side='left',padx=(0,8))
        self.volume_label=ttk.Label(audrow,text='100%',width=5)
        self.volume_label.pack(side='right')
        self.volume_scale=ttk.Scale(audrow,from_=30,to=170,orient='horizontal',command=self.update_volume)
        self.volume_scale.pack(side='left',fill='x',expand=True,padx=(0,8))
        self.volume_scale.set(70)
        eqrow=ttk.Frame(main); eqrow.pack(fill='x',pady=(0,8))
        ttk.Label(eqrow,text='Perfil de audio:').pack(side='left',padx=(0,8))
        self.eq_combo=ttk.Combobox(eqrow,state='readonly',values=PROFILES,width=30)
        self.eq_combo.set('Protegido (limpio)')
        self.eq_combo.pack(side='left')
        self.eq_combo.bind('<<ComboboxSelected>>',self.update_profile)
        ttk.Label(eqrow,text='Inicia a 70%; no aumentes si distorsiona.',font=('Segoe UI',9)).pack(side='left',padx=(12,0))
        # Estereo: necesita firmware 3.4 y el 2.o MAX98357A en el canal derecho.
        sterow=ttk.Frame(main); sterow.pack(fill='x',pady=(0,8))
        self.stereo_var=tk.BooleanVar(value=bool(self.config.get('stereo',False)))
        self.audio_controls.set_stereo(self.stereo_var.get())
        ttk.Checkbutton(sterow,text='Estéreo: izquierdo y derecho en dos parlantes',
            variable=self.stereo_var,command=self.update_stereo).pack(side='left')
        ttk.Label(sterow,text='Requiere firmware 3.4. "Prueba tono USB" suena primero a la izquierda y luego a la derecha.',
            font=('Segoe UI',9),wraplength=330).pack(side='left',padx=(12,0))
        self.auto_button=ttk.Button(row,text='Volver a automático',command=lambda:self.send('A'),state='disabled')
        self.auto_button.pack(side='left',padx=8)
        ttk.Label(main,text='Órdenes: duerme · despierta · feliz · sonido · graba · reproduce\nquieto · muévete · parpadea · guiño · curioso · sorpresa',wraplength=645).pack(anchor='w',pady=(0,10))
        self.log=tk.Text(main,height=7,bg='#192a35',fg='#d6e9e6',font=('Segoe UI',10),relief='flat',wrap='word',state='disabled')
        self.log.pack(fill='both',expand=True)
        # Linux: salida "Mochi" en el sonido del sistema (se quita al cerrar).
        if os.name!='nt':
            self.sink_module=ensure_mochi_sink()
            self.append('Salida de audio «Mochi» lista: elegila en el sonido del sistema (icono de volumen) '
                'o aca abajo, y pulsa Activar parlante PC. Lo que suene ahi sale por el parlante de Mochi.')
        self.refresh(preferred); self.refresh_audio()
        root.after(80,self.poll); root.protocol('WM_DELETE_WINDOW',self.close)
        if autoconnect and self.port.get(): root.after(300,self.connect)

    def update_volume(self,value):
        percent=int(round(float(value)))
        self.audio_controls.set_volume(percent)
        self.volume_label.configure(text=f'{percent}%')

    def update_profile(self,event=None):
        self.audio_controls.set_profile(self.eq_combo.get())

    def update_stereo(self):
        enabled=self.stereo_var.get()
        self.audio_controls.set_stereo(enabled)
        self.append('Salida '+('ESTÉREO: izquierdo y derecho por separado.' if enabled else 'MONO: los dos parlantes suenan igual.'))

    def refresh(self,preferred=None):
        from serial.tools import list_ports
        ports=list(list_ports.comports()); names=[p.device for p in ports]
        # --port con un puerto que no se lista (pty del simulador, alias udev).
        if preferred and preferred not in names: names.append(preferred)
        current=self.port.get(); self.port['values']=names
        native=[p.device for p in ports if p.vid==0x303a]
        chosen=preferred if preferred in names else current if current in names else native[0] if len(native)==1 else 'COM12' if 'COM12' in names else names[0] if len(names)==1 else ''
        self.port.set(chosen)

    def refresh_audio(self):
        try:
            outputs,default_id=audio_outputs()
            self.audio_device_map={f'{label} [{idx+1}]':devid for idx,(devid,label) in enumerate(outputs)}
            self.audio_combo['values']=list(self.audio_device_map)
            existing=self.audio_device_map.get(self.audio_combo.get())
            mochi='sc:'+MOCHI_SINK if 'sc:'+MOCHI_SINK in self.audio_device_map.values() else None
            selected=existing or mochi or default_id
            for label,devid in self.audio_device_map.items():
                if devid==selected:
                    self.audio_combo.set(label)
                    break
            self.append('Salida predeterminada (captura): '+str(next((name for did,name in outputs if did==default_id),'no detectada')))
            if any(x.startswith('pawp:') for x in self.audio_device_map.values()):
                self.append('Prueba primero AndroidTV (WASAPI alternativo). Elige la version SoundCard solo para comparar.')
        except Exception as exc:
            self.append('No pude enumerar las salidas de audio: '+str(exc))

    def connect(self):
        if self.session:
            self.session.close(); self.status.set('Desconectando…'); return
        selected=self.port.get()
        if not selected: self.status.set('Selecciona el puerto del ESP32.'); return
        self.session=VoiceSession(selected,self.events,self.config,self.model_path,
            audio_controls=self.audio_controls,assistant=self.assistant)
        self.connect_button.configure(text='Cancelar'); self.port.configure(state='disabled')
        self.session.start()

    def send(self,command):
        if self.session: self.session.send(command)

    def toggle_pc_audio(self):
        if self.session:
            if not self.pc_audio_active:
                device_id=self.audio_device_map.get(self.audio_combo.get())
                if not device_id:
                    self.append('Elige una salida de audio de la PC primero.')
                    return
                self.session.set_pc_audio(True,device_id)
            else: self.session.set_pc_audio(False)

    def listen(self):
        if self.session: self.session.listen_toggle()

    def ask(self):
        text=self.ask_entry.get().strip()
        if text and self.session:
            self.session.ask_text(text); self.ask_entry.delete(0,'end')

    def test_pc_usb(self):
        if self.session:
            if self.pc_audio_active:
                self.append('Primero pulsa Detener parlante PC.')
            else:
                self.session.test_usb_audio()

    def append(self,text):
        self.log.configure(state='normal'); self.log.insert('end',text+'\n')
        if int(self.log.index('end-1c').split('.')[0])>100: self.log.delete('1.0','20.0')
        self.log.see('end'); self.log.configure(state='disabled')

    def poll(self):
        while True:
            try: kind,value=self.events.get_nowait()
            except queue.Empty: break
            if kind=='status': self.status.set(value)
            elif kind=='transcript': self.transcript.set(value)
            elif kind=='level': self.meter['value']=value
            elif kind=='log': self.append(value)
            elif kind=='error': self.status.set(value); self.append('Aviso: '+value)
            elif kind=='pc_meter': self.pc_level['value']=value
            elif kind=='pc_state':
                self.pc_audio_active=value
                if not value: self.pc_level['value']=0
                self.pc_button.configure(text='Detener parlante PC' if value else 'Activar parlante PC')
            elif kind=='action': self.status.set('Orden recibida: '+value)
            elif kind=='llm': self.llm_text.set(value)
            elif kind=='listening' and self.assistant is not None:
                self.listen_button.configure(text='⏹ Enviar ya' if value else '🎤 Hablarle a Mochi')
            elif kind=='connected':
                state='normal' if value else 'disabled'
                self.test_button.configure(state=state); self.auto_button.configure(state=state)
                self.pc_button.configure(state=state)
                self.usb_button.configure(state=state)
                if self.assistant is not None:
                    self.listen_button.configure(state=state); self.ask_button.configure(state=state)
                if value: self.connect_button.configure(text='Desconectar')
                else: self.meter['value']=0
            elif kind=='finished':
                self.session=None; self.connect_button.configure(text='Conectar'); self.port.configure(state='readonly')
                if value: self.status.set('Desconectado. Pulsa Conectar para volver a escuchar.')
        self.root.after(80,self.poll)

    def close(self):
        if self.session: self.session.close()
        remove_mochi_sink(self.sink_module); self.sink_module=None
        self.root.destroy()

def run(port=None,model_path=None,assistant=None,autoconnect=False):
    """Abre la ventana. El puente del LLM la llama con su asistente."""
    root=tk.Tk()
    app=App(root,port,model_path,assistant=assistant,autoconnect=autoconnect)
    try: root.mainloop()
    finally: remove_mochi_sink(app.sink_module)

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--port'); parser.add_argument('--model')
    args=parser.parse_args(); run(args.port,args.model)
if __name__=='__main__': main()
