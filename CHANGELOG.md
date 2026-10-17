# Changelog

## 0.1.2 - 2026-10-17

- Capture window volume: slider in the GUI (0-100%, where 100% = 10x gain; default 50%, remembered,
  changes the open window live), plus mouse wheel / Up / Down inside the window. A soft limiter avoids
  harsh clipping when the signal is boosted.
- The capture panel warns when Windows' own recording level for the card is low (the UGREEN card
  defaulted to 17%, which made it very quiet). Setting it to 100% in Settings > System > Sound > Input
  is the best fix; the slider is the second step.
- No firmware changes (`bridge.uf2` is identical to 0.1.0/0.1.1).

## 0.1.1 - 2026-10-17

- Release bundle on the GitHub releases page: `rp2350-switch-pad-v0.1.1-windows.zip` (the GUI as a
  Windows exe plus `bridge.uf2`). Built with `tools/build_release.py` (PyInstaller).
- The capture window works from the packaged exe (the exe relaunches itself in viewer mode).
- `--check` smoke test for packaged builds; the GUI title shows the version.
- No firmware changes from 0.1.0 (`bridge.uf2` is identical).

## 0.1.0 - 2026-10-17

First complete version.

- **Firmware:** the RP2350-USB-C presents a PowerA wired pad (`20D6:A713`) to the Switch 2 dock on its
  native Type-C port; the PIO-USB Type-C port is a PC link that receives pad frames as HID feature
  reports. Survives console sleep and wake (re-attaches while the USB bus stays suspended).
- **GUI (`host/gui_hid.py`):** picks a PC controller (Auto or by name, with USB ID and a per-slot
  activity box), status lights for pad / link / Switch / inputs, Switch AB/XY swap (default on,
  remembered), idle keep-alive nudge, Xbox Guide button as HOME, HOME/CAPTURE buttons,
  state log in `%APPDATA%\mayflash_switch2_controller\gui.log`.
- **Capture card window:** low-latency video and audio from an HDMI capture card (UGREEN HDMI
  Capture tested) in a window you can share in Parsec, Discord or OBS; vsync, no tearing.
- **Release bundle:** `tools/build_release.py` builds a Windows exe (PyInstaller) of the GUI and zips
  it with `bridge.uf2`.
- **Tools:** `tools/link_test.py`, `tools/board_diag.py`, `tools/tear_check.py`.

### Known limitations

- One pad per board (the Switch sees one controller per USB device).
- The PIO-USB link can freeze the whole board if connected out of order or left plugged into an
  idle board; follow the connect/disconnect routine in the README. Root cause not found.
- Not yet tested: a real Parsec guest pad, multi-hour sessions, several PC controllers at once.
