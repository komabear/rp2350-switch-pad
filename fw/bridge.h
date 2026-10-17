#pragma once
/* bridge.h - declarations shared between firmware translation units */
#include <stdint.h>
#include <stdbool.h>

#include "pico/sync.h"
#include "pico/mutex.h"
#include "pico/time.h"

#include "proto.h"
#include "dinput_descriptors.h"
#include "switch_descriptors.h"
#include "ds4_device.h"
#include "powa_descriptors.h"

static inline uint32_t board_millis(void) {
    return to_ms_since_boot(get_absolute_time());
}

/* defined in main.c */
extern pad_state_t g_pad;
extern mutex_t g_pad_mutex;
extern volatile bool g_pio_configured;
extern volatile bool g_frames_live;
extern volatile uint32_t g_core1_alive_ms;

void store_pad(const pad_state_t *p);
void load_pad(pad_state_t *p);

/* dinput_map.c */
void build_dinput_report(const pad_state_t *pad, din_report_t *r);

/* switch_map.c */
void build_switch_report(const pad_state_t *pad, switch_report_t *r);

/* ds4_map.c */
void build_ds4_report(const pad_state_t *pad, ds4_report_t *r);

/* powa_map.c */
void build_powa_report(const pad_state_t *pad, powa_report_t *r);
