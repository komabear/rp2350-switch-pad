"""Record ~5 s from the capture card and look for torn frames (a visible seam where the
top of the frame is one video frame and the bottom is the next).

Needs motion on screen and the capture window closed (the card is exclusive).
  py tools/tear_check.py [camera_index]
Writes the worst candidate to tear_candidate.png in the current folder.
"""
import sys
import time

import cv2
import numpy as np

idx = int(sys.argv[1]) if len(sys.argv) > 1 else 1
cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"YUY2"))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
cap.set(cv2.CAP_PROP_FPS, 60)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

frames, times = [], []
t0 = time.time()
while time.time() - t0 < 5:
    ok, f = cap.read()
    if ok:
        frames.append(cv2.resize(f, (480, 270), interpolation=cv2.INTER_AREA).astype(np.int16))
        times.append(time.perf_counter())
cap.release()
print(f"{len(frames)} frames in 5 s ({len(frames) / 5:.1f} fps)")
gaps = np.diff(times) * 1000
print(f"frame interval ms: median {np.median(gaps):.1f}, worst {gaps.max():.0f}, "
      f">25 ms: {(gaps > 25).sum()}")

torn, repeats, best = [], 0, (0.0, None)
for i in range(1, len(frames)):
    d = np.abs(frames[i] - frames[i - 1]).mean(axis=(1, 2))  # per-row change
    if d.mean() < 0.15:
        repeats += 1
        continue
    thr = max(d.max() * 0.15, 0.5)
    ch = d > thr
    # a clean tear = rows above a seam unchanged, rows below changed (or the reverse)
    for s in range(20, len(d) - 20):
        top, bot = ch[:s].mean(), ch[s:].mean()
        if (top < 0.05 and bot > 0.8) or (top > 0.8 and bot < 0.05):
            torn.append((i, s))
            score = abs(top - bot)
            if score > best[0]:
                best = (score, i)
            break
print(f"repeated (duplicate) frames: {repeats}")
print(f"frames that look torn: {len(torn)} of {len(frames) - 1}  e.g. {torn[:5]} (frame, seam row of 270)")
if len(frames) > 1 and np.mean([np.abs(frames[i] - frames[i - 1]).mean() for i in range(1, len(frames))]) < 0.5:
    print("NOTE: very little motion in the recording - results are not meaningful, scroll something fast")
