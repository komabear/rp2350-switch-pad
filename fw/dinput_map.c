/*
 * dinput_map.c - neutral-protocol pad -> DInput (HID) report mapping.
 * The report goes out the PIO-USB port to the Mayflash in Switch mode.
 */
#include <string.h>

#include "bridge.h"

void build_dinput_report(const pad_state_t *pad, din_report_t *r) {
    memset(r, 0, sizeof(*r));

    /* Nintendo physical layout onto PlayStation labels the Mayflash expects
     * from a DInput pad: A(right)=circle, B(bottom)=cross, X(top)=triangle,
     * Y(left)=square */
    if (pad->y) r->square_btn = 1;
    if (pad->b) r->cross_btn = 1;
    if (pad->a) r->circle_btn = 1;
    if (pad->x) r->triangle_btn = 1;
    if (pad->l) r->l1_btn = 1;
    if (pad->r) r->r1_btn = 1;
    if (pad->zl) r->l2_btn = 1;
    if (pad->zr) r->r2_btn = 1;
    if (pad->minus) r->select_btn = 1;
    if (pad->plus) r->start_btn = 1;
    if (pad->l3) r->l3_btn = 1;
    if (pad->r3) r->r3_btn = 1;
    if (pad->home) r->ps_btn = 1;

    r->direction = pad->hat;

    /* HID axes: 0x80 centered; up = smaller value. Protocol has +Y up, so
     * invert with 128 - y (keeps center exactly at 0x80). */
    r->l_x_axis = (uint8_t)(pad->lx + 128);
    r->l_y_axis = (uint8_t)(128 - pad->ly);
    r->r_x_axis = (uint8_t)(pad->rx + 128);
    r->r_y_axis = (uint8_t)(128 - pad->ry);

    /* the PS3-style analog mirrors of dpad and buttons */
    switch (pad->hat) {
        case HID_HAT_UP:
            r->up_axis = 0xFF;
            break;
        case HID_HAT_UPRIGHT:
            r->up_axis = 0xFF;
            r->right_axis = 0xFF;
            break;
        case HID_HAT_RIGHT:
            r->right_axis = 0xFF;
            break;
        case HID_HAT_DOWNRIGHT:
            r->down_axis = 0xFF;
            r->right_axis = 0xFF;
            break;
        case HID_HAT_DOWN:
            r->down_axis = 0xFF;
            break;
        case HID_HAT_DOWNLEFT:
            r->down_axis = 0xFF;
            r->left_axis = 0xFF;
            break;
        case HID_HAT_LEFT:
            r->left_axis = 0xFF;
            break;
        case HID_HAT_UPLEFT:
            r->up_axis = 0xFF;
            r->left_axis = 0xFF;
            break;
        default:
            break;
    }

    if (pad->y) r->triangle_axis = 0xFF;
    if (pad->a) r->circle_axis = 0xFF;
    if (pad->b) r->cross_axis = 0xFF;
    if (pad->x) r->square_axis = 0xFF;
    if (pad->l) r->l1_axis = 0xFF;
    if (pad->r) r->r1_axis = 0xFF;
    if (pad->zl) r->l2_axis = 0xFF;
    if (pad->zr) r->r2_axis = 0xFF;
}
