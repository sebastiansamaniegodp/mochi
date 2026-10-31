"""Órdenes limitadas a Mochi. Nunca ejecuta texto reconocido en la PC."""
from array import array
from dataclasses import dataclass
import math
import re
import sys
import time
import unicodedata

DEFAULT_WAKE=['mochi','moche','mochis','moci','muchi','mochila']
# «Noche» solo se acepta junto a «hola» para reducir activaciones accidentales.
DEFAULT_WAKE_PHRASES=['hola noche','hola mucho']
SPOKEN_COMMANDS=['duerme','dormir','a dormir','despierta','despertar','automático',
 'modo automático','feliz','sonríe','sonrisa','curioso','sorpréndete','sorpresa',
 'bosteza','bostezar','parpadea','parpadear','guiño','guiña','un guiño','sonido',
 'haz un sonido','graba','grabar','reproduce','reproducir','quieto','muévete','normal']

def normalize(text: str)->str:
    text=unicodedata.normalize('NFD',text.lower())
    text=''.join(c for c in text if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[a-z0-9]+',text))

COMMANDS={
    'duerme':('D','Dormir'), 'dormir':('D','Dormir'), 'a dormir':('D','Dormir'),
    'despierta':('A','Despertar / automático'), 'despertar':('A','Despertar / automático'),
    'automatico':('A','Animaciones automáticas'), 'modo automatico':('A','Animaciones automáticas'),
    'feliz':('F','Feliz'), 'sonrie':('F','Feliz'), 'sonrisa':('F','Feliz'),
    'curioso':('C','Curioso'), 'sorprendete':('O','Sorpresa'), 'sorpresa':('O','Sorpresa'),
    'bosteza':('S','Bostezar'), 'bostezar':('S','Bostezar'),
    'parpadea':('B','Parpadear'), 'parpadear':('B','Parpadear'),
    'guino':('G','Guiño'), 'guina':('G','Guiño'), 'un guino':('G','Guiño'),
    'sonido':('T','Sonido'), 'haz un sonido':('T','Sonido'),
    'graba':('R','Grabar 1,5 segundos'), 'grabar':('R','Grabar 1,5 segundos'),
    'reproduce':('P','Reproducir grabación'), 'reproducir':('P','Reproducir grabación'),
    'quieto':('X','Detener desplazamiento'), 'muevete':('Y','Activar desplazamiento'),
    'normal':('N','Expresión normal'),
}

@dataclass(frozen=True)
class Decision:
    command: str|None=None
    label: str=''
    awakened: bool=False
    wake_words: int=0   # palabras de la llamada («mochi»: 1, «hola mochi»: 2)

class CommandRouter:
    def __init__(self,wake_words=None,wake_phrases=None,window=5.0):
        self.words=[normalize(w) for w in (wake_words or DEFAULT_WAKE)]
        self.phrases=[normalize(w) for w in (wake_phrases or DEFAULT_WAKE_PHRASES)]
        self.window=window
        self.armed_until=0.0

    def reset(self): self.armed_until=0.0

    def process(self,text: str,now: float|None=None)->Decision:
        now=time.monotonic() if now is None else now
        clean=normalize(text)
        if not clean: return Decision()
        heard=False; called=0
        # La llamada debe empezar la frase, opcionalmente después de «hola».
        for prefix in sorted(self.phrases+[w for w in self.words]+['hola '+w for w in self.words],key=len,reverse=True):
            if clean==prefix or clean.startswith(prefix+' '):
                clean=clean[len(prefix):].strip(); heard=True; called=len(prefix.split()); break
        if heard: self.armed_until=now+self.window
        if not heard and (not self.armed_until or now>self.armed_until): return Decision()
        for suffix in (' por favor',' gracias'):
            if clean.endswith(suffix): clean=clean[:-len(suffix)].strip()
        if clean in COMMANDS:
            command,label=COMMANDS[clean]
            self.armed_until=0.0
            return Decision(command,label,heard,called)
        return Decision(awakened=heard,wake_words=called)

# Con la escucha del LLM, la gramática suma palabras comunes de órdenes para
# el LLM. Sin ellas, Vosk fuerza cualquier frase a una orden fija («Mochi,
# sube el volumen» -> «mochi duerme», con confianza 1,0); con ellas sale
# «mochi sube volumen» y va al LLM. Sin «está»: «a dormir» -> «está dormir».
LLM_WORDS='''qué cuál cómo cuándo dónde quién cuánto hora horas es son estás hay hace tiempo
clima día hoy mañana sube subí subir baja bajá bajar el la los las un una lo le me te se de
del al en con por para y o mi tu su volumen brillo música canción video videos youtube
spotify navegador chrome firefox ventana ventanas pestaña abre abrí abrir cierra cerrá
cerrar pon poné poner pone quita quitá pará pausa sigue seguí reproducí busca buscá buscar
dime decime decí cuenta contame explica explicame escribe escribí manda mandá tal gracias
favor sí no bien mal más menos todo nada algo cosa esto eso pc computadora compu pantalla
archivo archivos carpeta programa juego juegos aplicación siguiente anterior otra otro
primero último nuevo nueva'''.split()

def recognition_grammar(model,wake_words,wake_phrases,llm_words=False)->list[str]:
    """Solo palabras existentes en el vocabulario del modelo español."""
    known=lambda phrase: all(model.vosk_model_find_word(w)>=0 for w in phrase.split())
    commands=SPOKEN_COMMANDS
    activators=[normalize(w) for w in wake_words+wake_phrases]
    activators+=['hola '+w for w in activators if not w.startswith('hola ')]
    activators=[a for a in activators if known(a)]
    vocabulary={'[unk]'}
    for command in commands:
        if known(command): vocabulary.add(command)
    for activator in activators:
        vocabulary.add(activator)
        for command in commands:
            if known(command): vocabulary.add(activator+' '+command)
    if llm_words: vocabulary.update(w for w in LLM_WORDS if known(w))
    return sorted(vocabulary)

# ---- «Mochi» abre la escucha del LLM (micrófono siempre abierto) ----
# Confianza mínima de la llamada: más que la de las órdenes fijas, porque
# lo que siga va al LLM (y el LLM ejecuta herramientas de verdad).
WAKE_LLM_CONFIDENCE=0.65
# ---- Sensibilidad del micrófono (escucha de la orden) ----
# (factor del umbral de voz, fracción de la voz más fuerte oída): un bloque
# es voz si supera el umbral por el factor y esa fracción del pico. Así lo
# que suena lejos (gente hablando, la tele) no deja de ser silencio cuando
# ya se oyó la voz de cerca, y la escucha termina sola.
MIC_SENSITIVITY={'alta':(1.0,0.0),'normal':(1.6,0.2),'baja':(2.5,0.35)}

def mic_sensitivity(config):
    return MIC_SENSITIVITY.get((config or {}).get('mic_sensitivity'),MIC_SENSITIVITY['normal'])

# Audio guardado para no perder lo dicho justo después de «Mochi»: Vosk da
# el resultado recién cuando hay un silencio.
TAIL_SECONDS=8.0

def wake_call(result,decision):
    """(fin en segundos, confianza) de la llamada en un resultado de Vosk, o (None,0)."""
    words=result.get('result',[])[:decision.wake_words]
    if not decision.awakened or not words or len(words)<decision.wake_words: return None,0.0
    return float(words[-1].get('end',0.0)),sum(w.get('conf',0) for w in words)/len(words)

class AudioTail:
    """Últimos segundos de PCM s16le, ubicados con los tiempos de Vosk
    (segundos desde el último Reset del reconocedor)."""
    def __init__(self,seconds=TAIL_SECONDS,rate=16000):
        self.rate=rate; self.limit=int(seconds*rate)*2; self.reset()
    def reset(self): self.data=bytearray(); self.start=0
    def extend(self,pcm):
        self.data.extend(pcm)
        extra=len(self.data)-self.limit
        if extra>0: del self.data[:extra]; self.start+=extra
    def since(self,seconds):
        offset=int(seconds*self.rate)*2-self.start
        return bytes(self.data[max(0,offset):])

def voice_in(pcm,threshold,rate=16000):
    """(¿hay voz?, segundos de silencio al final) mirando bloques de 20 ms."""
    step=rate//50*2; heard=False; quiet=0.0
    for i in range(0,len(pcm)-step+1,step):
        samples=array('h'); samples.frombytes(pcm[i:i+step])
        if sys.byteorder!='little': samples.byteswap()
        rms=math.sqrt(sum(float(v)*v for v in samples)/len(samples))/32768
        if rms>threshold: heard=True; quiet=0.0
        else: quiet+=0.02
    return heard,quiet

def fixed_command(text,wake_words=None,wake_phrases=None):
    """(letra, nombre) si el texto es solo una orden fija, con o sin «Mochi»
    adelante («Mochi» ... «duerme» dicho en dos veces llega al LLM así)."""
    router=CommandRouter(wake_words,wake_phrases); router.armed_until=float('inf')
    decision=router.process(text,now=0.0)
    return (decision.command,decision.label) if decision.command else None

