"""Órdenes limitadas a Mochi. Nunca ejecuta texto reconocido en la PC."""
from dataclasses import dataclass
import re
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
        heard=False
        # La llamada debe empezar la frase, opcionalmente después de «hola».
        for prefix in sorted(self.phrases+[w for w in self.words]+['hola '+w for w in self.words],key=len,reverse=True):
            if clean==prefix or clean.startswith(prefix+' '):
                clean=clean[len(prefix):].strip(); heard=True; break
        if heard: self.armed_until=now+self.window
        if not heard and (not self.armed_until or now>self.armed_until): return Decision()
        for suffix in (' por favor',' gracias'):
            if clean.endswith(suffix): clean=clean[:-len(suffix)].strip()
        if clean in COMMANDS:
            command,label=COMMANDS[clean]
            self.armed_until=0.0
            return Decision(command,label,heard)
        return Decision(awakened=heard)

def recognition_grammar(model,wake_words,wake_phrases)->list[str]:
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
    return sorted(vocabulary)
