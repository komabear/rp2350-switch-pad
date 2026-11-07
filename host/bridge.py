#!/usr/bin/env py
"""
bridge.py - XInput -> pad-frame sender for mayflash_switch2_controller.

Reads controller 0 with XInputGetState, maps it to the neutral Nintendo-layout
protocol (see fw/proto.h) and streams 11-byte frames to the bridge device's
CDC serial port at ~250 Hz. Prints the device's $S status flags.

Usage:
  py bridge.py --list
  py bridge.py --port COM5 [--pad 0]
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import struct
import sys
import threading
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("pyserial missing: py -m pip install pyserial")
    sys.exit(1)

# ---------------------------------------------------------------- XInput ---
XINPUT_GAMEPAD_DPAD_UP = 0x0001
XINPUT_GAMEPAD_DPAD_DOWN = 0x0002
XINPUT_GAMEPAD_DPAD_LEFT = 0x0004
XINPUT_GAMEPAD_DPAD_RIGHT = 0x0008
XINPUT_GAMEPAD_START = 0x0010
XINPUT_GAMEPAD_BACK = 0x0020
XINPUT_GAMEPAD_LEFT_THUMB = 0x0040
XINPUT_GAMEPAD_RIGHT_THUMB = 0x0080
XINPUT_GAMEPAD_LEFT_SHOULDER = 0x0100
XINPUT_GAMEPAD_RIGHT_SHOULDER = 0x0200
XINPUT_GAMEPAD_A = 0x1000
XINPUT_GAMEPAD_B = 0x2000
XINPUT_GAMEPAD_X = 0x4000
XINPUT_GAMEPAD_Y = 0x8000
XINPUT_TRIGGER_THRESHOLD = 30
ERROR_DEVICE_NOT_CONNECTED = 1167


class XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [
        ("wButtons", wt.USHORT),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class XINPUT_STATE(ctypes.Structure):
    _fields_ = [("dwPacketNumber", wt.DWORD), ("Gamepad", XINPUT_GAMEPAD)]


def _load_xinput():
    for dll in ("xinput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll"):
        try:
            xinput = ctypes.WinDLL(dll)
            xinput.XInputGetState.argtypes = [wt.DWORD, ctypes.POINTER(XINPUT_STATE)]
            xinput.XInputGetState.restype = wt.DWORD
            return xinput
        except OSError:
            continue
    return None


XINPUT = _load_xinput()

# ------------------------------------------------------------- protocol ---
PF_Y = 0x01
PF_B = 0x02
PF_A = 0x04
PF_X = 0x08
PF_L = 0x10
PF_R = 0x20
PF_ZL = 0x40
PF_ZR = 0x80
PF_MINUS = 0x01
PF_PLUS = 0x02
PF_L3 = 0x04
PF_R3 = 0x08
PF_HOME = 0x10
PF_CAPTURE = 0x20
HAT_CENTER = 8

# XInput physical button -> Nintendo-layout semantics
XIN_TO_FLAGS1 = [
    (XINPUT_GAMEPAD_Y, PF_Y),
    (XINPUT_GAMEPAD_B, PF_B),
    (XINPUT_GAMEPAD_A, PF_A),
    (XINPUT_GAMEPAD_X, PF_X),
    (XINPUT_GAMEPAD_LEFT_SHOULDER, PF_L),
    (XINPUT_GAMEPAD_RIGHT_SHOULDER, PF_R),
]
XIN_TO_FLAGS2 = [
    (XINPUT_GAMEPAD_BACK, PF_MINUS),
    (XINPUT_GAMEPAD_START, PF_PLUS),
    (XINPUT_GAMEPAD_LEFT_THUMB, PF_L3),
    (XINPUT_GAMEPAD_RIGHT_THUMB, PF_R3),
]
DPAD_TO_HAT = [
    (XINPUT_GAMEPAD_DPAD_UP, 0),
    (XINPUT_GAMEPAD_DPAD_UP | XINPUT_GAMEPAD_DPAD_RIGHT, 1),
    (XINPUT_GAMEPAD_DPAD_RIGHT, 2),
    (XINPUT_GAMEPAD_DPAD_DOWN | XINPUT_GAMEPAD_DPAD_RIGHT, 3),
    (XINPUT_GAMEPAD_DPAD_DOWN, 4),
    (XINPUT_GAMEPAD_DPAD_DOWN | XINPUT_GAMEPAD_DPAD_LEFT, 5),
    (XINPUT_GAMEPAD_DPAD_LEFT, 6),
    (XINPUT_GAMEPAD_DPAD_UP | XINPUT_GAMEPAD_DPAD_LEFT, 7),
]


def scale_axis(v):
    """XInput -32768..32767 -> -127..127 as unsigned byte"""
    q = int(round(v / 257.0))
    if q < -127:
        q = -127
    elif q > 127:
        q = 127
    return q & 0xFF


def build_frame(gp, home=False, capture=False):
    f1 = 0
    for xin_bit, pf in XIN_TO_FLAGS1:
        if gp.wButtons & xin_bit:
            f1 |= pf
    if gp.bLeftTrigger >= XINPUT_TRIGGER_THRESHOLD:
        f1 |= PF_ZL
    if gp.bRightTrigger >= XINPUT_TRIGGER_THRESHOLD:
        f1 |= PF_ZR

    f2 = 0
    for xin_bit, pf in XIN_TO_FLAGS2:
        if gp.wButtons & xin_bit:
            f2 |= pf
    if home:
        f2 |= PF_HOME
    if capture:
        f2 |= PF_CAPTURE

    dpad = gp.wButtons & 0x000F
    hat = HAT_CENTER
    for bits, h in DPAD_TO_HAT:
        if dpad == bits:
            hat = h
            break

    frame = bytearray(
        struct.pack(
            "<2s8B",
            b"$P",
            f1,
            f2,
            scale_axis(gp.sThumbLX),
            scale_axis(gp.sThumbLY),
            scale_axis(gp.sThumbRX),
            scale_axis(gp.sThumbRY),
            hat,
            0,
        )
    )
    xor = 0
    for b in frame[2:10]:
        xor ^= b
    frame.append(xor)
    return bytes(frame)


NEUTRAL_GAMEPAD = XINPUT_GAMEPAD(0, 0, 0, 0, 0, 0, 0)


# ---------------------------------------------------- keyboard -> gamepad ---
# Physical layout mirrors a controller grip: right hand on I/J/K/L (the face
# button diamond), left hand on WASD + Q/E + 1/2. While keyboard control is on,
# the capture window gives Q and the arrow keys to the pad (Esc still closes it).
#
# Bindings are configurable: physical name -> function name (see FUNC_TO_*).
# DEFAULT_KB_MAP mirrors the original hardcoded tables; the GUI can override
# individual bindings at runtime (get_kb_map / set_binding).

# function name -> XInput button bit (d-pad bits are handled separately)
FUNC_TO_XINPUT = {
    "Y": XINPUT_GAMEPAD_Y,
    "B": XINPUT_GAMEPAD_B,
    "A": XINPUT_GAMEPAD_A,
    "X": XINPUT_GAMEPAD_X,
    "L": XINPUT_GAMEPAD_LEFT_SHOULDER,
    "R": XINPUT_GAMEPAD_RIGHT_SHOULDER,
    "MINUS": XINPUT_GAMEPAD_BACK,
    "PLUS": XINPUT_GAMEPAD_START,
    "L3": XINPUT_GAMEPAD_LEFT_THUMB,
    "R3": XINPUT_GAMEPAD_RIGHT_THUMB,
}
FUNC_TO_DPAD = {
    "UP": XINPUT_GAMEPAD_DPAD_UP,
    "DOWN": XINPUT_GAMEPAD_DPAD_DOWN,
    "LEFT": XINPUT_GAMEPAD_DPAD_LEFT,
    "RIGHT": XINPUT_GAMEPAD_DPAD_RIGHT,
}
FUNC_TO_STICK = {
    "LS_UP": (0, 1), "LS_DOWN": (0, -1), "LS_LEFT": (-1, 0), "LS_RIGHT": (1, 0),
}
FUNC_TO_TRIGGER = {"ZL": "bLeftTrigger", "ZR": "bRightTrigger"}
# functions handled outside the button merge (build_frame args)
FUNC_SPECIAL = {"HOME", "CAPTURE", "MOUSE_MOVE"}

ALL_FUNCS = sorted(
    list(FUNC_TO_XINPUT) + list(FUNC_TO_DPAD) + list(FUNC_TO_STICK)
    + list(FUNC_TO_TRIGGER) + ["HOME", "CAPTURE", "(none)"]
)

DEFAULT_KB_MAP = {
    "i": "Y", "j": "X", "k": "A", "l": "B",
    "u": "ZL", "o": "ZR", "q": "L", "e": "R",
    "1": "MINUS", "2": "PLUS", "comma": "L3", "period": "R3",
    "h": "HOME", "g": "CAPTURE",
    "w": "LS_UP", "a": "LS_LEFT", "s": "LS_DOWN", "d": "LS_RIGHT",
    "up": "UP", "down": "DOWN", "left": "LEFT", "right": "RIGHT",
    "lmb": "A", "rmb": "B",
}

_kb_map = None  # None = use DEFAULT_KB_MAP


def get_kb_map():
    """-> the active physical-name -> function-name map (defaults + overrides)."""
    return dict(DEFAULT_KB_MAP if _kb_map is None else _kb_map)


def set_binding(physical, func):
    """Bind a physical key/mouse name to a function (or None = unbind).
    Enforces one function per binding: another physical name already bound to
    `func` is moved to (none). Saves to settings via the gui save_settings hook
    if installed (set_kb_map_hook)."""
    global _kb_map
    if _kb_map is None:
        _kb_map = dict(DEFAULT_KB_MAP)
    if func is None or func == "(none)":
        _kb_map[physical] = None
    else:
        for p, f in list(_kb_map.items()):
            if f == func and p != physical:
                _kb_map[p] = None  # conflict: old binding released
        _kb_map[physical] = func
    if _kb_map_hook is not None:
        try:
            _kb_map_hook(_kb_map)
        except Exception:
            pass


_kb_map_hook = None


def set_kb_map_hook(hook):
    """Install callable(map) invoked after every set_binding (GUI persistence)."""
    global _kb_map_hook
    _kb_map_hook = hook


def load_kb_map(saved):
    """Replace the active map with a dict from settings (unknown physical
    names / functions are ignored, missing ones keep defaults)."""
    global _kb_map
    merged = dict(DEFAULT_KB_MAP)
    known_physical = set(DEFAULT_KB_MAP) | {"lmb", "rmb", "mouse_move"}

    def _known_physical(p):
        # fixed vocabulary + any plausible key name (single char, or a short
        # lowercase token like "comma"/"up"): user bindings may use keys that
        # are not in the shipped defaults
        return p in known_physical or (
            isinstance(p, str) and p and len(p) <= 8
            and (len(p) == 1 or p.isalpha()))

    if isinstance(saved, dict):
        for p, f in saved.items():
            if not _known_physical(p):
                continue  # typo or stale entry: drop
            if f is None or f == "(none)":
                merged[p] = None
            elif f in FUNC_TO_XINPUT or f in FUNC_TO_DPAD or f in FUNC_TO_STICK \
                    or f in FUNC_TO_TRIGGER or f in FUNC_SPECIAL:
                merged[p] = f
    _kb_map = merged


def kb_parse(state_str):
    """Parse the viewer's KB state line "keys|rx,ry|lmb,rmb" ->
    dict with keys (set), rx, ry (floats -1..1), lmb, rmb (bool), or None."""
    if not state_str:
        return None
    try:
        keys_part, stick_part, mouse_part = state_str.split("|")
        keys = set(keys_part.split()) if keys_part else set()
        rx_s, ry_s = stick_part.split(",")
        lmb_s, rmb_s = mouse_part.split(",")
        return {"keys": keys, "rx": float(rx_s), "ry": float(ry_s),
                "lmb": lmb_s == "1", "rmb": rmb_s == "1"}
    except (ValueError, IndexError):
        return None


def kb_merge_full(gp, kb):
    """Merge parsed KB state (kb_parse result) into gp -> NEW XINPUT_GAMEPAD
    or None if nothing contributes. Uses the active binding map."""
    if kb is None:
        return None
    kmap = get_kb_map()
    keys = set(kb["keys"])
    if kb["lmb"]:
        keys.add("lmb")
    if kb["rmb"]:
        keys.add("rmb")
    if not keys:
        return None

    out = XINPUT_GAMEPAD()
    ctypes.pointer(out)[0] = gp  # struct copy
    changed = False
    buttons = out.wButtons
    dpad = 0
    lx = ly = 0
    for k in keys:
        func = kmap.get(k)
        if not func or func == "(none)":
            continue
        bit = FUNC_TO_XINPUT.get(func)
        if bit:
            buttons |= bit
            changed = True
            continue
        tattr = FUNC_TO_TRIGGER.get(func)
        if tattr:
            setattr(out, tattr, 255)
            changed = True
            continue
        if func in FUNC_TO_DPAD:
            dpad |= FUNC_TO_DPAD[func]
            changed = True
            continue
        if func in FUNC_TO_STICK:
            dx, dy = FUNC_TO_STICK[func]
            lx += dx
            ly += dy
            changed = True

    if dpad:
        buttons = (buttons & ~0x000F) | dpad
    out.wButtons = buttons
    if lx or ly:
        out.sThumbLX = max(-32767, min(32767, lx * 32767))
        out.sThumbLY = max(-32767, min(32767, ly * 32767))

    # mouse movement -> right stick (only while the mouse_move binding stays
    # on its default; if rebound away, the stick is left alone)
    if kmap.get("mouse_move", "MOUSE_MOVE") == "MOUSE_MOVE":
        if abs(kb["rx"]) > 0.001 or abs(kb["ry"]) > 0.001:
            out.sThumbRX = max(-32767, min(32767, int(kb["rx"] * 32767)))
            out.sThumbRY = max(-32767, min(32767, int(kb["ry"] * 32767)))
            changed = True

    return out if changed else None


def kb_home_capture(kb):
    """-> (home, capture): whether a key or mouse button bound to HOME / CAPTURE is held.
    They are not XInput buttons, so kb_merge_full cannot carry them; the caller passes them
    to build_frame."""
    if kb is None:
        return False, False
    kmap = get_kb_map()
    held = set(kb["keys"]) | {b for b in ("lmb", "rmb") if kb[b]}
    funcs = {kmap.get(k) for k in held}
    return "HOME" in funcs, "CAPTURE" in funcs


# legacy entry points kept so nothing else breaks (GUI no longer uses these)
def kb_merge(gp, keys):
    kb = {"keys": keys, "rx": 0.0, "ry": 0.0, "lmb": False, "rmb": False}
    return kb_merge_full(gp, kb)


# ------------------------------------------------------------ main loop ---
class Status:
    """thread-safe view of the device's $S flags"""

    def __init__(self):
        self.lock = threading.Lock()
        self.flags = 0
        self.last_rx = 0.0

    def update(self, flags):
        with self.lock:
            self.flags = flags
            self.last_rx = time.time()

    def snapshot(self):
        with self.lock:
            return self.flags, self.last_rx


def reader_thread(ser, status, stop):
    buf = b""
    while not stop.is_set():
        try:
            chunk = ser.read(64)
        except Exception:
            break
        if chunk:
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.decode("ascii", "replace").strip()
                if line.startswith("$S"):
                    # "$S <flags> [D[...] Fnn]" - flags is the 2nd token
                    parts = line.split()
                    try:
                        status.update(int(parts[1]))
                    except (ValueError, IndexError):
                        pass


def run(port, pad_index, status=None, stop=None, home_cb=None, capture_cb=None):
    """Stream frames until stop is set. Returns frames sent."""
    if XINPUT is None:
        raise RuntimeError("no XInput DLL found")
    status = status or Status()
    stop = stop or threading.Event()

    ser = serial.Serial(port, 115200, timeout=0.05)
    rt = threading.Thread(target=reader_thread, args=(ser, status, stop), daemon=True)
    rt.start()

    state = XINPUT_STATE()
    frame_count = 0
    next_frame = time.time()
    last_report = 0.0

    try:
        while not stop.is_set():
            now = time.time()
            if now >= next_frame:
                next_frame += 0.004

                home = bool(home_cb()) if home_cb else False
                capture = bool(capture_cb()) if capture_cb else False
                rc = XINPUT.XInputGetState(pad_index, ctypes.byref(state))

                if rc == 0:
                    gp = state.Gamepad
                    frame = build_frame(gp, home=home, capture=capture)
                elif home or capture:
                    frame = build_frame(NEUTRAL_GAMEPAD, home=home, capture=capture)
                else:
                    # no pad connected: idle, device auto-releases after 400 ms
                    time.sleep(0.05)
                    continue

                ser.write(frame)
                frame_count += 1
            else:
                time.sleep(0.0005)

            if now - last_report >= 2.0:
                last_report = now
                flags, _ = status.snapshot()
                print(
                    f"\rdevice flags: {flags:03b} (xinput, pio, frames)"
                    f"   frames sent: {frame_count}   ",
                    end="",
                    flush=True,
                )
    finally:
        stop.set()
        ser.close()
    return frame_count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="list serial ports")
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--pad", type=int, default=0)
    args = ap.parse_args()

    if args.list:
        for p in list_ports.comports():
            print(f"{p.device}: {p.description}")
        return

    print(f"opening {args.port} ... Ctrl+C to stop")
    stop = threading.Event()
    status = Status()
    try:
        run(args.port, args.pad, status=status, stop=stop)
    except KeyboardInterrupt:
        stop.set()
    except serial.SerialException as e:
        print(f"\nserial error: {e}")
        print("run with --list to see ports; close any serial monitor first")
        sys.exit(1)


if __name__ == "__main__":
    main()
