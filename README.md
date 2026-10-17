# rp2350-switch-pad

Play your Nintendo Switch 2 remotely with friends over [Parsec](https://parsec.app): a
Waveshare **RP2350-USB-C** board pretends to be a wired PowerA Switch controller, and a PC tool
streams a gamepad to it.

```
Friend's pad ──Parsec──▶ your PC (Xbox 360 virtual pad)
                              │  host/gui_hid.py  (XInput → frames)
                              │  USB HID feature reports
                              ▼
                  board PIO-USB Type-C ═══ RP2350-USB-C ═══ board native Type-C
                                                                   │  wired "PowerA" pad
                                                                   ▼
                                              Switch 2 dock USB port ──▶ capture card ──▶ PC (Parsec)
```

The Switch sees a normal licensed wired controller (PowerA "BDA NSW wired controller",
`20D6:A713`), so no adapter is needed.

## Why this design

The first design ran the Switch-facing pad on the board's *PIO-USB* port (bit-banged USB) and
fed it through a Mayflash Magic-NS. Neither the Mayflash nor the dock ever enumerated it, although
a PC did. Putting the pad on the **native** hardware USB port (TinyUSB) fixed it immediately. The
PC link then uses the PIO-USB port, which works fine towards a PC.

## Hardware

- Waveshare RP2350-USB-C (two Type-C ports: native/programming and PIO-USB).
- Two **data-capable** USB cables (some charge-only cables fail silently).
- Switch 2 dock, capture card, a PC with Windows.

## Daily routine (follow the order — the PIO-USB link is fragile)

Connect:

1. Unplug everything from the board.
2. Plug the board's **native** Type-C (the programming port) into a **dock USB port**. Wait ~10 s;
   the Switch sees a wired pad.
3. Plug the board's **PIO-USB** Type-C into the **PC**.
4. Right away run `py host/gui_hid.py`. All rows should turn green. Keep it open while playing.

Disconnect:

1. Close the GUI with its **X** button (don't kill it).
2. Unplug the **PIO** cable, then the **native** cable.

Never plug the PIO cable in before the native cable or leave it plugged into an idle board: the
whole board (both ports) can freeze. Only unplugging **both** cables recovers it. If the GUI says
"found but not responding", unplug both, wait 5 s, and redo the connect steps.

## The GUI (`host/gui_hid.py`)

- Pick a controller (Controller 1–4) or **Auto** (first connected one).
- Status lights: controller on the PC, link to the board, *Switch is reading the pad*, inputs flowing.
- **Switch AB/XY** (default on, remembered): swaps A↔B and X↔Y so an Xbox pad's physical layout
  matches a Switch pad. Settings live in `%APPDATA%\mayflash_switch2_controller\gui_settings.json`.
- HOME / CAPTURE are press-and-hold buttons. The Xbox Guide button maps to HOME when Windows
  exposes it.

Headless alternative: `py host/bridge_hid.py` (or `--demo` to cycle the d-pad without a gamepad).

## Setup

```
py -m pip install -r host/requirements.txt     # hidapi, pyserial
```

### Build the firmware

Needs `arm-none-eabi-gcc`, CMake, Ninja and the [pico-sdk](https://github.com/raspberrypi/pico-sdk)
**2.2.0** with submodules.

```
git clone --branch 2.2.0 --recurse-submodules https://github.com/raspberrypi/pico-sdk third_party/pico-sdk
cmake -S fw -B fw/build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build fw/build
```

(or set `PICO_SDK_PATH`). Options: `-DSELFTEST=ON` makes the board cycle the d-pad by itself, so you
can test it on the dock without a PC; `-DDEFAULT_IDENTITY=n` picks the pad identity (3 = PowerA).

### Flash

Plug the **native** port into the PC, hold **BOOT**, tap **RST**, copy `fw/build/bridge.uf2` to the
`RP2350` drive. Unplug the board afterwards and follow the daily routine.

## Troubleshooting / diagnostics

- `py tools/link_test.py`: writes frames to the PIO link and reports successes/failures
  (note: `hidapi` returns `-1` on failure instead of raising).
- `py tools/board_diag.py`: reads crash registers, core-1 heartbeat and frame counters from the
  board. Works only while the board's **native** port is on this PC.
- Switch row red: check the native cable (data-capable), that it is in a dock port, then redo the
  connect order.
- A PC test without the Switch: native port into the PC, then `py tools/board_diag.py` and joy.cpl
  ("NSW wired controller").

## Status and limits

- Verified: Xbox pad → PC → board → Switch 2 dock, buttons, sticks, AB/XY swap, console wake/sleep
  detection, repeated connect/disconnect cycles using the routine above.
- Not yet tested: a real Parsec guest pad, multi-hour sessions, more than one PC controller.
- The freeze with the PIO cable is understood only by its symptoms (not Windows USB suspend, not a
  firmware crash, not power back-feed). A USB-to-serial adapter on the board's UART pins would be a
  more robust PC link than PIO-USB; the firmware's frame parser is already link-agnostic.

## Layout

```
fw/        firmware (main.c, usb_descriptors.c, pad maps, CMakeLists.txt)
fw/pio_usb vendored + patched PIO-USB (see fw/pio_usb/README.md for origin and licenses)
host/      bridge.py (XInput → frames), bridge_hid.py (link sender), gui_hid.py (GUI)
tools/     link_test.py, board_diag.py
```

`fw/` still contains the DualShock-4 / DInput / HORI identities from the abandoned Mayflash
design; they are compiled in but unused (`DEFAULT_IDENTITY=3`).

## Credits

PIO-USB: [Pico-PIO-USB](https://github.com/sekigon-gonnoc/Pico-PIO-USB) (sekigon-gonnoc, MIT) via
Waveshare's RP2350-USB-C examples (Apache-2.0). Built on the Raspberry Pi pico-sdk and TinyUSB.
