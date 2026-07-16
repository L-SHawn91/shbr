#!/usr/bin/env python3
"""Render menubar BrainFrames — must match shbr_g1_route_a_activation_raster.js.

Uses lock idle + act100 peak at compositing resolution, then crops and
downscales. Idle PNG is RGB (paper bg) → alpha mask derived before glow clip.

Usage:
  python3 scripts/render_brain_frames.py [--write-swift]
"""
from __future__ import annotations

import argparse
import base64
import io
import math
import os
import sys
from pathlib import Path

from PIL import Image, ImageChops

HERE = Path(__file__).resolve().parent
MENUBAR = HERE.parent

# Source PNGs for the brain glyph. These are regeneration inputs only — the
# rendered frames are already embedded in BrainFrames.swift (committed), so the
# app builds and runs without them. Point SHBR_BRAIN_ASSETS at the directory
# holding the lock/ source art, or drop the two PNGs into apps/menubar-macos/
# assets/brain/ (the repo-relative fallback below).
ASSETS = Path(os.environ.get("SHBR_BRAIN_ASSETS", MENUBAR / "assets/brain"))

IDLE = ASSETS / "lock/shbr_g1_route_a_idle.png"
PEAK = ASSETS / "lock/activation_edits/shbr_g1a_edit_act100.png"
OUT_DIR = MENUBAR / "scripts/.generated_frames"
SWIFT = MENUBAR / "Sources/SHawnBrain/BrainFrames.swift"

COMPOSE_SIZE = 512  # same as production preview canvas
OUTPUT_SIZE = 44    # 22pt @2x — slightly bigger menubar glyph
OUTPUT_MARGIN = 5   # transparent breathing room at the edges
CROP_PAD = 4        # keep full brain (incl. top of head) inside the crop
ZOOM = 1.0          # no tightening → don't clip the head extremes
BG_LUM_THRESH = 45  # lock PNGs sit on near-black, not Warm Paper
FRAME_COUNT = 16    # Idle→Peak gradations; more = smoother menubar pulse
PEAK_GAIN = 1.4     # extra glow multiplier at 100% (idle unchanged); >1 = brighter peak
PAPER = (248, 246, 241)
PAPER_THRESH = 28


def eased_levels(n: int) -> list[int]:
    """Raised-cosine glow ramp 0→100 (%). Samples cluster near idle and peak so
    the ping-pong turnaround reads as a smooth breath, not a hard bounce."""
    if n < 2:
        return [0, 100]
    return [round(50 * (1 - math.cos(math.pi * i / (n - 1)))) for i in range(n)]


def paper_distance(r: int, g: int, b: int) -> int:
    return abs(r - PAPER[0]) + abs(g - PAPER[1]) + abs(b - PAPER[2])


def rgb_plate_to_rgba(img: Image.Image) -> Image.Image:
    """Lock plates: dark margin + brain. Transparent where background is near-black."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    out = Image.new("RGBA", (w, h))
    src = rgb.load()
    dst = out.load()
    for y in range(h):
        for x in range(w):
            r, g, b = src[x, y]
            lum = r + g + b
            if lum <= BG_LUM_THRESH:
                dst[x, y] = (0, 0, 0, 0)
            elif paper_distance(r, g, b) < PAPER_THRESH:
                dst[x, y] = (0, 0, 0, 0)
            else:
                dst[x, y] = (r, g, b, 255)
    return out


def build_peak_glow_layer(peak: Image.Image, idle: Image.Image) -> Image.Image:
    """canvas destination-in: peak clipped to idle alpha."""
    glow = peak.copy()
    mask = ImageChops.multiply(glow.getchannel("A"), idle.getchannel("A"))
    glow.putalpha(mask)
    return glow


def draw_shbr_g1a_png(idle: Image.Image, glow_layer: Image.Image, intensity: float, gain: float = 1.0) -> Image.Image:
    """Mirror drawShbrG1aPng: idle + lighter(glow × intensity × gain).

    `gain` overdrives the additive glow (>1 = brighter). Intensity is clamped at
    1.0, so the peak frame can't get brighter by intensity alone — gain is how the
    100% frame is pushed hotter than a linear ramp."""
    a = min(1.0, max(0.0, intensity))
    out = idle.copy()
    if a <= 0.001:
        return out

    glow = glow_layer
    if a < 0.999:
        r, g, b, ga = glow.split()
        ga = ga.point(lambda v: int(v * a))
        glow = Image.merge("RGBA", (r, g, b, ga))

    base_px = out.load()
    glow_px = glow.load()
    w, h = out.size
    for y in range(h):
        for x in range(w):
            br, bg, bb, ba = base_px[x, y]
            if ba == 0:
                continue
            gr, gg, gb, ga = glow_px[x, y]
            if ga == 0:
                continue
            k = ga / 255.0 * gain
            base_px[x, y] = (
                min(255, int(br + gr * k)),
                min(255, int(bg + gg * k)),
                min(255, int(bb + gb * k)),
                ba,
            )
    return out


def zoom_bbox(bbox: tuple[int, int, int, int], zoom: float, max_w: int, max_h: int) -> tuple[int, int, int, int]:
    """Shrink crop window around center (zoom>1 → tighter)."""
    if zoom <= 1.0:
        return bbox
    x0, y0, x1, y1 = bbox
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    w, h = (x1 - x0) / zoom, (y1 - y0) / zoom
    nx0 = int(round(cx - w / 2))
    ny0 = int(round(cy - h / 2))
    nx1 = int(round(cx + w / 2))
    ny1 = int(round(cy + h / 2))
    nx0 = max(0, nx0)
    ny0 = max(0, ny0)
    nx1 = min(max_w, nx1)
    ny1 = min(max_h, ny1)
    return nx0, ny0, nx1, ny1


def union_bbox(*imgs: Image.Image, pad: int = 0) -> tuple[int, int, int, int]:
    boxes = []
    for img in imgs:
        bb = img.getchannel("A").getbbox()
        if bb:
            boxes.append(bb)
    if not boxes:
        return 0, 0, imgs[0].width, imgs[0].height
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(imgs[0].width, x1 + pad)
    y1 = min(imgs[0].height, y1 + pad)
    return x0, y0, x1, y1


def fit_menubar_canvas(img: Image.Image, size: int = OUTPUT_SIZE, margin: int = OUTPUT_MARGIN) -> Image.Image:
    """Scale content to fill menubar slot — no letterbox dead space."""
    inner = max(1, size - 2 * margin)
    w, h = img.size
    scale = inner / max(w, h)
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    scaled = img.resize((nw, nh), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(scaled, ((size - nw) // 2, (size - nh) // 2), scaled)
    return canvas


def png_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def render_all(levels: list[int]) -> list[tuple[int, Image.Image, str]]:
    if not IDLE.is_file() or not PEAK.is_file():
        sys.exit(
            "Missing brain source PNGs — set SHBR_BRAIN_ASSETS to the dir holding "
            "them, or place them under apps/menubar-macos/assets/brain/.\n"
            f"  expected: {IDLE}\n            {PEAK}\n"
            "(Regeneration only — the committed BrainFrames.swift already ships "
            "the rendered frames.)"
        )

    idle_src = Image.open(IDLE)
    peak_src = Image.open(PEAK)
    idle_rgba = rgb_plate_to_rgba(idle_src)
    peak_rgba = rgb_plate_to_rgba(peak_src)
    glow_full = build_peak_glow_layer(peak_rgba, idle_rgba)
    peak_preview = draw_shbr_g1a_png(idle_rgba, glow_full, 1.0)
    bbox = union_bbox(idle_rgba, peak_preview, pad=CROP_PAD)
    bbox = zoom_bbox(bbox, ZOOM, idle_rgba.width, idle_rgba.height)

    idle_big = idle_rgba.crop(bbox).resize((COMPOSE_SIZE, COMPOSE_SIZE), Image.Resampling.LANCZOS)
    peak_big = peak_rgba.crop(bbox).resize((COMPOSE_SIZE, COMPOSE_SIZE), Image.Resampling.LANCZOS)
    glow_layer = build_peak_glow_layer(peak_big, idle_big)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[tuple[int, Image.Image, str]] = []
    for level in levels:
        a = level / 100.0
        # brighten toward the peak: gain 1.0 at idle → PEAK_GAIN at 100%, so only
        # the hot end blooms and the low frames stay as-is (no idle washout).
        gain = 1.0 + (PEAK_GAIN - 1.0) * a
        composed = draw_shbr_g1a_png(idle_big, glow_layer, a, gain)
        frame = fit_menubar_canvas(composed)
        frame.save(OUT_DIR / f"brain_g1a_{level:03d}.png")
        results.append((level, frame, png_b64(frame)))
    return results


def write_swift(frames: list[tuple[int, Image.Image, str]]) -> None:
    lines = [
        "import AppKit",
        "",
        "// SHbr G1 Route A — same compositor as shbr_g1_route_a_activation_raster.js",
        "// Regenerate: python3 scripts/render_brain_frames.py --write-swift",
        "enum BrainFrames {",
        "    static let pointSize = NSSize(width: 22, height: 22)",
        "",
        f"    static let count = {len(frames)}",
        "",
        "    static let images: [NSImage] = base64.map { b64 in",
        "        guard let data = Data(base64Encoded: b64), let img = NSImage(data: data) else {",
        "            return NSImage(size: pointSize)",
        "        }",
        "        img.size = pointSize",
        "        img.isTemplate = false",
        "        return img",
        "    }",
        "",
        "    /// Levels: " + ", ".join(f"{l}%" for l, _, _ in frames),
        "    private static let base64: [String] = [",
    ]
    for level, _, b64 in frames:
        lines.append(f'        // {level}%')
        lines.append(f'        "{b64}",')
    lines.extend(["    ]", "}", ""])
    SWIFT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {SWIFT}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-swift", action="store_true")
    parser.add_argument("--frames", type=int, default=FRAME_COUNT,
                        help=f"number of Idle→Peak gradations (default {FRAME_COUNT})")
    args = parser.parse_args()
    frames = render_all(eased_levels(args.frames))
    for level, _, b64 in frames:
        print(f"{level:3d}% -> {OUT_DIR}/brain_g1a_{level:03d}.png ({len(b64)} b64 chars)")
    if args.write_swift:
        write_swift(frames)


if __name__ == "__main__":
    main()
