#!/usr/bin/env python3
"""
Generate the app icon and splash images for the Briefcase iOS app.

Briefcase doesn't scale images: for each size its template asks for, it copies
`<icon>-<size>.png` and falls back to the template's own default -- the BeeWare
bee -- for any size that's missing.  So every size has to exist up front.

Source is the Lute logo the Tauri desktop app already ships at 1024x1024.
Run this after changing the logo; the output is committed, so a normal build
doesn't need it.

    ./ios/build_icons.py
"""

import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).parent
SOURCE = HERE.parent / "desktop/src-tauri/icons/ios/AppIcon-512@2x.png"
OUT_DIR = HERE / "icons"
PREFIX = "lute"

# Sizes from ios/build/luteios/ios/xcode/briefcase.toml.
ICON_SIZES = [20, 29, 40, 58, 60, 76, 80, 87, 120, 152, 167, 180, 1024]

# The template maps these to Splash.imageset rather than the app icon, so the
# logo is centred on a canvas instead of filling it.
SPLASH_SIZES = [640, 1280, 1920]
SPLASH_LOGO_FRACTION = 0.45

BACKGROUND = (255, 255, 255)


def load_source():
    "The logo, flattened onto white -- iOS app icons must not have an alpha channel."
    if not SOURCE.exists():
        sys.exit(f"Missing source icon: {SOURCE}")
    img = Image.open(SOURCE).convert("RGBA")
    flat = Image.new("RGB", img.size, BACKGROUND)
    flat.paste(img, mask=img.getchannel("A"))
    return flat


def write_icon(src, size):
    "Square app icon at the given size."
    out = OUT_DIR / f"{PREFIX}-{size}.png"
    src.resize((size, size), Image.LANCZOS).save(out, "PNG")
    return out


def write_splash(src, size):
    "Logo centred on a white square, rather than filling the whole frame."
    canvas = Image.new("RGB", (size, size), BACKGROUND)
    logo_px = max(1, int(size * SPLASH_LOGO_FRACTION))
    # Never upscale past the source; a soft logo looks worse than a small one.
    logo_px = min(logo_px, src.width)
    logo = src.resize((logo_px, logo_px), Image.LANCZOS)
    offset = ((size - logo_px) // 2, (size - logo_px) // 2)
    canvas.paste(logo, offset)
    out = OUT_DIR / f"{PREFIX}-{size}.png"
    canvas.save(out, "PNG")
    return out


def main():
    src = load_source()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for f in OUT_DIR.glob(f"{PREFIX}-*.png"):
        f.unlink()

    for size in ICON_SIZES:
        write_icon(src, size)
    for size in SPLASH_SIZES:
        write_splash(src, size)

    made = sorted(OUT_DIR.glob(f"{PREFIX}-*.png"), key=lambda p: p.stat().st_size)
    print(f"Wrote {len(made)} images to {OUT_DIR}:")
    print("  icons:  " + ", ".join(str(s) for s in ICON_SIZES))
    print("  splash: " + ", ".join(str(s) for s in SPLASH_SIZES))


if __name__ == "__main__":
    main()
