#!/usr/bin/env py
"""
bridge_hid.py - XInput -> pad frames over the board's PIO-USB "Pad Bridge Link".

New architecture: the board's native Type-C goes to the Switch dock (a real
hardware-USB PowerA pad), and its PIO-USB Type-C goes to this PC. Frames are
sent as 64-byte HID feature reports (SET_REPORT) to VID 2E8A / PID 400B.

  py bridge_hid.py            # stream XInput pad 0
  py bridge_hid.py --demo     # no pad needed: cycle d-pad + A for a link test
"""
import argparse
import ctypes
import sys
import time

import hid  # pip install hidapi

import bridge  # build_frame, XINPUT, XINPUT_STATE, NEUTRAL_GAMEPAD

VID, PID = 0x2E8A, 0x400B


def to_report(frame, seq):
    """11-byte '$P' frame -> 65-byte feature report (id 0 + 64 bytes)."""
    p = bytearray(frame[2:])          # 9 bytes: 8 payload + xor
    p[7] = seq & 0xFF                 # sequence byte so the board can dedupe
    x = 0
    for b in p[:8]:
        x ^= b
    p[8] = x
    return bytes([0]) + bytes(p) + bytes(55)


def open_link():
    d = hid.device()
    d.open(VID, PID)
    return d


def demo_frames():
    hats = [2, 4, 6, 0]
    step = 0
    while True:
        gp = bridge.XINPUT_GAMEPAD(0, 0, 0, 0, 0, 0, 0)
        if step < 4:
            f = bytearray(bridge.build_frame(gp))
            f[8] = hats[step]
        else:
            gp.wButtons = bridge.XINPUT_GAMEPAD_A
            f = bytearray(bridge.build_frame(gp))
        yield bytes(f)
        step = (step + 1) % 5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pad", type=int, default=0)
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--hz", type=int, default=125)
    args = ap.parse_args()

    try:
        dev = open_link()
    except OSError as e:
        print(f"cannot open link device {VID:04X}:{PID:04X}: {e}")
        print("is the PIO-USB cable plugged into the PC?")
        sys.exit(1)
    print("link open. Ctrl+C to stop")

    seq = 0
    period = 1.0 / args.hz
    state = bridge.XINPUT_STATE()
    demo = demo_frames() if args.demo else None
    demo_frame = None
    demo_t = 0.0
    sent = errors = 0
    last_print = 0.0
    try:
        while True:
            t0 = time.time()
            if demo:
                if t0 - demo_t >= 1.0:
                    demo_t = t0
                    demo_frame = next(demo)
                frame = demo_frame
            else:
                rc = bridge.XINPUT.XInputGetState(args.pad, ctypes.byref(state))
                # always stream (neutral if no pad): constant traffic keeps
                # Windows from selective-suspending the PIO-USB link
                frame = bridge.build_frame(
                    state.Gamepad if rc == 0 else bridge.NEUTRAL_GAMEPAD)
            if frame:
                seq = (seq + 1) & 0xFF
                # hidapi returns -1 on failure instead of raising
                if dev.send_feature_report(to_report(frame, seq)) >= 0:
                    sent += 1
                else:
                    errors += 1
            if t0 - last_print >= 2.0:
                last_print = t0
                print(f"\rsent {sent}  errors {errors}   ", end="", flush=True)
            dt = period - (time.time() - t0)
            if dt > 0:
                time.sleep(dt)
    except KeyboardInterrupt:
        pass
    finally:
        dev.close()


if __name__ == "__main__":
    main()
