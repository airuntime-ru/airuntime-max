"""Trace AIRUNTIME logo PNG into layered SVG paths."""
from __future__ import annotations

import colorsys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "brand-source" / "logo-full.png"
OUT_DIR = ROOT / "public" / "brand"


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def contour_to_path(contour: np.ndarray, scale_x: float, scale_y: float, offset_x: float, offset_y: float) -> str:
    pts = contour.reshape(-1, 2)
    if len(pts) < 3:
        return ""
    x0, y0 = pts[0]
    parts = [f"M{x0 * scale_x + offset_x:.2f},{y0 * scale_y + offset_y:.2f}"]
    for x, y in pts[1:]:
        parts.append(f"L{x * scale_x + offset_x:.2f},{y * scale_y + offset_y:.2f}")
    parts.append("Z")
    return " ".join(parts)


def trace_region(
    img_bgr: np.ndarray,
    y0: int,
    y1: int,
    min_area: float,
    epsilon: float,
    scale: float,
    offset_y: float,
) -> list[tuple[str, str, float]]:
    region = img_bgr[y0:y1, :]
    h, w = region.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    white = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    mask[white < 250] = 255

    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)

    quant = region.copy()
    quant[mask == 0] = (255, 255, 255)
    pixels = quant.reshape(-1, 3)
    pixels = pixels[np.any(pixels < 250, axis=1)]
    if len(pixels) == 0:
        return []

    pixels_f = np.float32(pixels)
    _compactness, labels, centers = cv2.kmeans(
        pixels_f, 12, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 0.5), 5, cv2.KMEANS_PP_CENTERS
    )

    layers: list[tuple[str, str, float]] = []
    for center in centers:
        b, g, r = [int(v) for v in center]
        if r > 245 and g > 245 and b > 245:
            continue
        color = (r, g, b)
        hex_color = rgb_to_hex(color)
        color_mask = cv2.inRange(region, (b - 18, g - 18, r - 18), (b + 18, g + 18, r + 18))
        color_mask = cv2.bitwise_and(color_mask, mask)
        contours, _ = cv2.findContours(color_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < min_area:
                continue
            approx = cv2.approxPolyDP(contour, epsilon, True)
            path = contour_to_path(approx, scale, scale, 0, offset_y)
            if not path:
                continue
            lum = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)[2]
            layers.append((hex_color, path, lum))
    layers.sort(key=lambda item: item[2])
    return layers


def build_svg(
    mark_layers: list[tuple[str, str, float]],
    word_layers: list[tuple[str, str, float]],
    view_w: int,
    view_h: int,
    text_fill: str,
) -> str:
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {view_w} {view_h}" fill="none" role="img" aria-label="AIRuntime">',
        "  <defs>",
        '    <linearGradient id="ar-word-a" x1="0%" y1="100%" x2="100%" y2="0%">',
        '      <stop stop-color="#4FC3F7"/>',
        '      <stop offset="1" stop-color="#1E88E5"/>',
        "    </linearGradient>",
        "  </defs>",
        '  <g id="mark">',
    ]
    for color, path, _ in mark_layers:
        lines.append(f'    <path d="{path}" fill="{color}" fill-opacity="0.95"/>')
    lines.append("  </g>")
    lines.append('  <g id="wordmark">')
    for color, path, lum in word_layers:
        fill = text_fill if lum < 0.45 else color
        if lum >= 0.45 and color.lower() in {"#4db5e8", "#4eb4e7", "#4cb3e6", "#50b6ea"}:
            fill = "url(#ar-word-a)"
        lines.append(f'    <path d="{path}" fill="{fill}"/>')
    lines.append("  </g>")
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def main() -> None:
    img = cv2.imread(str(SRC), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"Cannot read {SRC}")

    h, w = img.shape[:2]
    mark_split = int(h * 0.72)
    target_w = 640
    scale = target_w / w
    mark_h = int(mark_split * scale)
    word_h = int((h - mark_split) * scale) + 20
    view_h = mark_h + word_h

    mark_layers = trace_region(img, 0, mark_split, min_area=180, epsilon=2.8, scale=scale, offset_y=0)
    word_layers = trace_region(
        img, mark_split, h, min_area=40, epsilon=1.2, scale=scale, offset_y=mark_h - int(12 * scale)
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "logo.svg").write_text(
        build_svg(mark_layers, word_layers, target_w, view_h, "#0B1220"), encoding="utf-8"
    )
    (OUT_DIR / "logo-dark.svg").write_text(
        build_svg(mark_layers, word_layers, target_w, view_h, "#E8EEF7"), encoding="utf-8"
    )

    mark_only = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" fill="none" role="img" aria-label="AIRuntime">',
        '  <g transform="translate(0 8)">',
    ]
    mark_scale = 512 / target_w
    for color, path, _ in mark_layers:
        mark_only.append(
            f'    <path d="{path}" fill="{color}" fill-opacity="0.95" transform="scale({mark_scale:.4f})"/>'
        )
    mark_only.append("  </g>")
    mark_only.append("</svg>")
    (OUT_DIR / "logo-mark.svg").write_text("\n".join(mark_only) + "\n", encoding="utf-8")
    print("layers", len(mark_layers), len(word_layers))
    print("wrote", OUT_DIR)


if __name__ == "__main__":
    main()
