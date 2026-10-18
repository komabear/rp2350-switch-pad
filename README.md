# rp2350-switch-pad

**Version 0.1.4** - see [CHANGELOG.md](CHANGELOG.md).

Play your Nintendo Switch 2 remotely with friends over [Parsec](https://parsec.app). A Waveshare
**RP2350-USB-C** board pretends to be a wired PowerA Switch controller, and a PC tool streams a
gamepad to it. A built-in capture window shows the Switch's screen (video and audio) from an HDMI
capture card, ready to share. **Tested end to end with Parsec guests.**

```
Friend's pad ──Parsec──▶ your PC (Xbox 360 virtual pad)
                              │  host/gui_hid.py  (XInput → frames)
                              │  USB HID feature reports
                              ▼
                  board PIO-USB Type-C ═══ RP2350-USB-C ═══ board native Type-C
                                                                   │  wired "PowerA" pad
                                                                   ▼
Switch 2 dock USB port ◀───────────────────────────────────────────┘
        │ HDMI
        ▼
HDMI capture card ──USB──▶ your PC ──▶ "Switch Capture" window ──Parsec / Discord / OBS──▶ friends
```

The Switch sees a normal licensed wired controller (PowerA "BDA NSW wired controller",
`20D6:A713`), so no adapter is needed.

## Contents

- [Download](#download-no-python-needed) · [Hardware](#hardware) · [Daily routine](#daily-routine)
- [The GUI](#the-gui) · [Capture window](#capture-window-video--audio)
- [Troubleshooting](#troubleshooting) · [Build and flash](#build-and-flash) · [Status and limits](#status-and-limits)

## Download (no Python needed)

The GitHub releases page has `rp2350-switch-pad-v<version>-windows.zip`: the GUI as a Windows exe
(unzip and run `rp2350-switch-pad.exe`; Windows SmartScreen may warn because it is unsigned) plus
`bridge.uf2`, the board firmware, the README and the changelog. Flash the UF2 once (see
[Flash](#flash)), then follow the [daily routine](#daily-routine).

The capture window uses `ffmpeg` only to list device names (`winget install Gyan.FFmpeg`).

## Hardware

- Waveshare RP2350-USB-C (two Type-C ports: **native/programming** and **PIO-USB**).
- Two **data-capable** USB cables for the board (some charge-only cables fail silently).
- Switch 2 dock, an HDMI capture card (UGREEN HDMI Capture tested, 1080p60), a Windows PC.

## Daily routine

The PIO-USB link is fragile: **follow this order**.

Connect:

1. Unplug everything from the board.
2. Plug the board's **native** Type-C (the programming port) into a **dock USB port**. Wait ~10 s;
   the Switch sees a wired pad.
3. Plug the board's **PIO-USB** Type-C into the **PC**.
4. Right away start the GUI. All status rows should turn green. Keep it open while playing.

Disconnect:

1. Close the GUI with its **X** button (don't kill it).
2. Unplug the **PIO** cable, then the **native** cable.

Never plug the PIO cable in before the native cable, or leave it plugged into an idle board: the
whole board (both ports) can freeze. Only unplugging **both** cables recovers that. If the GUI shows
the link as stuck, see [the link troubleshooting](#the-gui-keeps-saying-board-not-found--not-responding)
below; replugging the PIO cable is usually all it takes.

## The GUI

Start it with `rp2350-switch-pad.exe` (release) or `py host/gui_hid.py`.

- **Controller picker:** *Auto* (first connected pad) or pick one of the four XInput slots. Each slot
  is listed by the name Windows reports plus its USB ID (for example "Controller (Xbox One For
  Windows) [045E:02FF]"; a Parsec guest's pad shows as "Controller (XBOX 360 For Windows)"). A box
  per slot turns green while that pad gets input, so you can press a button to see which is which.
- **Status lights:** controller on the PC, link to the board, *Switch has detected the pad*, inputs
  flowing PC → board. A hint line says what to do next when something is red.
- **Switch AB/XY** (default on, remembered): swaps A↔B and X↔Y so an Xbox pad's physical layout
  matches a Switch pad.
- **Keep-alive** (default on, remembered): after 30 s with no change on the pad, sends a 3-frame,
  ~10% right-stick nudge so the pad is never completely static. Untick it if a game reacts to it.
- **HOME / CAPTURE** are press-and-hold buttons; the Xbox Guide button maps to HOME when Windows
  exposes it.
- **Console sleep:** the Switch cuts and restores dock USB power, so the board re-presents its USB
  attach every 2 s while the bus is suspended. After waking the console, wait ~10 s and press a
  button once; the pad comes back on its own. The "Switch has detected the pad" light is yellow while
  the console sleeps.
- Settings: `%APPDATA%\mayflash_switch2_controller\gui_settings.json`; state log:
  `...\gui.log` (link and board state changes plus a heartbeat every minute).

Headless alternative: `py host/bridge_hid.py` (or `--demo` to cycle the d-pad without a gamepad).

## Capture window (video + audio)

The **Capture card window** panel opens a window titled **Switch Capture** that shows the capture
card's video and plays its audio. It is an ordinary desktop window, so you can share it in Parsec,
Discord or OBS.

- Pick the video device, the audio device and the quality (1080p 60 fps, 1080p 30 fps or
  720p 60 fps); the defaults pick the UGREEN card. Choices are remembered.
- **Low latency, no tearing:** capture runs at the card's full rate, only the newest frame is ever
  drawn (nothing queues up), and frames go to the screen through a GPU renderer with vsync. The
  audio runs in its own helper process with a small buffer and clock-drift compensation, so video
  load can never make it crackle.
- **Keys in the window:** `f` fullscreen, `m` mute, mouse wheel or Up/Down = volume, `q`/`Esc` close.
- **Volume:** a slider in the GUI (0–100%, remembered, changes the open window live). 10% is the
  card's level unchanged and 100% is a 10× boost, with a soft limiter against harsh clipping. The
  card's audio can arrive very quiet because of **Windows' own recording level** for it (it was 17%
  here): set it to 100% in *Settings → System → Sound → Input → the card → Volume*. The panel warns
  when that level is low.
- **Recovers by itself:** if frames stop for 2 s, the frame rate collapses, or the picture stays
  black for 10 s, the window reopens the card; if the card can't be opened it keeps retrying and
  shows "Waiting for the capture card...".
- **No-signal detection:** when the card is stuck sending its own "no valid video signal" picture
  (static vertical stripes), the window explains it instead of showing the stripes and tells you what
  to do (see below).
- A diagnostic log is written to `%APPDATA%\mayflash_switch2_controller\capture.log` (problems,
  recoveries, a heartbeat every minute).

Standalone: `py host/capture_view.py --video-index 1 --audio "<audio device name>"`.

## Troubleshooting

### The GUI keeps saying "board not found" / "not responding"

The link row loops between "board not found" and "found but not responding". The PIO-USB link goes
idle whenever nothing talks to it (for example after the GUI was closed for a while), and it does
not wake up on its own.

**Fix: unplug the PIO cable (from the board or from the PC), wait about 3 seconds, and plug it back
in.** The GUI reconnects by itself and everything works again; no restart is needed.

If that does not help (for example the Switch no longer sees the pad either), unplug **both** board
cables, wait 5 s, and redo the [connect steps](#daily-routine).

### The capture window shows stripes, goes black, or says it can't open the card

- **Vertical stripes / "no valid video" message:** the capture card wedged itself (it can after
  sitting idle for hours or after an HDMI mode change). **Unplug the card's USB cable and plug it
  back in.** If that is not enough, replug the HDMI cable between the dock and the card, and check
  the Switch's video output (1080p, 60 Hz, HDR and VRR off).
- **Short black pictures at full frame rate** are usually just game fades and loading screens.
- **Sound too quiet:** set the card's recording level in Windows to 100% (see above) before using
  the boost slider.
- Read `capture.log` for a timestamped account of what happened.

### Other

- *Switch has detected the pad* is red: check the native cable (data-capable), that it is in a dock
  USB port, then redo the connect order. If the GUI says the pad port is in this PC, the native cable
  is plugged into the computer instead of the dock.
- A PC-only test without the Switch: plug the native port into the PC, run `py tools/board_diag.py`
  and look for "NSW wired controller" in joy.cpl.
- Tools: `tools/link_test.py` (writes frames to the PIO link and reports successes/failures; note
  `hidapi` returns `-1` on failure instead of raising), `tools/board_diag.py` (crash registers,
  core-1 heartbeat, frame counters; works only while the native port is on the PC) and
  `tools/tear_check.py` (records ~5 s from the capture card and counts torn/duplicate frames).

## Build and flash

Only needed to change the firmware or the host tools.

```
py -m pip install -r host/requirements.txt     # hidapi, opencv, sounddevice, pygame-ce, pycaw, ...
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

### Build the Windows bundle

Build the firmware first, then `py -m pip install pyinstaller` and `py tools/build_release.py`
(output in `release/`).

## Status and limits

- **Verified:** Xbox pad → PC → board → Switch 2 dock (buttons, sticks, AB/XY swap, console
  sleep/wake, repeated connect/disconnect cycles) and **Parsec guests playing through it**; the
  capture window with sound at 1080p60 (a 57-minute play session without a dropout).
- One pad per board (the Switch sees one controller per USB device).
- The PIO-USB link goes idle when nothing talks to it and needs a replug (see above). The freeze
  with a wrongly ordered connection is understood only by its symptoms. A USB-to-serial adapter on
  the board's UART pins would be a more robust PC link than PIO-USB; the firmware's frame parser is
  already link-agnostic.
- The capture card can wedge after sitting idle; replugging its USB cable recovers it.
- Not tested: sessions longer than a few hours.

## Layout

```
fw/        firmware (main.c, usb_descriptors.c, pad maps, CMakeLists.txt)
fw/pio_usb vendored + patched PIO-USB (see fw/pio_usb/README.md for origin and licenses)
host/      gui_hid.py (GUI), capture_view.py (capture window), bridge.py (XInput → frames),
           bridge_hid.py (link sender / headless tool)
tools/     link_test.py, board_diag.py, tear_check.py, build_release.py
```

`fw/` still contains the DualShock-4 / DInput / HORI identities from the abandoned Mayflash
design; they are compiled in but unused (`DEFAULT_IDENTITY=3`).

## Credits

PIO-USB: [Pico-PIO-USB](https://github.com/sekigon-gonnoc/Pico-PIO-USB) (sekigon-gonnoc, MIT) via
Waveshare's RP2350-USB-C examples (Apache-2.0). Built on the Raspberry Pi pico-sdk and TinyUSB.
