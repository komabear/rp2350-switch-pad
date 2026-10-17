/*
 * mayflash_switch2_controller - firmware main
 *
 * Waveshare RP2350-USB-C (dual Type-C) as a remote-controller bridge:
 *
 *   Parsec guest pad -> PC host tool --CDC--> [native USB: XInput + CDC]
 *                                               | core0
 *                                               | shared pad state (spinlock)
 *                                               | core1
 *                                             [PIO-USB port: DInput HID pad]
 *                                               |
 *                                             Mayflash (Switch mode, red LED)
 *                                               |
 *                                             Switch 2 dock
 *
 * PIO-USB pinout (board wiring, from Waveshare's pio_usb_configuration.h):
 *   D+ = GPIO12, D- = GPIO13, pinout DPDM (DM = DP+1)
 *
 * Native USB: CDC serial only (device 2E8A:000A). We deliberately do NOT
 * composite XInput here: the Windows Xbox 360 driver binds at device level
 * and fails composites (XnaComposite error, proven on hardware). Parsec
 * already provides the virtual Xbox pad on the PC and host/bridge.py reads
 * it via the XInput API. PIO-USB: DInput HID pad defined here.
 */
#include <stdio.h>
#include <string.h>

#include "pico/stdlib.h"
#include "pico/multicore.h"
#include "pico/sync.h"
#include "hardware/watchdog.h"
#include "hardware/irq.h"
#include "hardware/clocks.h"

#include "tusb.h"

#include "pio_usb.h"
#include "pio_usb_ll.h"

#include "proto.h"
#include "bridge.h"
#include "usb_definitions.h"

extern const string_descriptor_t *pio_string_table(void);
extern void pio_strings_init(uint8_t identity);
extern void pio_strings_link(void);
extern volatile uint8_t pio_usb_set_report_buf[64];
extern volatile uint8_t pio_usb_status_buf[16];
extern void pio_identity_init(void);
extern uint8_t pio_identity_get(void);
extern void pio_identity_set(uint8_t id);
#include "powa_descriptors.h"
extern void build_powa_report(const pad_state_t *pad, powa_report_t *r);
#include "ds4_device.h"
extern const usb_descriptor_buffers_t *pio_identity_descriptors(void);
#include "dinput_descriptors.h"

//--------------------------------------------------------------------+
// Crash forensics (survives soft reset via uninitialized RAM)
//--------------------------------------------------------------------+
#define CRASH_MAGIC 0x43524153u  /* 'CRAS' */
typedef struct {
    uint32_t magic;
    uint32_t boots;
    uint32_t cfsr, hfsr, bfar, mmfar;
    uint32_t pc, lr;
} crash_info_t;
static crash_info_t __uninitialized_ram(g_crash);

/* breadcrumbs for post-mortem: survive a reset (read via picotool from
 * BOOTSEL: stage codes tell which core stopped and where) */
#define DBG_MAGIC 0x44424721u
typedef struct {
    uint32_t magic;
    uint32_t c0_loops, c1_loops;
    uint32_t c0_ms, c1_ms;
    uint32_t c0_stage, c1_stage;
    uint32_t spare[5];
} dbg_t;
dbg_t __uninitialized_ram(g_dbg);
dbg_t g_prev_dbg; /* snapshot of the previous run, taken at boot */
uint32_t g_prev_valid;

void __attribute__((used)) HardFault_Handler(void);
void __attribute__((used)) MemManage_Handler(void);
void __attribute__((used)) BusFault_Handler(void);
void __attribute__((used)) UsageFault_Handler(void);

static __attribute__((noinline)) void crash_capture(uint32_t *sp) {
    g_crash.magic = CRASH_MAGIC;
    io_rw_32 *scb = (io_rw_32 *)0xE000ED28; /* CFSR */
    g_crash.cfsr = scb[0];
    g_crash.hfsr = scb[1];
    g_crash.mmfar = scb[3];
    g_crash.bfar = scb[4];
    g_crash.pc = sp[6];
    g_crash.lr = sp[5];
    /* park with interrupts masked; the status line reports the registers */
    while (true) {
        asm volatile ("cpsid i" ::: "memory");
        __wfi();
    }
}

void __attribute__((used)) HardFault_Handler(void) {
    uint32_t msp;
    asm volatile ("mrs %0, msp" : "=r"(msp));
    crash_capture((uint32_t *)msp);
}
void __attribute__((used)) MemManage_Handler(void) {
    uint32_t msp;
    asm volatile ("mrs %0, msp" : "=r"(msp));
    crash_capture((uint32_t *)msp);
}
void __attribute__((used)) BusFault_Handler(void) {
    uint32_t msp;
    asm volatile ("mrs %0, msp" : "=r"(msp));
    crash_capture((uint32_t *)msp);
}
void __attribute__((used)) UsageFault_Handler(void) {
    uint32_t msp;
    asm volatile ("mrs %0, msp" : "=r"(msp));
    crash_capture((uint32_t *)msp);
}

//--------------------------------------------------------------------+
// Shared state (spinlock-protected, crosses cores)
//--------------------------------------------------------------------+
pad_state_t g_pad;
mutex_t g_pad_mutex;
volatile bool g_pio_configured; /* PIO-USB endpoints configured by host */
volatile bool g_frames_live;    /* frames arriving over CDC */
volatile uint32_t g_last_in_ms = 0;  /* last completed HID IN transfer */
volatile uint32_t g_core1_alive_ms = 0; /* core1 heartbeat for the watchdog */

void store_pad(const pad_state_t *p) {
    /* bounded: if core1 died holding the lock, core0 gives up after 2 ms
     * instead of spinning forever (mutex_enter_timeout_us returns false) */
    if (mutex_enter_timeout_ms(&g_pad_mutex, 2)) {
        g_pad = *p;
        mutex_exit(&g_pad_mutex);
    }
}

void load_pad(pad_state_t *p) {
    if (mutex_enter_timeout_ms(&g_pad_mutex, 2)) {
        *p = g_pad;
        mutex_exit(&g_pad_mutex);
    } else {
        /* keep last known state rather than hang */
    }
}

//--------------------------------------------------------------------+
// CDC frame parsing (core0)
//--------------------------------------------------------------------+
typedef enum {
    PS_SYNC0,
    PS_SYNC1,
    PS_PAYLOAD,
} parse_state_t;

static parse_state_t pstate = PS_SYNC0;
static uint8_t pidx = 0;
static uint8_t pbuf[PAD_FRAME_SIZE];
static uint32_t last_frame_ms = 0;
static bool released = true;

/* diagnostics: first bytes seen after each quiet period */
static uint8_t diag_buf[16];
static uint8_t diag_len = 0;
static uint32_t diag_last_byte_ms = 0;
static uint32_t diag_frames = 0;

static void apply_frame(const uint8_t *f) {
    /* f = 9-byte payload (sync bytes stripped by the parser):
     * [0]=flags1 [1]=flags2 [2]=lx [3]=ly [4]=rx [5]=ry
     * [6]=hat    [7]=reserved [8]=xor of [0..7] */
    uint8_t x = 0;
    for (int i = 0; i <= 7; i++) {
        x ^= f[i];
    }
    if (x != f[8]) {
        return; /* checksum mismatch: drop frame */
    }

    pad_state_t p;
    memset(&p, 0, sizeof(p));
    p.y = !!(f[0] & PF_Y);
    p.b = !!(f[0] & PF_B);
    p.a = !!(f[0] & PF_A);
    p.x = !!(f[0] & PF_X);
    p.l = !!(f[0] & PF_L);
    p.r = !!(f[0] & PF_R);
    p.zl = !!(f[0] & PF_ZL);
    p.zr = !!(f[0] & PF_ZR);
    p.minus = !!(f[1] & PF_MINUS);
    p.plus = !!(f[1] & PF_PLUS);
    p.l3 = !!(f[1] & PF_L3);
    p.r3 = !!(f[1] & PF_R3);
    p.home = !!(f[1] & PF_HOME);
    p.capture = !!(f[1] & PF_CAPTURE);
    p.lx = (int8_t)f[2];
    p.ly = (int8_t)f[3];
    p.rx = (int8_t)f[4];
    p.ry = (int8_t)f[5];
    p.hat = (f[6] > 7) ? HAT_CENTER : f[6];

    store_pad(&p);
    last_frame_ms = board_millis();
    released = false;
    g_frames_live = true;
    diag_frames++;
}

static void release_all(void) {
    pad_state_t p;
    pad_release(&p);
    store_pad(&p);
    released = true;
}

static void cdc_parse_byte(uint8_t b) {
    switch (pstate) {
        case PS_SYNC0:
            if (b == PAD_SYNC0) {
                pstate = PS_SYNC1;
            }
            break;
        case PS_SYNC1:
            pstate = (b == PAD_SYNC1) ? PS_PAYLOAD : PS_SYNC0;
            pidx = 0;
            break;
        case PS_PAYLOAD:
            pbuf[pidx++] = b;
            /* payload is frame minus the two sync bytes */
            if (pidx >= PAD_FRAME_SIZE - 2) {
                apply_frame(pbuf);
                pstate = PS_SYNC0;
            }
            break;
    }
}

#ifdef NATIVE_PAD
static void pump_cdc(void) {}
static void send_status_line(void) {}
#else
static void pump_cdc(void) {
    uint8_t buf[64];
    uint32_t n = tud_cdc_read(buf, sizeof(buf));
    for (uint32_t i = 0; i < n; i++) {
        uint32_t now = board_millis();
        /* diag: capture first bytes after a quiet period */
        if (diag_len == 0 ||
            (now - diag_last_byte_ms) > 500) {
            diag_len = 0;
        }
        if (diag_len < sizeof(diag_buf)) {
            diag_buf[diag_len++] = buf[i];
        }
        diag_last_byte_ms = now;

        /* identity commands: 'i' = DInput, 'h' = HORI (single letters,
         * outside $P framing; they never appear in frame payloads' sync) */
        uint8_t b = buf[i];
        if (b == 'i' || b == 'h' || b == 'd' || b == 'p') {
            pio_identity_set(b == 'h' ? 1 : (b == 'd' ? 2 : (b == 'p' ? 3 : 0)));
            continue;
        }

        cdc_parse_byte(buf[i]);
    }
}



//--------------------------------------------------------------------+
// CDC status line (core0)
//--------------------------------------------------------------------+
static void send_status_line(void) {
    static uint32_t last_status;
    uint32_t now = board_millis();
    if (now - last_status < 250) {
        return;
    }
    last_status = now;

    if (!tud_cdc_connected()) {
        return;
    }

    uint8_t flags = 0;
    flags |= tud_ready() ? 0x01 : 0; /* CDC mounted by the PC */
    flags |= g_pio_configured ? 0x02 : 0;
    flags |= g_frames_live ? 0x04 : 0;

    char line[160];
    char hex[3 * sizeof(diag_buf) + 1];
    for (int i = 0; i < diag_len; i++) {
        snprintf(&hex[i * 3], 4, "%02X ", diag_buf[i]);
    }
    if (diag_len == 0) {
        hex[0] = '-';
        hex[1] = '\0';
    }
    int n = snprintf(line, sizeof(line),
                     "$S %u D[%s] F%lu B%lu %08lX %08lX %08lX\n", flags,
                     hex, (unsigned long)diag_frames,
                     (unsigned long)g_crash.boots,
                     (unsigned long)g_crash.cfsr,
                     (unsigned long)g_crash.bfar,
                     (unsigned long)g_crash.pc);
    tud_cdc_write(line, (uint32_t)n);
    tud_cdc_write_flush();
}
#endif

//--------------------------------------------------------------------+
// core0: native USB (XInput towards Parsec host + CDC bridge)
//--------------------------------------------------------------------+
void core0_main(void) {
    /* BISECT EXPERIMENT: hardware watchdog disabled */
    /* watchdog_enable(2000, 1); */

    tusb_rhport_init_t dev_init = {
        .role = TUSB_ROLE_DEVICE,
        .speed = TUSB_SPEED_FULL,
    };
    tusb_init(0, &dev_init);

    while (1) {
        g_dbg.c0_loops++;
        g_dbg.c0_ms = board_millis();
        g_dbg.c0_stage = 1;
        tud_task();
        g_dbg.c0_stage = 2;
        pump_cdc();

#ifdef NATIVE_PAD
        {
            /* The Switch dock cuts/restores USB power around console sleep. We
             * have no VBUS sense, so after a wake the device would stay
             * attached-but-suspended forever and the console never re-enumerates
             * it. While the bus stays suspended, re-present ourselves
             * (drop and re-raise the D+ pull-up) every 2 s so a fresh attach is
             * always visible shortly after the console wakes. */
            static uint32_t susp_since, last_pulse;
            uint32_t pnow = board_millis();
            if (tud_suspended()) {
                if (susp_since == 0) {
                    susp_since = pnow ? pnow : 1;
                    last_pulse = pnow;
                } else if ((pnow - susp_since) > 1500 && (pnow - last_pulse) >= 2000) {
                    last_pulse = pnow;
                    tud_disconnect();
                    sleep_ms(30);
                    tud_connect();
                }
            } else {
                susp_since = 0;
            }
        }
        {
            /* status for the host GUI (GET_REPORT on the PIO link):
             * [0] magic 0xA5  [1] native USB mounted by the Switch/host
             * [2] suspended   [3] frames flowing  [4..7] frames applied
             * [8..11] uptime ms */
            static uint32_t st_pub;
            uint32_t snow = board_millis();
            if (snow - st_pub >= 100) {
                st_pub = snow;
                uint32_t fr = diag_frames;
                pio_usb_status_buf[0] = 0xA5;
                pio_usb_status_buf[1] = tud_mounted() ? 1 : 0;
                pio_usb_status_buf[2] = tud_suspended() ? 1 : 0;
                pio_usb_status_buf[3] = g_frames_live ? 1 : 0;
                pio_usb_status_buf[12] =
                    (snow - g_last_in_ms) < 500 ? 1 : 0; /* host polling IN */
                memcpy((void *)&pio_usb_status_buf[4], &fr, 4);
                memcpy((void *)&pio_usb_status_buf[8], &snow, 4);
            }
        }
        {
            static uint32_t hid_last;
            uint32_t hnow = board_millis();
            if (hnow != hid_last && tud_hid_ready()) {
                hid_last = hnow;
                pad_state_t hp;
                load_pad(&hp);
                powa_report_t hr;
                build_powa_report(&hp, &hr);
                tud_hid_report(0, &hr, sizeof(hr));
            }
        }
#endif

#ifdef SELFTEST
        /* standalone: cycle d-pad E,S,W,N (1 s each) then pulse A, forever,
         * so the board can be powered from the dock alone with no PC */
        {
            static uint32_t st_last;
            static uint8_t st_step;
            uint32_t now = board_millis();
            if (now - st_last >= 1000) {
                st_last = now;
                pad_state_t p;
                pad_release(&p);
                static const uint8_t hats[4] = {2, 4, 6, 0};
                if (st_step < 4) {
                    p.hat = hats[st_step];
                } else {
                    p.a = 1;
                }
                st_step = (st_step + 1) % 5;
                store_pad(&p);
                last_frame_ms = now;
                released = false;
            }
        }
#endif

        /* BISECT EXPERIMENT: core1 liveness reboot disabled */

        g_dbg.c0_stage = 3;
        if (!released &&
            (board_millis() - last_frame_ms) > PAD_RELEASE_TIMEOUT_MS) {
            g_dbg.c0_stage = 4;
            release_all();
            g_frames_live = false;
        }

        send_status_line();

        /* PIO-USB endpoints appear when the Mayflash sends SET_CONFIGURATION;
         * the endpoint pool lives in shared RAM, poll its flag from here */
        if (!g_pio_configured && PIO_USB_ENDPOINT(3)->is_tx) {
            g_pio_configured = true;
        }

        /* watchdog_update();  BISECT EXPERIMENT disabled */
    }
}

//--------------------------------------------------------------------+
// core1: PIO-USB device (HORI Pokken Switch pad towards the Mayflash)
//--------------------------------------------------------------------+
static usb_device_t *pio_dev;

static void send_pio_report(void) {
    static uint32_t last_send;
    uint32_t now = board_millis();
    if (now - last_send < 1) {
        return;
    }
    last_send = now;

    if (pio_dev == NULL) {
        return;
    }

    /* IN1 -> endpoint pool index (1<<1)|1 = 3, matching
     * pio_usb_device_get_endpoint_by_address() */
    endpoint_t *ep = PIO_USB_ENDPOINT(3);
    if (!ep->is_tx) {
        return;
    }

    pad_state_t p;
    load_pad(&p);

    switch (pio_identity_get()) {
        case 1: {
            switch_report_t r;
            build_switch_report(&p, &r);
            pio_usb_set_out_data(ep, (uint8_t *)&r, sizeof(r));
            break;
        }
        case 2: {
            ds4_report_t r;
            build_ds4_report(&p, &r);
            pio_usb_set_out_data(ep, (uint8_t *)&r, sizeof(r));
            break;
        }
        case 3: {
            powa_report_t r;
            build_powa_report(&p, &r);
            pio_usb_set_out_data(ep, (uint8_t *)&r, sizeof(r));
            break;
        }
        default: {
            din_report_t r;
            build_dinput_report(&p, &r);
            pio_usb_set_out_data(ep, (uint8_t *)&r, sizeof(r));
            break;
        }
    }
}

void core1_main(void) {
    sleep_ms(10);

    static pio_usb_configuration_t config = PIO_USB_DEFAULT_CONFIG;
#ifdef NATIVE_PAD
    /* PIO port = PC link: vendor-defined HID with one 64-byte feature
     * report; the host tool writes pad frames to it (SET_REPORT). */
    static uint8_t link_dev[18];
    static uint8_t link_cfg[POWA_CONFIG_TOTAL_LEN];
    static const uint8_t link_rd[64] = {
        0x06, 0x00, 0xFF, 0x09, 0x01, 0xA1, 0x01, 0x09, 0x02, 0x15, 0x00,
        0x26, 0xFF, 0x00, 0x75, 0x08, 0x95, 0x40, 0xB1, 0x02, 0xC0};
    static const uint8_t *link_rd_tab[1] = {link_rd};
    memcpy(link_dev, powa_device_descriptor, 18);
    link_dev[2] = 0x00; link_dev[3] = 0x02;   /* bcdUSB 2.00 */
    link_dev[8] = 0x8A; link_dev[9] = 0x2E;   /* VID 2E8A */
    link_dev[10] = 0x0B; link_dev[11] = 0x40; /* PID 400B */
    memcpy(link_cfg, powa_configuration_descriptor, POWA_CONFIG_TOTAL_LEN);
    link_cfg[25] = 21; link_cfg[26] = 0;      /* report descriptor length */
    static usb_descriptor_buffers_t link_desc;
    link_desc.device = link_dev;
    link_desc.config = link_cfg;
    link_desc.hid_report = link_rd_tab;
    link_desc.string = pio_string_table();
    const usb_descriptor_buffers_t *desc = &link_desc;
#else
    const usb_descriptor_buffers_t *desc = pio_identity_descriptors();
#endif

    pio_dev = pio_usb_device_init(&config, desc);

    while (true) {
        g_dbg.c1_loops++;
        g_dbg.c1_ms = board_millis();
        g_dbg.c1_stage = 10;
        pio_usb_device_task();
        g_dbg.c1_stage = 11;
#ifdef NATIVE_PAD
        {
            static uint8_t last_seq = 0xFF;
            uint8_t f[9];
            for (int i = 0; i < 9; i++) {
                f[i] = pio_usb_set_report_buf[i];
            }
            if (f[7] != last_seq) { /* [7] = sequence byte from the host */
                uint8_t x = 0;
                for (int i = 0; i <= 7; i++) {
                    x ^= f[i];
                }
                if (x == f[8]) {
                    last_seq = f[7];
                    g_dbg.c1_stage = 12;
                    apply_frame(f);
                    g_dbg.c1_stage = 13;
                }
            }
        }
#else
        send_pio_report();
#endif
        /* core1 heartbeat: if the PIO-USB stack hangs, core0 stops seeing
         * this bump and the hardware watchdog reboots the chip */
        g_core1_alive_ms = board_millis();
    }
}

//--------------------------------------------------------------------+
// main
//--------------------------------------------------------------------+
int main(void) {
    /* PIO-USB bit-banging needs a sysclock that is a multiple of 12 MHz */
    set_sys_clock_khz(120000, true);

    /* Pre-assert the D+ pull-up (GPIO12) as a solid GPIO high BEFORE the
     * PIO-USB stack starts. Boards of this family ship with the 1.5k D+
     * pull-up resistor unbuilt ("NC/1.5K" solder bridge), so attach
     * signaling depends entirely on the PIO driving D+ - which starts late
     * (after core1 boots) and with PIO drive strength. Some hosts (Switch
     * dock) fail to register the attach; a GPIO-high from reset solves it.
     * The PIO re-takes the pin when the stack starts. */
    /* D+ pre-assert removed: broke PIO-USB enumeration. See git history. */

    if (g_crash.magic != CRASH_MAGIC) {
        g_crash.magic = CRASH_MAGIC;
        g_crash.boots = 0;
        g_crash.cfsr = g_crash.hfsr = g_crash.bfar = 0;
        g_crash.mmfar = g_crash.pc = g_crash.lr = 0;
    }
    g_crash.boots++;
    if (g_dbg.magic != DBG_MAGIC) {
        memset(&g_dbg, 0, sizeof(g_dbg));
        g_dbg.magic = DBG_MAGIC;
        g_prev_valid = 0;
    } else {
        g_prev_dbg = g_dbg; /* previous run's final breadcrumbs */
        g_prev_valid = 1;
    }

    pio_identity_init();
    pio_strings_init(pio_identity_get());
#ifdef NATIVE_PAD
    pio_strings_link();
#endif
    mutex_init(&g_pad_mutex);
    pad_release(&g_pad);

    multicore_reset_core1();
    multicore_launch_core1(core1_main);

    sleep_ms(5); /* let core1 claim the PIO before core0 gets going */

    core0_main();
    return 0;
}

#ifdef NATIVE_PAD
/* diagnostics, read from the PC via GET_REPORT(feature) on the native pad */
uint16_t diag_fill(uint8_t *buf, uint16_t len) {
    /* 0 magic 1 boots 2 cfsr 3 pc | 4 now 5 core1_alive 6 applied 7 pio_cfg
     * 8 prev_valid 9 prev.c0_loops 10 prev.c1_loops 11 prev.c0_ms
     * 12 prev.c1_ms 13 prev.stages (c0<<16|c1) */
    uint32_t v[14] = {
        g_crash.magic, g_crash.boots, g_crash.cfsr, g_crash.pc,
        board_millis(), g_core1_alive_ms, diag_frames,
        (uint32_t)g_pio_configured,
        g_prev_valid, g_prev_dbg.c0_loops, g_prev_dbg.c1_loops,
        g_prev_dbg.c0_ms, g_prev_dbg.c1_ms,
        (g_prev_dbg.c0_stage << 16) | (g_prev_dbg.c1_stage & 0xFFFF)};
    uint16_t n = sizeof(v) < len ? sizeof(v) : len;
    memcpy(buf, v, n);
    return n;
}
#endif

#ifdef NATIVE_PAD
/* the host (Switch/PC) read our report: it is really polling the pad */
void tud_hid_report_complete_cb(uint8_t instance, uint8_t const *report,
                                uint16_t len) {
    (void)instance; (void)report; (void)len;
    g_last_in_ms = board_millis();
}
#endif
