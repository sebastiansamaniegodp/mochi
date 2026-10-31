"""Mochi Voz: usa exclusivamente el audio que recibe del ESP32 por USB."""
from pathlib import Path
import argparse
import array
import json
import math
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

from commands import CommandRouter,DEFAULT_WAKE,DEFAULT_WAKE_PHRASES,recognition_grammar
from model_setup import ensure_model
from protocol import Parser,PCM,HELLO,STATUS,TEXT
from pc_speaker import PCSpeaker,audio_outputs,frame_command
from audio_dsp import AudioControls, PROFILES

ROOT=Path(__file__).resolve().parent
USB_WRITE_TIMEOUT_SECONDS=2.0
USB_OPEN_SETTLE_SECONDS=1.0
USB_HANDSHAKE_SECONDS=10.0

def usb_timeout_message(port,connected):
    phase='Se interrumpió el envío a Mochi' if connected else 'No se pudo enviar la consulta inicial a Mochi'
    return (phase+' por '+port+'. Cierra el programa, pulsa RESET/EN sin mantener BOOT y vuelve a conectar. '
      'Si continúa, revisa el puerto USB nativo y carga el firmware con USB CDC On Boot: Enabled '
      'y USB Mode: Hardware CDC and JTAG.')

class VoiceSession:
    def __init__(self,port,events,config,model_path=None,audio_controls=None):
        self.port=port; self.events=events; self.config=config; self.model_path=model_path
        self.audio_controls=audio_controls or AudioControls()
        self.stop=threading.Event(); self.outgoing=queue.Queue()
        self.pc_audio_queue=queue.Queue(maxsize=12)
        self.pc_speaker=None
        self.thread=threading.Thread(target=self.run,daemon=True)
    def post(self,kind,value): self.events.put((kind,value))
    def start(self): self.thread.start()
    def send(self,command): self.outgoing.put(command.encode('ascii'))
    def set_pc_audio(self,active,device_id=None): self.outgoing.put(('pc_audio',bool(active),device_id))
    def test_usb_audio(self): self.outgoing.put(('pc_test',))
    def close(self):
        self.stop.set()
        if self.pc_speaker: self.pc_speaker.close()

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
                            connection.write(frame_command('K'))
                            self.pc_speaker=PCSpeaker(self.pc_audio_queue,self.events,mode='tone',controls=self.audio_controls)
                            self.pc_speaker.start()
                            self.post('pc_state',True)
                    elif isinstance(command,tuple) and command[0]=='pc_audio':
                        enabled=command[1]
                        if enabled and self.pc_speaker is None:
                            while not self.pc_audio_queue.empty():
                                try: self.pc_audio_queue.get_nowait()
                                except queue.Empty: break
                            connection.write(frame_command('K'))
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
                            connection.write(frame_command('J'))
                            self.post('pc_state',False)
                    else: connection.write(frame_command(command))
                if self.pc_speaker is not None:
                    if not self.pc_speaker.alive() and self.pc_speaker.failed:
                        self.post('log','Captura detenida: '+self.pc_speaker.failed)
                        self.pc_speaker=None
                        connection.write(frame_command('J')); self.post('pc_state',False)
                    elif (self.pc_speaker.mode=='tone' and not self.pc_speaker.alive()
                          and self.pc_audio_queue.empty()
                          and time.monotonic()-(self.pc_speaker.finished_at or 0)>0.5):
                        self.pc_speaker=None
                        connection.write(frame_command('J')); self.post('pc_state',False)
                        self.post('log','Prueba USB finalizada. Ahora prueba captura de Windows.')
                    else:
                        # Priorizar entrada del microfono y comandos; a lo sumo
                        # dos paquetes por vuelta, 40 ms de sonido.
                        for _ in range(2):
                            try: pcm=self.pc_audio_queue.get_nowait()
                            except queue.Empty: break
                            connection.write(pcm)
                # En modo audio no esperar 50 ms: llegan bloques cada 20 ms.
                connection.timeout=0.004 if self.pc_speaker is not None else 0.05
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
                            self.post('log','Audio limpio v3.3.8: PCM12 USB estable + filtro de graves en Windows. Firmware v3.3.7 compatible. Revisa BAD/under.')
                            if 'record_seconds' in info:
                                self.post('log','Grabación disponible: '+format(info['record_seconds'],'g')+' segundos.')
                    elif not connected:
                        continue
                    elif packet.kind==TEXT:
                        text=packet.payload.decode('utf-8',errors='replace').strip()
                        if not text.startswith('Mic RMS='): self.post('log',text)
                    elif packet.kind==STATUS:
                        status_info=json.loads(packet.payload.decode('utf-8'))
                        state=status_info.get('audio','idle')
                        busy=state in ('playing','recording','streaming')
                        block.clear(); recognizer.Reset(); router.reset(); last_partial=''
                        record_seconds=status_info.get('record_seconds',1.5)
                        self.post('status',{'playing':'Reproduciendo: escucharé al terminar',
                          'recording':'GRABANDO '+format(record_seconds,'g')+' segundos: habla ahora',
                          'streaming':'Parlante PC activo: musica, videos y sonidos de Windows',
                          'idle':'Escuchando a través del micrófono de Mochi'}.get(state,state))
                    elif packet.kind==PCM and not busy:
                        if len(packet.payload)%2: continue
                        samples=array.array('h'); samples.frombytes(packet.payload)
                        if sys.byteorder!='little': samples.byteswap()
                        if samples and time.monotonic()-last_level>0.1:
                            rms=math.sqrt(sum(float(v)*v for v in samples)/len(samples))/32768
                            self.post('level',min(100.0,rms*1500)); last_level=time.monotonic()
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
            self.post('pc_state',False)
            if connection is not None:
                try:
                    if connection.is_open and connected and not link_failed: connection.write(frame_command('Q'))
                except Exception: pass
                connection.close()
            self.post('connected',False)
            self.post('finished',self.stop.is_set())

class App:
    def __init__(self,root,preferred=None,model_path=None):
        self.root=root; self.model_path=model_path; self.session=None
        self.audio_controls=AudioControls(percent=70,profile='Protegido (limpio)')
        self.events=queue.Queue(); self.config=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
        root.title('Mochi · Audio limpio v3.3.8 (PCM12 estable)'); root.geometry('800x730'); root.minsize(700,640)
        root.configure(bg='#101b24')
        style=ttk.Style(root); style.theme_use('clam')
        style.configure('TFrame',background='#101b24')
        style.configure('TLabel',background='#101b24',foreground='#edf6f4',font=('Segoe UI',11))
        style.configure('TButton',font=('Segoe UI',10),padding=7)
        style.configure('TProgressbar',background='#82ead6',troughcolor='#243742')
        main=ttk.Frame(root,padding=22); main.pack(fill='both',expand=True)
        ttk.Label(main,text='Mochi escucha',font=('Segoe UI',22,'bold')).pack(anchor='w')
        ttk.Label(main,text='Habla cerca del INMP441: «Mochi, duerme»').pack(anchor='w',pady=(4,16))
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
        ttk.Label(outrow,text='Audio que sale de Windows:').pack(side='left',padx=(0,8))
        self.audio_combo=ttk.Combobox(outrow,state='readonly',width=48)
        self.audio_combo.pack(side='left',fill='x',expand=True)
        ttk.Button(outrow,text='Actualizar salidas',command=self.refresh_audio).pack(side='left',padx=6)
        self.audio_device_map={}
        self.pc_level=ttk.Progressbar(main,maximum=100)
        self.pc_level.pack(fill='x',pady=(0,2))
        ttk.Label(main,text='Nivel de musica/video de Windows (NO microfono)').pack(anchor='w',pady=(0,6))
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
        self.auto_button=ttk.Button(row,text='Volver a automático',command=lambda:self.send('A'),state='disabled')
        self.auto_button.pack(side='left',padx=8)
        ttk.Label(main,text='Órdenes: duerme · despierta · feliz · sonido · graba · reproduce\nquieto · muévete · parpadea · guiño · curioso · sorpresa',wraplength=645).pack(anchor='w',pady=(0,10))
        self.log=tk.Text(main,height=7,bg='#192a35',fg='#d6e9e6',font=('Segoe UI',10),relief='flat',wrap='word',state='disabled')
        self.log.pack(fill='both',expand=True)
        self.refresh(preferred); self.refresh_audio()
        root.after(80,self.poll); root.protocol('WM_DELETE_WINDOW',self.close)

    def update_volume(self,value):
        percent=int(round(float(value)))
        self.audio_controls.set_volume(percent)
        self.volume_label.configure(text=f'{percent}%')

    def update_profile(self,event=None):
        self.audio_controls.set_profile(self.eq_combo.get())

    def refresh(self,preferred=None):
        from serial.tools import list_ports
        ports=list(list_ports.comports()); names=[p.device for p in ports]
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
            selected=existing or default_id
            for label,devid in self.audio_device_map.items():
                if devid==selected:
                    self.audio_combo.set(label)
                    break
            self.append('Salida predeterminada Windows (captura): '+str(next((name for did,name in outputs if did==default_id),'no detectada')))
            if any(x.startswith('pawp:') for x in self.audio_device_map.values()):
                self.append('Prueba primero AndroidTV (WASAPI alternativo). Elige la version SoundCard solo para comparar.')
        except Exception as exc:
            self.append('No pude enumerar las salidas Windows: '+str(exc))

    def connect(self):
        if self.session:
            self.session.close(); self.status.set('Desconectando…'); return
        selected=self.port.get()
        if not selected: self.status.set('Selecciona el puerto del ESP32.'); return
        self.session=VoiceSession(selected,self.events,self.config,self.model_path,audio_controls=self.audio_controls)
        self.connect_button.configure(text='Cancelar'); self.port.configure(state='disabled')
        self.session.start()

    def send(self,command):
        if self.session: self.session.send(command)

    def toggle_pc_audio(self):
        if self.session:
            if not self.pc_audio_active:
                device_id=self.audio_device_map.get(self.audio_combo.get())
                if not device_id:
                    self.append('Elige una salida de audio de Windows primero.')
                    return
                self.session.set_pc_audio(True,device_id)
            else: self.session.set_pc_audio(False)

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
            elif kind=='connected':
                state='normal' if value else 'disabled'
                self.test_button.configure(state=state); self.auto_button.configure(state=state)
                self.pc_button.configure(state=state)
                self.usb_button.configure(state=state)
                if value: self.connect_button.configure(text='Desconectar')
                else: self.meter['value']=0
            elif kind=='finished':
                self.session=None; self.connect_button.configure(text='Conectar'); self.port.configure(state='readonly')
                if value: self.status.set('Desconectado. Pulsa Conectar para volver a escuchar.')
        self.root.after(80,self.poll)

    def close(self):
        if self.session: self.session.close()
        self.root.destroy()

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--port'); parser.add_argument('--model')
    args=parser.parse_args(); root=tk.Tk(); App(root,args.port,args.model); root.mainloop()
if __name__=='__main__': main()
