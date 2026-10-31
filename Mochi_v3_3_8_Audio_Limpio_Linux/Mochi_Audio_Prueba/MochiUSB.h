#pragma once
#include <Arduino.h>
#include <stdarg.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/queue.h>
#include "MochiFrame.h"

// Un unico emisor separa audio/mensajes y limita las esperas del USB.
namespace MochiUSB {
struct Packet { uint8_t type; uint16_t size; uint8_t data[MochiFrame::MAX_PAYLOAD]; };
static QueueHandle_t queue=nullptr;
static portMUX_TYPE lock=portMUX_INITIALIZER_UNLOCKED;
static bool streaming=false,ready=false;
static uint32_t hostSeen=0,dropped=0;
inline void heartbeat() {
  portENTER_CRITICAL(&lock); hostSeen=millis(); portEXIT_CRITICAL(&lock);
}
inline void stream(bool enabled) {
  portENTER_CRITICAL(&lock); streaming=enabled; hostSeen=millis(); portEXIT_CRITICAL(&lock);
}
inline bool streamEnabled() {
  portENTER_CRITICAL(&lock);
  if(streaming && millis()-hostSeen>8000) streaming=false;
  const bool active=streaming;
  portEXIT_CRITICAL(&lock); return active;
}
inline uint32_t droppedPackets() {
  portENTER_CRITICAL(&lock); const uint32_t n=dropped; portEXIT_CRITICAL(&lock); return n;
}
inline void countDrop() {
  portENTER_CRITICAL(&lock); ++dropped; portEXIT_CRITICAL(&lock);
}
inline bool emit(uint8_t type,const void* data,size_t size) {
  if(!ready || !queue || size>MochiFrame::MAX_PAYLOAD || (size && !data)) return false;
  Packet packet={}; packet.type=type; packet.size=size;
  if(size) memcpy(packet.data,data,size);
  if(xQueueSend(queue,&packet,0)!=pdTRUE) { countDrop(); return false; }
  return true;
}
inline void log(const char* message) { emit(MochiFrame::TEXT,message,strlen(message)); }
inline void logf(const char* format,...) {
  char buffer[320]; va_list args; va_start(args,format);
  vsnprintf(buffer,sizeof(buffer),format,args); va_end(args); log(buffer);
}
inline void state(const char* state) {
  char data[64]; snprintf(data,sizeof(data),"{\"audio\":\"%s\"}",state);
  emit(MochiFrame::STATUS,data,strlen(data));
}
inline void pcm(const int16_t* samples,size_t count) {
  if(streamEnabled()) emit(MochiFrame::PCM,samples,count*sizeof(int16_t));
}
inline bool write(const uint8_t* data,size_t count) {
  const uint32_t started=millis(); size_t sent=0;
  while(sent<count && millis()-started<40) {
    if(!Serial) return false;
    int space=Serial.availableForWrite();
    if(space>0) {
      size_t block=count-sent;
      if(block>static_cast<size_t>(space)) block=space;
      const size_t written=Serial.write(data+sent,block);
      sent+=written;
      if(!written) vTaskDelay(1);
    } else vTaskDelay(1);
  }
  return sent==count;
}
inline void worker(void*) {
  Packet packet; uint8_t data[MochiFrame::HEADER_SIZE+MochiFrame::MAX_PAYLOAD];
  uint32_t sequence=0;
  while(true) {
    if(xQueueReceive(queue,&packet,pdMS_TO_TICKS(100))==pdTRUE) {
      if(!Serial) { countDrop(); continue; }
      size_t size=MochiFrame::encode(data,sizeof(data),packet.type,sequence++,packet.data,packet.size);
      if(!write(data,size)) countDrop();
      vTaskDelay(1);
    }
  }
}
inline bool begin() {
  queue=xQueueCreate(12,sizeof(Packet));
  ready=queue && xTaskCreate(worker,"mochi_usb",4096,nullptr,1,nullptr)==pdPASS;
  if(!ready && queue) { vQueueDelete(queue); queue=nullptr; }
  return ready;
}
}
