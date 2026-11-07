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
    import capture_view as cap
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


def load_profiles():
    """-> (profiles dict, active profile name) from settings, normalized:
    always contains "Default"; unknown active name falls back to Default."""
    s = load_settings()
    profiles = s.get("kb_profiles")
    if not isinstance(profiles, dict):
        profiles = {}
    profiles.setdefault("Default", {})  # empty = pure defaults
    active = s.get("kb_profile")
    if active not in profiles:
        active = "Default"
    return profiles, active


def save_profiles(profiles, active):
    save_settings({"kb_profiles": profiles, "kb_profile": active})


def save_settings(d):
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        cur = load_settings()
        cur.update(d)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(cur, f)
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


__version__ = "0.1.6"

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
        )((100, bridge.XINPUT))
    except Exception:
        _getex = None


class _Caps(ctypes.Structure):
    _fields_ = [("Type", ctypes.c_ubyte), ("SubType", ctypes.c_ubyte),
                ("Flags", ctypes.c_uint16), ("Gamepad", bridge.XINPUT_GAMEPAD),
                ("LeftMotor", ctypes.c_uint16), ("RightMotor", ctypes.c_uint16)]


class _CapsEx(ctypes.Structure):
    _fields_ = [("Caps", _Caps), ("vendorId", ctypes.c_uint16),
                ("productId", ctypes.c_uint16), ("revisionId", ctypes.c_uint16),
                ("a4", ctypes.c_uint32)]


_getcapsex = None
if bridge.XINPUT is not None:
    try:  # ordinal 108 = XInputGetCapabilitiesEx (adds the USB vendor/product id)
        _getcapsex = ctypes.WINFUNCTYPE(
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.POINTER(_CapsEx),
        )((108, bridge.XINPUT))
    except Exception:
        _getcapsex = None

# names for ids Windows lists no HID entry for (virtual pads such as Parsec's / ViGEm's)
KNOWN_PADS = {
    (0x045E, 0x028E): "Controller (XBOX 360 For Windows)",
    (0x045E, 0x02FF): "Controller (Xbox One For Windows)",
    (0x045E, 0x02D1): "Controller (Xbox One For Windows)",
    (0x045E, 0x02DD): "Controller (Xbox One For Windows)",
    (0x045E, 0x02E3): "Xbox One Elite Controller",
    (0x045E, 0x0B12): "Xbox Series Controller",
    (0x045E, 0x0B13): "Xbox Series Controller",
}


def slot_ident(slot):
    """-> (vendor_id, product_id) of the pad in an XInput slot, or None."""
    if _getcapsex is None:
        return None
    c = _CapsEx()
    if _getcapsex(1, slot, 0, ctypes.byref(c)) != 0:
        return None
    return c.vendorId, c.productId


def build_labels(connected):
    """One readable line per XInput slot, using the names Windows reports."""
    names = {}
    try:
        for d in hid.enumerate(0, 0):
            if d["product_string"] and (d["vendor_id"], d["product_id"]) not in names:
                names[(d["vendor_id"], d["product_id"])] = d["product_string"]
    except Exception:
        pass
    labels = []
    for s in range(SLOTS):
        if not connected[s]:
            labels.append(f"{s + 1}: (not connected)")
            continue
        ident = slot_ident(s)
        if ident is None:
            labels.append(f"{s + 1}: Xbox-compatible controller")
            continue
        name = names.get(ident) or KNOWN_PADS.get(ident) or "Xbox-compatible controller"
        labels.append(f"{s + 1}: {name}  [{ident[0]:04X}:{ident[1]:04X}]")
    return labels


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
        self.kb_enabled = False  # keyboard/mouse -> gamepad checkbox
        self.kb_state = ""       # latest raw KB line from the capture window
        # outputs
        self.connected = [False] * SLOTS
        self.labels = [f"{i + 1}: (not connected)" for i in range(SLOTS)]
        self.last_active = [0.0] * SLOTS
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


def _refresh_labels(sh, conn):
    labels = build_labels(conn)
    with sh.lock:
        sh.labels = labels


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
    slot_sigs, prev_conn, next_labels = [None] * SLOTS, None, 0.0
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
            asig = None if gp is None else (
                gp.wButtons, gp.bLeftTrigger >> 4, gp.bRightTrigger >> 4,
                gp.sThumbLX >> 11, gp.sThumbLY >> 11, gp.sThumbRX >> 11, gp.sThumbRY >> 11)
            if asig != slot_sigs[s]:
                if slot_sigs[s] is not None and asig is not None:
                    with sh.lock:
                        sh.last_active[s] = t0
                slot_sigs[s] = asig
        if conn != prev_conn or t0 >= next_labels:
            next_labels = t0 + 30.0  # names rarely change; every HID query touches USB devices
            prev_conn = list(conn)
            threading.Thread(target=_refresh_labels, args=(sh, list(conn)), daemon=True).start()
        with sh.lock:
            auto, manual = sh.mode_auto, sh.manual_slot
            enabled, home, cap = sh.send_enabled, sh.home, sh.capture
            swap = sh.swap_abxy
            keepalive = sh.keepalive
            kb_on = sh.kb_enabled
            kb_state = sh.kb_state
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
                use = swap_abxy(gp)  # before the keyboard merge, so held pad buttons stay swapped
            kb_gp, kb_home, kb_cap = None, False, False
            if kb_on:
                kb = bridge.kb_parse(kb_state)
                kb_gp = bridge.kb_merge_full(use, kb)
                kb_home, kb_cap = bridge.kb_home_capture(kb)
            if kb_gp is not None:
                use = kb_gp

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
                use, home=home or kb_home or (guide and enabled), capture=cap or kb_cap
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

        # Is the pad port plugged into this PC by mistake? Only worth asking while the board link
        # is NOT working, and rarely: listing HID devices makes Windows query every USB device,
        # which can upset a capture card that is streaming.
        healthy = dev is not None and t0 - last_ok < 1.0
        if healthy:
            if sh.pad_on_pc:
                with sh.lock:
                    sh.pad_on_pc = False
        elif t0 >= next_enum:
            next_enum = t0 + 5.0
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
        root.title(f"Switch 2 Remote Pad Bridge  v{__version__}")
        root.resizable(False, False)
        self.sh = Shared()
        self.worker = threading.Thread(target=worker, args=(self.sh,), daemon=True)
        self.worker.start()

        pad = dict(padx=10, pady=4)

        # ---- controller picker
        box = ttk.LabelFrame(root, text="Controller to send to the Switch")
        box.grid(row=0, column=0, sticky="we", **pad)
        s0 = load_settings()
        self.mode = tk.StringVar(
            value="manual" if s0.get("picker_mode") == "manual" else "auto")
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
            box, textvariable=self.slot_var, state="readonly", width=58
        )
        self.combo.grid(row=1, column=1, padx=6, pady=2)
        self.combo.bind("<<ComboboxSelected>>", lambda e: self.apply_mode())
        self.using_var = tk.StringVar()
        ttk.Label(box, textvariable=self.using_var).grid(
            row=2, column=0, columnspan=2, sticky="w", padx=8, pady=(2, 2)
        )
        # one box per XInput slot: grey = empty, white = connected, green = input right now
        self.slot_cv = tk.Canvas(box, width=330, height=22, highlightthickness=0)
        self.slot_cv.grid(row=3, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 6))
        self.slot_boxes = []
        for i in range(SLOTS):
            r = self.slot_cv.create_rectangle(i * 82, 1, i * 82 + 78, 21, fill="#e6e6e6", outline="#aaa")
            tx = self.slot_cv.create_text(i * 82 + 39, 11, text=f"{i + 1}", font=("Segoe UI", 8))
            self.slot_boxes.append((r, tx))

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

        # ---- capture card window
        cf = ttk.LabelFrame(root, text="Capture card window (share it in Parsec / Discord)")
        cf.grid(row=3, column=0, sticky="we", **pad)
        s = load_settings()
        self.viewer = cap.Viewer()
        self.video_names = []
        self.cap_video = tk.StringVar(value=s.get("cap_video", ""))
        self.cap_audio = tk.StringVar(value=s.get("cap_audio", ""))
        self.cap_mode = tk.StringVar(value=s.get("cap_mode", cap.DEFAULT_MODE))
        ttk.Label(cf, text="Video:").grid(row=0, column=0, sticky="e", padx=(8, 4), pady=2)
        self.cap_video_cb = ttk.Combobox(cf, textvariable=self.cap_video, width=44, state="readonly")
        self.cap_video_cb.grid(row=0, column=1, columnspan=2, sticky="w", pady=2)
        ttk.Label(cf, text="Audio:").grid(row=1, column=0, sticky="e", padx=(8, 4), pady=2)
        self.cap_audio_cb = ttk.Combobox(cf, textvariable=self.cap_audio, width=44, state="readonly")
        self.cap_audio_cb.grid(row=1, column=1, columnspan=2, sticky="w", pady=2)
        ttk.Label(cf, text="Quality:").grid(row=2, column=0, sticky="e", padx=(8, 4), pady=2)
        cap_mode_cb = ttk.Combobox(
            cf, textvariable=self.cap_mode, width=14, state="readonly",
            values=list(cap.MODES),
        )
        cap_mode_cb.grid(row=2, column=1, sticky="w", pady=2)
        cap_mode_cb.bind("<<ComboboxSelected>>", lambda e: self.save_capture())
        self.cap_btn = ttk.Button(cf, text="Open capture window", width=22, command=self.toggle_capture)
        self.cap_btn.grid(row=2, column=2, sticky="e", padx=8, pady=2)
        ttk.Label(cf, text="Volume:").grid(row=3, column=0, sticky="e", padx=(8, 4), pady=2)
        # slider 0-100%, where 10% = 1x (unchanged) and 100% = 10x; with the card's Windows recording
        # level at 100% the default 10% is right, boost only if the card is still quiet
        self.cap_vol = tk.IntVar(value=int(s.get("cap_vol_pct", 10)))
        self.cap_vol_lbl = tk.StringVar(value=f"{self.cap_vol.get()}%")
        ttk.Scale(cf, from_=0, to=100, orient="horizontal", length=250, variable=self.cap_vol,
                  command=self.on_volume).grid(row=3, column=1, sticky="w", pady=2)
        ttk.Label(cf, textvariable=self.cap_vol_lbl, width=6).grid(row=3, column=2, sticky="w")
        self.cap_status = tk.StringVar(value="Looking for capture devices...")
        ttk.Label(cf, textvariable=self.cap_status, wraplength=470, foreground="#555555").grid(
            row=4, column=0, columnspan=3, sticky="w", padx=8, pady=(2, 6)
        )
        for v in (self.cap_video_cb, self.cap_audio_cb):
            v.bind("<<ComboboxSelected>>", lambda e: self.save_capture())
        threading.Thread(target=self.load_devices, daemon=True).start()

        # ---- bottom row
        bot = ttk.Frame(root)
        bot.grid(row=4, column=0, sticky="we", **pad)
        self.send_var = tk.BooleanVar(
            value=bool(load_settings().get("send_enabled", True)))
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
        self.kb_var = tk.BooleanVar(
            value=bool(load_settings().get("kb_controls", False))
        )
        ttk.Checkbutton(
            bot, text="Keyboard/mouse -> gamepad (capture window)",
            variable=self.kb_var, command=self.apply_mode,
        ).pack(side="left", padx=(14, 0))
        ttk.Button(
            bot, text="Remap keys...", width=12, command=self.open_remap
        ).pack(side="left", padx=(6, 0))
        # profile picker: switching applies the profile's map immediately
        self.profile_var = tk.StringVar()
        self.profile_cb = ttk.Combobox(
            bot, textvariable=self.profile_var, state="readonly", width=12
        )
        self.profile_cb.pack(side="left", padx=(8, 0))
        self.profile_cb.bind("<<ComboboxSelected>>", lambda e: self.on_profile_switch())
        ttk.Button(bot, text="Save as", width=7, command=self.profile_save_as).pack(
            side="left", padx=(4, 0))
        ttk.Button(bot, text="Duplicate", width=8, command=self.profile_duplicate).pack(
            side="left", padx=(4, 0))
        ttk.Button(bot, text="Rename", width=7, command=self.profile_rename).pack(
            side="left", padx=(4, 0))
        ttk.Button(bot, text="Del", width=4, command=self.profile_delete).pack(
            side="left", padx=(4, 0))
        self.kb_summary = tk.StringVar(value="")
        ttk.Label(bot, textvariable=self.kb_summary, foreground="#666666").pack(
            side="left", padx=(8, 0)
        )
        for name, attr in (("CAPTURE", "capture"), ("HOME", "home")):
            b = ttk.Button(bot, text=name, width=10)
            b.pack(side="right", padx=4)
            b.bind("<ButtonPress-1>", lambda e, a=attr: self.hold(a, True))
            b.bind("<ButtonRelease-1>", lambda e, a=attr: self.hold(a, False))

        # restore the picked controller (combo fills later via refresh_combo)
        self.manual_slot_restore = int(load_settings().get("picker_slot", 0))
        self.apply_mode()  # push restored settings to the worker
        self.profile_refresh(keep_current=False)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh_combo([False] * SLOTS)
        self.tick()

    # ---- profiles
    def profile_refresh(self, keep_current=True):
        """Reload the dropdown values from settings; select the active profile."""
        profiles, active = load_profiles()
        names = sorted(profiles.keys(), key=lambda n: (n != "Default", n.lower()))
        self.profile_cb["values"] = names
        if keep_current and self.profile_var.get() in names:
            self.profile_var.set(self.profile_var.get())
        else:
            self.profile_var.set(active)

    def on_profile_switch(self):
        name = self.profile_var.get()
        profiles, _ = load_profiles()
        saved = profiles.get(name)
        bridge.load_kb_map(saved or dict(bridge.DEFAULT_KB_MAP))  # {} = defaults
        # persist choice + mirror into the flat key the worker/consumer reads
        save_profiles(profiles, name)
        save_settings({"kb_map": bridge.get_kb_map()})
        if getattr(self, "remap_win", None) is not None and self.remap_win.winfo_exists():
            self.remap_win.destroy()  # rebuild against the new map
            self.open_remap()
        self.refresh_kb_summary()

    def profile_save_as(self):
        """Snapshot the map currently in effect under a new profile name."""
        name = self._prompt_name("Save profile as")
        if not name:
            return
        profiles, _ = load_profiles()
        profiles[name] = dict(bridge.get_kb_map())
        save_profiles(profiles, name)
        self.profile_refresh(keep_current=False)
        self.profile_var.set(name)

    def _prompt_name(self, title, initial=""):
        """Modal name prompt -> name or None. Enter = OK."""
        win = tk.Toplevel(self.root)
        win.title(title)
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        ttk.Label(win, text="Profile name:").pack(anchor="w", padx=10, pady=(10, 2))
        var = tk.StringVar(value=initial)
        ent = ttk.Entry(win, textvariable=var, width=24)
        ent.pack(padx=10, pady=2)
        ent.selection_range(0, "end")
        ent.focus_set()
        result = []

        def do_ok(_=None):
            name = var.get().strip()
            if name:
                result.append(name)
                win.destroy()

        ent.bind("<Return>", do_ok)
        ttk.Button(win, text="OK", command=do_ok).pack(pady=(4, 10))
        # wait like a modal dialog so the caller gets the answer synchronously
        win.wait_window()
        return result[0] if result else None

    def profile_duplicate(self):
        """Clone the selected profile under a new name (user prompted, default
        name '<selected> copy'), then switch to the clone."""
        src = self.profile_var.get()
        profiles, _ = load_profiles()
        base = f"{src} copy"
        name, i = base, 2
        while name in profiles:
            name, i = f"{src} copy {i}", i + 1
        name = self._prompt_name("Duplicate profile", initial=name)
        if not name or name == src:
            return
        profiles[name] = profiles.get(src) or dict(bridge.DEFAULT_KB_MAP)
        save_profiles(profiles, name)
        self.profile_refresh(keep_current=False)
        self.profile_var.set(name)
        self.on_profile_switch()

    def profile_rename(self):
        """Rename the selected profile in place (stays active if it was)."""
        src = self.profile_var.get()
        if src == "Default":
            return  # Default is the fixed fallback, not renameable
        profiles, _ = load_profiles()
        name = self._prompt_name("Rename profile", initial=src)
        if not name or name == src or name in profiles:
            return
        profiles[name] = profiles.pop(src)
        active = "Default"
        _, active = load_profiles()
        if active == src:
            active = name
        save_profiles(profiles, active)
        self.profile_refresh(keep_current=False)
        self.profile_var.set(name)

    def profile_delete(self):
        name = self.profile_var.get()
        if name == "Default":
            return
        profiles, _ = load_profiles()
        profiles.pop(name, None)
        save_profiles(profiles, "Default")
        self.profile_var.set("Default")
        self.on_profile_switch()

    # ---- remap dialog
    # rows: (function, physical-name-or-None); RESERVED keys can't be bound
    RESERVED = {"m", "f"}  # Esc cancels a binding; Q is bindable (it closes the window only while keyboard control is off)
    FUNC_LABELS = [
        ("A", "A (bottom face)"), ("B", "B (right face)"),
        ("X", "X (top face)"), ("Y", "Y (left face)"),
        ("L", "L (shoulder)"), ("R", "R (shoulder)"),
        ("ZL", "ZL (trigger)"), ("ZR", "ZR (trigger)"),
        ("MINUS", "Minus  -"), ("PLUS", "Plus  +"),
        ("L3", "L3 (stick click)"), ("R3", "R3 (stick click)"),
        ("HOME", "Home"), ("CAPTURE", "Capture"),
        ("LS_UP", "Left stick up"), ("LS_DOWN", "Left stick down"),
        ("LS_LEFT", "Left stick left"), ("LS_RIGHT", "Left stick right"),
        ("UP", "D-pad up"), ("DOWN", "D-pad down"),
        ("LEFT", "D-pad left"), ("RIGHT", "D-pad right"),
        ("MOUSE_MOVE", "Mouse movement -> right stick"),
    ]

    @staticmethod
    def key_label(p):
        if p is None:
            return "(none)"
        return {
            "lmb": "Mouse LMB", "rmb": "Mouse RMB", "mouse_move": "Mouse move",
            "comma": ",", "period": ".", "up": "Arrow Up", "down": "Arrow Down",
            "left": "Arrow Left", "right": "Arrow Right",
            "1": "1", "2": "2",
        }.get(p, p.upper() if len(p) == 1 else p.capitalize())

    def open_remap(self):
        if getattr(self, "remap_win", None) is not None and self.remap_win.winfo_exists():
            self.remap_win.lift()
            return
        win = tk.Toplevel(self.root)
        win.title("Remap keyboard / mouse")
        win.resizable(False, False)
        self.remap_win = win
        self.remap_rows = {}
        ttk.Label(
            win, wraplength=430, justify="left", foreground="#555555",
            text=("Click a binding, then press the new key (or click the mouse "
                  "button you want). Right-click a binding to unbind, Esc to cancel. "
                  "Reserved in the capture window: Esc = close, M = mute, F = fullscreen. "
                  "While keyboard control is on, Q and the arrow keys go to the pad."),
        ).pack(anchor="w", padx=10, pady=(8, 4))

        grid = ttk.Frame(win)
        grid.pack(padx=10, pady=2)
        kmap = bridge.get_kb_map()
        used = {}
        for phys, func in kmap.items():
            if func:
                used.setdefault(func, []).append(phys)
        for i, (func, label) in enumerate(self.FUNC_LABELS):
            r, c = divmod(i, 2)
            ttk.Label(grid, text=label, width=24).grid(row=r, column=c * 2, sticky="w", pady=1)
            phys_list = used.get(func, [])
            phys = phys_list[0] if phys_list else None
            txt = self.key_label(phys)
            b = ttk.Button(grid, text=txt, width=13)
            b.grid(row=r, column=c * 2 + 1, sticky="w", padx=(6, 14), pady=1)
            b.bind("<Button-1>", lambda e, f=func, btn=b: self.remap_listen(f, btn))
            b.bind("<Button-3>", lambda e, f=func, btn=b: self.remap_set(f, None, btn))
            self.remap_rows[func] = (b, phys)
        ttk.Button(win, text="Reset to defaults", command=self.remap_reset).pack(
            anchor="w", padx=10, pady=(6, 4))
        win.transient(self.root)
        win.grab_set()
        self.refresh_kb_summary()

    def remap_listen(self, func, btn):
        btn.config(text="press a key...")
        def key(e):
            # ignore plain modifiers; Esc cancels, reserved keys are refused
            name = self.event_key_name(e)
            self.remap_win.unbind("<Key>") if self.remap_win else None
            self.remap_mouse_unbind()
            if e.keysym == "Escape":
                pass  # cancel
            elif name is None:  # modifier or unsupported key: keep listening
                self.remap_win.bind("<Key>", key)
                return
            elif name in self.RESERVED:
                btn.config(text="reserved!")
                self.remap_win.after(900, lambda: self.remap_refresh_row(func))
                return
            else:
                self.remap_set(func, name, btn, from_event=True)
            self.remap_refresh_row(func)
        def mclick(e):
            self.remap_win.unbind("<Key>")
            self.remap_mouse_unbind()
            name = {1: "lmb", 3: "rmb"}.get(e.num)
            if name:
                self.remap_set(func, name, btn, from_event=True)
            self.remap_refresh_row(func)
        self.remap_win.bind("<Key>", key)
        self.remap_win.bind("<Button-1>", mclick, add="+")
        self.remap_win.bind("<Button-3>", mclick, add="+")
        # swallow the initiating click so it isn't captured as a binding
        self.remap_win.after_idle(lambda: self.remap_win.unbind("<Button-1>"))
        self.remap_win.focus_set()

    def remap_mouse_unbind(self):
        try:
            self.remap_win.unbind("<Button-1>")
            self.remap_win.unbind("<Button-3>")
        except Exception:
            pass

    @staticmethod
    def event_key_name(e):
        if e.keysym in ("Shift_L", "Shift_R", "Control_L", "Control_R",
                        "Alt_L", "Alt_R", "Meta_L", "Meta_R"):
            return None
        special = {"comma": "comma", "period": "period", "Up": "up",
                   "Down": "down", "Left": "left", "Right": "right",
                   "KP_1": "1", "KP_2": "2", "KP_Up": "up", "KP_Down": "down",
                   "KP_Left": "left", "KP_Right": "right"}
        if e.keysym in special:
            return special[e.keysym]
        if len(e.keysym) == 1:
            return e.keysym.lower()
        return None  # unsupported keys (F-keys etc.) are ignored

    def remap_set(self, func, phys, btn, from_event=False):
        bridge.set_binding(phys, func)  # releases conflicts, fires the save hook
        # a physical name can only serve one function; clear any other row that
        # showed it before (dialog-level consistency)
        for f2, (b2, p2) in self.remap_rows.items():
            if f2 != func and p2 == phys:
                self.remap_rows[f2] = (b2, None)
        self.remap_rows[func] = (btn, phys)

    def remap_refresh_row(self, func):
        if not (self.remap_win and self.remap_win.winfo_exists()):
            return
        btn, _ = self.remap_rows[func]
        cur = None
        for p, f in bridge.get_kb_map().items():
            if f == func:
                cur = p
                break
        self.remap_rows[func] = (btn, cur)
        btn.config(text=self.key_label(cur))
        self.refresh_kb_summary()

    def remap_reset(self):
        bridge.load_kb_map(dict(bridge.DEFAULT_KB_MAP))
        if bridge._kb_map_hook:
            bridge._kb_map_hook(bridge.get_kb_map())
        self.remap_win.destroy()
        self.open_remap()  # rebuild all rows from the restored map
        self.refresh_kb_summary()

    def refresh_kb_summary(self):
        kmap = bridge.get_kb_map()
        by_func = {}
        for p, f in kmap.items():
            if f and p not in ("lmb", "rmb", "mouse_move"):
                by_func.setdefault(f, []).append(self.key_label(p))
        order = ["A", "B", "X", "Y", "ZL", "ZR"]
        parts = [f"{f}:{'/'.join(by_func[f])}" for f in order if f in by_func]
        self.kb_summary.set("  ".join(parts))

    # ---- actions
    def hold(self, attr, val):
        with self.sh.lock:
            setattr(self.sh, attr, val)

    def apply_mode(self):
        kb_on = self.kb_var.get()
        with self.sh.lock:
            self.sh.mode_auto = self.mode.get() == "auto"
            idx = self.combo.current()
            self.sh.manual_slot = idx if idx >= 0 else 0
            self.sh.send_enabled = self.send_var.get()
            self.sh.swap_abxy = self.swap_var.get()
            self.sh.keepalive = self.keep_var.get()
            self.sh.kb_enabled = kb_on
            if not kb_on:
                self.sh.kb_state = ""
        save_settings({"swap_abxy": self.swap_var.get(),
                       "keepalive": self.keep_var.get(),
                       "kb_controls": kb_on,
                       "send_enabled": self.send_var.get(),
                       "picker_mode": self.mode.get(),
                       "picker_slot": self.combo.current() if self.combo.current() >= 0 else 0})
        # tell the capture window (if open) to forward or stop forwarding keys
        self.viewer.send_kb_enabled(kb_on)

    def refresh_combo(self, conn, labels=None):
        cur = self.combo.current()
        vals = list(labels) if labels else [f"{i + 1}: (not connected)" for i in range(SLOTS)]
        if list(self.combo["values"]) != vals:
            self.combo["values"] = vals
        if cur < 0:  # first fill: restore the saved picked controller
            cur = min(max(getattr(self, "manual_slot_restore", 0), 0), SLOTS - 1)
        self.combo.current(cur)

    def dot(self, key, color, text):
        c, item = self.dots[key]
        c.itemconfig(item, fill=color)
        self.texts[key].set(text)

    # ---- capture window
    def load_devices(self):
        video, audio = cap.list_devices()
        self.root.after(0, lambda: self.fill_devices(video, audio))

    def fill_devices(self, video, audio):
        self.video_names = video
        self.cap_video_cb["values"] = video
        self.cap_audio_cb["values"] = audio
        if self.cap_video.get() not in video:
            self.cap_video.set(cap.pick_default(video))
        if self.cap_audio.get() not in audio:
            self.cap_audio.set(cap.pick_default(audio))
        if not video:
            self.cap_status.set("No capture device found. Is ffmpeg installed "
                                "(winget install Gyan.FFmpeg) and the card plugged in?")
        else:
            self.cap_status.set("Ready. In the window: f = fullscreen, m = mute, "
                                "wheel/arrows = volume, q = close.")

    def on_volume(self, _=None):
        pct = int(round(self.cap_vol.get()))
        self.cap_vol_lbl.set(f"{pct}%")
        self.viewer.send_volume(pct * 10)  # slider percent -> gain percent (100% = 10x)
        save_settings({"cap_vol_pct": pct})

    def save_capture(self):
        save_settings({"cap_video": self.cap_video.get(),
                       "cap_audio": self.cap_audio.get(),
                       "cap_mode": self.cap_mode.get()})

    def toggle_capture(self):
        if self.viewer.running():
            self.viewer.stop()
            return
        self.save_capture()
        if not self.viewer.start(self.video_names, self.cap_video.get(),
                                 self.cap_audio.get(), self.cap_mode.get(),
                                 volume=int(round(self.cap_vol.get())) * 10):
            self.cap_status.set("Could not start: " + self.viewer.error)
        else:
            self.viewer.send_kb_enabled(self.kb_var.get())

    def update_capture(self):
        state, msg = self.viewer.state()
        if state == "running":
            self.cap_btn.config(text="Close capture window")
            lvl = self.viewer.level
            hint = ("" if lvl is None or lvl >= 60 else
                    f"  Windows recording level of this card is only {lvl}%: set it to 100% in "
                    "Settings > System > Sound > Input for a cleaner, louder sound.")
            self.cap_status.set(
                "Capture window is open (" + (msg or "starting...") + "). "
                "In it: f = fullscreen, m = mute, wheel/arrows = volume, q = close." + hint)
        else:
            self.cap_btn.config(text="Open capture window")
            if state == "failed":
                self.cap_status.set("Capture stopped: " + msg)

    def close(self):
        self.viewer.stop()
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
            labels = list(sh.labels)
            active = list(sh.last_active)
        self.refresh_combo(conn, labels)
        now_t = time.time()
        for i, (r, tx) in enumerate(self.slot_boxes):
            fill = "#7fd68f" if conn[i] and now_t - active[i] < 1.5 else (
                "#ffffff" if conn[i] else "#e6e6e6")
            self.slot_cv.itemconfig(r, fill=fill)

        # controller
        if slot is None:
            self.using_var.set("Using: none" if auto else "Using: (picked controller is not connected)")
            self.dot("pad", BADC, "none found" if auto else "picked one not connected")
        else:
            nm = labels[slot].split(": ", 1)[1] if ": " in labels[slot] else labels[slot]
            self.using_var.set(f"Using: {nm}  (slot {slot + 1})")
            self.dot("pad", OKC, nm.split("  [")[0][:34])

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
            h = ("The link is not responding (it goes idle when nothing talks to it). Unplug ONLY the "
                 "PIO cable (the one to this PC), wait 3 s, plug it back in; this window reconnects by "
                 "itself. If that does not help, unplug both cables and redo the connect order.")
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

        self.update_capture()
        with sh.lock:
            sh.kb_state = (self.viewer.kb_state
                           if self.kb_var.get() and self.viewer.running() else "")
        self.root.after(80, self.tick)


def self_check():
    """Smoke test for packaged builds (no board / capture card needed). The result goes to
    check.txt next to gui.log because a windowed exe has no console."""
    path = os.path.join(os.path.dirname(SETTINGS_PATH), "check.txt")
    try:
        import sounddevice as sd
        import cv2
        import pygame
        from pygame._sdl2 import video as sv  # noqa: F401
        root = tk.Tk()
        root.withdraw()
        root.destroy()
        hid.enumerate(0, 0)
        sd.query_devices()
        msg = (f"OK v{__version__}: tkinter, hidapi, opencv {cv2.__version__}, "
               f"pygame-ce {pygame.version.ver}, sounddevice, xinput "
               f"{'yes' if bridge.XINPUT is not None else 'NO'}")
        code = 0
    except Exception as e:  # report instead of a blocking crash dialog
        msg, code = f"FAILED: {type(e).__name__}: {e}", 1
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(msg + "\n")
    except OSError:
        pass
    print(msg)
    return code


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--capture-view":
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        sys.exit(cap.main())
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        sys.exit(self_check())
    if bridge.XINPUT is None:
        print("no XInput DLL found")
        sys.exit(1)
    setup_logging()
    keep_process_responsive()
    profiles, active = load_profiles()
    # a profile stored as {} means "pure defaults"; otherwise its saved map
    bridge.load_kb_map(profiles[active] or dict(bridge.DEFAULT_KB_MAP))

    def on_map_changed(m):
        """Persistence for binding edits: update the flat key AND the profile
        the edit happened under."""
        save_settings({"kb_map": m})
        profiles, active2 = load_profiles()
        profiles[active2] = dict(m)
        save_profiles(profiles, active2)

    bridge.set_kb_map_hook(on_map_changed)
    log.info("GUI started")
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
