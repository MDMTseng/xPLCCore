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
//     2-3    angle 0..4095, little-endian UINT (AS5600 ANGLE: filtered, 2 LSB hysteresis)
//     4      AS5600 STATUS: bit5 MD magnet detected, bit4 ML too weak,
//            bit3 MH too strong; bit0 set by us = sensor not answering
//     5-8    multi-turn position in counts, little-endian DINT; unwrapped
//            on the ESP32, starts at the power-up angle (not retained)
//     9      I2C read error counter (wraps)
//     10     AGC (automatic gain; mid-range = good magnet distance)
//     11-12  position probe: glitches in BufferOut 12-15 (UINT, wraps)
//     13-14  position probe: max |d2| over the last second / 16 (UINT)
//            (the SYNC0 interval max / min moved to the serial line only)
//     15     1 = cycles driven by the SYNC0 interrupt, 0 = polling fallback
//   BufferOut (PLC -> slave)
//     0      bit 0 drives the on-board LED
//     1      echoed on BufferIn[1]
//     2      stepper control: bit0 enable, bit1 fault reset (with enable off)
//     4-7    stepper target position in steps, little-endian DINT (CSP)
//   BufferIn, stepper (see stepper.h)
//     16-19  stepper actual position in steps (DINT) -- steps emitted
//     20     stepper status: bit0 enabled, bit1 fault, bit2 moving
//     21     stepper fault: 0 none, 1 comm lost, 2 setpoint jump
//   Bus timing probe (the delta drives saw stale targets, 2026-09-30):
//   BufferOut
//     8-11   PLC cycle counter, +1 per EtherCAT task cycle (UDINT)
//     12-15  a copy of EAxis0's Target Position PDO (DINT): the same
//            position stream the drive gets, checked here at SYNC0 like
//            the ASDA scope recordings -- second difference per cycle,
//            |d2| > POS_GLITCH_THRESH counts as a glitch
//   BufferIn
//     22-23  stale: SYNC0 cycles that read the same counter as the last one
//            (the frame had not arrived yet) -- the drives' symptom (UINT, wraps)
//     24-25  skipped: counter steps > 1 (UINT, wraps)
//     26-27  odd SYNC0 intervals: SYNC0-to-SYNC0 (micros() in the ISR) off
//            the SYNC0 cycle (ESC 0x09A0) by more than ODD_US (UINT, wraps)
//     28-29  deviation of the last odd interval, us (INT16)
//     30-31  late: cycles whose frame had not arrived by the next SYNC0,
//            last second (UINT)
//   Arrival = the first poll after MainTask that reads a new counter
//   (the whole output area re-read, ~50 us per poll: the resolution);
//   SYNC0 time = micros() in the interrupt. (Polling SM2 status 0x0815
//   bit0 instead never saw it set.)
//
// Synchronisation: DC_SYNC. The master runs distributed clocks on this
// slave like on the servo drives (DC_Sync opmode, AssignActivate #x300,
// SYNC0 every 1000 us). The LAN9252 maps SYNC0 to its AL event and raises
// INT (GPIO17); the ISR only wakes a high-priority task (core 1), which
// runs MainTask, hands the stepper its next RMT sequence, and wakes the
// encoder task (core 0) to sample. Every sample is taken at a fixed point
// of the shared DC time base; it reaches the PLC one cycle later.
// This needs the rev-1 EEPROM config (0x0151 = 0x6E), which dumpEscConfig()
// verifies at boot, and the rev-1 ESI (esi/EasyCAT_V2_0.xml) in CODESYS.
// If no interrupt arrives for 100 ms (master stopped, slave not yet in
// SAFEOP with DC running) the task falls back to polling so the slave can
// still come up.
//
// Serial, 115200: boot log, then one JSON status line every 100 ms.

#include <Arduino.h>
#include <SPI.h>
#include <Wire.h>
#include "EasyCAT.h"
#include "stepper.h"

static const uint8_t PIN_SCS = 5;
static const uint8_t PIN_LED = 2;
static const uint8_t PIN_SDA = 21;
static const uint8_t PIN_SCL = 22;
static const uint8_t PIN_INT = 17;

static const uint8_t AS5600_ADDR = 0x36;
static const uint8_t REG_STATUS = 0x0B;
static const uint8_t REG_CONF = 0x07;       // 0x07 high byte, 0x08 low byte
static const uint8_t REG_ANGLE = 0x0E;      // filtered + hysteresis (RAW_ANGLE 0x0C is neither)
static const uint8_t REG_AGC = 0x1A;

// AS5600 CONF, volatile (reset on power-up; nothing is burned to OTP):
//   SF   bits 9:8   = 00   slow filter 16x -- least noise, ~2.2 ms step response
//   FTH  bits 12:10 = 001  fast filter above 6 LSB -- real motion is not lagged
//   HYST bits 3:2   = 10   2 LSB output hysteresis -- no 864/865 flicker
// Hysteresis only acts on ANGLE, so that is the register read each cycle;
// with ZPOS/MPOS/MANG at their defaults it spans the same 0..4095.
static const uint16_t AS5600_CONF = (0x1 << 10) | (0x0 << 8) | (0x2 << 2);

static const uint8_t STATUS_NO_SENSOR = 0x01;

EasyCAT EASYCAT(PIN_SCS, DC_SYNC);

static TaskHandle_t ecatTask = nullptr;
static volatile uint32_t irqCount = 0;
static volatile uint32_t irqUs = 0;                 // micros() at the last SYNC0

// Bus timing probe (inputs 22-31).
static uint32_t seqPrev = 0;
static bool seqValid = false;
static uint16_t staleCount = 0, skipCount = 0;
static uint16_t arrMinUs = 0, arrMaxUs = 0, lateCount = 0;   // last full second
// Odd SYNC0 intervals (inputs 26-29): a shift of the DC system time moves
// every slave's SYNC0 at once.
static const int32_t ODD_US = 3;
static uint16_t oddCount = 0;
static int16_t oddLastDev = 0;
static uint32_t nomUs = 0;                          // SYNC0 cycle, from 0x09A0
static uint32_t oddPrevIrq = 0;
static bool oddValid = false;
static uint32_t oddHist[7] = {0};                   // |dev| 0,1,2,3,4-9,10-99,>=100 us; serial
static uint32_t arrMinAcc = 0xFFFFFFFF, arrMaxAcc = 0, lateAcc = 0;
// Position probe (inputs 11-14).
static const int32_t POS_GLITCH_THRESH = 150000;
static int32_t posPrev = 0, velPrev = 0;
static int posValid = 0;
static uint16_t posGlitch = 0, posD2Max16 = 0;
static uint32_t posD2MaxAcc = 0;
// First poll after MainTask (the probe's floor: a frame already in by then
// reads as arriving at the first poll) and how often that happened; serial only.
static uint32_t poll0MinAcc = 0xFFFFFFFF, poll0MaxAcc = 0, earlyAcc = 0;
static uint16_t poll0Min = 0, poll0Max = 0, earlyCount = 0;
static uint16_t intervalMax = 0, intervalMin = 0;   // last full second, us
static uint32_t workMaxUs = 0;                      // longest cycle, last second
static bool synced = false;

static void ecatTaskFn(void *);
static void encTaskFn(void *);
static TaskHandle_t encTask;

static void IRAM_ATTR onEcatIrq() {
  irqUs = micros();
  irqCount++;
  BaseType_t woken = pdFALSE;
  vTaskNotifyGiveFromISR(ecatTask, &woken);
  if (woken) portYIELD_FROM_ISR();
}

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
  // 400 kHz: one raw-angle read then takes ~0.1 ms. At 100 kHz the three
  // reads per cycle took over 1 ms and every other SYNC0 was missed
  // (measured 2 ms task interval with a 1 kHz interrupt).
  Wire.begin(PIN_SDA, PIN_SCL, 400000);
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
  // Every cycle: the angle only. STATUS and AGC change slowly; read them
  // every 64th cycle so the per-cycle I2C time stays well inside 1 ms.
  static uint8_t slow = 0;
  uint8_t b[2];
  if (!readRegs(REG_ANGLE, b, 2)) {
    errCount++;
    sensorStatus = STATUS_NO_SENSOR;
    sensorPresent = false;
    return;
  }
  uint16_t a = ((uint16_t)(b[0] & 0x0F) << 8) | b[1];
  if ((slow++ & 0x3F) == 0) {
    uint8_t st, g;
    if (readRegs(REG_STATUS, &st, 1)) sensorStatus = st & 0x38;
    if (readRegs(REG_AGC, &g, 1)) agc = g;
  }

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

// Direct LAN9252 system register access over SPI (read 0x03 / write 0x02,
// 16-bit address, little-endian), used for the read-only ESC dump below.
static uint32_t sysRead(uint16_t addr) {
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

static void sysWrite(uint16_t addr, uint32_t v) {
  SPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE0));
  digitalWrite(PIN_SCS, LOW);
  SPI.transfer(0x02);
  SPI.transfer(addr >> 8);
  SPI.transfer(addr & 0xFF);
  for (int i = 0; i < 4; i++) SPI.transfer((v >> (8 * i)) & 0xFF);
  digitalWrite(PIN_SCS, HIGH);
  SPI.endTransaction();
}

// Read an EtherCAT core (ESC) register through the CSR indirect window
// (ECAT_CSR_CMD 0x0304 / ECAT_CSR_DATA 0x0300).
static uint32_t escRead(uint16_t addr, uint8_t len) {
  sysWrite(0x0304, 0xC0000000UL | ((uint32_t)len << 16) | addr);
  for (int i = 0; i < 1000 && (sysRead(0x0304) & 0x80000000UL); i++) {}
  uint32_t v = sysRead(0x0300);
  return len >= 4 ? v : v & ((1UL << (8 * len)) - 1);
}

// The config area the ESC loaded from EEPROM at power-up. 0x0151 decides
// whether DC works: bit2 SYNC0 output, bit3 SYNC0 -> AL event (interrupt).
static void dumpEscConfig() {
  uint32_t pdi = escRead(0x0140, 2);
  uint32_t cfg = escRead(0x0150, 2);
  uint32_t alias = escRead(0x0012, 2);
  uint32_t dcAct = escRead(0x0981, 1);
  uint8_t sync = (cfg >> 8) & 0xFF;
  Serial.printf("ESC 0x0140 PDI ctrl=%02lX ESC cfg=%02lX | 0x0150 PDI cfg=%02lX | 0x0151 SYNC/LATCH cfg=%02X | alias=%lu | 0x0981 DC activation=%02lX\n",
                pdi & 0xFF, (pdi >> 8) & 0xFF, cfg & 0xFF, sync, alias, dcAct);
  Serial.printf("  SYNC0 output %s, SYNC0 -> AL event %s  => DC_SYNC %s\n",
                (sync & 0x04) ? "on" : "OFF", (sync & 0x08) ? "on" : "OFF",
                ((sync & 0x0C) == 0x0C) ? "POSSIBLE with this EEPROM" : "needs an EEPROM rewrite");
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
  if (sensorPresent) {
    Wire.beginTransmission(AS5600_ADDR);
    Wire.write(REG_CONF);
    Wire.write((uint8_t)(AS5600_CONF >> 8));
    Wire.write((uint8_t)(AS5600_CONF & 0xFF));
    Wire.endTransmission();
    uint8_t c[2] = {0, 0};
    readRegs(REG_CONF, c, 2);
    uint16_t got = ((uint16_t)(c[0] & 0x3F) << 8) | c[1];
    Serial.printf("AS5600 CONF 0x%04X (wanted 0x%04X): SF 16x, FTH 6 LSB, HYST 2 LSB %s\n",
                  got, AS5600_CONF, got == AS5600_CONF ? "ok" : "MISMATCH");
  }

  while (!EASYCAT.Init()) {
    Serial.println("EasyCAT Init FAILED -- retrying");
    digitalWrite(PIN_LED, !digitalRead(PIN_LED));
    delay(1000);
  }
  Serial.println("EasyCAT Init OK");
  dumpEscConfig();
  stepper::begin();

  // The library leaves INT push-pull active-high. Pick the edge away from
  // the idle level, so an inverted board (the Arduino shield has a MOSFET
  // inverter) works too.
  pinMode(PIN_INT, INPUT);
  int idle = digitalRead(PIN_INT);
  Serial.printf("INT idle level %d -> interrupt on %s edge\n", idle, idle ? "falling" : "rising");
  xTaskCreatePinnedToCore(encTaskFn, "enc", 4096, nullptr, configMAX_PRIORITIES - 2, &encTask, 0);
  xTaskCreatePinnedToCore(ecatTaskFn, "ecat", 4096, nullptr, configMAX_PRIORITIES - 1, &ecatTask, 1);
  attachInterrupt(digitalPinToInterrupt(PIN_INT), onEcatIrq, idle ? FALLING : RISING);
}

// Encoder on core 0. The Arduino I2C driver costs hundreds of us per
// transaction; inside the EtherCAT task (core 1) the cycles with the
// periodic STATUS/AGC reads took 1071 us -- longer than the 1 ms cycle.
// The EtherCAT task now only wakes this task at SYNC0 and publishes the
// last completed sample: the sample instant stays locked to the DC clock,
// the value reaches the PLC one cycle later (fixed latency).
static volatile uint32_t encWorkMaxUs = 0;

static void encTaskFn(void *) {
  uint32_t winStart = micros(), mx = 0;
  for (;;) {
    if (ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(100)) == 0) continue;
    uint32_t t0 = micros();
    sampleEncoder();
    uint32_t w = micros() - t0;
    if (w > mx) mx = w;
    if (t0 - winStart >= 1000000) { encWorkMaxUs = mx; mx = 0; winStart = t0; }
  }
}

// One EtherCAT cycle: fill the inputs, then exchange. MainTask() copies
// BufferIn to the LAN9252, so filling it first sends the freshest values.
static void ecatCycle() {
  static uint8_t counter = 0;
  if (encTask) xTaskNotifyGive(encTask);   // sample the encoder now, on core 0

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
  in[11] = posGlitch & 0xFF;
  in[12] = posGlitch >> 8;
  in[13] = posD2Max16 & 0xFF;
  in[14] = posD2Max16 >> 8;
  in[15] = synced ? 1 : 0;
  int32_t sp = stepper::position();
  in[16] = sp & 0xFF;
  in[17] = (sp >> 8) & 0xFF;
  in[18] = (sp >> 16) & 0xFF;
  in[19] = (sp >> 24) & 0xFF;
  in[20] = stepper::status();
  in[21] = stepper::fault;
  in[22] = staleCount & 0xFF;
  in[23] = staleCount >> 8;
  in[24] = skipCount & 0xFF;
  in[25] = skipCount >> 8;
  in[26] = oddCount & 0xFF;
  in[27] = oddCount >> 8;
  in[28] = (uint16_t)oddLastDev & 0xFF;
  in[29] = (uint16_t)oddLastDev >> 8;
  in[30] = lateCount & 0xFF;
  in[31] = lateCount >> 8;

  escState = EASYCAT.MainTask();
  digitalWrite(PIN_LED, EASYCAT.BufferOut.Byte[0] & 0x01);

  // Fresh setpoint from this frame -> this cycle's interpolation segment.
  const uint8_t *out = EASYCAT.BufferOut.Byte;

  // PLC cycle counter: the same value as last SYNC0 = stale frame.
  uint32_t seq = (uint32_t)out[8] | ((uint32_t)out[9] << 8) |
                 ((uint32_t)out[10] << 16) | ((uint32_t)out[11] << 24);
  if (synced && (escState & 0x0F) == 0x08) {
    if (seqValid) {
      uint32_t d = seq - seqPrev;
      if (d == 0) staleCount++;
      else if (d > 1 && d < 1000) skipCount += (uint16_t)(d - 1);
    }
    seqValid = true;
  } else {
    seqValid = false;
  }
  seqPrev = seq;

  // Position probe: the drive's target stream, as received at this SYNC0.
  int32_t pos = (int32_t)((uint32_t)out[12] | ((uint32_t)out[13] << 8) |
                          ((uint32_t)out[14] << 16) | ((uint32_t)out[15] << 24));
  if (synced && (escState & 0x0F) == 0x08) {
    int32_t v = pos - posPrev;
    int32_t d2 = v - velPrev;
    if (posValid >= 2) {
      uint32_t a = d2 < 0 ? (uint32_t)(-(int64_t)d2) : (uint32_t)d2;
      if (a > posD2MaxAcc) posD2MaxAcc = a;
      if (a > (uint32_t)POS_GLITCH_THRESH) posGlitch++;
    } else {
      posValid++;
    }
    velPrev = v;
  } else {
    posValid = 0;
  }
  posPrev = pos;
  int32_t target = (int32_t)((uint32_t)out[4] | ((uint32_t)out[5] << 8) |
                             ((uint32_t)out[6] << 16) | ((uint32_t)out[7] << 24));
  bool inOp = synced && (escState & 0x0F) == 0x08;
  stepper::newSetpoint(out[2], target, inOp);
}

static void ecatTaskFn(void *) {
  uint32_t last = 0, winStart = micros();
  uint32_t mx = 0, mn = 0xFFFFFFFF, wmx = 0;
  for (;;) {
    // Frame interrupt, or a 100 ms timeout -> poll so INIT->OP can happen.
    bool irq = ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(100)) > 0;
    if (!irq) {
      synced = false;
      oddValid = false;
      nomUs = 0;
      ecatCycle();
      continue;
    }
    uint32_t now = micros();
    if (synced) {
      uint32_t d = now - last;
      if (d > mx) mx = d;
      if (d < mn) mn = d;
    }
    last = now;
    synced = true;
    if (now - winStart >= 1000000) {
      intervalMax = mx > 65535 ? 65535 : mx;
      intervalMin = mn == 0xFFFFFFFF ? 0 : mn;
      workMaxUs = wmx;
      mx = 0; mn = 0xFFFFFFFF; wmx = 0; winStart = now;
      arrMinUs = arrMinAcc == 0xFFFFFFFF ? 0 : (arrMinAcc > 65535 ? 65535 : arrMinAcc);
      arrMaxUs = arrMaxAcc > 65535 ? 65535 : arrMaxAcc;
      lateCount = lateAcc > 65535 ? 65535 : lateAcc;
      arrMinAcc = 0xFFFFFFFF; arrMaxAcc = 0; lateAcc = 0;
      posD2Max16 = posD2MaxAcc / 16 > 65535 ? 65535 : posD2MaxAcc / 16;
      posD2MaxAcc = 0;
      poll0Min = poll0MinAcc == 0xFFFFFFFF ? 0 : poll0MinAcc;
      poll0Max = poll0MaxAcc > 65535 ? 65535 : poll0MaxAcc;
      earlyCount = earlyAcc > 65535 ? 65535 : earlyAcc;
      poll0MinAcc = 0xFFFFFFFF; poll0MaxAcc = 0; earlyAcc = 0;
    }
    uint32_t tSync = irqUs;
    uint32_t seen = irqCount;
    // SYNC0 interval from the ISR timestamps (no task wake-up jitter).
    if (nomUs == 0) {
      uint32_t c = EASYCAT.ReadEsc(0x09A0, 4);
      if (c >= 100000UL && c < 100000000UL) nomUs = (c + 500) / 1000;
    }
    if (oddValid && nomUs) {
      int32_t dev = (int32_t)(tSync - oddPrevIrq) - (int32_t)nomUs;
      int32_t a = dev < 0 ? -dev : dev;
      oddHist[a < 4 ? a : (a < 10 ? 4 : (a < 100 ? 5 : 6))]++;
      if (a > ODD_US && a < 100000) {
        oddCount++;
        oddLastDev = dev > 32767 ? 32767 : (dev < -32767 ? -32767 : (int16_t)dev);
      }
    }
    oddPrevIrq = tSync;
    oddValid = true;
    ecatCycle();
    // CPU time of one cycle (encoder read, process data, stepper setup):
    // with the pulses in RMT this is all the CPU spends per millisecond.
    uint32_t w = micros() - now;
    if (w > wmx) wmx = w;

    // Frame arrival: MainTask read the outputs (clearing SM2 status bit0);
    // poll until the next frame writes them, or the next SYNC0 comes.
    if ((escState & 0x0F) == 0x08) {
      bool got = false;
      uint32_t arr = 0;
      static uint8_t peek[64];
      bool first = true;
      while (irqCount == seen) {
        if (first) {
          uint32_t p0 = micros() - tSync;
          if (p0 < poll0MinAcc) poll0MinAcc = p0;
          if (p0 > poll0MaxAcc) poll0MaxAcc = p0;
        }
        EASYCAT.PeekOutputs(peek);
        uint32_t q = (uint32_t)peek[8] | ((uint32_t)peek[9] << 8) |
                     ((uint32_t)peek[10] << 16) | ((uint32_t)peek[11] << 24);
        if (q != seqPrev) {                  // the next frame's counter is in
          arr = micros() - tSync;
          got = true;
          if (first) earlyAcc++;
          break;
        }
        first = false;
      }
      if (got) {
        if (arr < arrMinAcc) arrMinAcc = arr;
        if (arr > arrMaxAcc) arrMaxAcc = arr;
      } else {
        lateAcc++;
      }
    }
  }
}

void loop() {
  static uint32_t lastPrint = 0;
  // Stop the stepper even if the EtherCAT task stops being woken at all.
  stepper::watchdog();
  uint32_t now = millis();
  if (now - lastPrint >= 100) {
    lastPrint = now;
    Serial.printf("{\"esc\":\"%s\",\"sync\":%d,\"irq\":%lu,\"int_min\":%u,\"int_max\":%u,"
                  "\"angle\":%u,\"pos\":%ld,\"status\":%u,\"agc\":%u,\"err\":%u,"
                  "\"step\":%ld,\"st_cmd\":%ld,\"st_status\":%u,\"st_fault\":%u,\"work_max_us\":%lu,"
                  "\"enc_max_us\":%lu,\"stale\":%u,\"skip\":%u,\"odd\":%u,\"odd_dev\":%d,\"odd_hist\":[%lu,%lu,%lu,%lu,%lu,%lu,%lu],\"arr_min\":%u,\"arr_max\":%u,\"late\":%u,\"poll0_min\":%u,\"poll0_max\":%u,\"early\":%u,\"pos_glitch\":%u,\"pos_d2max\":%lu}\n",
                  escName(escState), synced ? 1 : 0, (unsigned long)irqCount,
                  intervalMin, intervalMax, rawAngle, (long)position,
                  sensorStatus, agc, errCount,
                  (long)stepper::position(), (long)stepper::cmdPos, stepper::status(),
                  stepper::fault, (unsigned long)workMaxUs, (unsigned long)encWorkMaxUs,
                  staleCount, skipCount, oddCount, (int)oddLastDev,
                  (unsigned long)oddHist[0], (unsigned long)oddHist[1], (unsigned long)oddHist[2], (unsigned long)oddHist[3],
                  (unsigned long)oddHist[4], (unsigned long)oddHist[5], (unsigned long)oddHist[6],
                  arrMinUs, arrMaxUs, lateCount, poll0Min, poll0Max, earlyCount,
                  posGlitch, (unsigned long)posD2Max16 * 16);
  }
  delay(2);
}
