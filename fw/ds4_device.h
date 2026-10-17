#pragma once
/* ds4_device.h - DS4 identity for the PIO-USB device */
#include <stdint.h>

#define DS4_CONFIG_TOTAL_LEN 41
#define DS4_INPUT_REPORT_LEN 64

extern const uint8_t ds4_hid_class_desc[9];

/* input report (64 B, report id 0x01 first byte) */
typedef struct __attribute__((packed)) {
    uint8_t report_id; /* 0x01 */
    uint8_t lx, ly, rx, ry;
    uint8_t dpad_face;  /* low nibble hat, high nibble square/cross/circle/triangle */
    uint8_t sys_misc;   /* low nibble L1,R1,L2,R2; high nibble share,options,l3,r3 */
    uint8_t ps_counter; /* PS(1<<0) tpad(1<<1), counter bits */
    uint8_t l2_analog;
    uint8_t r2_analog;
    uint8_t rest[55];
} ds4_report_t;
