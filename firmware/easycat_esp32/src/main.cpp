// EasyCAT PRO + ESP32: EtherCAT slave with an I2C absolute encoder.
//
// Encoder: QY2204-IIC. Its pinout (GND, VCC, OUT, SCL, SDA) is the AS5600
// magnetic angle sensor's; the boot-time I2C scan confirms the chip
// answers at 0x36. 12-bit angle, 4096 counts per turn.
//
// Process image, standard 32+32 bytes (EasyCAT PRO stock EEPROM / ESI):
//
//   BufferIn (slave -> PLC)
//     0      heartbeat counter, +1 per MainTask cycle
//     1      echo of BufferOut[1] (round-trip check)
//     2-3    raw angle 0..4095, little-endian UINT
//     4      AS5600 STATUS: bit5 MD magnet detected, bit4 ML too weak,
//            bit3 MH too strong; bit0 set by us = sensor not answering
//     5-8    multi-turn position in counts, little-endian DINT; unwrapped
//            on the ESP32, starts at the power-up angle (not retained)
//     9      I2C read error counter (wraps)
//     10     AGC (automatic gain; mid-range = good magnet distance)
//   BufferOut (PLC -> slave)
//     0      bit 0 drives the on-board LED
//     1      echoed on BufferIn[1]
//
// Serial, 115200: boot log, then one JSON status line every 100 ms for
// tools/ecat_esp_dashboard.py.

#include <Arduino.h>
#include <SPI.h>
#include <Wire.h>
#include "EasyCAT.h"

static const uint8_t PIN_SCS = 5;
static const uint8_t PIN_LED = 2;
static const uint8_t PIN_SDA = 21;
static const uint8_t PIN_SCL = 22;

static const uint8_t AS5600_ADDR = 0x36;
static const uint8_t REG_STATUS = 0x0B;
static const uint8_t REG_RAW_ANGLE = 0x0C;
static const uint8_t REG_AGC = 0x1A;

static const uint8_t STATUS_NO_SENSOR = 0x01;

EasyCAT EASYCAT(PIN_SCS);

static bool sensorPresent = false;
static uint16_t rawAngle = 0;
static uint8_t sensorStatus = STATUS_NO_SENSOR;
static uint8_t agc = 0;
static int32_t position = 0;
static uint8_t errCount = 0;
static uint8_t escState = 0;

static bool readRegs(uint8_t reg, uint8_t *buf, uint8_t n) {
  Wire.beginTransmission(AS5600_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(AS5600_ADDR, n) != n) return false;
  for (uint8_t i = 0; i < n; i++) buf[i] = Wire.read();
  return true;
}

static int i2cScan(const char *label) {
  Serial.printf("I2C scan %s:", label);
  int found = 0;
  for (uint8_t a = 1; a < 127; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      Serial.printf(" 0x%02X", a);
      found++;
    }
  }
  Serial.println(found ? "" : " nothing");
  return found;
}

// Try the wiring as specified, then the likely mistakes, and report which
// one sees a device. Ends configured as specified (SDA 21, SCL 22).
static void i2cProbe() {
  struct { uint8_t sda, scl; uint32_t hz; const char *label; } tries[] = {
    {PIN_SDA, PIN_SCL, 100000, "SDA21/SCL22 100k"},
    {PIN_SCL, PIN_SDA, 100000, "SDA22/SCL21 100k (swapped)"},
  };
  for (auto &t : tries) {
    Wire.end();
    pinMode(t.sda, INPUT_PULLUP);
    pinMode(t.scl, INPUT_PULLUP);
    Serial.printf("  idle levels: GPIO%u=%d GPIO%u=%d (1 = pulled up)\n",
                  t.sda, digitalRead(t.sda), t.scl, digitalRead(t.scl));
    Wire.begin(t.sda, t.scl, t.hz);
    i2cScan(t.label);
  }
  Wire.end();
  Wire.begin(PIN_SDA, PIN_SCL, 100000);
}

// Read the sensor and unwrap the angle into a multi-turn position.
static void sampleEncoder() {
  static bool first = true;
  static uint32_t retryAt = 0;
  // Without a sensor every read fails slowly; retry once a second instead
  // of every cycle so MainTask keeps its 1 ms rhythm.
  if (!sensorPresent) {
    if (millis() < retryAt) return;
    retryAt = millis() + 1000;
    uint8_t probe;
    sensorPresent = readRegs(REG_STATUS, &probe, 1);
    if (!sensorPresent) { errCount++; sensorStatus = STATUS_NO_SENSOR; return; }
    Serial.println("AS5600 appeared");
  }
  uint8_t b[2];
  uint8_t st;
  if (!readRegs(REG_RAW_ANGLE, b, 2) || !readRegs(REG_STATUS, &st, 1)) {
    errCount++;
    sensorStatus = STATUS_NO_SENSOR;
    sensorPresent = false;
    return;
  }
  uint16_t a = ((uint16_t)(b[0] & 0x0F) << 8) | b[1];
  sensorStatus = st & 0x38;
  uint8_t g;
  if (readRegs(REG_AGC, &g, 1)) agc = g;

  if (first) {
    first = false;
  } else {
    int16_t d = (int16_t)a - (int16_t)rawAngle;
    if (d > 2048) d -= 4096;
    if (d < -2048) d += 4096;
    position += d;
  }
  rawAngle = a;
}

static const char *escName(uint8_t s) {
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
  Serial.println("EasyCAT PRO + ESP32 + AS5600");

  i2cProbe();
  uint8_t st;
  sensorPresent = readRegs(REG_STATUS, &st, 1);
  Serial.printf("AS5600 at 0x36: %s\n", sensorPresent ? "yes" : "NOT FOUND");

  while (!EASYCAT.Init()) {
    Serial.println("EasyCAT Init FAILED -- retrying");
    digitalWrite(PIN_LED, !digitalRead(PIN_LED));
    delay(1000);
  }
  Serial.println("EasyCAT Init OK");
}

void loop() {
  static uint8_t counter = 0;
  static uint32_t lastTask = 0;
  static uint32_t lastPrint = 0;

  uint32_t now = millis();
  if (now - lastTask >= 1) {
    lastTask = now;
    sampleEncoder();
    escState = EASYCAT.MainTask();

    uint8_t *in = EASYCAT.BufferIn.Byte;
    in[0] = counter++;
    in[1] = EASYCAT.BufferOut.Byte[1];
    in[2] = rawAngle & 0xFF;
    in[3] = rawAngle >> 8;
    in[4] = sensorStatus;
    in[5] = position & 0xFF;
    in[6] = (position >> 8) & 0xFF;
    in[7] = (position >> 16) & 0xFF;
    in[8] = (position >> 24) & 0xFF;
    in[9] = errCount;
    in[10] = agc;
    digitalWrite(PIN_LED, EASYCAT.BufferOut.Byte[0] & 0x01);
  }

  if (now - lastPrint >= 100) {
    lastPrint = now;
    Serial.printf("{\"esc\":\"%s\",\"angle\":%u,\"deg\":%.2f,\"pos\":%ld,"
                  "\"status\":%u,\"agc\":%u,\"err\":%u,\"out0\":%u,\"out1\":%u}\n",
                  escName(escState), rawAngle, rawAngle * 360.0 / 4096.0,
                  (long)position, sensorStatus, agc, errCount,
                  EASYCAT.BufferOut.Byte[0], EASYCAT.BufferOut.Byte[1]);
  }
}
