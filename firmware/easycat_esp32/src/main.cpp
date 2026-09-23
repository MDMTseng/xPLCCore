// EasyCAT PRO + ESP32 bring-up firmware.
//
// Standard 32+32 byte process image (the EasyCAT PRO's stock EEPROM /
// EasyCAT.xml ESI). No master is needed for the first check: Init()
// resets the LAN9252 and reads its byte-order test register over SPI,
// so "Init OK" on the serial monitor proves the wiring.
//
// Once a master is running:
//   BufferIn.Byte[0]      free-running counter (slave -> master), shows
//                         the slave is alive
//   BufferIn.Byte[1..31]  loopback of BufferOut.Byte[1..31], so a value
//                         written by the master comes back on its inputs
//   BufferOut.Byte[0]     drives the on-board LED (bit 0)

#include <Arduino.h>
#include <SPI.h>
#include "EasyCAT.h"

static const uint8_t PIN_SCS = 5;
static const uint8_t PIN_LED = 2;  // ESP32 DevKit on-board LED

EasyCAT EASYCAT(PIN_SCS);

// Raw LAN9252 register read (SPI "read" 0x03, 16-bit address, 4 bytes,
// little-endian), independent of the library, for diagnosing wiring.
static uint32_t rawRead(uint16_t addr) {
  SPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE0));
  digitalWrite(PIN_SCS, LOW);
  SPI.transfer(0x03);
  SPI.transfer(addr >> 8);
  SPI.transfer(addr & 0xFF);
  uint32_t v = 0;
  for (int i = 0; i < 4; i++) v |= (uint32_t)SPI.transfer(0x00) << (8 * i);
  digitalWrite(PIN_SCS, HIGH);
  SPI.endTransaction();
  return v;
}

// What the raw BYTE_TEST (0x0064) value says about the wiring.
static const char *diagnose(uint32_t v) {
  if (v == 0x87654321) return "correct -- SPI works";
  if (v == 0xFFFFFFFF) return "all ones: MISO floating/high -- no power, MI not connected, or SCS wrong";
  if (v == 0x00000000) return "all zeros: MISO stuck low -- MI/MO swapped, or board unpowered";
  return "garbage: SPI mode/speed or a loose SCK/MOSI";
}

static const char *escState(uint8_t s) {
  switch (s & 0x0F) {
    case 0x01: return "INIT";
    case 0x02: return "PREOP";
    case 0x03: return "BOOT";
    case 0x04: return "SAFEOP";
    case 0x08: return "OP";
    default:   return "?";
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_LED, OUTPUT);
  delay(500);
  Serial.println();
  Serial.println("EasyCAT PRO + ESP32 test");

  // Retry forever and keep printing, so a monitor opened late still
  // sees why it failed.
  while (!EASYCAT.Init()) {
    SPI.begin();
    pinMode(PIN_SCS, OUTPUT);
    uint32_t v = rawRead(0x0064);
    Serial.printf("Init FAILED  BYTE_TEST=0x%08lX  %s\n", (unsigned long)v, diagnose(v));
    digitalWrite(PIN_LED, !digitalRead(PIN_LED));
    delay(1000);
  }
  Serial.println("Init OK: LAN9252 answered on SPI (byte test 0x87654321)");
}

void loop() {
  static uint8_t counter = 0;
  static uint32_t lastTask = 0;
  static uint32_t lastPrint = 0;
  static uint8_t lastState = 0xFF;

  uint32_t now = millis();
  if (now - lastTask >= 1) {
    lastTask = now;
    uint8_t state = EASYCAT.MainTask();

    EASYCAT.BufferIn.Byte[0] = counter++;
    for (int i = 1; i < 32; i++) {
      EASYCAT.BufferIn.Byte[i] = EASYCAT.BufferOut.Byte[i];
    }
    digitalWrite(PIN_LED, EASYCAT.BufferOut.Byte[0] & 0x01);

    if (state != lastState) {
      Serial.printf("ESC state: %s (0x%02X)\n", escState(state), state);
      lastState = state;
    }
  }

  if (now - lastPrint >= 2000) {
    lastPrint = now;
    Serial.printf("alive  out[0..3] = %02X %02X %02X %02X\n",
                  EASYCAT.BufferOut.Byte[0], EASYCAT.BufferOut.Byte[1],
                  EASYCAT.BufferOut.Byte[2], EASYCAT.BufferOut.Byte[3]);
  }
}
