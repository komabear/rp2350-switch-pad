# Vendored PIO-USB

Source: Waveshare `RP2350-USB-C` examples (`examples/C/01_USB/src`), which is a copy of
[Pico-PIO-USB](https://github.com/sekigon-gonnoc/Pico-PIO-USB) (MIT, (c) sekigon-gonnoc /
Ha Thach). The Waveshare repository is licensed under Apache-2.0 (see
`LICENSE-Waveshare-Apache-2.0`). File headers are kept as shipped.

Local modifications (all in `pio_usb_device.c`):
- a stuck-SE0 line no longer spins forever (processed for at most 1 ms per tick);
- `GET_REPORT(feature) 0x0300` answers with the PS3-style magic bytes;
- HID `SET_REPORT` is stored in `pio_usb_set_report_buf[64]` (the host tool streams
  pad frames this way) instead of being discarded;
- HID class `GET_REPORT` returns `pio_usb_status_buf[16]` (board status for the GUI).
