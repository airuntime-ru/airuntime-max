"""Generate AIRUNTIME wordmark paths for logo SVG."""
from __future__ import annotations

import os
from pathlib import Path

from fontTools.misc.transform import Transform
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

FONT_PATH = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeui.ttf"
OUT = Path(__file__).resolve().parents[1] / "public" / "brand" / "_wordmark_paths.txt"

TEXT = "IRUNTIME"
TRACKING = 0.38  # em
SIZE = 44
Y = 0


def letter_paths(font: TTFont) -> list[tuple[str, str, float]]:
    glyph_set = font.getGlyphSet()
    cmap = font.getBestCmap()
    units_per_em = font["head"].unitsPerEm
    scale = SIZE / units_per_em
    tracking_units = TRACKING * units_per_em

    x = 0.0
    result: list[tuple[str, str, float]] = []
    for ch in TEXT:
        glyph_name = cmap.get(ord(ch))
        if not glyph_name:
            continue
        path_pen = SVGPathPen(glyph_set)
        transform = Transform(scale, 0, 0, scale, x, Y)
        tpen = TransformPen(path_pen, transform)
        glyph_set[glyph_name].draw(tpen)
        result.append((ch, path_pen.getCommands(), x))
        advance = glyph_set[glyph_name].width * scale + tracking_units * scale
        x += advance
    return result


def main() -> None:
    font = TTFont(str(FONT_PATH))
    paths = letter_paths(font)
    lines: list[str] = []
    for ch, d, x in paths:
        lines.append(f"<!-- {ch} x={x:.2f} -->")
        lines.append(d)
    lines.append(f"<!-- total_width={x + SIZE:.2f} -->")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
