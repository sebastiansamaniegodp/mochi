#pragma once
#include <Arduino.h>
#include <esp_arduino_version.h>
#include <esp_heap_caps.h>
#include <driver/gpio.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <math.h>

#if !defined(CONFIG_IDF_TARGET_ESP32S3)
#error "Selecciona ESP32S3 Dev Module, con S3."
#endif
#if ESP_ARDUINO_VERSION_MAJOR < 3
#error "Usa el paquete esp32 3.x de Espressif Systems."
#endif
#include <driver/i2s_std.h>
#include "MochiUSB.h"

// Audio local y PCM por USB; el reconocimiento de palabras se hace en la PC.
// Los controladores I2S pueden iniciar aunque el modulo este desconectado.
namespace AudioMochi {
constexpr int MIC_SCK=4, MIC_WS=5, MIC_SD=6;
constexpr int AMP_BCLK=16, AMP_LRC=17, AMP_DIN=18;
constexpr uint32_t SAMPLE_RATE=16000, CLIP_SECONDS=6;
constexpr uint32_t CLIP_SAMPLES=SAMPLE_RATE*CLIP_SECONDS; // 6 s; 192 kB.
constexpr size_t INTERNAL_RESERVE=32768;
constexpr size_t BLOCK_FRAMES=128;
constexpr float MIC_GAIN=8.0f, TONE_VOLUME=0.035f, PLAYBACK_VOLUME=0.08f;
enum class State : uint8_t { Idle, Recording, Playing, Streaming };
struct Snapshot {
  State state=State::Idle;
  float inputRms=0, outputLevel=0;
  uint32_t clipSamples=0, clipCapacity=0, readErrors=0;
  int peak=0;
  bool micReady=false, ampReady=false, bufferReady=false, workerReady=false;
};
static portMUX_TYPE lock=portMUX_INITIALIZER_UNLOCKED;
static Snapshot shared;
static char pending=0;
static i2s_chan_handle_t rx=nullptr, tx=nullptr;
static int16_t* clip=nullptr;
static uint32_t clipLength=0, clipCapacity=0;
static float dc=0;
// Tramas PCM 16 kHz, 16 bits, L/R intercalados. Un bloque equivale a 20 ms.
// Los paquetes mono (A/B) se copian a los dos canales; los S traen L y R.
constexpr size_t PC_FRAMES=320;
struct PCBlock { int16_t data[PC_FRAMES*2]; };
static QueueHandle_t pcQueue=nullptr;
// Diagnostico de la ruta USB -> PCM -> I2S, contadores desde arranque.
static volatile uint32_t pcDropped=0, pcPlayed=0, pcNonzero=0, pcI2sErrors=0;
// Subidas de nivel en la cola e interrupciones por falta de audio USB.
static volatile uint32_t pcUnderruns=0, pcRebuffers=0, pcHighWater=0;
constexpr size_t PC_PREBUFFER_BLOCKS=8; // 160 ms antes de comenzar a reproducir.



inline Snapshot snapshot() {
  portENTER_CRITICAL(&lock);
  const Snapshot copy=shared;
  portEXIT_CRITICAL(&lock);
  return copy;
}
inline int16_t boundedSample(float value) {
  if (value>32767) return 32767;
  if (value<-32768) return -32768;
  return static_cast<int16_t>(value);
}
inline void setState(State state,float output=0) {
  portENTER_CRITICAL(&lock);
  shared.state=state; shared.outputLevel=output;
  portEXIT_CRITICAL(&lock);
}
inline bool request(char command) {
  portENTER_CRITICAL(&lock);
  const bool possible=shared.workerReady && shared.state==State::Idle && pending==0
    && ((command=='T' && shared.ampReady)
      || (command=='R' && shared.micReady && shared.bufferReady)
      || (command=='P' && shared.ampReady && shared.clipSamples>0));
  if (possible) pending=command;
  portEXIT_CRITICAL(&lock);
  return possible;
}
inline bool requestPC(bool enable) {
  portENTER_CRITICAL(&lock);
  const bool possible=shared.workerReady && shared.ampReady && pcQueue && pending==0
    && (enable ? shared.state==State::Idle : shared.state==State::Streaming);
  if (possible) pending=enable?'K':'J';
  portEXIT_CRITICAL(&lock);
  return possible;
}
// Solo la tarea receptora USB llama a pushPC*: los bloques de trabajo son
// estaticos para no ocupar 2,5 kB de su pila (un bloque estereo mide 1280 bytes).
static PCBlock pcIncoming, pcDiscard;
// Si Windows va mas rapido que la salida I2S, eliminar el bloque mas antiguo
// permite recuperar sincronizacion y evita una latencia creciente.
inline bool queuePC(const PCBlock& frame) {
  if (xQueueSend(pcQueue,&frame,0)==pdTRUE) {
    const uint32_t depth=uxQueueMessagesWaiting(pcQueue);
    if (depth>pcHighWater) pcHighWater=depth;
    return true;
  }
  ++pcDropped;
  xQueueReceive(pcQueue,&pcDiscard,0);
  return xQueueSend(pcQueue,&frame,0)==pdTRUE;
}
// Copia un bloque mono a los dos canales, en el mismo lugar: de atras hacia
// adelante, para no pisar muestras que todavia no se copiaron.
inline void monoToStereo(PCBlock& frame) {
  for (size_t i=PC_FRAMES;i-->0;) frame.data[2*i]=frame.data[2*i+1]=frame.data[i];
}
inline bool pushPC(const uint8_t* bytes,size_t size) {
  if (!pcQueue || !bytes || size!=PC_FRAMES*sizeof(int16_t)) return false;
  memcpy(pcIncoming.data,bytes,size);
  monoToStereo(pcIncoming);
  return queuePC(pcIncoming);
}
// PCM12: cada par de muestras ocupa 3 bytes. La perdida de los 4 bits menos
// significativos deja un paso de cuantizacion de 16 niveles (~72 dB de rango),
// por debajo de los limites del parlante.
inline void unpackPCM12(const uint8_t* bytes,size_t pairs,int16_t* out) {
  for (size_t i=0;i<pairs;++i) {
    const size_t p=3*i;
    uint16_t a=static_cast<uint16_t>(bytes[p]) | (static_cast<uint16_t>(bytes[p+1]&0x0F)<<8);
    uint16_t b=static_cast<uint16_t>(bytes[p+1]>>4) | (static_cast<uint16_t>(bytes[p+2])<<4);
    // Sign extension 12bit -> 16bit sin desplazamientos izquierdos con signo.
    out[2*i]  =static_cast<int16_t>(static_cast<uint16_t>(a<<4));
    out[2*i+1]=static_cast<int16_t>(static_cast<uint16_t>(b<<4));
  }
}
// Mono: 320 muestras PCM16 representadas en 480 bytes PCM12 firmados.
inline bool pushPC12(const uint8_t* bytes,size_t size) {
  if (!pcQueue || !bytes || size!=480) return false;
  unpackPCM12(bytes,PC_FRAMES/2,pcIncoming.data);
  monoToStereo(pcIncoming);
  return queuePC(pcIncoming);
}
// Estereo: 640 muestras PCM12 (L,R,L,R...) en 960 bytes. El primer
// MAX98357A (SD a 3V3) toma L; el segundo (SD con divisor ~1 V) toma R.
inline bool pushPC12Stereo(const uint8_t* bytes,size_t size) {
  if (!pcQueue || !bytes || size!=960) return false;
  unpackPCM12(bytes,PC_FRAMES,pcIncoming.data);
  return queuePC(pcIncoming);
}
inline char takeRequest() {
  portENTER_CRITICAL(&lock);
  const char command=pending; pending=0;
  if (command) shared.state=command=='R' ? State::Recording : command=='K' ? State::Streaming : command=='J' ? State::Idle : State::Playing;
  portEXIT_CRITICAL(&lock);
  return command;
}
inline void disposeChannel(i2s_chan_handle_t& handle) {
  if (!handle) return;
  i2s_channel_disable(handle);
  i2s_del_channel(handle); handle=nullptr;
}
inline bool initChannel(bool input) {
  i2s_chan_config_t channel=I2S_CHANNEL_DEFAULT_CONFIG(
    input ? I2S_NUM_0 : I2S_NUM_1,I2S_ROLE_MASTER);
  channel.dma_desc_num=4; channel.dma_frame_num=BLOCK_FRAMES;
  channel.auto_clear=true;
  esp_err_t error=i2s_new_channel(&channel,input ? nullptr : &tx,input ? &rx : nullptr);
  i2s_std_config_t config={};
  config.clk_cfg=I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE);
  config.slot_cfg=I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(
    input ? I2S_DATA_BIT_WIDTH_32BIT : I2S_DATA_BIT_WIDTH_16BIT,I2S_SLOT_MODE_STEREO);
  config.slot_cfg.slot_mask=I2S_STD_SLOT_BOTH;
  config.gpio_cfg.mclk=I2S_GPIO_UNUSED;
  config.gpio_cfg.bclk=static_cast<gpio_num_t>(input ? MIC_SCK : AMP_BCLK);
  config.gpio_cfg.ws=static_cast<gpio_num_t>(input ? MIC_WS : AMP_LRC);
  config.gpio_cfg.dout=input ? I2S_GPIO_UNUSED : static_cast<gpio_num_t>(AMP_DIN);
  config.gpio_cfg.din=input ? static_cast<gpio_num_t>(MIC_SD) : I2S_GPIO_UNUSED;
  i2s_chan_handle_t& handle=input ? rx : tx;
  if (error==ESP_OK) error=i2s_channel_init_std_mode(handle,&config);
  // RX funciona siempre; TX se activa solo durante una reproduccion.
  if (error==ESP_OK && input) error=i2s_channel_enable(handle);
  if (error!=ESP_OK) {
    MochiUSB::logf("Error I2S %s: %s\n",input ? "RX" : "TX",esp_err_to_name(error));
    disposeChannel(handle); return false;
  }
  if (input) gpio_set_pull_mode(static_cast<gpio_num_t>(MIC_SD),GPIO_PULLDOWN_ONLY);
  return true;
}
inline bool writeBlock(const int16_t* stereo,size_t frames) {
  const uint8_t* data=reinterpret_cast<const uint8_t*>(stereo);
  size_t offset=0,total=frames*2*sizeof(int16_t);
  const uint32_t started=millis();
  while (offset<total) {
    size_t written=0;
    const esp_err_t error=i2s_channel_write(tx,data+offset,total-offset,&written,100);
    offset+=written;
    if (error!=ESP_OK && error!=ESP_ERR_TIMEOUT) return false;
    if (offset==total) return true;
    if (millis()-started>=500) return false;
    // Un timeout puede dejar parte del bloque escrita; continuamos desde ahi.
    // Si no avanza, la espera queda limitada y no se repite el audio anterior.
    if (!written) vTaskDelay(1);
  }
  return true;
}
inline void reserveClip() {
  clipCapacity=0;
  clip=static_cast<int16_t*>(heap_caps_malloc(CLIP_SAMPLES*sizeof(int16_t),
    MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT));
  if (clip) { clipCapacity=CLIP_SAMPLES; return; }
  // Conserva RAM para las tareas, I2S y USB si PSRAM esta desactivada.
  constexpr uint32_t candidates[]={CLIP_SAMPLES,SAMPLE_RATE*5,SAMPLE_RATE*4,
    SAMPLE_RATE*3,SAMPLE_RATE*2,SAMPLE_RATE*3/2};
  const uint32_t caps=MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT;
  for (const uint32_t samples:candidates) {
    const size_t bytes=samples*sizeof(int16_t);
    if (heap_caps_get_free_size(caps)<bytes+INTERNAL_RESERVE) continue;
    clip=static_cast<int16_t*>(heap_caps_malloc(bytes,caps));
    if (clip) { clipCapacity=samples; return; }
  }
}
inline void recordingStatus() {
  char info[96];
  snprintf(info,sizeof(info),"{\"audio\":\"recording\",\"record_seconds\":%.2f}",
    static_cast<double>(clipCapacity)/SAMPLE_RATE);
  MochiUSB::emit(MochiFrame::STATUS,info,strlen(info));
}
inline void drainMic() {
  if (!rx) return;
  int32_t discard[BLOCK_FRAMES*2];
  for (int i=0;i<8;++i) {
    size_t bytes=0;
    if (i2s_channel_read(rx,discard,sizeof(discard),&bytes,0)!=ESP_OK || !bytes) break;
  }
}
inline void play(bool recorded) {
  MochiUSB::state("playing");
  if (recorded) MochiUSB::logf("Reproduciendo grabacion: %.2f s.\n",
    static_cast<double>(clipLength)/SAMPLE_RATE);
  else MochiUSB::log("Sonido de prueba...");
  esp_err_t error=i2s_channel_enable(tx);
  bool ok=error==ESP_OK;
  int16_t stereo[BLOCK_FRAMES*2];
  const uint32_t total=recorded ? clipLength : SAMPLE_RATE*3/5;
  int peak=1;
  if (recorded) for (uint32_t i=0;i<clipLength;++i) {
    const int p=abs(static_cast<int>(clip[i])); if (p>peak) peak=p;
  }
  const float gain=fminf(8.0f,PLAYBACK_VOLUME*32767.0f/peak);
  for (uint32_t offset=0;offset<total && ok;offset+=BLOCK_FRAMES) {
    const size_t count=(total-offset)<BLOCK_FRAMES ? total-offset : BLOCK_FRAMES;
    float sum=0;
    for (size_t j=0;j<count;++j) {
      const uint32_t i=offset+j;
      float value;
      if (recorded) value=clip[i]*gain;
      else {
        const uint32_t noteLength=SAMPLE_RATE/5,index=i%noteLength;
        const float frequencies[3]={784,988,1175};
        const float envelope=sinf(3.14159265f*index/noteLength);
        value=32767*TONE_VOLUME*envelope*envelope
          *sinf(6.2831853f*frequencies[i/noteLength]*index/SAMPLE_RATE);
      }
      const int16_t sample=boundedSample(value);
      stereo[j*2]=stereo[j*2+1]=sample;
      const float normalized=sample/32768.0f; sum+=normalized*normalized;
    }
    setState(State::Playing,fminf(1.0f,sqrtf(sum/count)*20));
    ok=writeBlock(stereo,count);
    vTaskDelay(1);
  }
  if (ok) {
    // Silencio mayor que la cola DMA: tambien deja salir el final del clip.
    memset(stereo,0,sizeof(stereo));
    setState(State::Playing,0);
    for (int i=0;i<6 && ok;++i) ok=writeBlock(stereo,BLOCK_FRAMES);
    vTaskDelay(pdMS_TO_TICKS(40));
  }
  i2s_channel_disable(tx); drainMic();
  setState(State::Idle);
  MochiUSB::state("idle");
  MochiUSB::log(ok ? "Audio terminado." : "Error de salida I2S; revisa el panel de Mochi en la PC.");
}
inline void worker(void*) {
  int32_t stereo[BLOCK_FRAMES*2];
  int16_t mono[BLOCK_FRAMES];
  bool recording=false, pcPlaying=false, pcPrimed=false;
  bool pcNeedsFadeIn=false, pcEverPrimed=false;
  uint8_t pcMissesInRow=0;
  int16_t pcLastLeft=0, pcLastRight=0;
  uint32_t recordStarted=0;
  while (true) {
    const char command=takeRequest();
    if (command=='K') {
      xQueueReset(pcQueue);
      if (i2s_channel_enable(tx)==ESP_OK) {
        pcPlaying=true; pcPrimed=false; pcNeedsFadeIn=false;
        pcEverPrimed=false; pcMissesInRow=0; pcLastLeft=pcLastRight=0;
        setState(State::Streaming);
        MochiUSB::state("streaming"); MochiUSB::log("Reproduciendo sonido del PC.");
      } else {
        setState(State::Idle); MochiUSB::state("idle");
        MochiUSB::log("No se pudo iniciar I2S para PC.");
      }
      continue;
    }
    if (command=='J') {
      pcPlaying=false; pcPrimed=false; pcNeedsFadeIn=false;
      pcMissesInRow=0; pcLastLeft=pcLastRight=0;
      // Cola + DMA pueden contener una pequena cantidad de audio pendiente.
      xQueueReset(pcQueue);
      i2s_channel_disable(tx);
      setState(State::Idle); MochiUSB::state("idle");
      MochiUSB::log("Parlante PC detenido.");
      continue;
    }
    if (command=='T' || command=='P') { play(command=='P'); continue; }
    if (pcPlaying) {
      // Buffer de jitter de 160 ms: tolera variaciones de Windows/USB.
      // Este margen aumenta la latencia pero estabiliza la reproduccion.
      if (!pcPrimed) {
        if (uxQueueMessagesWaiting(pcQueue)<PC_PREBUFFER_BLOCKS) {
          vTaskDelay(pdMS_TO_TICKS(2)); continue;
        }
        if (pcEverPrimed) { ++pcRebuffers; pcNeedsFadeIn=true; }
        pcEverPrimed=true;
        pcPrimed=true;
        pcMissesInRow=0;
      }
      // Bloque L/R intercalado; se procesa en el mismo lugar y se envia a I2S.
      PCBlock block={};
      // NUNCA esperar 12 ms aqui: writeBlock ya reproduce 20 ms por bloque.
      // Aquella espera hacia que 20 ms de audio consumieran ~32 ms.
      const bool hasAudio=xQueueReceive(pcQueue,&block,0)==pdTRUE;
      if (hasAudio) {
        ++pcPlayed;
        pcMissesInRow=0;
        // Una trama distinta de silencio permite distinguir datos reales
        // de relleno que genera el receptor cuando falta la entrada USB.
        int16_t peak=0;
        for (size_t i=0;i<PC_FRAMES*2;++i) {
          const int32_t v=block.data[i];
          const int32_t av=v<0 ? -v : v;
          if (av>peak) peak=static_cast<int16_t>(av>32767 ? 32767 : av);
        }
        if (peak>100) ++pcNonzero;
      } else {
        ++pcUnderruns;
        if (pcMissesInRow<255) ++pcMissesInRow;
        // Reanudar solo tras reunir datos si falla mas de un bloque seguido.
        if (pcMissesInRow>=3) {
          pcPrimed=false;
          pcNeedsFadeIn=true;
        }
      }
      float power=0;
      for (size_t i=0;i<PC_FRAMES*2;++i) {
        // No repetir bloques viejos. Atenuar los bordes cuando se pierde un
        // bloque: los cambios instantaneos introducen chasquidos audibles.
        // Cada canal se desvanece desde su propia ultima muestra.
        const size_t frame=i/2;
        float value=block.data[i]*0.55f;
        if (!hasAudio) {
          const int16_t last=(i&1) ? pcLastRight : pcLastLeft;
          if (frame<64) value=last*(1.0f-static_cast<float>(frame+1)/64.0f);
          else value=0;
        } else if (pcNeedsFadeIn && frame<64) {
          value*=static_cast<float>(frame+1)/64.0f;
        }
        const int16_t sample=boundedSample(value);
        block.data[i]=sample;
        const float v=sample/32768.0f; power+=v*v;
      }
      pcLastLeft=block.data[2*(PC_FRAMES-1)];
      pcLastRight=block.data[2*(PC_FRAMES-1)+1];
      if (hasAudio) pcNeedsFadeIn=false;
      else { pcNeedsFadeIn=true; pcLastLeft=pcLastRight=0; }
      setState(State::Streaming,fminf(1.0f,sqrtf(power/(PC_FRAMES*2))*15.0f));
      if (!writeBlock(block.data,PC_FRAMES)) {
        ++pcI2sErrors;
        pcPlaying=false; xQueueReset(pcQueue); i2s_channel_disable(tx);
        setState(State::Idle); MochiUSB::state("idle");
        MochiUSB::log("Error I2S en sonido PC. Desactiva y vuelve a probar.");
      }
      continue;
    }
    if (command=='R') {
      drainMic(); clipLength=0; recording=true; recordStarted=millis();
      portENTER_CRITICAL(&lock); shared.clipSamples=0; portEXIT_CRITICAL(&lock);
      MochiUSB::logf("GRABANDO %.2f segundos: habla ahora...\n",
        static_cast<double>(clipCapacity)/SAMPLE_RATE);
      recordingStatus();
    }
    if (rx) {
      size_t bytes=0;
      const esp_err_t error=i2s_channel_read(rx,stereo,sizeof(stereo),&bytes,20);
      const size_t frames=bytes/(2*sizeof(int32_t));
      if (frames && (error==ESP_OK || error==ESP_ERR_TIMEOUT)) {
        float sum=0; int peak=0;
        for (size_t i=0;i<frames;++i) {
          // INMP441: 24 bits en la parte alta de cada slot de 32 bits.
          // L/R a GND selecciona el slot izquierdo; 64 BCLK por frame.
          const float raw=static_cast<float>(stereo[i*2] >> 16);
          dc+=0.01f*(raw-dc);
          const int16_t sample=boundedSample((raw-dc)*MIC_GAIN);
          mono[i]=sample;
          const int amplitude=abs(static_cast<int>(sample));
          if (amplitude>peak) peak=amplitude;
          const float normalized=sample/32768.0f; sum+=normalized*normalized;
          if (recording && clipLength<clipCapacity) clip[clipLength++]=sample;
        }
        const float rms=sqrtf(sum/frames);
        portENTER_CRITICAL(&lock);
        shared.inputRms=rms; shared.peak=peak;
        portEXIT_CRITICAL(&lock);
        if(!recording) MochiUSB::pcm(mono,frames);
      } else {
        portENTER_CRITICAL(&lock);
        shared.inputRms=0; shared.peak=0;
        if (error!=ESP_OK && error!=ESP_ERR_TIMEOUT) ++shared.readErrors;
        portEXIT_CRITICAL(&lock);
      }
    } else vTaskDelay(pdMS_TO_TICKS(8));
    const uint32_t recordTimeout=clipCapacity*1000/SAMPLE_RATE+2000;
    if (recording && (clipLength>=clipCapacity || millis()-recordStarted>recordTimeout)) {
      recording=false;
      portENTER_CRITICAL(&lock); shared.clipSamples=clipLength; portEXIT_CRITICAL(&lock);
      setState(State::Idle);
      MochiUSB::state("idle");
      MochiUSB::logf("Grabacion: %lu muestras (%.2f s). Envia P para escuchar.\n",
        static_cast<unsigned long>(clipLength),static_cast<double>(clipLength)/SAMPLE_RATE);
      if (clipLength<clipCapacity)
        MochiUSB::log("La captura termino antes del limite: revisa el microfono y sus conexiones.");
    }
    vTaskDelay(1);
  }
}
inline void begin() {
  const bool micOK=initChannel(true),ampOK=initChannel(false);
  reserveClip();
  pcQueue=xQueueCreate(24,sizeof(PCBlock));
  portENTER_CRITICAL(&lock);
  shared.micReady=micOK; shared.ampReady=ampOK; shared.bufferReady=clip!=nullptr;
  shared.clipCapacity=clipCapacity;
  portEXIT_CRITICAL(&lock);
  const bool taskOK=(micOK || ampOK) && xTaskCreate(worker,"mochi_audio",8192,nullptr,1,nullptr)==pdPASS;
  portENTER_CRITICAL(&lock); shared.workerReady=taskOK; portEXIT_CRITICAL(&lock);
  MochiUSB::logf("Controladores RX=%s TX=%s; buffer=%s; tarea=%s\n",
    micOK?"listo":"error",ampOK?"listo":"error",clip?"listo":"sin RAM",taskOK?"lista":"error");
  MochiUSB::log("RX/TX listos indican software iniciado: prueba fisicamente microfono y parlante.");
  MochiUSB::logf("Grabacion disponible: %.2f s.\n",static_cast<double>(clipCapacity)/SAMPLE_RATE);
  if (clipCapacity<CLIP_SAMPLES)
    MochiUSB::log("Para grabar 6 segundos, activa la PSRAM correcta de tu placa en Arduino (2 MB: QSPI).");
}
} // namespace AudioMochi
