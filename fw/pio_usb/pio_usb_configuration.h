
#pragma once

/* Board selection (local change): fw/CMakeLists.txt defines both macros per
 * firmware target (bridge = RP2350-USB-C, bridge_cm = RP2350-USB-CM). Without
 * them the RP2350-USB-C is assumed, as in Waveshare's original file. */
#if !defined(RP2350_USB_C) && !defined(RP2350_USB_CM)
#define RP2350_USB_C  1
#define RP2350_USB_CM 0
#elif !defined(RP2350_USB_C) || !defined(RP2350_USB_CM)
#error "define both RP2350_USB_C and RP2350_USB_CM (one 1, the other 0)"
#elif RP2350_USB_C == RP2350_USB_CM
#error "exactly one of RP2350_USB_C and RP2350_USB_CM must be 1"
#endif

#if RP2350_USB_C
typedef enum {
  PIO_USB_PINOUT_DPDM = 0,  // DM = DP+1
  PIO_USB_PINOUT_DMDP,      // DM = DP-1
} PIO_USB_PINOUT;

typedef struct {
    uint8_t pin_dp;
    uint8_t pio_tx_num;
    uint8_t sm_tx;
    uint8_t tx_ch;
    uint8_t pio_rx_num;
    uint8_t sm_rx;
    uint8_t sm_eop;
    void* alarm_pool;
    int8_t debug_pin_rx;
    int8_t debug_pin_eop;
    bool skip_alarm_pool;
    PIO_USB_PINOUT pinout;
} pio_usb_configuration_t;

#ifndef PIO_USB_DP_PIN_DEFAULT
#define PIO_USB_DP_PIN_DEFAULT 12
#endif

#define PIO_USB_TX_DEFAULT 0
#define PIO_SM_USB_TX_DEFAULT 0
#define PIO_USB_DMA_TX_DEFAULT 0

#define PIO_USB_RX_DEFAULT 1
#define PIO_SM_USB_RX_DEFAULT 0
#define PIO_SM_USB_EOP_DEFAULT 1

#define PIO_USB_DEBUG_PIN_NONE (-1)

#define PIO_USB_DEFAULT_CONFIG                                             \
  {                                                                        \
    PIO_USB_DP_PIN_DEFAULT, PIO_USB_TX_DEFAULT, PIO_SM_USB_TX_DEFAULT,     \
        PIO_USB_DMA_TX_DEFAULT, PIO_USB_RX_DEFAULT, PIO_SM_USB_RX_DEFAULT, \
        PIO_SM_USB_EOP_DEFAULT, NULL, PIO_USB_DEBUG_PIN_NONE,              \
        PIO_USB_DEBUG_PIN_NONE, false, PIO_USB_PINOUT_DPDM                 \
  }

#define PIO_USB_EP_POOL_CNT 32
#define PIO_USB_DEV_EP_CNT 16
#define PIO_USB_DEVICE_CNT 4
#define PIO_USB_HUB_PORT_CNT 8
#define PIO_USB_ROOT_PORT_CNT 2

#define PIO_USB_EP_SIZE 64

#elif RP2350_USB_CM
typedef enum {
  PIO_USB_PINOUT_DPDM = 0,  // DM = DP+1
  PIO_USB_PINOUT_DMDP,      // DM = DP-1
} PIO_USB_PINOUT;

typedef struct {
    uint8_t pin_dp;
    uint8_t pio_tx_num;
    uint8_t sm_tx;
    uint8_t tx_ch;
    uint8_t pio_rx_num;
    uint8_t sm_rx;
    uint8_t sm_eop;
    void* alarm_pool;
    int8_t debug_pin_rx;
    int8_t debug_pin_eop;
    bool skip_alarm_pool;
    PIO_USB_PINOUT pinout;
} pio_usb_configuration_t;

#ifndef PIO_USB_DP_PIN_DEFAULT
#define PIO_USB_DP_PIN_DEFAULT 13
#endif

#define PIO_USB_TX_DEFAULT 0
#define PIO_SM_USB_TX_DEFAULT 0
#define PIO_USB_DMA_TX_DEFAULT 0

#define PIO_USB_RX_DEFAULT 1
#define PIO_SM_USB_RX_DEFAULT 0
#define PIO_SM_USB_EOP_DEFAULT 1

#define PIO_USB_DEBUG_PIN_NONE (-1)

#define PIO_USB_DEFAULT_CONFIG                                             \
  {                                                                        \
    PIO_USB_DP_PIN_DEFAULT, PIO_USB_TX_DEFAULT, PIO_SM_USB_TX_DEFAULT,     \
        PIO_USB_DMA_TX_DEFAULT, PIO_USB_RX_DEFAULT, PIO_SM_USB_RX_DEFAULT, \
        PIO_SM_USB_EOP_DEFAULT, NULL, PIO_USB_DEBUG_PIN_NONE,              \
        PIO_USB_DEBUG_PIN_NONE, false, PIO_USB_PINOUT_DMDP                 \
  }

#define PIO_USB_EP_POOL_CNT 32
#define PIO_USB_DEV_EP_CNT 16
#define PIO_USB_DEVICE_CNT 4
#define PIO_USB_HUB_PORT_CNT 8
#define PIO_USB_ROOT_PORT_CNT 2

#define PIO_USB_EP_SIZE 64
#endif
