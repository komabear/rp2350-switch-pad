/*
 * powa_map.c - neutral-protocol pad -> PowerA (NSW wired) 8-byte report.
 * Button order verified by live calibration against the user's real pad
 * (host/powa_mapping.txt). Hat: 0-7 = directions, 0xF = center.
 */
#include <string.h>

#include "bridge.h"
#include "powa_descriptors.h"

void build_powa_report(const pad_state_t *pad, powa_report_t *r) {
    memset(r, 0, sizeof(*r));

    uint8_t b0 = 0, b1 = 0;
    if (pad->y) b0 |= POWA_MASK_Y;
    if (pad->b) b0 |= POWA_MASK_B;
    if (pad->a) b0 |= POWA_MASK_A;
    if (pad->x) b0 |= POWA_MASK_X;
    if (pad->l) b0 |= POWA_MASK_L;
    if (pad->r) b0 |= POWA_MASK_R;
    if (pad->zl) b0 |= POWA_MASK_ZL;
    if (pad->zr) b0 |= POWA_MASK_ZR;
    if (pad->minus) b1 |= POWA_MASK_MINUS;
    if (pad->plus) b1 |= POWA_MASK_PLUS;
    if (pad->l3) b1 |= POWA_MASK_L3;
    if (pad->r3) b1 |= POWA_MASK_R3;
    if (pad->home) b1 |= POWA_MASK_HOME;
    if (pad->capture) b1 |= POWA_MASK_CAPTURE;
    r->buttons0 = b0;
    r->buttons1 = b1;

    /* hat: protocol 0-7 identical; center is 0xF on this pad */
    r->hat_pad = (pad->hat > 7) ? POWA_HAT_CENTER : pad->hat;

    /* sticks: 8-bit, 0x80 center, +Y up -> direct copy */
    r->lx = (uint8_t)(pad->lx + 128);
    r->ly = (uint8_t)(128 - pad->ly);
    r->rx = (uint8_t)(pad->rx + 128);
    r->ry = (uint8_t)(128 - pad->ry);

    r->pad = 0;
}
