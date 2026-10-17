/*
 * switch_map.c - neutral-protocol pad -> HORI Pokken (Switch) report.
 * Sent out the PIO-USB port. The Pokken identity is on every
 * adapter/Switch whitelist; its wire format is trivial:
 *   u16 buttons | u8 hat | u8 lx | u8 ly | u8 rx | u8 ry | u8 vendor
 */
#include <string.h>

#include "bridge.h"
#include "switch_descriptors.h"

void build_switch_report(const pad_state_t *pad, switch_report_t *r) {
    memset(r, 0, sizeof(*r));

    uint16_t b = 0;
    /* protocol flag order matches the Switch mask order exactly */
    if (pad->y) b |= SWITCH_MASK_Y;
    if (pad->b) b |= SWITCH_MASK_B;
    if (pad->a) b |= SWITCH_MASK_A;
    if (pad->x) b |= SWITCH_MASK_X;
    if (pad->l) b |= SWITCH_MASK_L;
    if (pad->r) b |= SWITCH_MASK_R;
    if (pad->zl) b |= SWITCH_MASK_ZL;
    if (pad->zr) b |= SWITCH_MASK_ZR;
    if (pad->minus) b |= SWITCH_MASK_MINUS;
    if (pad->plus) b |= SWITCH_MASK_PLUS;
    if (pad->l3) b |= SWITCH_MASK_L3;
    if (pad->r3) b |= SWITCH_MASK_R3;
    if (pad->home) b |= SWITCH_MASK_HOME;
    if (pad->capture) b |= SWITCH_MASK_CAPTURE;
    r->buttons = b;

    r->hat = pad->hat; /* same 0..8 convention */

    /* both protocols: 8-bit sticks, 0x80 center, +Y up. Direct copy. */
    r->lx = (uint8_t)(pad->lx + 128);
    r->ly = (uint8_t)(128 - pad->ly);
    r->rx = (uint8_t)(pad->rx + 128);
    r->ry = (uint8_t)(128 - pad->ry);

    r->vendor = 0;
}
