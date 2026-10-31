"""DSP ligero de audio para un solo parlante. No modifica el audio del microfono.

Filtro anti-aliasing en el remuestreo a 16k, perfiles de ecualizacion,
ganancia de software y limitador de picos. El limitador digital no aumenta
la potencia maxima ni sustituye protecciones termicas del parlante.
"""
import math
import threading

PROFILES=("Protegido (limpio)", "Protegido (antiraspado)", "Directo (diagnostico)", "Equilibrado", "Musica", "Voces claras", "Mas volumen")

class AudioControls:
    def __init__(self, percent=100, profile="Equilibrado", stereo=False):
        self._lock=threading.Lock()
        self._percent=int(percent)
        self._profile=profile
        self._stereo=bool(stereo)
    def set_volume(self,percent):
        with self._lock:
            self._percent=max(30,min(170,int(float(percent))))
    def set_profile(self,profile):
        if profile not in PROFILES: raise ValueError("Perfil de audio desconocido")
        with self._lock: self._profile=profile
    def snapshot(self):
        with self._lock: return self._percent,self._profile
    # Estereo: L al parlante del 1.er MAX98357A, R al del 2.o (firmware 3.4+).
    def set_stereo(self,enabled):
        with self._lock: self._stereo=bool(enabled)
    def stereo(self):
        with self._lock: return self._stereo

class _Biquad:
    """Filtro IIR de dos polos, estado continuo entre bloques de 20 ms."""
    def __init__(self, kind, freq, rate):
        omega=2*math.pi*float(freq)/float(rate)
        cos=math.cos(omega)
        alpha=math.sin(omega)/(2*math.sqrt(0.5))  # Butterworth, Q=1/sqrt(2)
        if kind=='highpass':
            b0=(1+cos)/2; b1=-(1+cos); b2=(1+cos)/2
        else:
            b0=(1-cos)/2; b1=1-cos; b2=(1-cos)/2
        a0=1+alpha
        self.b0=b0/a0; self.b1=b1/a0; self.b2=b2/a0
        self.a1=-2*cos/a0; self.a2=(1-alpha)/a0
        self.s1=0.0; self.s2=0.0

    def apply(self,data):
        import numpy as np
        dest=np.empty_like(data)
        b0,b1,b2,a1,a2=self.b0,self.b1,self.b2,self.a1,self.a2
        s1,s2=self.s1,self.s2
        for i,v in enumerate(data):
            x=float(v)
            y=b0*x+s1
            s1=b1*x-a1*y+s2
            s2=b2*x-a2*y
            dest[i]=y
        self.s1=s1;self.s2=s2
        return dest

class AudioProcessor:
    """DSP con estado entre paquetes de 20 ms."""
    def __init__(self, controls=None, rate=16000):
        self.controls=controls or AudioControls()
        self.rate=int(rate)
        self.low=0.0
        self.treble=0.0
        self.limit_envelope=0.0
        self.comp_envelope=0.0
        # Coeficientes estables entre buffers para evitar pops.
        self.treble_a=math.exp(-2*math.pi*1600/self.rate)
        self.peak_release=math.exp(-1/(0.10*self.rate))
        self.rms_release=math.exp(-1/(0.20*self.rate))
        self.last_peak=0.0
        self.protected_profile=None
        self.protected_hp=None
        self.protected_lp=None
        self.protected_gain=1.0
        self.profile_fade_samples=0
    def to_pcm16(self,samples):
        import numpy as np
        data=np.nan_to_num(np.asarray(samples,dtype=np.float32),nan=0.0,posinf=0.0,neginf=0.0)
        percent,profile=self.controls.snapshot()
        if profile in ("Protegido (limpio)","Protegido (antiraspado)"):
            # Sin aumento artificial de graves ni compresion que infle picos.
            # Mantener 16 kHz y PCM12; no cambia la carga del USB ni el firmware.
            if self.protected_profile!=profile:
                self.protected_profile=profile
                high_cut=220.0 if profile=="Protegido (limpio)" else 320.0
                low_cut=5700.0 if profile=="Protegido (limpio)" else 5200.0
                self.protected_hp=_Biquad('highpass',high_cut,self.rate)
                self.protected_lp=_Biquad('lowpass',low_cut,self.rate)
                self.protected_gain=1.0
                self.profile_fade_samples=128  # Evita chasquido al seleccionar el perfil
            filtered=self.protected_lp.apply(self.protected_hp.apply(data))
            # Es un control conservador del nivel electrico, NO mas potencia del amplificador.
            strength=0.82 if profile=="Protegido (limpio)" else 0.68
            filtered*=min(100,max(0,percent))/100.0*strength
            peak=float(np.max(np.abs(filtered))) if len(filtered) else 0.0
            goal=min(1.0,0.70/max(peak,1e-9))
            # Reaccion inmediata ante picos, recuperacion lenta para evitar bombeo.
            if goal<self.protected_gain:
                self.protected_gain=goal
            else:
                self.protected_gain=min(goal,self.protected_gain+0.035)
            filtered*=self.protected_gain
            if self.profile_fade_samples and len(filtered):
                count=min(len(filtered),self.profile_fade_samples)
                starting=128-self.profile_fade_samples
                filtered[:count]*=(np.arange(starting+1,starting+count+1,dtype=np.float32)/128.0)
                self.profile_fade_samples-=count
            self.last_peak=float(np.max(np.abs(filtered))) if len(filtered) else 0.0
            return (np.clip(filtered,-0.70,0.70)*32767.0).astype('<i2').tobytes()
        if profile=="Directo (diagnostico)":
            # Sin EQ/compresion/limitador dinamico: permite comprobar si el
            # procesamiento provoca aspereza. Mantener ganancia conservadora.
            factor=min(percent,100)/100.0
            direct=np.clip(data*factor,-0.90,0.90)
            self.last_peak=float(np.max(np.abs(direct))) if len(direct) else 0.0
            return (direct*32767.0).astype('<i2').tobytes()
        # Proteccion de graves muy bajos para un parlante pequeno.
        cutoff={"Equilibrado":85.0,"Musica":95.0,"Voces claras":155.0,"Mas volumen":125.0}[profile]
        presence_db={"Equilibrado":0.0,"Musica":1.2,"Voces claras":2.5,"Mas volumen":1.0}[profile]
        boost=10**(presence_db/20)-1
        low_a=math.exp(-2*math.pi*cutoff/self.rate)
        hp_prev=self.low
        hi_prev=self.treble
        processed=np.empty_like(data)
        for i,value in enumerate(data):
            x=float(value)
            hp_prev=(1-low_a)*x + low_a*hp_prev
            high=x-hp_prev
            # Gradiente suave desde ~1.6k, enfasis de presencia controlado.
            hi_prev=(1-self.treble_a)*high + self.treble_a*hi_prev
            processed[i]=high + boost*(high-hi_prev)
        self.low=hp_prev
        self.treble=hi_prev
        volume=percent/100.0
        if profile=="Mas volumen":
            # Compresion suave y pequeno realce de salida para elevar nivel percibido.
            volume*=1.12
            rms=float(np.sqrt(np.mean(processed.astype(np.float64)**2))) if len(processed) else 0.0
            self.comp_envelope=max(rms,self.comp_envelope*self.rms_release**len(processed))
            threshold=0.20
            if self.comp_envelope>threshold:
                compressed_level=threshold*(self.comp_envelope/threshold)**(1/2.0)
                volume*=compressed_level/self.comp_envelope
        else:
            self.comp_envelope*=self.rms_release**len(processed)
        processed*=volume
        # Limitador sample-by-sample: pico absoluto <= 0.88 FS.
        # Disminuye la ganancia en los picos y se recupera gradualmente.
        limit=0.88
        env=self.limit_envelope
        for i in range(len(processed)):
            sample=float(processed[i])
            env=max(abs(sample), env*self.peak_release)
            processed[i]=sample*min(1.0,limit/max(env,1e-10))
        self.limit_envelope=env
        # Truncamiento final de seguridad ante redondeos de coma flotante.
        self.last_peak=float(np.max(np.abs(processed))) if len(processed) else 0.0
        pcm=(np.clip(processed,-limit,limit)*32767.0).astype('<i2')
        return pcm.tobytes()

class FIRDownsampleFilter:
    """LPF FIR (ventana Hamming) para evitar aliasing al bajar de 44.1/48k a 16k."""
    def __init__(self,in_rate,out_rate,taps=63):
        import numpy as np
        self.enabled=in_rate>out_rate
        if not self.enabled: return
        fc=0.43*float(out_rate)/float(in_rate)
        indexes=np.arange(taps,dtype=np.float64)-(taps-1)/2
        kernel=2*fc*np.sinc(2*fc*indexes)*np.hamming(taps)
        kernel=kernel/kernel.sum()
        self.kernel=kernel.astype(np.float32)
        self.tail=np.zeros(taps-1,dtype=np.float32)
    def feed(self,samples):
        import numpy as np
        a=np.asarray(samples,dtype=np.float32).reshape(-1)
        if not self.enabled or a.size==0: return a
        combined=np.concatenate((self.tail,a))
        result=np.convolve(combined,self.kernel,mode='valid').astype(np.float32)
        self.tail=combined[-(self.kernel.size-1):]
        return result
