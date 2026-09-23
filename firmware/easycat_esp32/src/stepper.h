// Open-loop CSP stepper: STEP/DIR/EN, driven by a position setpoint the
// PLC sends every EtherCAT cycle (1 ms, DC SYNC0).
//
// The ESP32 only counts steps. Degrees, steps per revolution, velocity and
// acceleration live on the PLC, which computes the trajectory and sends
// the absolute target in steps each cycle (cyclic synchronous position).
//
// Pulses are generated in hardware:
//
//   RMT  Each cycle, newSetpoint() turns the move for this cycle
//        (target - cmdPos) into N RMT items, one step each, spread evenly
//        across WINDOW_US with 0.1 us resolution (Bresenham remainder), and
//        hands them to the RMT peripheral. No CPU per step, no 10 us grid.
//        The window ends before the next cycle, so sequences never overlap.
//
//   PCNT The STEP pin is also routed back in to pulse counter 0, with DIR as
//        its direction control. actualPos is what the hardware really
//        emitted -- it doubles as a self-check of the RMT output, and it
//        stays exact when a sequence is aborted mid-way.
//
// Pin routing, in this order (the IDF drivers otherwise undo each other):
// PCNT configured without pins; RMT claims STEP as output; then the input
// buffers of STEP and DIR are enabled by hand and connected to PCNT.
//
// Limits: pulse high STEP_HIGH_US, period >= 2 x that, so at most
// MAX_STEPS_PER_CYCLE steps per 1 ms cycle (150 kHz). The PLC caps itself
// lower. The first pulse of every sequence comes LEAD_US after it starts,
// and DIR only changes once the previous sequence has finished -- DIR
// setup time is always >= LEAD_US.
//
// Safety: stepping stops at once (rmt_tx_stop) and EN drops when
//   - the PLC clears the enable bit,
//   - the slave leaves OP or SYNC0 stops for COMM_TIMEOUT_US (cable pulled,
//     master stopped) -> fault 1,
//   - one cycle asks for more than MAX_STEPS_PER_CYCLE      -> fault 2.
// Faults latch until the PLC pulses the reset bit with enable off.

#pragma once
#include <Arduino.h>
#include "driver/rmt.h"
#include "driver/pcnt.h"
#include "esp_rom_gpio.h"
#include "soc/gpio_sig_map.h"
#include "soc/gpio_periph.h"
#include "soc/io_mux_reg.h"

namespace stepper {

static const uint8_t PIN_STEP = 25;
static const uint8_t PIN_DIR = 26;
static const uint8_t PIN_EN = 27;
static const bool EN_ACTIVE_LOW = true;   // most STEP/DIR drivers: EN low = enabled

static const rmt_channel_t RMT_CH = RMT_CHANNEL_0;
static const uint8_t RMT_CLK_DIV = 8;               // 80 MHz / 8 = 10 MHz, 0.1 us
static const uint32_t TICKS_PER_US = 10;
static const uint32_t STEP_HIGH_US = 3;             // >= 2.5 us for DM542-class drivers
static const uint32_t LEAD_US = 5;                  // before the first pulse
static const uint32_t WINDOW_US = 900;              // sequence length, < 1000 us cycle
static const int32_t MAX_STEPS_PER_CYCLE = 150;     // period >= 2 x STEP_HIGH_US
static const uint32_t COMM_TIMEOUT_US = 5000;

static const pcnt_unit_t PCNT_UNIT = PCNT_UNIT_0;
static const int16_t PCNT_LIM = 30000;              // counter wraps to 0 at +/-LIM

enum Fault : uint8_t { FAULT_NONE = 0, FAULT_COMM = 1, FAULT_JUMP = 2 };

// Control byte from the PLC.
static const uint8_t CTRL_ENABLE = 0x01;
static const uint8_t CTRL_RESET = 0x02;

// Status byte to the PLC.
static const uint8_t ST_ENABLED = 0x01;
static const uint8_t ST_FAULT = 0x02;
static const uint8_t ST_MOVING = 0x04;

static rmt_item32_t items[MAX_STEPS_PER_CYCLE];
static portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;

static int32_t cmdPos = 0;        // steps scheduled so far
static int32_t actualPos = 0;     // steps counted by PCNT
static int16_t lastCount = 0;
static int8_t dir = 1;
static bool enabled = false;
static uint8_t fault = FAULT_NONE;
static uint32_t lastCycleUs = 0;
static uint32_t lastSteps = 0;    // steps in the last sequence, for status

static void setDriver(bool on) {
  digitalWrite(PIN_EN, (on ^ EN_ACTIVE_LOW) ? HIGH : LOW);
}

// Fold the PCNT counter into actualPos. The counter resets to 0 at
// +/-PCNT_LIM, so the difference is taken modulo PCNT_LIM.
static void updateCount() {
  int16_t c = 0;
  pcnt_get_counter_value(PCNT_UNIT, &c);
  int32_t d = (int32_t)c - lastCount;
  if (d > PCNT_LIM / 2) d -= PCNT_LIM;
  if (d < -PCNT_LIM / 2) d += PCNT_LIM;
  lastCount = c;
  portENTER_CRITICAL(&mux);
  actualPos += d;
  portEXIT_CRITICAL(&mux);
}

// Stop now: abort the sequence in flight, drop EN, and resynchronise the
// schedule to what the hardware actually emitted.
static void halt() {
  rmt_tx_stop(RMT_CH);
  delayMicroseconds(STEP_HIGH_US + 1);   // let a pulse in progress finish
  updateCount();
  setDriver(false);
  enabled = false;
  cmdPos = actualPos;
  lastSteps = 0;
}

static void begin() {
  pinMode(PIN_DIR, OUTPUT);
  pinMode(PIN_EN, OUTPUT);
  digitalWrite(PIN_DIR, LOW);
  setDriver(false);

  // 1. PCNT without pins: count rising edges, reverse while DIR is high.
  pcnt_config_t pc = {};
  pc.pulse_gpio_num = PCNT_PIN_NOT_USED;
  pc.ctrl_gpio_num = PCNT_PIN_NOT_USED;
  pc.channel = PCNT_CHANNEL_0;
  pc.unit = PCNT_UNIT;
  pc.pos_mode = PCNT_COUNT_INC;
  pc.neg_mode = PCNT_COUNT_DIS;
  pc.lctrl_mode = PCNT_MODE_KEEP;       // DIR low  = forward = count up
  pc.hctrl_mode = PCNT_MODE_REVERSE;    // DIR high = reverse = count down
  pc.counter_h_lim = PCNT_LIM;
  pc.counter_l_lim = -PCNT_LIM;
  pcnt_unit_config(&pc);
  pcnt_filter_disable(PCNT_UNIT);
  pcnt_counter_pause(PCNT_UNIT);
  pcnt_counter_clear(PCNT_UNIT);

  // 2. RMT claims STEP as its output, idle low.
  rmt_config_t rc = RMT_DEFAULT_CONFIG_TX((gpio_num_t)PIN_STEP, RMT_CH);
  rc.clk_div = RMT_CLK_DIV;
  rc.mem_block_num = 4;                 // 256 items >= MAX_STEPS_PER_CYCLE + end
  rc.tx_config.carrier_en = false;
  rc.tx_config.idle_output_en = true;
  rc.tx_config.idle_level = RMT_IDLE_LEVEL_LOW;
  rc.tx_config.loop_en = false;
  rmt_config(&rc);
  rmt_driver_install(RMT_CH, 0, 0);

  // 3. Read STEP and DIR back into PCNT without disturbing their outputs.
  PIN_INPUT_ENABLE(GPIO_PIN_MUX_REG[PIN_STEP]);
  PIN_INPUT_ENABLE(GPIO_PIN_MUX_REG[PIN_DIR]);
  esp_rom_gpio_connect_in_signal(PIN_STEP, PCNT_SIG_CH0_IN0_IDX, false);
  esp_rom_gpio_connect_in_signal(PIN_DIR, PCNT_CTRL_CH0_IN0_IDX, false);
  pcnt_counter_resume(PCNT_UNIT);
}

// Called once per EtherCAT cycle with the fresh outputs from the PLC.
// inOp: the slave is in OP and this cycle was driven by SYNC0.
static void newSetpoint(uint8_t ctrl, int32_t target, bool inOp) {
  updateCount();
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

  int32_t d = target - cmdPos;
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
  lastSteps = d < 0 ? -d : d;
  if (d == 0) return;

  int8_t nd = d < 0 ? -1 : 1;
  if (nd != dir) {
    // Never flip DIR under a running sequence: PCNT would count the tail
    // the wrong way and the driver would step it the wrong way.
    rmt_wait_tx_done(RMT_CH, pdMS_TO_TICKS(2));
    digitalWrite(PIN_DIR, nd > 0 ? LOW : HIGH);
    dir = nd;
  }

  // N pulses spread over the window; periods differ by at most one tick.
  uint32_t n = lastSteps;
  uint32_t span = (WINDOW_US - LEAD_US) * TICKS_PER_US;
  uint32_t base = span / n, rem = span % n, err = 0;
  uint32_t high = STEP_HIGH_US * TICKS_PER_US;
  for (uint32_t i = 0; i < n; i++) {
    uint32_t period = base;
    err += rem;
    if (err >= n) { err -= n; period++; }
    uint32_t low = period - high;
    if (i == 0) low += LEAD_US * TICKS_PER_US;
    items[i].level0 = 0;
    items[i].duration0 = low;
    items[i].level1 = 1;
    items[i].duration1 = high;
  }
  rmt_write_items(RMT_CH, items, n, false);
  cmdPos = target;
}

// Called by the main loop: stepping must stop even if newSetpoint is no
// longer being called at all (the EtherCAT task stopped being woken).
static void watchdog() {
  if (enabled && lastCycleUs != 0 && micros() - lastCycleUs > COMM_TIMEOUT_US) {
    fault = FAULT_COMM;
    halt();
  }
}

static int32_t position() {
  portENTER_CRITICAL(&mux);
  int32_t p = actualPos;
  portEXIT_CRITICAL(&mux);
  return p;
}

static uint8_t status() {
  uint8_t s = 0;
  if (enabled) s |= ST_ENABLED;
  if (fault != FAULT_NONE) s |= ST_FAULT;
  if (lastSteps != 0 || position() != cmdPos) s |= ST_MOVING;
  return s;
}

}  // namespace stepper
