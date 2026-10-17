#!/usr/bin/env py
"""
gui_hid.py - GUI for the Switch 2 remote pad bridge (PIO-USB link version).

Streams one PC controller (XInput) to the board over the PIO-USB link; the
board's native port presents it to the Switch as a PowerA pad.

Setup order (the PIO link wedges if the native cable is changed while up):
  1. board native Type-C -> Switch dock   2. board PIO Type-C -> this PC
  3. run this GUI

  py gui_hid.py
"""
import ctypes
import json
import logging
import logging.handlers
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

try:
    import hid
    import bridge
    import bridge_hid as bh
except ImportError as e:
    print(f"import error: {e}\nrun: python -m pip install hidapi pyserial")
    sys.exit(1)

SETTINGS_PATH = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "mayflash_switch2_controller", "gui_settings.json",
)


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(d):
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except OSError:
        pass


LOG_PATH = os.path.join(os.path.dirname(SETTINGS_PATH), "gui.log")
log = logging.getLogger("padbridge")


def setup_logging():
    """Small rotating log of link/board state changes, to diagnose freezes."""
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        h = logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=200_000, backupCount=2, encoding="utf-8"
        )
        h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
    except OSError:
        pass


def keep_process_responsive():
    """Windows 11 throttles background/minimised apps (timers coarsen, CPU
    drops to efficiency cores). The link needs steady 125 Hz traffic, so opt
    out of power throttling and use 1 ms timers."""
    try:
        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception:
        pass
    try:
        class PPT(ctypes.Structure):
            _fields_ = [("Version", ctypes.c_ulong),
                        ("ControlMask", ctypes.c_ulong),
                        ("StateMask", ctypes.c_ulong)]
        k32 = ctypes.windll.kernel32
        st = PPT(1, 0x1, 0x0)  # EXECUTION_SPEED throttling: controlled, OFF
        k32.SetProcessInformation(
            k32.GetCurrentProcess(), 4, ctypes.byref(st), ctypes.sizeof(st)
        )
        k32.SetPriorityClass(k32.GetCurrentProcess(), 0x8000)  # above normal
    except Exception:
        pass


SLOTS = 4
KEEPALIVE_S = 30  # idle seconds before a keep-alive nudge
RATE_HZ = 125
GUIDE = 0x0400  # only visible through the undocumented XInputGetStateEx

# --------------------------------------------------------------- XInput ---
_getex = None
if bridge.XINPUT is not None:
    try:  # ordinal 100 = XInputGetStateEx (includes the Guide button)
        _getex = ctypes.WINFUNCTYPE(
            ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(bridge.XINPUT_STATE)
        )(100, bridge.XINPUT)
    except Exception:
        _getex = None


def read_pad(slot):
    """-> (gamepad or None, guide_pressed)"""
    st = bridge.XINPUT_STATE()
    if _getex is not None:
        rc = _getex(slot, ctypes.byref(st))
        guide = bool(st.Gamepad.wButtons & GUIDE)
    else:
        rc = bridge.XINPUT.XInputGetState(slot, ctypes.byref(st))
        guide = False
    return (st.Gamepad, guide) if rc == 0 else (None, False)


def swap_abxy(gp):
    """Xbox A<->B and X<->Y, so the Xbox pad's physical layout matches the
    Switch's (Switch A is the right button, Xbox B is)."""
    out = bridge.XINPUT_GAMEPAD()
    ctypes.pointer(out)[0] = gp  # struct copy
    b = gp.wButtons
    a, bb = bool(b & bridge.XINPUT_GAMEPAD_A), bool(b & bridge.XINPUT_GAMEPAD_B)
    x, y = bool(b & bridge.XINPUT_GAMEPAD_X), bool(b & bridge.XINPUT_GAMEPAD_Y)
    b &= ~(bridge.XINPUT_GAMEPAD_A | bridge.XINPUT_GAMEPAD_B
           | bridge.XINPUT_GAMEPAD_X | bridge.XINPUT_GAMEPAD_Y)
    if a:
        b |= bridge.XINPUT_GAMEPAD_B
    if bb:
        b |= bridge.XINPUT_GAMEPAD_A
    if x:
        b |= bridge.XINPUT_GAMEPAD_Y
    if y:
        b |= bridge.XINPUT_GAMEPAD_X
    out.wButtons = b
    return out


# --------------------------------------------------------------- worker ---
class Shared:
    """state written by the worker thread, read by the UI"""

    def __init__(self):
        self.lock = threading.Lock()
        self.mode_auto = True
        self.manual_slot = 0
        self.send_enabled = True
        self.swap_abxy = False
        self.keepalive = True
        self.pad_on_pc = False  # the board's pad port is plugged into THIS PC
        self.home = False
        self.capture = False
        # outputs
        self.connected = [False] * SLOTS
        self.active_slot = None
        self.gamepad = None
        self.link_open = False
        self.link_stuck = False
        self.sent = 0
        self.failed = 0
        self.rate = 0.0
        self.board = None  # dict from the status readback
        self.board_age = 1e9
        self.stop = threading.Event()


def parse_board(r):
    if len(r) >= 12 and r[0] == 0xA5:
        return {
            "mounted": bool(r[1]),
            "suspended": bool(r[2]),
            "live": bool(r[3]),
            "polling": bool(r[12]) if len(r) > 12 else None,
            "applied": int.from_bytes(bytes(r[4:8]), "little"),
            "uptime": int.from_bytes(bytes(r[8:12]), "little") / 1000.0,
        }
    return None


def worker(sh):
    dev = None
    seq = 0
    period = 1.0 / RATE_HZ
    consec_fail = 0
    next_status = 0.0
    next_open = 0.0
    win_t, win_n = time.time(), 0
    last_ok = 0.0
    prev_state = None
    last_sig, last_input, last_nudge, nudge_left = None, time.time(), 0.0, 0
    next_enum = 0.0
    next_beat = 0.0
    log.info("worker started")

    while not sh.stop.is_set():
        t0 = time.time()

        # ---- pads
        conn = []
        pads = {}
        for s in range(SLOTS):
            gp, guide = read_pad(s)
            conn.append(gp is not None)
            pads[s] = (gp, guide)
        with sh.lock:
            auto, manual = sh.mode_auto, sh.manual_slot
            enabled, home, cap = sh.send_enabled, sh.home, sh.capture
            swap = sh.swap_abxy
            keepalive = sh.keepalive
        if auto:
            slot = next((s for s in range(SLOTS) if conn[s]), None)
        else:
            slot = manual if conn[manual] else None
        gp, guide = pads.get(slot, (None, False)) if slot is not None else (None, False)

        # ---- link
        if dev is None and t0 >= next_open:
            next_open = t0 + 1.0
            try:
                dev = bh.open_link()
                consec_fail = 0
            except OSError:
                dev = None

        if dev is not None:
            use = gp if (gp is not None and enabled) else bridge.NEUTRAL_GAMEPAD
            if swap and use is gp:
                use = swap_abxy(gp)

            # keep-alive: if nothing changed for KEEPALIVE_S, send a tiny input
            # (3 frames of 10% right-stick) so neither the board nor the
            # Switch ever sees a completely static pad
            sig = None if gp is None else (
                gp.wButtons, gp.bLeftTrigger >> 4, gp.bRightTrigger >> 4,
                gp.sThumbLX >> 11, gp.sThumbLY >> 11,
                gp.sThumbRX >> 11, gp.sThumbRY >> 11)
            if sig != last_sig:
                last_sig, last_input = sig, t0
            if (keepalive and enabled and nudge_left == 0
                    and t0 - last_input >= KEEPALIVE_S
                    and t0 - last_nudge >= KEEPALIVE_S):
                nudge_left, last_nudge = 3, t0
            if nudge_left > 0:
                nudge_left -= 1
                out = bridge.XINPUT_GAMEPAD()
                ctypes.pointer(out)[0] = use
                out.sThumbRY = 3300  # ~10% of full scale
                use = out
            frame = bridge.build_frame(
                use, home=home or (guide and enabled), capture=cap
            )
            seq = (seq + 1) & 0xFF
            ok = dev.send_feature_report(bh.to_report(frame, seq)) >= 0
            if ok:
                consec_fail = 0
                last_ok = t0
                win_n += 1
            else:
                consec_fail += 1
                with sh.lock:
                    sh.failed += 1
            with sh.lock:
                if ok:
                    sh.sent += 1

            # board status readback (2 Hz)
            if ok and t0 >= next_status:
                next_status = t0 + 0.5
                try:
                    r = dev.get_feature_report(0, 65)
                    b = parse_board(bytes(r[1:])) if r else None
                    if b:
                        with sh.lock:
                            sh.board, sh.board_age = b, 0.0
                except Exception:
                    pass

            if consec_fail > 125:  # ~1 s of failures
                try:
                    dev.close()
                except Exception:
                    pass
                dev = None
                next_open = t0 + 1.0
                consec_fail = 0

        if t0 >= next_enum:  # is the pad port plugged into this PC by mistake?
            next_enum = t0 + 1.0
            try:
                on_pc = bool(hid.enumerate(0x20D6, 0xA713))
            except Exception:
                on_pc = False
            with sh.lock:
                sh.pad_on_pc = on_pc

        with sh.lock:
            sh.connected = conn
            sh.active_slot = slot
            sh.gamepad = gp
            sh.link_open = dev is not None
            sh.link_stuck = dev is None or (t0 - last_ok > 1.0)
            sh.board_age += period
            if t0 - win_t >= 1.0:
                sh.rate = win_n / (t0 - win_t)
                win_t, win_n = t0, 0

        # ---- log state changes + a once-a-minute heartbeat
        with sh.lock:
            b = sh.board
            state = (dev is not None, sh.link_stuck, slot,
                     None if b is None else (b["mounted"], b["suspended"], b["polling"]))
            rate_now, sent_now, failed_now = sh.rate, sh.sent, sh.failed
        if state != prev_state:
            log.info("state link_open=%s stuck=%s slot=%s board(mounted,susp,polling)=%s "
                     "rate=%.0f sent=%d failed=%d", *state, rate_now, sent_now, failed_now)
            prev_state = state
        if t0 >= next_beat:
            next_beat = t0 + 60
            log.info("heartbeat rate=%.0f sent=%d failed=%d late_ms=%.0f",
                     rate_now, sent_now, failed_now, (t0 - win_t) * 1000)

        dt = period - (time.time() - t0)
        if dt > 0:
            time.sleep(dt)

    log.info("worker stopping")
    if dev is not None:
        try:  # release everything before leaving
            dev.send_feature_report(
                bh.to_report(bridge.build_frame(bridge.NEUTRAL_GAMEPAD), seq + 1)
            )
            dev.close()
        except Exception:
            pass


# ------------------------------------------------------------------- UI ---
BTN_LABELS = [
    ("Y", 0x8000), ("B", 0x2000), ("A", 0x1000), ("X", 0x4000),
    ("L", 0x0100), ("R", 0x0200), ("-", 0x0020), ("+", 0x0010),
    ("L3", 0x0040), ("R3", 0x0080),
    ("Up", 0x0001), ("Down", 0x0002), ("Left", 0x0004), ("Right", 0x0008),
]
OKC, BADC, WARNC, IDLEC = "#2e9e4f", "#cc3333", "#d89a1e", "#888888"


class App:
    def __init__(self, root):
        self.root = root
        root.title("Switch 2 Remote Pad Bridge")
        root.resizable(False, False)
        self.sh = Shared()
        self.worker = threading.Thread(target=worker, args=(self.sh,), daemon=True)
        self.worker.start()

        pad = dict(padx=10, pady=4)

        # ---- controller picker
        box = ttk.LabelFrame(root, text="Controller to send to the Switch")
        box.grid(row=0, column=0, sticky="we", **pad)
        self.mode = tk.StringVar(value="auto")
        ttk.Radiobutton(
            box, text="Auto (first available)", value="auto",
            variable=self.mode, command=self.apply_mode,
        ).grid(row=0, column=0, sticky="w", padx=8, pady=2)
        ttk.Radiobutton(
            box, text="Pick one:", value="manual",
            variable=self.mode, command=self.apply_mode,
        ).grid(row=1, column=0, sticky="w", padx=8, pady=2)
        self.slot_var = tk.StringVar()
        self.combo = ttk.Combobox(
            box, textvariable=self.slot_var, state="readonly", width=34
        )
        self.combo.grid(row=1, column=1, padx=6, pady=2)
        self.combo.bind("<<ComboboxSelected>>", lambda e: self.apply_mode())
        self.using_var = tk.StringVar()
        ttk.Label(box, textvariable=self.using_var).grid(
            row=2, column=0, columnspan=2, sticky="w", padx=8, pady=(2, 6)
        )

        # ---- status checklist
        st = ttk.LabelFrame(root, text="Status")
        st.grid(row=1, column=0, sticky="we", **pad)
        self.dots = {}
        self.texts = {}
        rows = [
            ("pad", "Controller on this PC"),
            ("link", "Link to board (PIO-USB cable)"),
            ("switch", "Switch has detected the pad"),
            ("flow", "Inputs flowing PC -> board"),
        ]
        for i, (k, label) in enumerate(rows):
            c = tk.Canvas(st, width=16, height=16, highlightthickness=0)
            c.grid(row=i, column=0, padx=(8, 4), pady=3)
            self.dots[k] = (c, c.create_oval(2, 2, 14, 14, fill=IDLEC, outline=""))
            ttk.Label(st, text=label, width=30).grid(row=i, column=1, sticky="w")
            self.texts[k] = tk.StringVar(value="...")
            ttk.Label(st, textvariable=self.texts[k], width=34).grid(
                row=i, column=2, sticky="w"
            )
        self.hint = tk.StringVar()
        ttk.Label(
            st, textvariable=self.hint, wraplength=470, foreground="#555555"
        ).grid(row=len(rows), column=0, columnspan=3, sticky="w", padx=8, pady=(4, 6))

        # ---- live preview
        pv = ttk.LabelFrame(root, text="Live input (what is being sent)")
        pv.grid(row=2, column=0, sticky="we", **pad)
        self.cv = tk.Canvas(pv, width=480, height=120, highlightthickness=0)
        self.cv.pack(padx=6, pady=4)
        self.btn_items = {}
        for i, (name, mask) in enumerate(BTN_LABELS):
            x = 10 + (i % 7) * 36
            y = 8 + (i // 7) * 26
            r = self.cv.create_rectangle(x, y, x + 32, y + 22, fill="#e6e6e6", outline="#aaa")
            self.cv.create_text(x + 16, y + 11, text=name, font=("Segoe UI", 8))
            self.btn_items[mask] = r
        self.stick_items = []
        for cx in (330, 420):
            self.cv.create_oval(cx - 30, 10, cx + 30, 70, outline="#aaa")
            self.stick_items.append(self.cv.create_oval(cx - 4, 36, cx + 4, 44, fill="#3366cc", outline=""))
        self.trig = [
            self.cv.create_rectangle(10, 70, 10, 82, fill="#3366cc", outline=""),
            self.cv.create_rectangle(10, 88, 10, 100, fill="#3366cc", outline=""),
        ]
        self.cv.create_text(260, 76, text="ZL", font=("Segoe UI", 8))
        self.cv.create_text(260, 94, text="ZR", font=("Segoe UI", 8))

        # ---- bottom row
        bot = ttk.Frame(root)
        bot.grid(row=3, column=0, sticky="we", **pad)
        self.send_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            bot, text="Send inputs to Switch", variable=self.send_var,
            command=self.apply_mode,
        ).pack(side="left")
        # default ON (Xbox layout -> Switch layout); remembered between runs
        self.swap_var = tk.BooleanVar(
            value=bool(load_settings().get("swap_abxy", True))
        )
        ttk.Checkbutton(
            bot, text="Switch AB/XY", variable=self.swap_var,
            command=self.apply_mode,
        ).pack(side="left", padx=(14, 0))
        self.keep_var = tk.BooleanVar(
            value=bool(load_settings().get("keepalive", True))
        )
        ttk.Checkbutton(
            bot, text="Keep-alive (nudge after 30 s idle)", variable=self.keep_var,
            command=self.apply_mode,
        ).pack(side="left", padx=(14, 0))
        for name, attr in (("CAPTURE", "capture"), ("HOME", "home")):
            b = ttk.Button(bot, text=name, width=10)
            b.pack(side="right", padx=4)
            b.bind("<ButtonPress-1>", lambda e, a=attr: self.hold(a, True))
            b.bind("<ButtonRelease-1>", lambda e, a=attr: self.hold(a, False))

        self.apply_mode()  # push restored settings to the worker
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh_combo([False] * SLOTS)
        self.tick()

    # ---- actions
    def hold(self, attr, val):
        with self.sh.lock:
            setattr(self.sh, attr, val)

    def apply_mode(self):
        with self.sh.lock:
            self.sh.mode_auto = self.mode.get() == "auto"
            idx = self.combo.current()
            self.sh.manual_slot = idx if idx >= 0 else 0
            self.sh.send_enabled = self.send_var.get()
            self.sh.swap_abxy = self.swap_var.get()
            self.sh.keepalive = self.keep_var.get()
        save_settings({"swap_abxy": self.swap_var.get(),
                       "keepalive": self.keep_var.get()})

    def refresh_combo(self, conn):
        cur = self.combo.current()
        vals = [
            f"Controller {i + 1}  -  {'connected' if conn[i] else 'not connected'}"
            for i in range(SLOTS)
        ]
        if list(self.combo["values"]) != vals:
            self.combo["values"] = vals
        self.combo.current(cur if cur >= 0 else 0)

    def dot(self, key, color, text):
        c, item = self.dots[key]
        c.itemconfig(item, fill=color)
        self.texts[key].set(text)

    def close(self):
        # let the worker finish its in-flight transfer and close the device
        # before the process exits (killing it mid-transfer wedges the link)
        self.sh.stop.set()
        self.worker.join(timeout=2.0)
        self.root.destroy()

    # ---- periodic UI refresh
    def tick(self):
        sh = self.sh
        with sh.lock:
            conn = list(sh.connected)
            slot, gp = sh.active_slot, sh.gamepad
            link, stuck = sh.link_open, sh.link_stuck
            rate, sent, failed = sh.rate, sh.sent, sh.failed
            board, age = sh.board, sh.board_age
            pad_on_pc = sh.pad_on_pc
            auto = sh.mode_auto
        self.refresh_combo(conn)

        # controller
        if slot is None:
            self.using_var.set("Using: none" if auto else "Using: (picked controller is not connected)")
            self.dot("pad", BADC, "none found" if auto else "picked one not connected")
        else:
            self.using_var.set(f"Using: Controller {slot + 1}")
            self.dot("pad", OKC, f"Controller {slot + 1}")

        # link
        if not link:
            self.dot("link", BADC, "board not found")
        elif stuck:
            self.dot("link", BADC, f"found but not responding")
        else:
            self.dot("link", OKC, f"connected ({rate:.0f} frames/s)")

        # switch
        if pad_on_pc:
            self.dot("switch", BADC, "pad port is plugged into THIS PC")
        elif not link or stuck or board is None or age > 3.0:
            self.dot("switch", IDLEC, "unknown (no board status)")
        elif board["mounted"] and board["polling"]:
            self.dot("switch", OKC, "yes - console is reading the pad")
        elif board["mounted"] and not board["suspended"]:
            # enumerated and the bus is awake; the Switch sometimes stops
            # polling a static pad, which is normal
            self.dot("switch", OKC, "yes - pad enumerated by the console")
        elif board["mounted"]:
            self.dot("switch", WARNC, "pad found, USB bus suspended (console asleep?)")
        else:
            # normal while the console sleeps (the board keeps re-attaching so it
            # is picked up on wake); otherwise a cable/port problem
            self.dot("switch", WARNC, "not enumerated (console asleep, or check cable)")

        # flow
        if board is not None and age <= 3.0 and link and not stuck:
            self.dot("flow", OKC if board["live"] else WARNC,
                     f"{board['applied']} frames applied" if board["live"] else "board idle (no pad input)")
        else:
            self.dot("flow", IDLEC, "-")

        # hint: the next thing the user should do
        if pad_on_pc:
            h = ("The board's native (programming) cable is in this PC, so the Switch can't "
                 "see the pad. Close this window, unplug both cables, then: native -> DOCK "
                 "first (wait 10 s), PIO -> PC last.")
        elif not link:
            h = "Plug the board's PIO-USB Type-C cable into this PC."
        elif stuck:
            h = ("The link is frozen. Close this window, unplug BOTH board cables, wait 5 s, "
                 "then: native cable -> dock first, PIO cable -> PC last, and reopen this.")
        elif board is not None and age <= 3.0 and not board["mounted"]:
            h = ("The Switch has not detected the pad. If the console is asleep this is normal: "
                 "wake it, wait ~10 s and press a button on your pad. Otherwise: Check the board's native "
                 "(programming) Type-C is in a dock USB port with a data cable, then redo "
                 "the connect order: native -> dock first, PIO -> PC last.")
        elif slot is None:
            h = "Connect a controller to this PC (or have the Parsec guest connect)."
        else:
            h = "All good. Keep this window open while playing."
        self.hint.set(h)

        # preview
        pressed = gp.wButtons if gp is not None else 0
        for mask, item in self.btn_items.items():
            self.cv.itemconfig(item, fill="#7fd68f" if pressed & mask else "#e6e6e6")
        for k, (cx, lx, ly) in enumerate(
            ((330, gp.sThumbLX if gp else 0, gp.sThumbLY if gp else 0),
             (420, gp.sThumbRX if gp else 0, gp.sThumbRY if gp else 0))
        ):
            x, y = cx + lx / 32768 * 26, 40 - ly / 32768 * 26
            self.cv.coords(self.stick_items[k], x - 4, y - 4, x + 4, y + 4)
        for k, v in enumerate((gp.bLeftTrigger if gp else 0, gp.bRightTrigger if gp else 0)):
            y0 = 70 + k * 18
            self.cv.coords(self.trig[k], 10, y0, 10 + v / 255 * 230, y0 + 12)

        self.root.after(80, self.tick)


def main():
    if bridge.XINPUT is None:
        print("no XInput DLL found")
        sys.exit(1)
    setup_logging()
    keep_process_responsive()
    log.info("GUI started")
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
