/*
 * tusb_config.h - TinyUSB configuration for the native-USB stack.
 *
 * Device only: XInput (custom app driver) + CDC. The Mayflash-side pad runs
 * on Pico-PIO-USB's own device stack (no TinyUSB involvement) on core1.
 */
#ifndef _TUSB_CONFIG_H_
#define _TUSB_CONFIG_H_

#ifdef __cplusplus
extern "C" {
#endif

#define CFG_TUSB_OS OPT_OS_PICO

#define CFG_TUD_ENABLED 1
#ifndef CFG_TUD_MAX_SPEED
#define CFG_TUD_MAX_SPEED OPT_MODE_FULL_SPEED
#endif

/* CDC for the bridge protocol */
#ifdef NATIVE_PAD
#define CFG_TUD_CDC 0
#define CFG_TUD_HID 1
#define CFG_TUD_HID_EP_BUFSIZE 64
#else
#define CFG_TUD_CDC 1
#define CFG_TUD_HID 0
#endif
#define CFG_TUD_CDC_RX_BUFSIZE 256
#define CFG_TUD_CDC_TX_BUFSIZE 256
#define CFG_TUD_CDC_EP_BUFSIZE 64

/* everything else off; XInput is our own app-provided class driver */
#define CFG_TUD_MSC 0
#define CFG_TUD_MIDI 0
#define CFG_TUD_VENDOR 0
#define CFG_TUD_DFU 0
#define CFG_TUD_DFU_RT 0
#define CFG_TUD_BTH 0
#define CFG_TUD_ECM_RNDIS 0
#define CFG_TUD_NCM 0

#ifndef CFG_TUD_ENDPOINT0_SIZE
#define CFG_TUD_ENDPOINT0_SIZE 64
#endif

#define CFG_TUD_ENDPOINT_PAIRS 8  /* RP2040 USB device: 16 endpoints */
#ifndef CFG_TUD_TASK_QUEUE_SZ
#define CFG_TUD_TASK_QUEUE_SZ 32
#endif

#ifdef __cplusplus
}
#endif

#endif
