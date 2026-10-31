#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>
namespace MochiFrame {
constexpr uint16_t MAX_PAYLOAD=384;
constexpr size_t HEADER_SIZE=16;
enum Type : uint8_t { PCM=1, HELLO=2, STATUS=3, TEXT=4 };
inline uint32_t crcUpdate(uint32_t crc,const uint8_t* data,size_t count) {
  for(size_t i=0;i<count;++i) {
    crc^=data[i];
    for(int bit=0;bit<8;++bit) crc=(crc>>1)^((crc&1)?0xEDB88320u:0);
  }
  return crc;
}
inline void put32(uint8_t* dst,uint32_t value) {
  for(int i=0;i<4;++i) dst[i]=static_cast<uint8_t>(value>>(8*i));
}
inline size_t encode(uint8_t* out,size_t capacity,uint8_t type,uint32_t sequence,
                     const void* payload,uint16_t size) {
  if(size>MAX_PAYLOAD || capacity<HEADER_SIZE+size || (size && !payload)) return 0;
  memcpy(out,"MCHI",4); out[4]=1; out[5]=type;
  out[6]=static_cast<uint8_t>(size); out[7]=static_cast<uint8_t>(size>>8);
  put32(out+8,sequence);
  if(size) memcpy(out+HEADER_SIZE,payload,size);
  uint32_t crc=crcUpdate(0xFFFFFFFFu,out+4,8);
  crc=crcUpdate(crc,out+HEADER_SIZE,size)^0xFFFFFFFFu;
  put32(out+12,crc); return HEADER_SIZE+size;
}
}
