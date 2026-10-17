#pragma once
/* Wire protocol between the PC tool and the pad bridge firmware.
 *
 * Frame (11 bytes):
 *   [0]='$'  [1]='P'
 *   [2] flags1  bit0 Y bit1 B bit2 A bit3 X bit4 L bit5 R bit6 ZL bit7 ZR
 *               (Nintendo physical layout: A=right, B=bottom, X=top, Y=left)
 *   [3] flags2  bit0 MINUS bit1 PLUS bit2 L3 bit3 R3 bit4 HOME bit5 CAPTURE
 *   [4] lx   int8  -127..127
 *   [5] ly   int8
 *   [6] rx   int8
 *   [7] ry   int8
 *   [8] hat  8=center, 0=N 1=NE 2=E 3=SE 4=S 5=SW 6=W 7=NW (HID convention)
 *   [9] 0x00 (reserved)
 *   [10]     XOR of bytes [2..9]
 *
 * Device -> PC async lines (LF terminated, plain ASCII):
 *   "$S <flags>"  every 250 ms; flags bit0=xinput mounted, bit1=pio configured,
 *                 bit2=frames flowing
 *   "EV <text>"   rare events
 *
 * If no $P frame arrives for 400 ms the firmware releases all inputs.
 */
#include <stdint.h>
#include <stdbool.h>
#include <string.h>

#define PAD_FRAME_SIZE 11
#define PAD_SYNC0 '$'
#define PAD_SYNC1 'P'
#define PAD_RELEASE_TIMEOUT_MS 400

typedef struct __attribute__((packed)) {
  uint8_t y, b, a, x;      /* 1 = pressed */
  uint8_t l, r, zl, zr;
  uint8_t minus, plus, l3, r3;
  uint8_t home, capture;
  int8_t lx, ly, rx, ry;   /* -127..127 */
  uint8_t hat;
} pad_state_t;             /* 15 bytes */

/* flags1 bits */
#define PF_Y    (1u << 0)
#define PF_B    (1u << 1)
#define PF_A    (1u << 2)
#define PF_X    (1u << 3)
#define PF_L    (1u << 4)
#define PF_R    (1u << 5)
#define PF_ZL   (1u << 6)
#define PF_ZR   (1u << 7)
/* flags2 bits */
#define PF_MINUS  (1u << 0)
#define PF_PLUS   (1u << 1)
#define PF_L3     (1u << 2)
#define PF_R3     (1u << 3)
#define PF_HOME   (1u << 4)
#define PF_CAPTURE (1u << 5)

#define HAT_CENTER 8u

static inline void pad_release(pad_state_t *p) {
  memset(p, 0, sizeof(*p));
  p->hat = HAT_CENTER;
}
