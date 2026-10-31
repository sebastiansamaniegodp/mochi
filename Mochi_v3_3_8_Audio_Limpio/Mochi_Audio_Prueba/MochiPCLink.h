#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>
// Protocolo PC->Mochi: COBS sobre [A5 5A | C/A/B | len16 | datos | CRC16] + 00.
// A = PCM16 (640 bytes); B = PCM12 empaquetado (480 bytes, sin compresion con perdida perceptible en este parlante).
// 00 es un delimitador reservado: la sincronizacion se recupera en cada paquete.
// Ningun byte de musica se interpreta como orden aun con perdida parcial USB.
namespace MochiPCLink {
constexpr size_t MAX_DATA=640;
constexpr size_t MAX_RAW=2+1+2+MAX_DATA+2;
constexpr size_t MAX_ENCODED=MAX_RAW+MAX_RAW/254+2;
inline uint16_t crcStep(uint16_t crc,uint8_t value) {
  crc^=static_cast<uint16_t>(value)<<8;
  for (int i=0;i<8;++i) crc=(crc&0x8000) ? static_cast<uint16_t>((crc<<1)^0x1021) : static_cast<uint16_t>(crc<<1);
  return crc;
}
class Decoder {
  uint8_t encoded[MAX_ENCODED]{};
  size_t used=0;
  bool overflow=false;
  uint8_t raw[MAX_RAW]{};
  bool decode() {
    size_t src=0,dst=0;
    while (src<used) {
      uint8_t code=encoded[src++];
      if (!code || src+code-1>used || dst+code-1>MAX_RAW) return false;
      for (uint16_t n=1;n<code;++n) raw[dst++]=encoded[src++];
      if (code<0xFF && src<used) {
        if (dst>=MAX_RAW) return false;
        raw[dst++]=0;
      }
    }
    if (dst<7 || raw[0]!=0xA5 || raw[1]!=0x5A) return false;
    const uint8_t msgType=raw[2];
    const uint16_t len=static_cast<uint16_t>(raw[3]) | (static_cast<uint16_t>(raw[4])<<8);
    if ((msgType!='C' && msgType!='A' && msgType!='B')
        || len!=(msgType=='C'?1:(msgType=='B'?480:640))
        || dst!=static_cast<size_t>(7+len)) return false;
    uint16_t crc=0xFFFF;
    for (size_t i=2;i<static_cast<size_t>(5+len);++i) crc=crcStep(crc,raw[i]);
    uint16_t received=static_cast<uint16_t>(raw[5+len]) | (static_cast<uint16_t>(raw[6+len])<<8);
    if (crc!=received) return false;
    type=msgType; length=len;
    memcpy(buffer,raw+5,len);
    return true;
  }
public:
  volatile uint32_t goodAudio=0, badFrames=0;
  uint8_t type=0;
  uint16_t length=0;
  uint8_t buffer[MAX_DATA]{};
  void reset() { used=0; overflow=false; }
  bool feed(uint8_t value) {
    if (value) {
      if (used<MAX_ENCODED && !overflow) encoded[used++]=value;
      else overflow=true;
      return false;
    }
    const bool hadData=overflow || used>0;
    const bool valid=!overflow && used>0 && decode();
    if (valid && (type=='A' || type=='B')) ++goodAudio;
    if (hadData && !valid) ++badFrames;
    reset();
    return valid;
  }
};
} // namespace MochiPCLink
