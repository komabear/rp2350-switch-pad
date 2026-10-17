/*
 * pio_strings.c - string descriptors for the PIO-USB device, per identity.
 * Embedded hosts (Mayflash, Switch-adjacent) commonly abort enumeration
 * when a device advertises string indices but stalls GET_DESCRIPTOR(STRING)
 * - so each identity gets its full string set.
 */
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "usb_definitions.h"

/* string_descriptor_t: { u8 len, u8 type, u8 string[62] } - UTF-16LE */
static string_descriptor_t str_lang;
static string_descriptor_t str_mfr;
static string_descriptor_t str_product;
static string_descriptor_t str_version;

static void fill_utf16(string_descriptor_t *s, const char *ascii) {
    uint8_t n = 0;
    while (ascii[n] && n < 31) {
        s->string[2 * n] = (uint8_t)ascii[n];
        s->string[2 * n + 1] = 0x00;
        n++;
    }
    s->length = (uint8_t)(2 + 2 * n);
    s->type = 0x03; /* STRING */
}

static const string_descriptor_t *string_table[4];

/* called once from main() before core1 starts. identity: 0=DInput, 1=HORI */
void pio_strings_init(uint8_t identity) {
    fill_utf16(&str_lang, "\x09\x04");
    /* str_lang holds just the LANGID word */
    str_lang.string[0] = 0x09;
    str_lang.string[1] = 0x04;
    str_lang.length = 4;
    str_lang.type = 0x03;

    if (identity == 1) {
        fill_utf16(&str_mfr, "HORI CO.,LTD.");
        fill_utf16(&str_product, "POKKEN CONTROLLER");
    } else if (identity == 2) {
        fill_utf16(&str_mfr, "Sony Interactive Entertainment");
        fill_utf16(&str_product, "Wireless Controller");
    } else if (identity == 3) {
        fill_utf16(&str_mfr, "BDA");
        fill_utf16(&str_product, "NSW wired controller");
    } else {
        fill_utf16(&str_mfr, "GamepadConverter");
        fill_utf16(&str_product, "(D-Input)");
    }
    fill_utf16(&str_version, "1.0");

    string_table[0] = &str_lang;
    string_table[1] = &str_mfr;
    string_table[2] = &str_product;
    string_table[3] = &str_version;
}

const string_descriptor_t *pio_string_table(void) {
    return string_table[0];
}

/* NATIVE_PAD builds: the PIO port is the PC link, not a gamepad */
void pio_strings_link(void) {
    fill_utf16(&str_mfr, "Spindly");
    fill_utf16(&str_product, "Pad Bridge Link");
}
