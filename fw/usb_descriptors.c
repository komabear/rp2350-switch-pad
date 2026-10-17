/*
 * usb_descriptors.c - native USB descriptors: CDC serial device only.
 *
 * NOTE: we deliberately do NOT composite XInput with CDC. The Windows Xbox
 * 360 driver binds at device level (XnaComposite) and fails on a composite
 * device (tested on hardware: device enumerates with Error, CDC never
 * appears). The Parsec host already provides the virtual Xbox pad; the
 * bridge tool reads it via the XInput API, so the board only needs CDC.
 */
#include "tusb.h"
#ifdef NATIVE_PAD
#include "powa_descriptors.h"
uint8_t const *tud_descriptor_device_cb(void) { return powa_device_descriptor; }
uint8_t const *tud_descriptor_configuration_cb(uint8_t i) { (void)i; return powa_configuration_descriptor; }
extern uint16_t diag_fill(uint8_t *buf, uint16_t len);
uint16_t tud_hid_get_report_cb(uint8_t i, uint8_t id, hid_report_type_t t, uint8_t *b, uint16_t n) { (void)i;(void)id; return t == HID_REPORT_TYPE_FEATURE ? diag_fill(b, n) : 0; }
void tud_hid_set_report_cb(uint8_t i, uint8_t id, hid_report_type_t t, uint8_t const *b, uint16_t n) { (void)i;(void)id;(void)t;(void)b;(void)n; }
uint8_t const *tud_hid_descriptor_report_cb(uint8_t i) { (void)i; return powa_report_descriptor; }
uint16_t const *tud_descriptor_string_cb(uint8_t index, uint16_t langid) {
    (void)langid;
    static uint16_t d[32];
    static char const *s[] = { [1] = "BDA", [2] = "NSW wired controller" };
    uint8_t len = 0;
    if (index == 0) { d[1] = 0x0409; len = 1; }
    else if (index < 3) { for (; s[index][len]; len++) d[1 + len] = (uint16_t)s[index][len]; }
    else return NULL;
    d[0] = (uint16_t)((TUSB_DESC_STRING << 8) | (2 * len + 2));
    return d;
}
#else


enum {
    ITF_NUM_CDC = 0,
    ITF_NUM_CDC_DATA,
    ITF_NUM_TOTAL
};

#define EPNUM_CDC_NOTIF 0x81
#define EPNUM_CDC_OUT   0x02
#define EPNUM_CDC_IN    0x82

#define USBD_VID 0x2E8A /* Raspberry Pi */
#define USBD_PID 0x000A

#define CONFIG_TOTAL_LEN (TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN)

static const tusb_desc_device_t dev_desc = {
    .bLength            = sizeof(tusb_desc_device_t),
    .bDescriptorType    = TUSB_DESC_DEVICE,
    .bcdUSB             = 0x0200,
    .bDeviceClass       = 0x00,
    .bDeviceSubClass    = 0x00,
    .bDeviceProtocol    = 0x00,
    .bMaxPacketSize0    = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor           = USBD_VID,
    .idProduct          = USBD_PID,
    .bcdDevice          = 0x0100,
    .iManufacturer      = 0x01,
    .iProduct           = 0x02,
    .iSerialNumber      = 0x00,
    .bNumConfigurations = 0x01,
};

static const uint8_t cfg_desc[] = {
    TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_TOTAL, 0, CONFIG_TOTAL_LEN, 0x00, 100),
    TUD_CDC_DESCRIPTOR(ITF_NUM_CDC, 0, EPNUM_CDC_NOTIF, 64,
                       EPNUM_CDC_OUT, EPNUM_CDC_IN, 64),
};

uint8_t const *tud_descriptor_device_cb(void) {
    return (uint8_t const *)&dev_desc;
}

uint8_t const *tud_descriptor_configuration_cb(uint8_t index) {
    (void)index;
    return cfg_desc;
}

uint16_t const *tud_descriptor_string_cb(uint8_t index, uint16_t langid) {
    (void)langid;
    static uint16_t desc_str[32];

    static char const *strings[] = {
        [1] = "mayflash_switch2_controller",
        [2] = "Pad Bridge",
    };

    uint8_t len = 0;
    if (index == 0) {
        desc_str[1] = 0x0409;
        len = 1;
    } else if (index < sizeof(strings) / sizeof(strings[0]) && strings[index]) {
        char const *s = strings[index];
        for (; s[len] && len < 31; len++) {
            desc_str[1 + len] = (uint16_t)s[len];
        }
    } else {
        return NULL;
    }

    desc_str[0] = (uint16_t)((TUSB_DESC_STRING << 8) | (2 * len + 2));
    return desc_str;
}

#endif
