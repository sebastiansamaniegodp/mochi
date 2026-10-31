/*
  MOCHI 3.5 (unificado) - ESP32-S3 + ST7789 240x280 + INMP441 + MAX98357A.

  Un solo firmware para los dos programas de la PC:
  - Mochi_PC (ventana de voz v3.3.8): ordenes fijas por voz, grabar,
    reproducir y "Parlante PC" (audio de la PC por USB, mono o estereo).
  - Puente del LLM (mochi-desktop/voice_bridge): boton BOOT para hablar,
    estados de la cara (escuchando, pensando, hablando...), texto en la
    pantalla y la respuesta de Piper por el parlante.
  El protocolo USB es el de v3.3.8 (sigue funcionando el Mochi_PC viejo) mas:
    PC->ESP32  orden '0'..'5' = estado del LLM (reposo, escuchando,
               pensando, hablando, hecho, error); paquete 'T' = texto.
    ESP32->PC  STATUS {"button":"down"} / {"button":"up"} con el boton BOOT.
  La voz del LLM viaja como el Parlante PC (K, paquetes A/B, J).

  CABLEADO (el de v3.3.8):
  TFT: GND->GND, VCC->3V3, BLK->3V3, SCL->12, SDA->11, RES->8, DC->9, CS->10.
  INMP441: VDD->3V3, GND/LR->GND, SCK->4, WS->5, SD->6.
  MAX98357A: VIN->5V, GND->GND, BCLK->16, LRC->17, DIN->18.
  SD/EN del MAX->3V3; GAIN sin conectar. Parlante solo a SPK+/SPK-.
  ESTEREO: 2.o MAX98357A con BCLK/LRC/DIN en paralelo (16/17/18) y
  SD/EN a ~1 V (divisor 22k desde 3V3, 10k a GND) = canal derecho.
  Boton para hablar: BOOT de la placa (GPIO0), no hay que cablear nada.

  ARDUINO IDE: paquete esp32 de Espressif 3.x (probado con 3.3.12),
  placa ESP32S3 Dev Module, USB CDC On Boot=Enabled,
  USB Mode=Hardware CDC and JTAG, Upload Mode=UART0 / Hardware CDC,
  PSRAM=QSPI PSRAM. Bibliotecas: Adafruit GFX, Adafruit ST7735 and ST7789,
  Adafruit BusIO. Guarda la carpeta Mochi completa (con los .h).
  Para subirlo SIN compilar: ver binarios/LEEME.txt.

  Comandos de v3.3.8: N normal, F feliz, C curioso, O sorpresa,
  S bostezo, D dormir, G guino, B parpadeo, M movimiento, A automatico,
  T sonido, R grabar, P reproducir, V reaccion al sonido, H ayuda,
  K/J parlante PC, L/Q microfono por USB, ? saludo, # latido.
*/
#include <Arduino.h>
#include <esp_system.h>
#include <SPI.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ST7789.h>
#include <math.h>
#include "AudioMochi.h"
#include "MochiPCLink.h"

constexpr int TFT_SCLK=12, TFT_MOSI=11, TFT_CS=10, TFT_DC=9, TFT_RST=8;
constexpr uint32_t SPI_HZ=20000000;
constexpr uint8_t ROTACION=0;
constexpr int SCREEN_W=(ROTACION & 1) ? 280 : 240;
constexpr int SCREEN_H=(ROTACION & 1) ? 240 : 280;
constexpr uint32_t FRAME_MS=33;
constexpr bool AUTO_ANIMACIONES=true, MOVER_AL_INICIO=true;
constexpr bool MIRAR_ALREDEDOR=true, RESPIRACION_SUAVE=true;
constexpr uint16_t FONDO=0x0000, COLOR_OJOS=0x7FBD;
constexpr uint16_t COLOR_BRILLO=0xEFFF, COLOR_BOCA=0xFF1C;
constexpr uint8_t NORMAL=0, FELIZ=1, CURIOSO=2, SORPRESA=3, SOMNOLIENTO=4, DORMIDO=5;

struct Pose { float lh,rh,lw,rw,ly,ry,happy,mouthOpen,sleep; };
struct Bounds { int left,top,right,bottom; };
const Pose POSES[6]={
  {52,52,38,38,0,0,0,0,0}, {22,22,40,40,0,0,1,0,0},
  {53,42,38,40,-3,2,0,0,0}, {60,60,42,42,-1,-1,0,1,0},
  {24,24,38,38,3,3,0,0.85f,0.4f}, {3,3,36,36,4,4,0,0,1}
};
Adafruit_ST7789 tft(&SPI,TFT_CS,TFT_DC,TFT_RST);
GFXcanvas16 canvas(SCREEN_W,SCREEN_H);
Pose pose=POSES[NORMAL], poseFrom=POSES[NORMAL];
Bounds currentBox={0,0,0,0}, previousBox={0,0,0,0};
uint8_t mood=NORMAL;
bool memoryOK=false, automatic=AUTO_ANIMACIONES, movingEnabled=MOVER_AL_INICIO;
bool blinkActive=false, secondBlink=false, winkActive=false, havePrevious=false;
float centerX=0,centerY=0,fromX=0,fromY=0,toX=0,toY=0;
float gazeX=0,gazeY=0,gazeFromX=0,gazeFromY=0,gazeToX=0,gazeToY=0;
float drawCenterX=0,drawCenterY=0,breathPhase=0;
uint32_t movementStarted=0,movementDuration=0,nextMovement=0;
uint32_t gazeStarted=0,gazeDuration=650,nextGaze=0;
uint32_t moodStarted=0,nextMood=0,nextBlink=0,blinkStarted=0;
uint32_t winkStarted=0,nextWink=0,lastFrame=0;
AudioMochi::Snapshot audioView;
AudioMochi::State lastAudioState=AudioMochi::State::Idle;
bool reactToSound=true,savedAutomatic=true,audioSaved=false;
uint8_t savedMood=NORMAL;
float speechLevel=0;
constexpr float VOICE_THRESHOLD=0.008f; // Aumenta si reacciona al ruido ambiente.
uint32_t soundStarted=0,nextSoundReaction=0,lastAudioLog=0,audioEnded=0;

// ---- Puente del LLM: estado, texto en pantalla y boton BOOT ----
constexpr int PIN_BOTON=0;                 // BOOT de la placa
constexpr int CAPTION_Y=228, CAPTION_H=SCREEN_H-CAPTION_Y; // debajo de la cara
constexpr uint32_t CAPTION_HOLD_MS=15000;  // en reposo, el texto se borra solo
constexpr uint32_t RESULT_HOLD_MS=3000;    // "hecho"/"error" vuelven a reposo
enum : uint8_t { LLM_IDLE=0, LLM_LISTENING, LLM_THINKING, LLM_SPEAKING, LLM_DONE, LLM_ERROR };
// Si la PC deja de mandar estados, la cara no queda trabada.
const uint32_t LLM_MAX_MS[6]={0,20000,120000,90000,RESULT_HOLD_MS,RESULT_HOLD_MS};
uint8_t llmState=LLM_IDLE, llmSavedMood=NORMAL;
bool llmSavedAutomatic=true, llmHost=false, captionDirty=true;
uint32_t llmStateSince=0, captionSince=0, lastCaptionAnim=0;
char caption[MochiPCLink::MAX_TEXT+1]="";
// El texto llega por la tarea USB y se dibuja en loop().
static portMUX_TYPE captionLock=portMUX_INITIALIZER_UNLOCKED;
static char captionIncoming[MochiPCLink::MAX_TEXT+1]="";
static volatile bool captionNew=false;

float clampValue(float v,float lo,float hi) { return v<lo ? lo : (v>hi ? hi : v); }
float mixValue(float a,float b,float t) { return a+(b-a)*t; }
float ease(float t) {
  t=clampValue(t,0,1);
  return t*t*t*(t*(t*6-15)+10);
}
bool reached(uint32_t now,uint32_t deadline) {
  return static_cast<int32_t>(now-deadline)>=0;
}
bool isMoving(uint32_t now) {
  return movingEnabled && movementDuration && now-movementStarted<movementDuration;
}

void blendPixel(int x,int y,uint16_t color,float alpha) {
  if (x<0 || x>=SCREEN_W || y<0 || y>=SCREEN_H || alpha<=0) return;
  alpha=clampValue(alpha,0,1);
  uint16_t& dst=canvas.getBuffer()[y*SCREEN_W+x];
  if (alpha>=1) { dst=color; return; }
  const int r=static_cast<int>(mixValue((dst>>11)&31,(color>>11)&31,alpha)+0.5f);
  const int g=static_cast<int>(mixValue((dst>>5)&63,(color>>5)&63,alpha)+0.5f);
  const int b=static_cast<int>(mixValue(dst&31,color&31,alpha)+0.5f);
  dst=static_cast<uint16_t>((r<<11)|(g<<5)|b);
}
void roundedEye(float cx,float cy,float width,float height,uint16_t color,float alpha) {
  if (alpha<=0.001f) return;
  const float hw=width*0.5f,hh=height*0.5f,radius=hw<hh ? hw : hh;
  const int x0=static_cast<int>(floorf(cx-hw-1)),x1=static_cast<int>(ceilf(cx+hw+1));
  const int y0=static_cast<int>(floorf(cy-hh-1)),y1=static_cast<int>(ceilf(cy+hh+1));
  for (int y=y0;y<=y1;++y) for (int x=x0;x<=x1;++x) {
    const float qx=fabsf(x+0.5f-cx)-(hw-radius);
    const float qy=fabsf(y+0.5f-cy)-(hh-radius);
    const float distance=(qx>0 && qy>0) ? sqrtf(qx*qx+qy*qy)-radius
                         : (qx>qy ? qx : qy)-radius;
    blendPixel(x,y,color,clampValue(0.5f-distance,0,1)*alpha);
  }
}
void ellipse(float cx,float cy,float rx,float ry,uint16_t color,float alpha) {
  if (alpha<=0.001f || rx<=0 || ry<=0) return;
  const float scale=rx<ry ? rx : ry;
  const int x0=static_cast<int>(floorf(cx-rx-1)),x1=static_cast<int>(ceilf(cx+rx+1));
  const int y0=static_cast<int>(floorf(cy-ry-1)),y1=static_cast<int>(ceilf(cy+ry+1));
  for (int y=y0;y<=y1;++y) for (int x=x0;x<=x1;++x) {
    const float dx=(x+0.5f-cx)/rx,dy=(y+0.5f-cy)/ry;
    const float distance=(sqrtf(dx*dx+dy*dy)-1)*scale;
    blendPixel(x,y,color,clampValue(0.5f-distance,0,1)*alpha);
  }
}
void curvedStroke(float cx,float cy,float halfWidth,float sag,float thickness,
                  uint16_t color,float alpha) {
  if (alpha<=0.001f) return;
  const float extent=thickness+1;
  const int x0=static_cast<int>(floorf(cx-halfWidth-extent));
  const int x1=static_cast<int>(ceilf(cx+halfWidth+extent));
  const int y0=static_cast<int>(floorf(cy+(sag<0?sag:0)-extent));
  const int y1=static_cast<int>(ceilf(cy+(sag>0?sag:0)+extent));
  for (int x=x0;x<=x1;++x) {
    const float dx=x+0.5f-cx,bounded=clampValue(dx,-halfWidth,halfWidth);
    const float targetY=cy+sag*(1-bounded*bounded/(halfWidth*halfWidth));
    const float slope=-2*sag*bounded/(halfWidth*halfWidth);
    const float normalScale=1/sqrtf(1+slope*slope);
    for (int y=y0;y<=y1;++y) {
      const float dy=y+0.5f-targetY;
      const float distance=fabsf(dx)<=halfWidth ? fabsf(dy)*normalScale
                           : sqrtf((dx-bounded)*(dx-bounded)+dy*dy);
      blendPixel(x,y,color,clampValue(thickness*0.5f+0.5f-distance,0,1)*alpha);
    }
  }
}

void setMood(uint8_t selected,uint32_t now) {
  if (selected>DORMIDO) return;
  poseFrom=pose; mood=selected; moodStarted=now;
  nextMood=now+static_cast<uint32_t>(random(5400,8501));
  if (selected==SOMNOLIENTO) nextMood=now+4000;
  if (selected==DORMIDO) {
    movementDuration=0;
    fromX=toX=centerX; fromY=toY=centerY;
    gazeFromX=gazeX; gazeFromY=gazeY; gazeToX=gazeToY=0;
    gazeStarted=now; nextMood=now+4200;
  }
  if (selected==SORPRESA) nextMood=now+2700;
}

// ---- Estados del LLM ----
uint8_t llmMood(uint8_t state) {
  if (state==LLM_SPEAKING || state==LLM_DONE) return FELIZ;
  if (state==LLM_ERROR) return SORPRESA;
  return CURIOSO; // escuchando / pensando
}
void setLlmState(uint8_t state,uint32_t now) {
  if (state>LLM_ERROR) return;
  if (llmState==LLM_IDLE && state!=LLM_IDLE) {
    llmSavedMood=mood; llmSavedAutomatic=automatic;
  }
  if (state==LLM_LISTENING) caption[0]=0; // orden nueva: se borra el texto anterior
  llmState=state; llmStateSince=now; captionSince=now; captionDirty=true;
  if (state==LLM_IDLE) {
    // Si suena el parlante PC, la cara la maneja updateAudioFace.
    if (AudioMochi::snapshot().state==AudioMochi::State::Idle) {
      automatic=llmSavedAutomatic; setMood(automatic ? NORMAL : llmSavedMood,now);
    }
    return;
  }
  automatic=false; setMood(llmMood(state),now);
  if (state==LLM_LISTENING || state==LLM_ERROR) { blinkActive=true; blinkStarted=now; }
}
void updateLlm(uint32_t now) {
  if (llmState!=LLM_IDLE && now-llmStateSince>LLM_MAX_MS[llmState])
    setLlmState(LLM_IDLE,now);
  if (llmState==LLM_IDLE && caption[0] && now-captionSince>CAPTION_HOLD_MS) {
    caption[0]=0; captionDirty=true;
  }
  if (captionNew) {
    portENTER_CRITICAL(&captionLock);
    memcpy(caption,captionIncoming,sizeof(caption)); captionNew=false;
    portEXIT_CRITICAL(&captionLock);
    captionSince=now; captionDirty=true;
  }
  // Puntos suspensivos animados mientras piensa.
  if (llmState==LLM_THINKING && now-lastCaptionAnim>=400) captionDirty=true;
}
// Texto debajo de la cara: una linea de estado y hasta 4 de texto.
// Se dibuja directo en la TFT y solo cuando cambia (la cara nunca baja de y=222).
void drawCaption(uint32_t now) {
  if (!captionDirty) return;
  captionDirty=false; lastCaptionAnim=now;
  tft.fillRect(0,CAPTION_Y,SCREEN_W,CAPTION_H,FONDO);
  tft.setTextSize(1); tft.setTextWrap(false);
  static const char* const labels[6]={"","Escuchando","Pensando","Hablando","Listo","Error"};
  if (llmState!=LLM_IDLE) {
    char status[24];
    const int dots=llmState==LLM_THINKING ? static_cast<int>((now/400)%4) : 0;
    snprintf(status,sizeof(status),"%s%.*s",labels[llmState],dots,"...");
    tft.setTextColor(llmState==LLM_ERROR ? 0xF9A6 : COLOR_OJOS);
    tft.setCursor((SCREEN_W-6*static_cast<int>(strlen(labels[llmState])))/2,CAPTION_Y+2);
    tft.print(status);
  }
  // Ajuste de linea por palabras: 6 px por caracter, 40 por linea.
  constexpr int COLS=SCREEN_W/6, ROWS=4;
  tft.setTextColor(0xFFFF);
  const char* p=caption;
  for (int row=0;row<ROWS && *p;++row) {
    while (*p==' ') ++p;
    int len=strlen(p);
    if (len>COLS) {
      len=COLS;
      while (len>0 && p[len]!=' ') --len;
      if (len==0) len=COLS;
    }
    char line[COLS+1]; memcpy(line,p,len); line[len]=0;
    tft.setCursor((SCREEN_W-6*len)/2,CAPTION_Y+14+row*10);
    tft.print(line);
    p+=len;
  }
}
// Boton BOOT: mantener apretado para hablar con el LLM. La PC junta el audio
// del microfono entre "down" y "up".
void pumpButton(uint32_t now) {
  static bool stablePressed=false, lastRead=false;
  static uint32_t changedAt=0;
  const bool pressed=digitalRead(PIN_BOTON)==LOW;
  if (pressed!=lastRead) { lastRead=pressed; changedAt=now; }
  if (pressed==stablePressed || now-changedAt<30) return;
  stablePressed=pressed;
  const char* data=pressed ? "{\"button\":\"down\"}" : "{\"button\":\"up\"}";
  MochiUSB::emit(MochiFrame::STATUS,data,strlen(data));
  // Sin el puente del LLM (Mochi_PC solo) el boton no cambia la cara.
  if (!llmHost) return;
  if (pressed && llmState!=LLM_THINKING && llmState!=LLM_SPEAKING) setLlmState(LLM_LISTENING,now);
  if (!pressed && llmState==LLM_LISTENING) setLlmState(LLM_THINKING,now);
}
void updateAudioFace(uint32_t now) {
  audioView=AudioMochi::snapshot();
  if (audioView.state!=lastAudioState) {
    // Con el LLM activo manda su estado; el audio solo mueve la boca.
    // audioSaved: si el LLM pausa el parlante PC, al reanudar no se pisa
    // la expresion guardada al empezar la musica.
    if (lastAudioState==AudioMochi::State::Idle && llmState==LLM_IDLE && !audioSaved) {
      savedMood=mood; savedAutomatic=automatic; audioSaved=true;
    }
    if (lastAudioState==AudioMochi::State::Idle) automatic=false;
    if (audioView.state==AudioMochi::State::Recording) setMood(CURIOSO,now);
    if ((audioView.state==AudioMochi::State::Playing ||
        audioView.state==AudioMochi::State::Streaming) && llmState==LLM_IDLE) setMood(FELIZ,now);
    if (audioView.state==AudioMochi::State::Idle) {
      if (llmState==LLM_IDLE) {
        if (audioSaved) { automatic=savedAutomatic; setMood(savedMood,now); }
        audioSaved=false;
      }
      else if (mood!=llmMood(llmState)) setMood(llmMood(llmState),now);
      audioEnded=now;
    }
    lastAudioState=audioView.state; soundStarted=0;
  }
  const float target=(audioView.state==AudioMochi::State::Playing ||
    audioView.state==AudioMochi::State::Streaming) ? audioView.outputLevel : 0;
  speechLevel+=(target-speechLevel)*0.28f;
  if (audioView.micReady && audioView.workerReady && reactToSound && automatic
      && audioView.state==AudioMochi::State::Idle && now-audioEnded>900
      && reached(now,nextSoundReaction) && audioView.inputRms>VOICE_THRESHOLD) {
    if (!soundStarted) soundStarted=now;
    if (now-soundStarted>100) {
      setMood(CURIOSO,now); blinkActive=true; blinkStarted=now;
      nextSoundReaction=now+4500; soundStarted=0;
    }
  } else soundStarted=0;
  if (now-lastAudioLog>=1000) {
    lastAudioLog=now;
    MochiUSB::logf("Mic RMS=%.5f pico=%d errores=%lu | T sonido, R grabar, P escuchar\n",
      static_cast<double>(audioView.inputRms),audioView.peak,
      static_cast<unsigned long>(audioView.readErrors));
  }
}
void updatePose(uint32_t now) {
  if (automatic && reached(now,nextMood)) {
    uint8_t next;
    if (mood==SOMNOLIENTO) next=DORMIDO;
    else if (mood==DORMIDO || mood==SORPRESA || mood==FELIZ) next=NORMAL;
    else {
      const long choice=random(100);
      next=choice<35 ? NORMAL : choice<60 ? FELIZ : choice<82 ? CURIOSO
           : choice<90 ? SORPRESA : SOMNOLIENTO;
    }
    setMood(next,now);
  }
  const float t=ease(static_cast<float>(now-moodStarted)/700.0f);
  const Pose& target=POSES[mood];
  pose.lh=mixValue(poseFrom.lh,target.lh,t); pose.rh=mixValue(poseFrom.rh,target.rh,t);
  pose.lw=mixValue(poseFrom.lw,target.lw,t); pose.rw=mixValue(poseFrom.rw,target.rw,t);
  pose.ly=mixValue(poseFrom.ly,target.ly,t); pose.ry=mixValue(poseFrom.ry,target.ry,t);
  pose.happy=mixValue(poseFrom.happy,target.happy,t);
  pose.mouthOpen=mixValue(poseFrom.mouthOpen,target.mouthOpen,t);
  pose.sleep=mixValue(poseFrom.sleep,target.sleep,t);
}
void startMovement(uint32_t now) {
  fromX=centerX; fromY=centerY;
  toX=SCREEN_W*0.5f+random(-18,19);
  toY=SCREEN_H*0.5f-14+random(-32,33);
  movementStarted=now; movementDuration=static_cast<uint32_t>(random(5200,8001));
  nextMovement=now+movementDuration+static_cast<uint32_t>(random(3200,5601));
  gazeFromX=gazeX; gazeFromY=gazeY; gazeToX=gazeToY=0;
  gazeStarted=now; nextGaze=now+movementDuration+1100;
}
void updateMotion(uint32_t now,uint32_t dt) {
  if (movingEnabled && mood!=DORMIDO && reached(now,nextMovement)) startMovement(now);
  if (movingEnabled && movementDuration) {
    const float t=ease(static_cast<float>(now-movementStarted)/movementDuration);
    centerX=mixValue(fromX,toX,t); centerY=mixValue(fromY,toY,t);
  }
  if (MIRAR_ALREDEDOR && mood!=DORMIDO && !isMoving(now) && reached(now,nextGaze)) {
    gazeFromX=gazeX; gazeFromY=gazeY;
    gazeToX=static_cast<float>(random(-6,7)); gazeToY=static_cast<float>(random(-2,3));
    if (random(100)<40) gazeToX=gazeToY=0;
    gazeStarted=now; gazeDuration=static_cast<uint32_t>(random(650,1001));
    nextGaze=now+static_cast<uint32_t>(random(3500,6501));
  }
  const float gazeT=ease(static_cast<float>(now-gazeStarted)/gazeDuration);
  gazeX=mixValue(gazeFromX,gazeToX,gazeT); gazeY=mixValue(gazeFromY,gazeToY,gazeT);
  breathPhase+=static_cast<float>(dt)*(6.2831853f/5200.0f);
  if (breathPhase>=6.2831853f) breathPhase=fmodf(breathPhase,6.2831853f);
  drawCenterX=centerX;
  drawCenterY=centerY+(RESPIRACION_SUAVE ? 0.35f*sinf(breathPhase) : 0);
}

float closingPulse(uint32_t elapsed,uint32_t closeMs,uint32_t holdMs,uint32_t openMs) {
  if (elapsed<closeMs) return 1-ease(static_cast<float>(elapsed)/closeMs);
  if (elapsed<closeMs+holdMs) return 0;
  if (elapsed<closeMs+holdMs+openMs)
    return ease(static_cast<float>(elapsed-closeMs-holdMs)/openMs);
  return 1;
}
float blinkOpenness(uint32_t now) {
  if (!blinkActive && mood!=DORMIDO && reached(now,nextBlink)) {
    blinkActive=true; blinkStarted=now;
  }
  if (!blinkActive) return 1;
  const uint32_t elapsed=now-blinkStarted;
  if (elapsed<345) return closingPulse(elapsed,120,45,180);
  blinkActive=false;
  if (!secondBlink && random(100)<12) { secondBlink=true; nextBlink=now+180; }
  else { secondBlink=false; nextBlink=now+static_cast<uint32_t>(random(3200,6801)); }
  return 1;
}
void updateWink(uint32_t now) {
  if (automatic && !winkActive && !blinkActive && !isMoving(now)
      && (mood==NORMAL || mood==CURIOSO) && reached(now,nextWink)) {
    winkActive=true; winkStarted=now;
  }
  if (winkActive && now-winkStarted>=720) {
    winkActive=false; nextWink=now+static_cast<uint32_t>(random(18000,30001));
  }
}
void drawEye(float cx,float cy,float width,float height,float open) {
  const float h=3+(height-3)*open;
  roundedEye(cx,cy,width,h,COLOR_OJOS,1-pose.happy);
  curvedStroke(cx,cy+3,18,-7,3.0f,COLOR_OJOS,pose.happy);
  const float shine=clampValue((h-19)/12,0,1)*(1-pose.happy);
  ellipse(cx-6,cy-h*0.5f+10,3,3,COLOR_BRILLO,shine);
}
void composeFace(uint32_t now,float open) {
  canvas.fillScreen(FONDO);
  const float leftOpen=winkActive ? closingPulse(now-winkStarted,130,340,250) : 1;
  const float eyesX=drawCenterX+gazeX,eyesY=drawCenterY+gazeY;
  drawEye(eyesX-40,eyesY+pose.ly,pose.lw,pose.lh,open*leftOpen);
  drawEye(eyesX+40,eyesY+pose.ry,pose.rw,pose.rh,open);
  const float mx=drawCenterX+gazeX*0.25f,my=drawCenterY+43;
  float mouthOpen=pose.mouthOpen;
  if (audioView.state==AudioMochi::State::Playing ||
      audioView.state==AudioMochi::State::Streaming)
    mouthOpen=fmaxf(mouthOpen,clampValue(speechLevel*1.8f,0,1));
  if (mood==SOMNOLIENTO) {
    const float t=clampValue(static_cast<float>(now-moodStarted)/3600.0f,0,1);
    mouthOpen*=sinf(t*3.14159265f);
  }
  const float normalMouth=(1-mouthOpen)*(1-pose.happy)*(1-pose.sleep);
  curvedStroke(mx-6,my,6,4,2.2f,COLOR_BOCA,normalMouth);
  curvedStroke(mx+6,my,6,4,2.2f,COLOR_BOCA,normalMouth);
  curvedStroke(mx,my,13,6,2.2f,COLOR_BOCA,(1-mouthOpen)*pose.happy);
  curvedStroke(mx,my+2,8,1,1.8f,COLOR_BOCA,(1-mouthOpen)*pose.sleep);
  if (mouthOpen>0.001f) {
    const float ry=5+pose.sleep*12;
    ellipse(mx,my+3,5,ry,COLOR_BOCA,mouthOpen);
    ellipse(mx,my+3,3.2f,ry-1.8f,FONDO,mouthOpen);
  }
  currentBox.left=static_cast<int>(floorf(drawCenterX-72));
  currentBox.right=static_cast<int>(ceilf(drawCenterX+72));
  currentBox.top=static_cast<int>(floorf(drawCenterY-42));
  currentBox.bottom=static_cast<int>(ceilf(drawCenterY+64));
}
void presentFace() {
  int x0=currentBox.left,y0=currentBox.top,x1=currentBox.right,y1=currentBox.bottom;
  if (havePrevious) {
    if (previousBox.left<x0) x0=previousBox.left;
    if (previousBox.top<y0) y0=previousBox.top;
    if (previousBox.right>x1) x1=previousBox.right;
    if (previousBox.bottom>y1) y1=previousBox.bottom;
  }
  if (x0<0) x0=0;
  if (y0<0) y0=0;
  if (x1>SCREEN_W) x1=SCREEN_W;
  if (y1>SCREEN_H) y1=SCREEN_H;
  const int w=x1-x0,h=y1-y0;
  if (w>0 && h>0) {
    tft.startWrite(); tft.setAddrWindow(x0,y0,w,h);
    for (int y=y0;y<y1;++y) tft.writePixels(canvas.getBuffer()+y*SCREEN_W+x0,w);
    tft.endWrite();
  }
  previousBox=currentBox; havePrevious=true;
}

void handleCommand(char command,uint32_t now) {
  if (command=='#') {
    MochiUSB::heartbeat();
    // El microfono deja de enviar PCM durante musica: responder para conservar watchdog PC.
    if (AudioMochi::snapshot().state==AudioMochi::State::Streaming) MochiUSB::state("streaming");
    return;
  }
  if (command=='?') {
    const auto a=AudioMochi::snapshot(); char info[300];
    snprintf(info,sizeof(info),"{\"device\":\"Mochi\",\"firmware\":\"3.5\",\"llm\":1,\"protocol\":1,\"pc_rx_protocol\":2,\"sample_rate\":16000,\"pcm\":\"s16le\",\"mic\":%s,\"amp\":%s,\"screen\":%s,\"stream\":%s,\"dropped\":%lu,\"record_seconds\":%.2f}",
      a.micReady && a.workerReady?"true":"false",a.ampReady && a.workerReady?"true":"false",
      memoryOK?"true":"false",MochiUSB::streamEnabled()?"true":"false",
      static_cast<unsigned long>(MochiUSB::droppedPackets()),
      static_cast<double>(a.clipCapacity)/AudioMochi::SAMPLE_RATE);
    MochiUSB::emit(MochiFrame::HELLO,info,strlen(info)); return;
  }
  if (command>='0' && command<='5') {
    llmHost=true; setLlmState(static_cast<uint8_t>(command-'0'),now); return;
  }
  if (command>='a' && command<='z') command-=('a'-'A');
  if (command=='L') {
    const auto a=AudioMochi::snapshot();
    MochiUSB::stream(a.micReady && a.workerReady);
    MochiUSB::log(a.micReady && a.workerReady ? "Escuchando por USB." : "Microfono no disponible.");
    return;
  }
  if (command=='Q') { MochiUSB::stream(false); return; }
  if (command=='K' || command=='J') {
    if (!AudioMochi::requestPC(command=='K'))
      MochiUSB::log("Parlante PC ocupado o no disponible.");
    return;
  }
  if (command=='T' || command=='R' || command=='P') {
    if (!AudioMochi::request(command))
      MochiUSB::log("Audio ocupado o no disponible. P requiere primero una grabacion R.");
    return;
  }
  if (command=='V') {
    reactToSound=!reactToSound;
    MochiUSB::log(reactToSound ? "Reaccion al sonido activada." : "Reaccion al sonido desactivada.");
    return;
  }
  if (command=='H') {
    MochiUSB::log("K iniciar audio PC | J detener audio PC | T sonido | R grabar | P escuchar");
    MochiUSB::log("N F C O S D expresion | G guino | B parpadeo | M mover | A automatico");
    return;
  }
  uint8_t selected=255;
  if (command=='N') selected=NORMAL;
  if (command=='F') selected=FELIZ;
  if (command=='C') selected=CURIOSO;
  if (command=='O') selected=SORPRESA;
  if (command=='S') selected=SOMNOLIENTO;
  if (command=='D') selected=DORMIDO;
  if (selected!=255) { automatic=false; setMood(selected,now); return; }
  if (command=='A') { automatic=true; setMood(NORMAL,now); }
  if (command=='G') { winkActive=true; winkStarted=now; }
  if (command=='B') { blinkActive=true; secondBlink=false; blinkStarted=now; }
  if (command=='M' || command=='X' || command=='Y') {
    movingEnabled=command=='M' ? !movingEnabled : command=='Y'; movementDuration=0;
    fromX=toX=centerX; fromY=toY=centerY; nextMovement=now+1000;
  }
}
// Recursos compartidos para comandos y recepcion USB.
// Deben declararse antes de setup(), donde se crea pcCommands.
static QueueHandle_t pcCommands=nullptr;
static MochiPCLink::Decoder usbDecoder;
static uint32_t lastUSBByte=0;
void mochiUSBReceiver(void*);
void setup() {
  // HWCDC y USBCDC arrancan normalmente con solo 256 bytes de RX.
  // Un paquete PCM de 20 ms mide 650+ bytes (estereo: ~980): ampliar ANTES de begin().
  const size_t rxCapacity=Serial.setRxBufferSize(8192);
  Serial.begin(921600);
#if ARDUINO_USB_CDC_ON_BOOT
  Serial.setTxTimeoutMs(0);
#endif
  MochiUSB::begin();
  pinMode(PIN_BOTON,INPUT_PULLUP);
  MochiUSB::logf("Mochi 3.5 unificado: USB RX buffer=%lu bytes (objetivo: 8192), PCM12 mono y estereo, LLM.\n",
    static_cast<unsigned long>(rxCapacity));
  SPI.begin(TFT_SCLK,-1,TFT_MOSI,TFT_CS);
  tft.init(240,280); tft.setSPISpeed(SPI_HZ); tft.setRotation(ROTACION);
  tft.fillScreen(FONDO);
  memoryOK=canvas.getBuffer()!=nullptr;
  if (!memoryOK) {
    tft.setCursor(12,125); tft.setTextColor(0xFFFF); tft.setTextSize(1);
    tft.print("Falta RAM: activa PSRAM");
    MochiUSB::log("No se pudo reservar el lienzo. Revisa PSRAM o el uso de RAM.");
    return;
  }
  randomSeed(esp_random());
  const uint32_t now=millis();
  centerX=fromX=toX=SCREEN_W*0.5f; centerY=fromY=toY=SCREEN_H*0.5f-14;
  drawCenterX=centerX; drawCenterY=centerY;
  mood=NORMAL; pose=poseFrom=POSES[NORMAL];
  automatic=AUTO_ANIMACIONES; movingEnabled=MOVER_AL_INICIO;
  blinkActive=secondBlink=winkActive=havePrevious=false;
  gazeX=gazeY=gazeFromX=gazeFromY=gazeToX=gazeToY=breathPhase=0;
  movementDuration=0; movementStarted=gazeStarted=moodStarted=lastFrame=now;
  nextMovement=now+4200; nextGaze=now+1200; nextMood=now+6500;
  nextBlink=now+2300; nextWink=now+16000;
  composeFace(now,1); presentFace();
  AudioMochi::begin();
  pcCommands=xQueueCreate(32,sizeof(char));
  // Prioridad mayor que la tarea de dibujo. La lectura de USB no se bloquea
  // por el envio de pixeles a la TFT.
  if (!pcCommands || xTaskCreate(mochiUSBReceiver,"mochi_usb_rx",4096,nullptr,3,nullptr)!=pdPASS)
    MochiUSB::log("ERROR: no se pudo iniciar receptor USB protegido.");
  MochiUSB::log("Mochi listo. Ventana de voz: Mochi_PC. LLM: boton BOOT para hablar.");
}
// Protocolo protegido PC->ESP32: COBS([A5 5A | C/A/B/S | len16 | datos | CRC16]) + 00.
// Los comandos no se pueden confundir con PCM, ni siquiera si se pierde un byte.
// Una tarea FreeRTOS independiente recibe USB mientras la pantalla dibuja.
void mochiUSBReceiver(void*) {
  uint8_t rxChunk[256];
  for (;;) {
    size_t processed=0;
    while (processed<4096) {
      const int available=Serial.available();
      if (available<=0) break;
      size_t wanted=static_cast<size_t>(available);
      if (wanted>sizeof(rxChunk)) wanted=sizeof(rxChunk);
      const size_t got=Serial.read(rxChunk,wanted);
      if (!got) break;
      const uint32_t now=millis();
      if (lastUSBByte && now-lastUSBByte>500) usbDecoder.reset();
      lastUSBByte=now;
      for (size_t i=0;i<got;++i) {
        if (usbDecoder.feed(rxChunk[i])) {
          if (usbDecoder.type=='C') {
            const char command=static_cast<char>(usbDecoder.buffer[0]);
            if (pcCommands) xQueueSend(pcCommands,&command,0);
          } else if (usbDecoder.type=='A') {
            AudioMochi::pushPC(usbDecoder.buffer,usbDecoder.length);
          } else if (usbDecoder.type=='B') {
            AudioMochi::pushPC12(usbDecoder.buffer,usbDecoder.length);
          } else if (usbDecoder.type=='S') {
            AudioMochi::pushPC12Stereo(usbDecoder.buffer,usbDecoder.length);
          } else if (usbDecoder.type=='T') {
            // Solo ASCII imprimible: la fuente de la TFT no tiene tildes.
            portENTER_CRITICAL(&captionLock);
            size_t n=0;
            for (size_t k=0;k<usbDecoder.length && n<MochiPCLink::MAX_TEXT;++k) {
              const char c=static_cast<char>(usbDecoder.buffer[k]);
              captionIncoming[n++]=(c>=32 && c<127) ? c : ' ';
            }
            captionIncoming[n]=0; captionNew=true; llmHost=true;
            portEXIT_CRITICAL(&captionLock);
          }
        }
      }
      processed+=got;
    }
    // Vaciar primero el buffer USB, sin cargar el procesador con
    // miles de llamadas a Serial.available() individuales por segundo.
    vTaskDelay(pdMS_TO_TICKS(processed ? 1 : 2));
  }
}

void loop() {
  const uint32_t now=millis();
  static uint32_t lastPcDiagnostic=0;
  static bool pcWasStreaming=false;
  const bool pcStreaming=AudioMochi::snapshot().state==AudioMochi::State::Streaming;
  if ((pcStreaming && now-lastPcDiagnostic>=1000) || (pcWasStreaming && !pcStreaming)) {
    lastPcDiagnostic=now;
    MochiUSB::logf("USB PC: OK=%lu BAD=%lu cola=%u drop=%lu played=%lu audio=%lu i2sErr=%lu under=%lu rebuf=%lu picoCola=%lu\n",
      static_cast<unsigned long>(usbDecoder.goodAudio),
      static_cast<unsigned long>(usbDecoder.badFrames),
      static_cast<unsigned int>(AudioMochi::pcQueue ? uxQueueMessagesWaiting(AudioMochi::pcQueue) : 0),
      static_cast<unsigned long>(AudioMochi::pcDropped),
      static_cast<unsigned long>(AudioMochi::pcPlayed),
      static_cast<unsigned long>(AudioMochi::pcNonzero),
      static_cast<unsigned long>(AudioMochi::pcI2sErrors),
      static_cast<unsigned long>(AudioMochi::pcUnderruns),
      static_cast<unsigned long>(AudioMochi::pcRebuffers),
      static_cast<unsigned long>(AudioMochi::pcHighWater));
  }
  pcWasStreaming=pcStreaming;
  char command;
  while (pcCommands && xQueueReceive(pcCommands,&command,0)==pdTRUE)
    handleCommand(command,now);
  pumpButton(now);
  if (!memoryOK) { delay(100); return; }
  updateLlm(now); drawCaption(now);
  const uint32_t dt=now-lastFrame;
  // Menos trabajo grafico mientras escuchamos musica: USB e I2S tienen
  // prioridad y los ojos siguen animados a 12.5 fps.
  const uint32_t desiredFrameMs=pcStreaming ? 80 : FRAME_MS;
  if (dt<desiredFrameMs) { delay(1); return; }
  lastFrame=now;
  updateAudioFace(now);
  updatePose(now); updateMotion(now,dt>100 ? 100 : dt);
  const float open=blinkOpenness(now); updateWink(now);
  composeFace(now,open); presentFace();
}
