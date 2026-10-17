/*
 * ds4_map.c - neutral-protocol pad -> DS4 USB input report (64 B).
 * Bit-exact with what the real DS4 sends over USB in report 0x01.
 */
#include <string.h>

#include "bridge.h"
#include "ds4_device.h"

/* hat mapping identical to protocol (0=N..7=NW, 8=neutral) */
void build_ds4_report(const pad_state_t *pad, ds4_report_t *r) {
    memset(r, 0, sizeof(*r));
    r->report_id = 0x01;

    /* sticks: both 8-bit, 0x80 center, +Y up -> direct copy */
    r->lx = (uint8_t)(pad->lx + 128);
    r->ly = (uint8_t)(128 - pad->ly);
    r->rx = (uint8_t)(pad->rx + 128);
    r->ry = (uint8_t)(128 - pad->ry);

    /* byte 5: low nibble = hat, high nibble = face buttons
     * DS4 face bit order (high nibble, from bit4): SQUARE, CROSS, CIRCLE, TRIANGLE
     * Nintendo physical layout: Y=left(SQUARE), B=bottom(CROSS), A=right(CIRCLE), X=top(TRIANGLE)
     */
    r->dpad_face = (uint8_t)((pad->hat & 0x0F) |
                             (pad->y ? (1 << 4) : 0) |
                             (pad->b ? (1 << 5) : 0) |
                             (pad->a ? (1 << 6) : 0) |
                             (pad->x ? (1 << 7) : 0));

    /* byte 6: low nibble = L1,R1,L2,R2 ; high nibble = SHARE,OPTIONS,L3,R3 */
    r->sys_misc = (uint8_t)((pad->l ? (1 << 0) : 0) |
                            (pad->r ? (1 << 1) : 0) |
                            (pad->zl ? (1 << 2) : 0) |
                            (pad->zr ? (1 << 3) : 0) |
                            (pad->minus ? (1 << 4) : 0) |
                            (pad->plus ? (1 << 5) : 0) |
                            (pad->l3 ? (1 << 6) : 0) |
                            (pad->r3 ? (1 << 7) : 0));

    /* byte 7: bit0 PS(HOME), bit1 touchpad(CAPTURE), bits2-7 counter */
    static uint8_t counter;
    counter = (counter + 1) & 0x3F;
    r->ps_counter = (uint8_t)((pad->home ? 0x01 : 0) |
                              (pad->capture ? 0x02 : 0) |
                              (counter << 2));

    /* triggers analog: full press */
    r->l2_analog = pad->zl ? 0xFF : 0x00;
    r->r2_analog = pad->zr ? 0xFF : 0x00;
}
