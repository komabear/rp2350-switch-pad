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
