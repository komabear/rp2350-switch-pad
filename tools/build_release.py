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
print(f"\nbuilt {zpath} ({os.path.getsize(zpath) / 1e6:.0f} MB)")
