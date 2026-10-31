"""Framing compartido con MochiFrame.h; todos los enteros little endian.

ESP32->PC: paquetes MCHI (este archivo). PC->ESP32: COBS con CRC16
(frame_packet en pc_speaker.py, igual a MochiPCLink.h del firmware).
Si se cambia algo aca, cambiarlo tambien en firmware/Mochi/.
"""
from dataclasses import dataclass
import struct
import zlib

MAGIC=b'MCHI'
VERSION=1
PCM,HELLO,STATUS,TEXT=1,2,3,4
HEADER=struct.Struct('<4sBBHII')
MAX_PAYLOAD=384

@dataclass(frozen=True)
class Packet:
    kind: int
    sequence: int
    payload: bytes

def encode(kind: int,payload: bytes,sequence: int=0)->bytes:
    if kind not in (PCM,HELLO,STATUS,TEXT) or len(payload)>MAX_PAYLOAD:
        raise ValueError('Paquete inválido')
    fields=struct.pack('<BBHI',VERSION,kind,len(payload),sequence & 0xffffffff)
    checksum=zlib.crc32(fields+payload) & 0xffffffff
    return MAGIC+fields+struct.pack('<I',checksum)+payload

class Parser:
    def __init__(self):
        self.buffer=bytearray()
        self.bad_crc=0

    def feed(self,data: bytes)->list[Packet]:
        self.buffer.extend(data)
        result=[]
        while True:
            start=self.buffer.find(MAGIC)
            if start<0:
                if len(self.buffer)>3: del self.buffer[:-3]
                break
            if start: del self.buffer[:start]
            if len(self.buffer)<HEADER.size: break
            _,version,kind,size,sequence,expected=HEADER.unpack_from(self.buffer)
            if version!=VERSION or kind not in (PCM,HELLO,STATUS,TEXT) or size>MAX_PAYLOAD:
                del self.buffer[0]
                continue
            end=HEADER.size+size
            if len(self.buffer)<end: break
            payload=bytes(self.buffer[HEADER.size:end])
            actual=zlib.crc32(bytes(self.buffer[4:12])+payload) & 0xffffffff
            if actual!=expected:
                self.bad_crc+=1
                del self.buffer[0]
                continue
            result.append(Packet(kind,sequence,payload))
            del self.buffer[:end]
        return result

# Estados de la cara para el LLM (firmware 3.5): se mandan como orden C
# con el digito ('0'..'5'). Iguales a LLM_IDLE... en firmware/Mochi/Mochi.ino.
LLM_IDLE,LLM_LISTENING,LLM_THINKING,LLM_SPEAKING,LLM_DONE,LLM_ERROR=range(6)
