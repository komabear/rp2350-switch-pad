# Third-party notices

rp2350-switch-pad's own code is under the [MIT License](LICENSE). It builds on, and the release
downloads contain, the third-party software below, each under its own license. Full license texts are in
`licenses/` in the repository and in the `licenses/` folder of the release zip.

## Board firmware (`bridge.uf2` and `fw/`)

| Component | License | Where |
|---|---|---|
| **Pico-PIO-USB** (sekigon-gonnoc, Ha Thach), via Waveshare's RP2350-USB-C examples | MIT; Waveshare's repository is Apache-2.0 | `fw/pio_usb/` (vendored and patched, see `fw/pio_usb/README.md`, license file `fw/pio_usb/LICENSE-Waveshare-Apache-2.0`) |
| **Raspberry Pi pico-sdk** 2.2.0 | BSD-3-Clause, (c) 2020 Raspberry Pi (Trading) Ltd. | compiled into `bridge.uf2`; text in `licenses/pico-sdk-BSD-3-Clause.txt` |
| **TinyUSB** (hathach) | MIT, (c) 2018 hathach | compiled into `bridge.uf2`; text in `licenses/tinyusb-MIT.txt` |

## Windows app (`rp2350-switch-pad.exe` and its `_internal` folder)

Built with PyInstaller from the Python sources in `host/`. The bundle contains:

| Component | License | Notes |
|---|---|---|
| **Python** 3.14 | PSF License | text in `licenses/python-LICENSE.txt` |
| **Tcl/Tk** (tkinter) | Tcl/Tk (BSD-style) | `_internal/_tk_data/license.terms` |
| **hidapi** (cython-hidapi binding + HIDAPI C library) | BSD-3-Clause (this project uses it under the BSD option; it is also offered under GPL-3.0 and the original HIDAPI license) | `licenses/python-packages/hidapi/` |
| **pyserial** 3.5 | BSD-3-Clause, (C) 2001-2020 Chris Liechti | `licenses/pyserial-BSD-3-Clause.txt` |
| **OpenCV** (opencv-python) | Apache-2.0 | its bundled libraries (FFmpeg under the LGPL, libpng, libjpeg-turbo, libwebp, ...) are listed with their licenses in `licenses/python-packages/opencv-python/LICENSE-3RD-PARTY.txt`; FFmpeg ships as a separate DLL (`opencv_videoio_ffmpeg*.dll`) |
| **NumPy** | BSD-3-Clause and others | `licenses/python-packages/numpy/` |
| **sounddevice** and **PortAudio** | MIT | `licenses/python-packages/sounddevice/` |
| **pygame-ce** and **SDL2** (SDL2_image, SDL2_mixer, SDL2_ttf) | pygame-ce: LGPL-2.1; SDL2: zlib | pygame-ce is **LGPL**: it is shipped as separate `.pyd`/`.dll` files in `_internal/pygame/` and `_internal/SDL2*.dll`, so you can replace them with another build. Source: https://github.com/pygame-community/pygame-ce. Texts: `licenses/LGPL-2.1.txt`, `licenses/pygame-ce-NOTICE.txt` |
| **pycaw** | MIT | `licenses/python-packages/pycaw/` |
| **comtypes** | MIT | `licenses/python-packages/comtypes/` |
| **PyInstaller** bootloader | GPL-2.0-or-later with a special exception that allows it to be used in programs under any license | https://pyinstaller.org |

## Not included

`ffmpeg` is **not** bundled. The capture window only calls it (if you installed it, for example with
`winget install Gyan.FFmpeg`) to list device names.

Trademarks: Nintendo Switch is a trademark of Nintendo; PowerA is a trademark of its owner. This project
is not affiliated with or endorsed by either. "PowerA" only describes the generic wired controller the
board imitates.
