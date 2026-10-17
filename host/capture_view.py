"""
capture_view.py - low-latency HDMI capture card window (video + audio).

Run by the GUI as a child process (or directly: py capture_view.py --list):
  - video: OpenCV/DirectShow capture, a reader thread keeps ONLY the newest frame; the window
    (pygame/SDL2 GPU renderer with vsync, so no tearing) always shows the latest one
    (older frames are dropped, never queued);
  - audio: the card's audio input is played to the default output through a small
    WASAPI buffer (~40 ms) in its own helper process (so the video can never starve it).

It is an ordinary desktop window ("Switch Capture") so you can share it in Parsec,
Discord or OBS. Keys in the window: f = fullscreen, m = mute, Up/Down or mouse wheel = volume (100% = 10x), q / Esc = close.
"""
import argparse
import os
import re
import subprocess
import sys
import threading
import time

try:
    import cv2
    import numpy as np
    import sounddevice as sd
except ImportError:  # the GUI shows the message; only the viewer process needs these
    cv2 = np = sd = None

HERE = os.path.abspath(__file__)
WINGET_GLOB = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages")

MODES = {
    "1080p 60 fps": (1920, 1080, 60),
    "1080p 30 fps": (1920, 1080, 30),
    "720p 60 fps": (1280, 720, 60),
}
DEFAULT_MODE = "1080p 60 fps"
TITLE = "Switch Capture"


# ---------------------------------------------------------------- devices ---
def find_ffmpeg():
    import shutil
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        for root, _d, files in os.walk(WINGET_GLOB):
            if "ffmpeg.exe" in files and "ffmpeg" in root.lower():
                return os.path.join(root, "ffmpeg.exe")
    except OSError:
        pass
    return None


def list_devices():
    """-> (video_names, audio_names) in DirectShow order (= OpenCV camera index order)."""
    ff = find_ffmpeg()
    if not ff:
        return ["Camera 0", "Camera 1", "Camera 2"], []
    try:
        raw = subprocess.run(
            [ff, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
        ).stderr
    except (OSError, subprocess.SubprocessError):
        return ["Camera 0", "Camera 1", "Camera 2"], []
    try:  # piped ffmpeg output is UTF-8 on some builds, the ANSI codepage on others
        out = raw.decode("utf-8")
    except UnicodeDecodeError:
        out = raw.decode("cp1252", "replace")
    video, audio = [], []
    for m in re.finditer(r'"(.+)" \((video|audio)\)', out):
        (video if m.group(2) == "video" else audio).append(m.group(1))
    return video, audio


def pick_default(names, hint="ugreen"):
    for n in names:
        if hint in n.lower():
            return n
    return names[0] if names else ""


def video_index(name, names):
    m = re.fullmatch(r"Camera (\d+)", name or "")
    if m:
        return int(m.group(1))
    return names.index(name) if name in names else 0


# ------------------------------------------------------------ the viewer ---
class LatestFrame:
    def __init__(self):
        self.lock = threading.Lock()
        self.frame, self.t, self.n = None, 0.0, 0
        self.cv = threading.Condition(self.lock)
        self.gap_max = 0.0  # longest time between two captured frames since last take_gap()

    def put(self, f):
        with self.cv:
            now = time.perf_counter()
            if self.t:
                self.gap_max = max(self.gap_max, now - self.t)
            self.frame, self.t, self.n = f, now, self.n + 1
            self.cv.notify_all()

    def take_gap(self):
        with self.lock:
            g, self.gap_max = self.gap_max, 0.0
            return g * 1000

    def wait_new(self, last_n, timeout=0.2):
        with self.cv:
            if self.n == last_n:
                self.cv.wait(timeout)
            return self.frame, self.t, self.n


def open_capture(vidx, w, h, fps):
    cap = cv2.VideoCapture(vidx, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap.release()
        return None
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"YUY2"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
    cap.set(cv2.CAP_PROP_FPS, fps)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


CLOG_PATH = os.path.join(os.environ.get("APPDATA", "."), "mayflash_switch2_controller", "capture.log")


def clog(msg):
    """Append a timestamped line to capture.log (small, rotated) so problems can be diagnosed later."""
    try:
        os.makedirs(os.path.dirname(CLOG_PATH), exist_ok=True)
        if os.path.exists(CLOG_PATH) and os.path.getsize(CLOG_PATH) > 300_000:
            os.replace(CLOG_PATH, CLOG_PATH + ".old")
        with open(CLOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def is_error_pattern(f):
    """True for the card's own 'no valid video signal' picture: vertical stripes, i.e. every row
    identical. A real game frame never has that (a plain black frame is handled separately)."""
    import numpy as np
    rows = f[::108].astype(np.int16)
    return float(f[::64, ::64].mean()) > 3.0 and float(np.abs(rows - rows[0]).mean()) < 0.5


def grab_loop(cap, vidx, w, h, fps, slot, stop, status):
    """Read frames into `slot` and keep the card healthy. Reopens it by itself when
      - no frame arrives for 2 s (stall after an HDMI mode change),
      - the frame rate stays under 20 fps for 2 s (the card falls to ~1 fps when its input drops),
      - the picture stays completely black for 10 s,
    (rate-limited), and keeps retrying while the card cannot be opened. Everything notable is
    written to capture.log."""
    now = time.time()
    last_ok, last_reopen, t_sec, n_sec, n = now, 0.0, now, 0, 0
    fresh_reopen = False
    t_open = now  # warm-up: the card needs ~2 s after opening before the rate means anything
    bright, low_secs, black_secs, bad, t_beat = 255.0, 0, 0, False, now
    pattern_secs, last_pattern_reopen = 0, 0.0
    clog(f"capture loop started (device {vidx}, {w}x{h}@{fps})")
    while not stop.is_set():
        if cap is None:
            stop.wait(0.3 if fresh_reopen else 1.0)  # first retry quickly, then every second
            fresh_reopen = False
            cap = open_capture(vidx, w, h, fps)
            last_ok, t_sec, n_sec, low_secs, black_secs = time.time(), time.time(), 0, 0, 0
            t_open = time.time()
            if cap is not None:
                print("INFO capture device reopened", file=sys.stderr, flush=True)
                clog("capture device reopened")
            continue
        ok, f = cap.read()
        now = time.time()
        reason = ""
        if ok:
            last_ok = now
            slot.put(f)
            n += 1
            n_sec += 1
            if n % 10 == 0:
                bright = float(f[::64, ::64].mean())
            if n % 15 == 0:
                if is_error_pattern(f):
                    pattern_secs = pattern_secs + 0.25
                else:
                    pattern_secs = 0
                status["no_signal"] = pattern_secs >= 2.0
        else:
            time.sleep(0.005)
        if now - t_sec >= 1.0:  # once a second: judge the stream
            rate = n_sec / (now - t_sec)
            t_sec, n_sec = now, 0
            # a black picture alone is normal (game fades, loading screens): only a long one counts
            low_secs = low_secs + 1 if rate < 20 else 0
            black_secs = black_secs + 1 if bright < 1.5 else 0
            problem = (rate < 45 or black_secs >= 5 or status.get("no_signal")) and now - t_open > 3
            if problem and not bad:
                clog(f"PROBLEM: {rate:.0f} fps, picture brightness {bright:.0f}"
                     + (" - the card shows its 'no valid video signal' pattern" if status.get("no_signal") else ""))
            elif bad and not problem:
                clog(f"recovered: {rate:.0f} fps, brightness {bright:.0f}")
            bad = problem
            if now - t_beat >= 60:
                t_beat = now
                clog(f"ok: {rate:.0f} fps, brightness {bright:.0f}")
            if low_secs >= 2 and now - last_reopen > 15:
                reason = f"frame rate collapsed ({rate:.0f} fps)"
            elif black_secs >= 10 and now - last_reopen > 60:
                reason = "picture black for 10 s"
            elif status.get("no_signal") and now - last_reopen > 20:
                reason = "card shows its no-signal pattern"  # a reopen sometimes makes it lock again
        if not reason and now - last_ok > 2.0:
            reason = "no frames for 2 s"
        if reason:
            print(f"INFO capture reopening: {reason}", file=sys.stderr, flush=True)
            clog(f"reopening the card: {reason}")
            cap.release()
            cap, last_reopen, low_secs, black_secs, fresh_reopen = None, now, 0, 0, True


def input_level(hint):
    """Windows' own recording level (0-100) of the capture device, or None. A low value here
    (e.g. 17%) makes the card very quiet before any software boost."""
    try:
        import comtypes
        from comtypes import CLSCTX_ALL
        from ctypes import POINTER, cast
        from pycaw.constants import CLSID_MMDeviceEnumerator
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume, IMMDeviceEnumerator
        key = (re.findall(r"\(([^)]*)\)", hint) or [hint])[-1].lower() if hint else "ugreen"
        words = [w for w in re.split(r"\W+", key) if len(w) > 2]
        comtypes.CoInitialize()
        enum = comtypes.CoCreateInstance(
            CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER)
        coll = enum.EnumAudioEndpoints(1, 1)  # capture devices, active
        for i in range(coll.GetCount()):
            d = coll.Item(i)
            if all(w in AudioUtilities.CreateDevice(d).FriendlyName.lower() for w in words):
                vol = cast(d.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None),
                           POINTER(IAudioEndpointVolume))
                return int(round(vol.GetMasterVolumeLevelScalar() * 100))
    except Exception:
        pass
    return None


class AudioPipe:
    """capture-card input -> default output, with clock-drift compensation.

    The card's audio clock and the sound card's clock never match exactly (here the output ran
    ~0.2% fast), so a plain queue runs dry every few seconds and each gap is an audible click.
    Instead the input goes into a ring buffer and the output reads it with linear interpolation at
    a rate nudged by +-1% max to keep the buffer near a fixed target (~40 ms): an inaudible
    pitch change instead of clicks.
    """

    TARGET_S = 0.040   # buffer fill the controller aims for
    RING_S = 1.0

    def __init__(self, name_hint, gain=1.0):
        self.muted = False
        self.gain = gain  # linear; 1.0 = unchanged, 10.0 = +20 dB
        self.err = ""
        self.streams = []
        self.under = 0      # callbacks that had to output silence
        self.overflow = 0   # times the buffer was cut back
        try:
            self._open(name_hint)
        except Exception as e:  # keep the video window alive without audio
            self.err = f"audio off: {e}"

    def _open(self, hint):
        wasapi = next(i for i, h in enumerate(sd.query_hostapis()) if "WASAPI" in h["name"])
        key = (re.findall(r"\(([^)]*)\)", hint) or [hint])[-1].lower() if hint else "ugreen"
        words = [w for w in re.split(r"\W+", key) if len(w) > 2]
        cand = [(i, d) for i, d in enumerate(sd.query_devices())
                if d["hostapi"] == wasapi and d["max_input_channels"] > 0]
        pick = next((i for i, d in cand if all(w in d["name"].lower() for w in words)), None)
        if pick is None:
            raise RuntimeError(f"no WASAPI input matching '{hint}'")
        out = sd.query_hostapis(wasapi)["default_output_device"]
        rate = int(sd.query_devices(pick)["default_samplerate"])
        ch_in = min(2, sd.query_devices(pick)["max_input_channels"])
        ex = sd.WasapiSettings(auto_convert=True)
        block = rate // 100  # 10 ms

        size = int(rate * self.RING_S)
        ring = np.zeros((size, 2), dtype=np.float32)
        target = int(rate * self.TARGET_S)
        st = {"w": 0, "r": 0.0, "primed": False}

        def cb_in(indata, frames, t, status):
            x = indata if ch_in == 2 else np.repeat(indata, 2, axis=1)
            idx = (st["w"] + np.arange(frames)) % size
            ring[idx] = x
            st["w"] += frames

        def cb_out(outdata, frames, t, status):
            fill = st["w"] - st["r"]
            if not st["primed"]:
                if fill < target:
                    outdata.fill(0)
                    return
                st["primed"] = True
            if fill > 3 * target:  # far behind real time: cut back to the target
                st["r"] = st["w"] - target
                fill = target
                self.overflow += 1
            # proportional speed control: buffer too full -> read a bit faster, too empty -> slower
            ratio = 1.0 + max(-0.01, min(0.01, (fill - target) / target * 0.02))
            need = frames * ratio + 2
            if fill < need:  # underrun: play silence and refill to the target before resuming
                outdata.fill(0)
                st["primed"] = False
                self.under += 1
                return
            pos = st["r"] + np.arange(frames) * ratio
            i0 = np.floor(pos).astype(np.int64)
            fr = (pos - i0).astype(np.float32)[:, None]
            x = ring[i0 % size] * (1.0 - fr) + ring[(i0 + 1) % size] * fr
            st["r"] += frames * ratio
            if self.muted:
                outdata.fill(0)
                return
            x = x * self.gain
            if self.gain > 1.0:  # soft knee above 0.8 instead of hard clipping
                a = np.abs(x)
                x = np.where(a > 0.8, np.sign(x) * (0.8 + 0.2 * np.tanh((a - 0.8) / 0.2)), x)
            outdata[:] = x

        i = sd.InputStream(device=pick, channels=ch_in, samplerate=rate, blocksize=block,
                           dtype="float32", latency="low", callback=cb_in, extra_settings=ex)
        o = sd.OutputStream(device=out, channels=2, samplerate=rate, blocksize=block,
                            dtype="float32", latency="low", callback=cb_out, extra_settings=ex)
        i.start()
        o.start()
        self.streams = [i, o]

    def close(self):
        for s in self.streams:
            try:
                s.stop()
                s.close()
            except Exception:
                pass


def boost_process():
    """Keep Windows from throttling this process (timer resolution, efficiency mode, priority)."""
    import ctypes
    try:
        ctypes.windll.winmm.timeBeginPeriod(1)

        class PPT(ctypes.Structure):
            _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong),
                        ("StateMask", ctypes.c_ulong)]
        k32 = ctypes.windll.kernel32
        st = PPT(1, 1, 0)  # EXECUTION_SPEED throttling: controlled, OFF
        k32.SetProcessInformation(k32.GetCurrentProcess(), 4, ctypes.byref(st), ctypes.sizeof(st))
        k32.SetPriorityClass(k32.GetCurrentProcess(), 0x8000)  # above normal
    except Exception:
        pass


def _self_cmd():
    """Command that re-runs this module (the frozen exe relaunches itself)."""
    return ([sys.executable, "--capture-view"] if getattr(sys, "frozen", False)
            else [sys.executable, HERE])


class AudioProxy:
    """Runs the audio in its OWN process. In the same process as the video, the vsync wait and the
    texture upload keep Python's interpreter lock for tens of milliseconds, so the audio callbacks
    starved (measured: ~100 audible cuts per 30 s). Same interface as AudioPipe (gain, muted,
    under, overflow, err, close)."""

    def __init__(self, hint, gain=1.0):
        self._gain, self._muted = gain, False
        self.err, self.under, self.overflow = "", 0, 0
        self.proc = None
        cmd = _self_cmd() + ["--audio-only", "--audio", hint or "",
                             "--volume", str(int(round(gain * 100)))]
        try:
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError as e:
            self.err = f"audio off: {e}"
            return
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for raw in iter(self.proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("ASTATS"):
                try:
                    _, u, o = line.split()
                    self.under, self.overflow = int(u), int(o)
                except ValueError:
                    pass
            elif line.startswith("WARN"):
                self.err = line
                print(line, file=sys.stderr, flush=True)

    def _send(self, text):
        try:
            self.proc.stdin.write((text + "\n").encode())
            self.proc.stdin.flush()
        except (OSError, ValueError, AttributeError):
            pass

    @property
    def gain(self):
        return self._gain

    @gain.setter
    def gain(self, v):
        self._gain = v
        self._send(f"VOL {int(round(v * 100))}")

    @property
    def muted(self):
        return self._muted

    @muted.setter
    def muted(self, v):
        self._muted = bool(v)
        self._send(f"MUTE {int(self._muted)}")

    def close(self):
        if self.proc is None:
            return
        self._send("QUIT")
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=2)
        except Exception:
            self.proc.terminate()


def run_audio_only(hint, volume):
    """Audio helper process: card input -> default output, controlled over stdin."""
    boost_process()
    a = AudioPipe(hint, volume / 100.0)
    if a.err:
        print("WARN " + a.err, file=sys.stderr, flush=True)

    def stats():
        while True:
            time.sleep(2)
            print(f"ASTATS {a.under} {a.overflow}", file=sys.stderr, flush=True)

    threading.Thread(target=stats, daemon=True).start()
    for raw in sys.stdin:  # ends when the viewer closes our stdin or dies
        parts = raw.split()
        try:
            if parts[0] == "VOL":
                a.gain = max(0.0, min(10.0, int(parts[1]) / 100.0))
            elif parts[0] == "MUTE":
                a.muted = parts[1] == "1"
            elif parts[0] == "QUIT":
                break
        except (ValueError, IndexError):
            pass
    a.close()
    return 0


def run_viewer(vidx, audio_hint, mode, no_audio=False, volume=100):
    w, h, fps = MODES.get(mode, MODES[DEFAULT_MODE])
    cap = open_capture(vidx, w, h, fps)
    if cap is None:
        print(f"WARN could not open capture device {vidx} (in use by another app, or the card "
              "is not responding): retrying", file=sys.stderr, flush=True)
        got = (w, h)
    else:
        got = (int(cap.get(3)), int(cap.get(4)))
    print(f"INFO video {got[0]}x{got[1]} requested {w}x{h}@{fps}", file=sys.stderr, flush=True)
    clog(f"viewer started: video {got[0]}x{got[1]} requested {w}x{h}@{fps}")

    boost_process()
    audio = None if no_audio else AudioProxy(audio_hint, volume / 100.0)
    if audio is not None and audio.err:
        print("WARN " + audio.err, file=sys.stderr, flush=True)
    if audio is not None:
        lvl = input_level(audio_hint)
        if lvl is not None:
            print(f"LEVEL {lvl}", file=sys.stderr, flush=True)

    def read_commands():  # "VOL <percent>" lines from the GUI process
        for raw in sys.stdin:
            try:
                parts = raw.split()
                if len(parts) == 2 and parts[0] == "VOL" and audio is not None:
                    audio.gain = max(0.0, min(10.0, int(parts[1]) / 100.0))
            except ValueError:
                pass

    if sys.stdin is not None:
        threading.Thread(target=read_commands, daemon=True).start()

    slot, stop = LatestFrame(), threading.Event()
    status = {"no_signal": False}
    threading.Thread(target=grab_loop, args=(cap, vidx, w, h, fps, slot, stop, status), daemon=True).start()

    # GPU renderer with vsync (OpenCV's own window has no vsync -> tearing in fullscreen)
    import pygame
    from pygame._sdl2 import video as sv
    pygame.display.init()
    win = sv.Window(TITLE, size=(1280, int(1280 * got[1] / max(got[0], 1))), resizable=True)
    ren = sv.Renderer(win, vsync=True)
    tex = sv.Texture(ren, got, streaming=True)
    ren.draw_color = (0, 0, 0, 255)
    pygame.font.init()
    msg = sv.Texture.from_surface(ren, pygame.font.Font(None, 56).render(
        "Waiting for the capture card / video signal...", True, (230, 230, 230)))
    font = pygame.font.Font(None, 44)
    lines = ["The capture card is not getting a valid video signal.",
             "Unplug and replug the HDMI cable between the dock and the card,",
             "and check the Switch's video output (1080p, 60 Hz, HDR and VRR off)."]
    surfs = [font.render(s, True, (240, 240, 240)) for s in lines]
    sw, sh_ = max(s.get_width() for s in surfs), sum(s.get_height() + 10 for s in surfs)
    big = pygame.Surface((sw, sh_), pygame.SRCALPHA)
    yy = 0
    for s in surfs:
        big.blit(s, ((sw - s.get_width()) // 2, yy))
        yy += s.get_height() + 10
    nosig = sv.Texture.from_surface(ren, big)
    last_frame_t = time.time()
    last_n, shown, t_stat, age_sum = 0, 0, time.time(), 0.0
    d_dropped, d_late, d_gap_max, d_presents, d_stall = 0, 0, 0.0, [], 0.0
    t_new_prev, t_loop_prev, cap0 = None, time.perf_counter(), 0
    fullscreen, running, code = False, True, 0
    while running:
        frame, t_frame, n = slot.wait_new(last_n, timeout=0.004)
        if frame is not None and n != last_n:
            d_dropped += n - last_n - 1 if last_n else 0  # captured but replaced before it was shown
            tn = time.perf_counter()
            if t_new_prev is not None:
                gapm = (tn - t_new_prev) * 1000
                d_gap_max = max(d_gap_max, gapm)
                if gapm > 25:
                    d_late += 1
            t_new_prev = tn
            last_n = n
            if frame.shape[1] == got[0] and frame.shape[0] == got[1]:
                surf = pygame.image.frombuffer(frame.data, got, "BGR")
                tex.update(surf)
            shown += 1
            last_frame_t = time.time()
            age_sum += (time.perf_counter() - t_frame) * 1000
        for e in pygame.event.get():
            if e.type in (pygame.QUIT, pygame.WINDOWCLOSE):
                running = False
            elif e.type == pygame.MOUSEWHEEL and audio is not None:
                audio.gain = max(0.0, min(10.0, audio.gain + 0.2 * e.y))
            elif e.type == pygame.KEYDOWN:
                if e.key in (pygame.K_q, pygame.K_ESCAPE):
                    running = False
                elif e.key == pygame.K_m and audio is not None:
                    audio.muted = not audio.muted
                elif e.key in (pygame.K_UP, pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS) and audio:
                    audio.gain = min(10.0, audio.gain + 0.2)
                elif e.key in (pygame.K_DOWN, pygame.K_MINUS, pygame.K_KP_MINUS) and audio:
                    audio.gain = max(0.0, audio.gain - 0.2)
                elif e.key == pygame.K_f:
                    fullscreen = not fullscreen
                    if fullscreen:
                        win.set_fullscreen(True)
                    else:
                        win.set_windowed()
        ww, wh = win.size  # fit the picture into the window, keep the aspect ratio
        sc = min(ww / got[0], wh / got[1])
        dw, dh = int(got[0] * sc), int(got[1] * sc)
        ren.clear()
        if status["no_signal"]:  # the card's own error pattern: hide it and explain
            sc = min(1.0, ww * 0.9 / nosig.width)
            nw, nh = int(nosig.width * sc), int(nosig.height * sc)
            nosig.draw(dstrect=((ww - nw) // 2, (wh - nh) // 2, nw, nh))
        elif time.time() - last_frame_t > 1.5:  # stream stalled: say so instead of freezing
            mw, mh = msg.width, msg.height
            msg.draw(dstrect=((ww - mw) // 2, (wh - mh) // 2, mw, mh))
        else:
            tex.draw(dstrect=((ww - dw) // 2, (wh - dh) // 2, dw, dh))
        t_before = time.perf_counter()
        ren.present()  # waits for vsync
        t_after = time.perf_counter()
        d_presents.append(t_after - t_loop_prev)
        d_stall = max(d_stall, (t_after - t_before) * 1000)
        t_loop_prev = t_after
        now = time.time()
        if now - t_stat >= 5:
            print(f"STATS shown {shown / (now - t_stat):.0f} fps, capture->screen "
                  f"{age_sum / max(shown, 1):.0f} ms"
                  + (f", volume {audio.gain * 10:.0f}%, audio gaps {audio.under}/cuts {audio.overflow}"
                     if audio is not None else "")
                  + (" (muted)" if audio is not None and audio.muted else ""),
                  file=sys.stderr, flush=True)
            per = sorted(d_presents)[len(d_presents) // 2] if d_presents else 0
            print(f"DIAG captured {(last_n - cap0) / 5:.1f}/s, capture max gap {slot.take_gap():.0f} ms | "
                  f"dropped before display {d_dropped}, late frames (>25 ms) {d_late}, "
                  f"display max gap {d_gap_max:.0f} ms | present period {per * 1000:.2f} ms "
                  f"(~{(1 / per if per else 0):.0f} Hz), longest present {d_stall:.0f} ms",
                  file=sys.stderr, flush=True)
            cap0, d_dropped, d_late, d_gap_max, d_presents, d_stall = last_n, 0, 0, 0.0, [], 0.0
            t_stat, shown, age_sum = now, 0, 0.0
    stop.set()
    if audio is not None:
        audio.close()
    clog("viewer closed")
    if cap is not None:
        cap.release()
    pygame.quit()
    return code


class Viewer:
    """GUI-side handle: runs the viewer in a child process."""

    def __init__(self):
        self.proc = None
        self.started = 0.0
        self.lines = []
        self.error = ""
        self.stats = ""
        self.level = None  # Windows recording level of the capture device (percent)

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, video_names, video, audio, mode=DEFAULT_MODE, mute_audio=False, volume=200):
        if self.running():
            return True
        if not video:
            self.error = "no video device selected"
            return False
        # frozen (PyInstaller) build: relaunch this same exe in viewer mode
        cmd = _self_cmd() + ["--video-index", str(video_index(video, video_names)),
                      "--audio", audio or "", "--mode", mode, "--volume", str(int(volume))]
        if mute_audio or not audio:
            cmd.append("--no-audio")
        self.lines, self.error, self.stats, self.level = [], "", "", None
        try:
            self.proc = subprocess.Popen(
                cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, stdin=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except OSError as e:
            self.error = str(e)
            return False
        self.started = time.time()
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
        return True

    def _read(self, proc):
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            if line.startswith("STATS"):
                self.stats = line[6:]
            elif line.startswith("LEVEL "):
                try:
                    self.level = int(line[6:])
                except ValueError:
                    pass
            self.lines.append(line[-200:])
            del self.lines[:-30]

    def send_volume(self, percent):
        if self.running():
            try:
                self.proc.stdin.write(f"VOL {int(percent)}\n".encode())
                self.proc.stdin.flush()
            except (OSError, ValueError):
                pass

    def stop(self):
        p, self.proc = self.proc, None
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()

    def state(self):
        """-> ('stopped'|'running'|'failed', message)"""
        if self.running():
            return "running", self.stats
        if self.proc is not None:  # the window closed or the process died
            code = self.proc.returncode
            self.proc = None
            err = next((l for l in reversed(self.lines)
                        if l.startswith(("ERROR", "Traceback")) or "Error" in l), "")
            if code not in (0, None) or err:
                self.error = err or f"exited ({code})"
                return "failed", self.error
            return "stopped", ""
        return ("failed", self.error) if self.error else ("stopped", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--video-index", type=int, default=1)
    ap.add_argument("--audio", default="")
    ap.add_argument("--mode", default=DEFAULT_MODE)
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--audio-only", action="store_true", help="internal: audio helper process")
    ap.add_argument("--volume", type=int, default=100, help="percent, 100 = unchanged")
    a = ap.parse_args()
    if a.list:
        print(list_devices())
        return 0
    if cv2 is None:
        print("ERROR missing packages: py -m pip install opencv-python numpy sounddevice", file=sys.stderr)
        return 3
    if a.audio_only:
        return run_audio_only(a.audio, a.volume)
    return run_viewer(a.video_index, a.audio, a.mode, a.no_audio, a.volume)


if __name__ == "__main__":
    sys.exit(main())
