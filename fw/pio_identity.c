/*
 * pio_identity.c - selectable PIO-USB device identity.
 *
 * The PC tool picks the downstream pad identity with a single-letter
 * command over the CDC bridge; the choice is stored in unwiped RAM and
 * applied on the next reboot (soft reboot via watchdog, ~2 s).
 *
 *   'i' = DInput generic pad (10C4:82C0, PS3-style)
 *   'h' = HORI Pokken Switch pad (0F0D:0092)
 *   'd' = DualShock 4 (054C:05C4, real dumped descriptor, 64B reports)
 *
 * Default (RAM wiped / first boot): DInput.
 */
#include <string.h>

#include "hardware/watchdog.h"

#include "bridge.h"
#include "ds4_device.h"
#include "ds4_descriptors.h"
#include "dinput_descriptors.h"
#include "powa_descriptors.h"
#include "switch_descriptors.h"
#include "usb_definitions.h"

extern const uint8_t ds4_device_descriptor[18];
extern const uint8_t ds4_configuration_descriptor[41];

#define IDENT_MAGIC 0x49444550u /* 'IDEN'+2, bumped: identity table changed */

typedef struct {
    uint32_t magic;
    uint8_t identity; /* 0 = dinput, 1 = hori, 2 = ds4, 3 = powa */
} ident_info_t;

static ident_info_t __uninitialized_ram(g_ident);

typedef struct {
    const uint8_t *device;
    const uint8_t *config;
    const uint8_t *report_desc;
    uint16_t report_desc_len;
} identity_def_t;

static const identity_def_t identities[4] = {
    [0] = {
        .device = (const uint8_t *)din_device_descriptor,
        .config = din_configuration_descriptor,
        .report_desc = din_report_descriptor,
        .report_desc_len = sizeof(din_report_descriptor),
    },
    [1] = {
        .device = (const uint8_t *)switch_device_descriptor,
        .config = switch_configuration_descriptor,
        .report_desc = switch_report_descriptor,
        .report_desc_len = sizeof(switch_report_descriptor),
    },
    [2] = {
        .device = (const uint8_t *)ds4_device_descriptor,
        .config = ds4_configuration_descriptor,
        .report_desc = ds4_report_descriptor,
        .report_desc_len = sizeof(ds4_report_descriptor),
    },
    [3] = {
        .device = (const uint8_t *)powa_device_descriptor,
        .config = powa_configuration_descriptor,
        .report_desc = powa_report_descriptor,
        .report_desc_len = sizeof(powa_report_descriptor),
    },
};

static const uint8_t *identity_report_table[1];

#ifndef DEFAULT_IDENTITY
#define DEFAULT_IDENTITY 0 /* DInput */
#endif

void pio_identity_init(void) {
    if (g_ident.magic != IDENT_MAGIC) {
        g_ident.magic = IDENT_MAGIC;
        g_ident.identity = DEFAULT_IDENTITY;
    }
    if (g_ident.identity > 3) {
        g_ident.identity = DEFAULT_IDENTITY;
    }
    identity_report_table[0] = identities[g_ident.identity].report_desc;
}

uint8_t pio_identity_get(void) {
    return g_ident.identity;
}

void pio_identity_set(uint8_t id) {
    if (id > 3) {
        return;
    }
    if (id != g_ident.identity) {
        g_ident.identity = id;
        /* soft reboot (~2 s) to re-enumerate with the new identity */
        watchdog_reboot(0, 0, 0);
    }
}

const usb_descriptor_buffers_t *pio_identity_descriptors(void) {
    static usb_descriptor_buffers_t desc;
    desc.device = identities[g_ident.identity].device;
    desc.config = identities[g_ident.identity].config;
    desc.hid_report = identity_report_table;
    desc.string = pio_string_table();
    return &desc;
}
