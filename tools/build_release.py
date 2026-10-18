"""Build the release bundle: GUI exe (PyInstaller, one folder) + firmware UF2 -> zip.

  py -m pip install pyinstaller
  py tools/build_release.py [path/to/bridge.uf2]

Without an argument the UF2 is taken from fw/build/bridge.uf2 (build the firmware first,
see README). Output: release/rp2350-switch-pad-v<version>-windows.zip and release/bridge.uf2
"""
import os
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(ROOT)

version = re.search(r'__version__ = "([^"]+)"', open("host/gui_hid.py", encoding="utf-8").read()).group(1)
uf2 = sys.argv[1] if len(sys.argv) > 1 else os.path.join("fw", "build", "bridge.uf2")
if not os.path.exists(uf2):
    sys.exit(f"firmware image not found: {uf2} (build the firmware first)")

name = "rp2350-switch-pad"
for d in ("dist", "build-exe", "release"):
    shutil.rmtree(d, ignore_errors=True)

subprocess.check_call([
    sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--noconsole",
    "--name", name, "--paths", "host", "--workpath", "build-exe", "--specpath", "build-exe",
    "--distpath", "dist",
    "--hidden-import", "pygame._sdl2.video",
    "--exclude-module", "matplotlib", "--exclude-module", "scipy", "--exclude-module", "pandas",
    "host/gui_hid.py",
])



def collect_licenses():
    """-> [(source path, path inside the zip's licenses/ folder)]: the license texts of the Python
    packages bundled into the exe (read from the installed distributions), Python's own, and the
    texts kept in this repo (pico-sdk, TinyUSB, PIO-USB)."""
    from importlib import metadata

    out = []
    for dist_name, folder in (("hidapi", "hidapi"), ("opencv-python", "opencv-python"),
                              ("numpy", "numpy"), ("sounddevice", "sounddevice"),
                              ("pycaw", "pycaw"), ("comtypes", "comtypes"),
                              ("pyserial", "pyserial"), ("pygame-ce", "pygame-ce")):
        try:
            dist = metadata.distribution(dist_name)
        except metadata.PackageNotFoundError:
            print(f"warning: {dist_name} is not installed, its license files are missing from the zip")
            continue
        for f in dist.files or []:
            base = os.path.basename(str(f)).upper()
            if str(f).replace("\\", "/").count("/") <= 2 and any(k in base for k in ("LICENSE", "COPYING", "NOTICE")):
                out.append((str(dist.locate_file(f)), f"python-packages/{folder}/{os.path.basename(str(f))}"))
    py_license = os.path.join(sys.base_prefix, "LICENSE.txt")
    if os.path.exists(py_license):
        out.append((py_license, "python-LICENSE.txt"))
    for fn in sorted(os.listdir("licenses")):
        out.append((os.path.join("licenses", fn), fn))
    out.append((os.path.join("fw", "pio_usb", "LICENSE-Waveshare-Apache-2.0"), "pio-usb-Waveshare-Apache-2.0.txt"))
    return out


os.makedirs("release")
shutil.copy(uf2, os.path.join("release", "bridge.uf2"))
bundle = f"{name}-v{version}-windows"
zpath = os.path.join("release", bundle + ".zip")
with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for dp, _dn, fn in os.walk(os.path.join("dist", name)):
        for f in fn:
            full = os.path.join(dp, f)
            z.write(full, os.path.join(bundle, os.path.relpath(full, os.path.join("dist", name))))
    z.write(uf2, os.path.join(bundle, "bridge.uf2"))
    z.write("README.md", os.path.join(bundle, "README.md"))
    z.write("CHANGELOG.md", os.path.join(bundle, "CHANGELOG.md"))
    z.write("LICENSE", os.path.join(bundle, "LICENSE"))
    z.write("THIRD_PARTY_NOTICES.md", os.path.join(bundle, "THIRD_PARTY_NOTICES.md"))
    seen = set()
    for src, arc in collect_licenses():
        if arc not in seen and os.path.exists(src):
            seen.add(arc)
            z.write(src, os.path.join(bundle, "licenses", arc))
    print(f"license files in the zip: {len(seen)}")
print(f"\nbuilt {zpath} ({os.path.getsize(zpath) / 1e6:.0f} MB)")
