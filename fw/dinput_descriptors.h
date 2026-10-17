#pragma once
/* DInput (generic HID gamepad) descriptors, byte-for-byte from
 * PicoGamepadConverter (MIT, based on GP2040-CE PS3/DInput descriptors).
 * This is a classic DirectInput gamepad that adapters like the Mayflash
 * Magic-NS accept in their PC-DInput mode and, more importantly, that the
 * Mayflash treats as a normal wired pad in Switch mode.
 */
#include <stdint.h>

#define DIN_ENDPOINT0_SIZE 64
#define DIN_GAMEPAD_INTERFACE 0
#define DIN_GAMEPAD_ENDPOINT 1
#define DIN_GAMEPAD_SIZE 64

#define DIN_VENDOR_ID  0x10C4
#define DIN_PRODUCT_ID 0x82C0

#define LSB(n) ((n) & 255)
#define MSB(n) (((n) >> 8) & 255)

/* HAT report (4 bits) */
#define HID_HAT_UP        0x00
#define HID_HAT_UPRIGHT   0x01
#define HID_HAT_RIGHT     0x02
#define HID_HAT_DOWNRIGHT 0x03
#define HID_HAT_DOWN      0x04
#define HID_HAT_DOWNLEFT  0x05
#define HID_HAT_LEFT      0x06
#define HID_HAT_UPLEFT    0x07
#define HID_HAT_NOTHING   0x08

/* Button report (16 bits) */
#define HID_MASK_SQUARE   (1U << 0)
#define HID_MASK_CROSS    (1U << 1)
#define HID_MASK_CIRCLE   (1U << 2)
#define HID_MASK_TRIANGLE (1U << 3)
#define HID_MASK_L1       (1U << 4)
#define HID_MASK_R1       (1U << 5)
#define HID_MASK_L2       (1U << 6)
#define HID_MASK_R2       (1U << 7)
#define HID_MASK_SELECT   (1U << 8)
#define HID_MASK_START    (1U << 9)
#define HID_MASK_L3       (1U << 10)
#define HID_MASK_R3       (1U << 11)
#define HID_MASK_PS       (1U << 12)
#define HID_MASK_TP       (1U << 13)

/* HID analog sticks report 8 bits, 0x80 centered */
#define HID_JOYSTICK_MIN 0x00
#define HID_JOYSTICK_MID 0x80
#define HID_JOYSTICK_MAX 0xFF

typedef struct __attribute__((packed)) {
  /* digital buttons, 1 = pressed. Bit order matches the report descriptor. */
  uint8_t square_btn : 1;
  uint8_t cross_btn : 1;
  uint8_t circle_btn : 1;
  uint8_t triangle_btn : 1;
  uint8_t l1_btn : 1;
  uint8_t r1_btn : 1;
  uint8_t l2_btn : 1;
  uint8_t r2_btn : 1;
  uint8_t select_btn : 1;
  uint8_t start_btn : 1;
  uint8_t l3_btn : 1;
  uint8_t r3_btn : 1;
  uint8_t ps_btn : 1;
  uint8_t tp_btn : 1;
  uint8_t unused : 2;

  uint8_t direction; /* hat: 0=N 1=NE 2=E 3=SE 4=S 5=SW 6=W 7=NW 8=center */

  uint8_t l_x_axis;  /* 0x00 left/up, 0x80 middle, 0xff right/down */
  uint8_t l_y_axis;
  uint8_t r_x_axis;
  uint8_t r_y_axis;

  uint8_t right_axis; /* dpad analog axes, 0x00 released, 0xff pressed */
  uint8_t left_axis;
  uint8_t up_axis;
  uint8_t down_axis;

  uint8_t triangle_axis;
  uint8_t circle_axis;
  uint8_t cross_axis;
  uint8_t square_axis;
  uint8_t l1_axis;
  uint8_t r1_axis;
  uint8_t l2_axis;
  uint8_t r2_axis;
} din_report_t; /* 20 bytes */

static const uint8_t din_report_descriptor[] = {
    0x05, 0x01,       /* USAGE_PAGE (Generic Desktop) */
    0x09, 0x05,       /* USAGE (Gamepad) */
    0xa1, 0x01,       /* COLLECTION (Application) */
    0x15, 0x00,       /*   LOGICAL_MINIMUM (0) */
    0x25, 0x01,       /*   LOGICAL_MAXIMUM (1) */
    0x35, 0x00,       /*   PHYSICAL_MINIMUM (0) */
    0x45, 0x01,       /*   PHYSICAL_MAXIMUM (1) */
    0x75, 0x01,       /*   REPORT_SIZE (1) */
    0x95, 0x0e,       /*   REPORT_COUNT (14) */
    0x05, 0x09,       /*   USAGE_PAGE (Button) */
    0x19, 0x01,       /*   USAGE_MINIMUM (Button 1) */
    0x29, 0x0e,       /*   USAGE_MAXIMUM (Button 14) */
    0x81, 0x02,       /*   INPUT (Data,Var,Abs) */
    0x95, 0x02,       /*   REPORT_COUNT (2) */
    0x81, 0x01,       /*   INPUT (Cnst,Ary,Abs) */
    0x05, 0x01,       /*   USAGE_PAGE (Generic Desktop) */
    0x25, 0x07,       /*   LOGICAL_MAXIMUM (7) */
    0x46, 0x3b, 0x01, /*   PHYSICAL_MAXIMUM (315) */
    0x75, 0x04,       /*   REPORT_SIZE (4) */
    0x95, 0x01,       /*   REPORT_COUNT (1) */
    0x65, 0x14,       /*   UNIT (Eng Rot:Angular Pos) */
    0x09, 0x39,       /*   USAGE (Hat switch) */
    0x81, 0x42,       /*   INPUT (Data,Var,Abs,Null) */
    0x65, 0x00,       /*   UNIT (None) */
    0x95, 0x01,       /*   REPORT_COUNT (1) */
    0x81, 0x01,       /*   INPUT (Cnst,Ary,Abs) */
    0x26, 0xff, 0x00, /*   LOGICAL_MAXIMUM (255) */
    0x46, 0xff, 0x00, /*   PHYSICAL_MAXIMUM (255) */
    0x09, 0x30,       /*   USAGE (X) */
    0x09, 0x31,       /*   USAGE (Y) */
    0x09, 0x32,       /*   USAGE (Z) */
    0x09, 0x35,       /*   USAGE (Rz) */
    0x75, 0x08,       /*   REPORT_SIZE (8) */
    0x95, 0x04,       /*   REPORT_COUNT (4) */
    0x81, 0x02,       /*   INPUT (Data,Var,Abs) */
    0x06, 0x00, 0xff, /*   USAGE_PAGE (Vendor Specific) */
    0x09, 0x20,       /*   Unknown */
    0x09, 0x21,       /*   Unknown */
    0x09, 0x22,       /*   Unknown */
    0x09, 0x23,       /*   Unknown */
    0x09, 0x24,       /*   Unknown */
    0x09, 0x25,       /*   Unknown */
    0x09, 0x26,       /*   Unknown */
    0x09, 0x27,       /*   Unknown */
    0x09, 0x28,       /*   Unknown */
    0x09, 0x29,       /*   Unknown */
    0x09, 0x2a,       /*   Unknown */
    0x09, 0x2b,       /*   Unknown */
    0x95, 0x0c,       /*   REPORT_COUNT (12) */
    0x81, 0x02,       /*   INPUT (Data,Var,Abs) */
    0x0a, 0x21, 0x26, /*   Unknown */
    0x95, 0x08,       /*   REPORT_COUNT (8) */
    0xb1, 0x02,       /*   FEATURE (Data,Var,Abs) */
    0xc0              /* END_COLLECTION */
};

static const uint8_t din_device_descriptor[18] = {
    18,                          /* bLength */
    0x01,                        /* bDescriptorType: device */
    0x00, 0x02,                  /* bcdUSB 2.00 */
    0x00,                        /* bDeviceClass */
    0x00,                        /* bDeviceSubClass */
    0x00,                        /* bDeviceProtocol */
    DIN_ENDPOINT0_SIZE,          /* bMaxPacketSize0 */
    LSB(DIN_VENDOR_ID), MSB(DIN_VENDOR_ID),
    LSB(DIN_PRODUCT_ID), MSB(DIN_PRODUCT_ID),
    0x00, 0x01,                  /* bcdDevice */
    0x01,                        /* iManufacturer */
    0x02,                        /* iProduct */
    0x00,                        /* iSerialNumber */
    0x01                         /* bNumConfigurations */
};

#define DIN_CONFIG_TOTAL_LEN (9 + 9 + 9 + 7)
static const uint8_t din_configuration_descriptor[DIN_CONFIG_TOTAL_LEN] = {
    /* Configuration */
    9, 0x02,
    LSB(DIN_CONFIG_TOTAL_LEN), MSB(DIN_CONFIG_TOTAL_LEN),
    1,    /* bNumInterfaces */
    1,    /* bConfigurationValue */
    0,    /* iConfiguration */
    0x80, /* bmAttributes: bus powered */
    50,   /* bMaxPower: 100 mA */
    /* Interface */
    9, 0x04,
    DIN_GAMEPAD_INTERFACE, /* bInterfaceNumber */
    0,                     /* bAlternateSetting */
    1,                     /* bNumEndpoints */
    0x03,                  /* bInterfaceClass: HID */
    0x00,                  /* bInterfaceSubClass: no boot */
    0x00,                  /* bInterfaceProtocol: none */
    0,                     /* iInterface */
    /* HID class descriptor */
    9, 0x21,
    0x11, 0x01,          /* bcdHID 1.11 */
    0x00,                /* bCountryCode */
    0x01,                /* bNumDescriptors */
    0x22,                /* bDescriptorType: report */
    sizeof(din_report_descriptor), 0x00,
    /* Endpoint: IN 1, interrupt, 64 bytes, 1 ms */
    7, 0x05,
    (DIN_GAMEPAD_ENDPOINT | 0x80),
    0x03,
    DIN_GAMEPAD_SIZE, 0,
    1 /* bInterval */
};
