// Open-loop CSP stepper: STEP/DIR/EN, driven by a position setpoint the
// PLC sends every EtherCAT cycle (1 ms, DC SYNC0).
//
// The ESP32 only counts steps. Degrees, steps per revolution, velocity and
// acceleration live on the PLC, which computes the trajectory and sends
// the absolute target in steps each cycle (cyclic synchronous position).
//
// Interpolation: a 100 kHz timer runs a DDA. At each cycle start
// (newSetpoint, called right after MainTask delivers the setpoint)
// the move for this cycle is  target - stepPos  -- what was actually
// emitted, so steps left over from a late cycle roll into the next one
// instead of being lost. That distance is spread evenly over the
// TICKS_PER_CYCLE ticks: rate/TICKS steps per tick.
//
// Each step is one tick high, at least one tick low, so the ceiling is
// TICKS_PER_CYCLE/2 = 50 steps per cycle (50 kHz). A move larger than
// MAX_STEPS_PER_CYCLE in one cycle is a fault, not something to chase.
//
// Safety: the driver is disabled and stepping stops at once when
//   - the PLC clears the enable bit,
//   - the slave leaves OP or SYNC0 stops for COMM_TIMEOUT_US (cable pulled,
//     master stopped) -> fault 1,
//   - one cycle asks for more than MAX_STEPS_PER_CYCLE      -> fault 2.
// Faults latch until the PLC pulses the reset bit with enable off.

#pragma once
#include <Arduino.h>
#include "soc/gpio_struct.h"

namespace stepper {

static const uint8_t PIN_STEP = 25;
static const uint8_t PIN_DIR = 26;
static const uint8_t PIN_EN = 27;
static const bool EN_ACTIVE_LOW = true;   // most STEP/DIR drivers: EN low = enabled

static const uint32_t TICK_US = 10;                 // 100 kHz DDA
static const uint32_t TICKS_PER_CYCLE = 100;        // 1000 us / 10 us
static const int32_t MAX_STEPS_PER_CYCLE = 50;      // one tick high, one low
static const uint32_t COMM_TIMEOUT_US = 5000;

enum Fault : uint8_t { FAULT_NONE = 0, FAULT_COMM = 1, FAULT_JUMP = 2 };

// Control byte from the PLC.
static const uint8_t CTRL_ENABLE = 0x01;
static const uint8_t CTRL_RESET = 0x02;

// Status byte to the PLC.
static const uint8_t ST_ENABLED = 0x01;
static const uint8_t ST_FAULT = 0x02;
static const uint8_t ST_MOVING = 0x04;

static hw_timer_t *timer = nullptr;
static portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;

static volatile int32_t stepPos = 0;     // steps actually emitted
static volatile int32_t segTarget = 0;   // where this cycle ends
static volatile uint32_t rate = 0;       // steps this cycle (DDA increment)
static volatile uint32_t acc = 0;
static volatile int8_t dir = 1;
static volatile bool pulseHigh = false;
static volatile bool running = false;    // driver enabled and stepping allowed

static bool enabled = false;
static uint8_t fault = FAULT_NONE;
static uint32_t lastCycleUs = 0;

static void IRAM_ATTR onTick() {
  portENTER_CRITICAL_ISR(&mux);
  // Direct register writes: the ISR runs 100 000 times a second and must
  // not depend on digitalWrite being in IRAM.
  // Accumulate on every tick, including the one that ends a pulse; that
  // tick only defers the step. Accumulating only on low ticks would give
  // one step per three ticks at full rate (33 instead of 50 per cycle).
  bool pending = running && stepPos != segTarget;
  if (pending) acc += rate;
  if (pulseHigh) {
    GPIO.out_w1tc = (1UL << PIN_STEP);
    pulseHigh = false;
  } else if (pending && acc >= TICKS_PER_CYCLE) {
    acc -= TICKS_PER_CYCLE;
    GPIO.out_w1ts = (1UL << PIN_STEP);
    pulseHigh = true;
    stepPos += dir;
  }
  portEXIT_CRITICAL_ISR(&mux);
}

static void setDriver(bool on) {
  digitalWrite(PIN_EN, (on ^ EN_ACTIVE_LOW) ? HIGH : LOW);
}

// Stop stepping now; the position counter keeps what was emitted.
static void halt() {
  portENTER_CRITICAL(&mux);
  running = false;
  segTarget = stepPos;
  rate = 0;
  portEXIT_CRITICAL(&mux);
  setDriver(false);
  enabled = false;
}

static void begin() {
  pinMode(PIN_STEP, OUTPUT);
  pinMode(PIN_DIR, OUTPUT);
  pinMode(PIN_EN, OUTPUT);
  digitalWrite(PIN_STEP, LOW);
  digitalWrite(PIN_DIR, LOW);
  setDriver(false);
  timer = timerBegin(1, 80, true);                 // 80 MHz / 80 = 1 us
  timerAttachInterrupt(timer, &onTick, true);
  timerAlarmWrite(timer, TICK_US, true);
  timerAlarmEnable(timer);
}

// Called once per EtherCAT cycle with the fresh outputs from the PLC.
// inOp: the slave is in OP and this cycle was driven by SYNC0.
static void newSetpoint(uint8_t ctrl, int32_t target, bool inOp) {
  uint32_t now = micros();
  bool commOk = inOp && (lastCycleUs == 0 || now - lastCycleUs < COMM_TIMEOUT_US);
  lastCycleUs = now;

  if (!commOk) {
    if (enabled) fault = FAULT_COMM;
    halt();
    return;
  }
  if ((ctrl & CTRL_RESET) && !(ctrl & CTRL_ENABLE)) fault = FAULT_NONE;
  if (!(ctrl & CTRL_ENABLE) || fault != FAULT_NONE) {
    halt();
    return;
  }

  int32_t d = target - stepPos;
  if (d > MAX_STEPS_PER_CYCLE || d < -MAX_STEPS_PER_CYCLE) {
    // Also catches enabling with target != actual: the PLC must start
    // from the reported actual position.
    fault = FAULT_JUMP;
    halt();
    return;
  }

  if (!enabled) {
    setDriver(true);
    enabled = true;
  }
  int8_t nd = d < 0 ? -1 : 1;
  if (nd != dir) {
    // DIR changes here, at the cycle start; the first step of the new
    // segment is at least two ticks (20 us) later -- inside every common
    // driver's DIR setup time.
    digitalWrite(PIN_DIR, nd > 0 ? LOW : HIGH);
  }
  portENTER_CRITICAL(&mux);
  dir = nd;
  segTarget = target;
  rate = (uint32_t)(d < 0 ? -d : d);
  acc = 0;
  running = true;
  portEXIT_CRITICAL(&mux);
}

// Called by the main loop when no SYNC0 arrived: stepping must stop even
// if newSetpoint is no longer being called at all.
static void watchdog() {
  if (enabled && lastCycleUs != 0 && micros() - lastCycleUs > COMM_TIMEOUT_US) {
    fault = FAULT_COMM;
    halt();
  }
}

static int32_t position() {
  portENTER_CRITICAL(&mux);
  int32_t p = stepPos;
  portEXIT_CRITICAL(&mux);
  return p;
}

static uint8_t status() {
  uint8_t s = 0;
  if (enabled) s |= ST_ENABLED;
  if (fault != FAULT_NONE) s |= ST_FAULT;
  if (running && position() != segTarget) s |= ST_MOVING;
  return s;
}

}  // namespace stepper
