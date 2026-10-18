# Changelog

## Unreleased

- Added the MIT `LICENSE`, `THIRD_PARTY_NOTICES.md` and a `licenses/` folder; the release zip now contains
  the license texts of everything bundled (collected by `tools/build_release.py`).

## 0.1.4 - 2026-10-17

- The capture window recognises the card's own "no valid video signal" picture (vertical stripes, every
  row identical) and shows an explanation instead of the stripes: replug the card's USB cable (that is
  what fixed it in practice; the card had sat idle for two hours), else the HDMI cable, and check the
  Switch's video output. It also tries reopening the card now and then.
- No firmware changes (`bridge.uf2` is identical to 0.1.0 - 0.1.3).

## 0.1.3 - 2026-10-17

- **Fixed the capture card dropping out during play.** The GUI listed all HID devices every 1-3 s to
  name the controllers, which made Windows query every USB device, including the capture card;
  the card then collapsed to 1-2 fps for several seconds, up to three times in two minutes. The
  listing now happens every 30 s (and the "pad port is in this PC" check only while the board link
  is down). A 57-minute session afterwards had no collapse at all.
- The capture window writes `%APPDATA%\mayflash_switch2_controller\capture.log` (problems, recoveries, a
  once-a-minute heartbeat; a short black fade is not counted as a problem) and reopens the card when
  its frame rate collapses for 2 s.
- The "link not responding" hint now says to replug only the PIO cable first (that is usually enough).
- No firmware changes (`bridge.uf2` is identical to 0.1.0 - 0.1.2).

## 0.1.2 - 2026-10-17

- Capture window volume: slider in the GUI (0-100%, where 100% = 10x gain; default 50%, remembered,
  changes the open window live), plus mouse wheel / Up / Down inside the window. A soft limiter avoids
  harsh clipping when the signal is boosted.
- Capture audio no longer crackles: the output clock ran ~0.2% faster than the card's, so the buffer
  ran dry every few seconds (~15 clicks a minute). The audio path now keeps a ~40 ms ring buffer and
  adjusts its read speed by a fraction of a percent (0 gaps measured over 40 s). The default volume is
  10% on the slider (= 1x, no boost) once the card's Windows recording level is at 100%.
- The capture audio now runs in its own helper process. Sharing a process with the video drawing
  (vsync wait, texture upload) starved the audio callbacks: ~80-120 audible cuts per 30 s while
  drawing 1080p, worse when the window lost focus or went fullscreen; 0 cuts measured with the
  separate process, and the drawing also got smoother.
- The capture window now recovers by itself: if no frame arrives for 2 s (the card stalls after an HDMI
  mode change) or the picture stays completely black for 10 s, it reopens the card; while the card
  cannot be opened it keeps retrying and shows "Waiting for the capture card / video signal..." instead
  of exiting or freezing on the last frame.
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
