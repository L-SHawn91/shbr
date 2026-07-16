#!/usr/bin/env python3
"""Render the app's representative icon from the 100% Peak brain frame.

Reuses the same compositor as render_brain_frames.py (idle + full peak glow),
but keeps the full-color 512px composite instead of downscaling to the 44px
menubar glyph. Emits a 1024×1024 transparent PNG iconset source so make-app.sh
picks up Resources/AppIcon.icns.

Usage:
  python3 scripts/render_app_icon.py        # writes Resources/AppIcon_1024.png
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from render_brain_frames import (
    CROP_PAD,
    COMPOSE_SIZE,
    IDLE,
    PEAK,
    build_peak_glow_layer,
    draw_shbr_g1a_png,
    rgb_plate_to_rgba,
    union_bbox,
)

HERE = Path(__file__).resolve().parent
MENUBAR = HERE.parent
OUT_PNG = MENUBAR / "Resources/AppIcon_1024.png"

ICON_SIZE = 1024
ICON_MARGIN = 110  # rounded-rect breathing room around the glyph


def render_peak() -> Image.Image:
    """Full-color 100% Peak composite, cropped tight to the brain (COMPOSE_SIZE)."""
    idle = rgb_plate_to_rgba(Image.open(IDLE))
    peak = rgb_plate_to_rgba(Image.open(PEAK))
    peak_preview = draw_shbr_g1a_png(idle, build_peak_glow_layer(peak, idle), 1.0)
    bbox = union_bbox(idle, peak_preview, pad=CROP_PAD)
    idle_big = idle.crop(bbox).resize((COMPOSE_SIZE, COMPOSE_SIZE), Image.Resampling.LANCZOS)
    peak_big = peak.crop(bbox).resize((COMPOSE_SIZE, COMPOSE_SIZE), Image.Resampling.LANCZOS)
    glow = build_peak_glow_layer(peak_big, idle_big)
    return draw_shbr_g1a_png(idle_big, glow, 1.0)


def fit_icon(glyph: Image.Image) -> Image.Image:
    inner = ICON_SIZE - 2 * ICON_MARGIN
    w, h = glyph.size
    scale = inner / max(w, h)
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    scaled = glyph.resize((nw, nh), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    canvas.paste(scaled, ((ICON_SIZE - nw) // 2, (ICON_SIZE - nh) // 2), scaled)
    return canvas


def main() -> None:
    icon = fit_icon(render_peak())
    OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    icon.save(OUT_PNG)
    print(f"Wrote {OUT_PNG} ({ICON_SIZE}x{ICON_SIZE})")


if __name__ == "__main__":
    main()
