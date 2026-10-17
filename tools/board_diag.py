"""Read the board's diagnostics through the pad's feature report.

Only works while the board's NATIVE port is connected to this PC (not the dock).
  py tools/board_diag.py
"""
import struct

import hid

NAMES = ("magic boots cfsr pc now_ms core1_alive_ms frames_applied pio_configured "
         "prev_valid prev_c0_loops prev_c1_loops prev_c0_ms prev_c1_ms prev_stages").split()

d = hid.device()
d.open(0x20D6, 0xA713)
r = d.get_feature_report(0, 65)
v = struct.unpack("<14I", bytes(r[1:57]))
for n, x in zip(NAMES, v):
    print(f"{n:16s}{x:#010x} ({x})")
