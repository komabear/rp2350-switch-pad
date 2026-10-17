"""
capture_view.py - low-latency HDMI capture card window (video + audio).

Run by the GUI as a child process (or directly: py capture_view.py --list):
  - video: OpenCV/DirectShow capture, a reader thread keeps ONLY the newest frame; the window
    (pygame/SDL2 GPU renderer with vsync, so no tearing) always shows the latest one
    (older frames are dropped, never queued);
  - audio: the card's audio input is played to the default output through a small
    WASAPI buffer (~40-80 ms), dropping audio if it ever backs up.

It is an ordinary desktop window ("Switch Capture") so you can share it in Parsec,
Discord or OBS. Keys in the window: f = fullscreen, m = mute, q / Esc = close.
"""
import argparse
import collections
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

    def put(self, f):
        with self.cv:
            self.frame, self.t, self.n = f, time.perf_counter(), self.n + 1
            self.cv.notify_all()

    def wait_new(self, last_n, timeout=0.2):
        with self.cv:
            if self.n == last_n:
                self.cv.wait(timeout)
            return self.frame, self.t, self.n


def grab_loop(cap, slot, stop):
    while not stop.is_set():
        ok, f = cap.read()
        if ok:
            slot.put(f)
        else:
            time.sleep(0.005)


class AudioPipe:
    """capture-card input -> default output with a tiny drop-oldest buffer."""

    def __init__(self, name_hint):
        self.q = collections.deque()
        self.muted = False
        self.err = ""
        self.streams = []
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

        def cb_in(indata, frames, t, status):
            if len(self.q) > 12:  # > ~120 ms queued: drop the oldest, stay live
                while len(self.q) > 3:
                    self.q.popleft()
            self.q.append(indata.copy())

        def cb_out(outdata, frames, t, status):
            try:
                blk = self.q.popleft()
            except IndexError:
                outdata.fill(0)
                return
            n = min(len(blk), frames)
            outdata[:] = 0
            if not self.muted:
                outdata[:n, :ch_in] = blk[:n]
                if ch_in == 1:
                    outdata[:n, 1:] = blk[:n]

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


def run_viewer(vidx, audio_hint, mode, no_audio=False):
    w, h, fps = MODES.get(mode, MODES[DEFAULT_MODE])
    cap = cv2.VideoCapture(vidx, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print(f"ERROR could not open capture device {vidx} (in use by another app?)", file=sys.stderr)
        return 2
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"YUY2"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
    cap.set(cv2.CAP_PROP_FPS, fps)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    got = (int(cap.get(3)), int(cap.get(4)))
    print(f"INFO video {got[0]}x{got[1]} requested {w}x{h}@{fps}", file=sys.stderr, flush=True)

    audio = None if no_audio else AudioPipe(audio_hint)
    if audio is not None and audio.err:
        print("WARN " + audio.err, file=sys.stderr, flush=True)

    slot, stop = LatestFrame(), threading.Event()
    threading.Thread(target=grab_loop, args=(cap, slot, stop), daemon=True).start()

    # GPU renderer with vsync (OpenCV's own window has no vsync -> tearing in fullscreen)
    import pygame
    from pygame._sdl2 import video as sv
    pygame.display.init()
    win = sv.Window(TITLE, size=(1280, int(1280 * got[1] / max(got[0], 1))), resizable=True)
    ren = sv.Renderer(win, vsync=True)
    tex = sv.Texture(ren, got, streaming=True)
    ren.draw_color = (0, 0, 0, 255)
    last_n, shown, t_stat, age_sum = 0, 0, time.time(), 0.0
    fullscreen, running, code = False, True, 0
    while running:
        frame, t_frame, n = slot.wait_new(last_n, timeout=0.004)
        if frame is not None and n != last_n:
            last_n = n
            if frame.shape[1] == got[0] and frame.shape[0] == got[1]:
                surf = pygame.image.frombuffer(frame.data, got, "BGR")
                tex.update(surf)
            shown += 1
            age_sum += (time.perf_counter() - t_frame) * 1000
        for e in pygame.event.get():
            if e.type in (pygame.QUIT, pygame.WINDOWCLOSE):
                running = False
            elif e.type == pygame.KEYDOWN:
                if e.key in (pygame.K_q, pygame.K_ESCAPE):
                    running = False
                elif e.key == pygame.K_m and audio is not None:
                    audio.muted = not audio.muted
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
        tex.draw(dstrect=((ww - dw) // 2, (wh - dh) // 2, dw, dh))
        ren.present()  # waits for vsync
        now = time.time()
        if now - t_stat >= 5:
            print(f"STATS shown {shown / (now - t_stat):.0f} fps, capture->screen "
                  f"{age_sum / max(shown, 1):.0f} ms"
                  + (", muted" if audio is not None and audio.muted else ""),
                  file=sys.stderr, flush=True)
            t_stat, shown, age_sum = now, 0, 0.0
    stop.set()
    if audio is not None:
        audio.close()
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

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, video_names, video, audio, mode=DEFAULT_MODE, mute_audio=False):
        if self.running():
            return True
        if not video:
            self.error = "no video device selected"
            return False
        # frozen (PyInstaller) build: relaunch this same exe in viewer mode
        base = ([sys.executable, "--capture-view"] if getattr(sys, "frozen", False)
                else [sys.executable, HERE])
        cmd = base + ["--video-index", str(video_index(video, video_names)),
                      "--audio", audio or "", "--mode", mode]
        if mute_audio or not audio:
            cmd.append("--no-audio")
        self.lines, self.error, self.stats = [], "", ""
        try:
            self.proc = subprocess.Popen(
                cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
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
            self.lines.append(line[-200:])
            del self.lines[:-30]

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
    a = ap.parse_args()
    if a.list:
        print(list_devices())
        return 0
    if cv2 is None:
        print("ERROR missing packages: py -m pip install opencv-python numpy sounddevice", file=sys.stderr)
        return 3
    return run_viewer(a.video_index, a.audio, a.mode, a.no_audio)


if __name__ == "__main__":
    sys.exit(main())
