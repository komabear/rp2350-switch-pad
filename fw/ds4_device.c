/*
 * ds4_device.c - full DualShock 4 USB device identity for the PIO-USB port.
 * Cloned from the user's real DS4 (054C:05C4): device descriptor, HID config
 * with both IN1 (64B input) and OUT2 (32B output? real DS4 out is 0x02, 64B),
 * the dumped 789-byte report descriptor, and the DS4 string descriptors.
 */
#include <stdint.h>
#include <string.h>

#include "ds4_descriptors.h"

/* --- device descriptor (real DS4 values) */
const uint8_t ds4_device_descriptor[18] = {
    0x12,       /* bLength */
    0x01,       /* bDescriptorType: DEVICE */
    0x00, 0x02, /* bcdUSB 2.00 */
    0x00,       /* bDeviceClass: per-interface */
    0x00, 0x00, /* bDeviceSubClass, bDeviceProtocol */
    0x40,       /* bMaxPacketSize0 64 */
    0x4C, 0x05, /* idVendor 0x054C (Sony) */
    0xC4, 0x05, /* idProduct 0x05C4 (DS4 v1) */
    0x00, 0x01, /* bcdDevice 1.00 */
    0x01,       /* iManufacturer */
    0x02,       /* iProduct */
    0x00,       /* iSerialNumber */
    0x01,       /* bNumConfigurations */
};

/* --- HID class descriptor (for the interface descriptor) */
static const uint8_t ds4_hid_class_desc[9] = {
    0x09, 0x21,
    0x11, 0x01, /* bcdHID 1.11 */
    0x00,       /* bCountryCode */
    0x01,       /* bNumDescriptors */
    0x22,       /* bDescriptorType: report */
    (uint8_t)(DS4_REPORT_DESC_LEN & 0xFF),
    (uint8_t)(DS4_REPORT_DESC_LEN >> 8),
};

/* --- configuration descriptor: 1 interface, IN1 + OUT2, like the real DS4.
 * Total = 9 (cfg) + 9 (ifc) + 9 (hid) + 7 (out2) + 7 (in1) = 41 */
#define DS4_CONFIG_TOTAL_LEN 41
uint8_t ds4_configuration_descriptor[DS4_CONFIG_TOTAL_LEN] = {
    0x09, 0x02,
    DS4_CONFIG_TOTAL_LEN, 0x00,
    0x01, 0x01, 0x00, 0x80, 0xFA,
    0x09, 0x04,
    0x00, 0x00, 0x02, 0x03, 0x00, 0x00, 0x00,
    0x09, 0x21,
    0x11, 0x01, 0x00, 0x01, 0x22,
    (uint8_t)(DS4_REPORT_DESC_LEN & 0xFF),
    (uint8_t)((DS4_REPORT_DESC_LEN >> 8) & 0xFF),
    0x07, 0x05, 0x02, 0x03, 0x40, 0x00, 0x01, /* OUT2 64B 1ms */
    0x07, 0x05, 0x81, 0x03, 0x40, 0x00, 0x01, /* IN1 64B 1ms */
};

/* --- strings (DS4 real values) */
static const char *ds4_strings[4] = {
    "\x09\x04",
    "Sony Interactive Entertainment",
    "Wireless Controller",
    "1.0",
};
