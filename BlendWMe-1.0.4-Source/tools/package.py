"""Build dist/blendwme-<version>.zip for Blender's "Install from Disk".

    python tools/package.py

The zip holds the CONTENTS of blendwme/ (blender_manifest.toml at the zip
root), without caches. The version comes from blender_manifest.toml.
"""

import os
import re
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "blendwme")


def main():
    with open(os.path.join(ADDON, "blender_manifest.toml"), encoding="utf-8") as f:
        version = re.search(r'^version\s*=\s*"([^"]+)"', f.read(), re.M).group(1)
    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    target = os.path.join(ROOT, "dist", f"blendwme-{version}.zip")
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for folder, dirs, files in os.walk(ADDON):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in sorted(files):
                if name.endswith((".pyc", ".pyo")):
                    continue
                path = os.path.join(folder, name)
                z.write(path, os.path.relpath(path, ADDON).replace(os.sep, "/"))
                count += 1
    print(f"{target}: {count} files")


if __name__ == "__main__":
    main()
