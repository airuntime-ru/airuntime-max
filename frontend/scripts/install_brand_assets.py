"""Install AIRuntime brand assets from the master logo PNG."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
BRAND = PUBLIC / "brand"
BACKEND_BRAND = ROOT.parent / "backend" / "src" / "assets" / "brand"
SOURCE = ROOT / "brand-source"
SRC_FULL = SOURCE / "logo-full.png"

# Fraction of image height that contains only the symbol (above the wordmark).
SYMBOL_HEIGHT_RATIO = 0.605
# Padding around trimmed content as a fraction of the longer side.
PADDING_RATIO = 0.10


def remove_near_white_background(image: Image.Image, threshold: int = 246) -> Image.Image:
    rgba = image.convert("RGBA")
    pixels = rgba.load()
    width, height = rgba.size
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if r >= threshold and g >= threshold and b >= threshold:
                pixels[x, y] = (r, g, b, 0)
    return rgba


def content_bbox(image: Image.Image, *, alpha_threshold: int = 8) -> tuple[int, int, int, int] | None:
    rgba = image.convert("RGBA")
    width, height = rgba.size
    pixels = rgba.load()
    min_x, min_y = width, height
    max_x, max_y = 0, 0
    found = False
    for y in range(height):
        for x in range(width):
            if pixels[x, y][3] >= alpha_threshold:
                found = True
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
    if not found:
        return None
    return min_x, min_y, max_x + 1, max_y + 1


def trim_to_square(image: Image.Image, padding_ratio: float = PADDING_RATIO) -> Image.Image:
    rgba = image.convert("RGBA")
    bbox = content_bbox(rgba)
    if not bbox:
        return rgba
    cropped = rgba.crop(bbox)
    w, h = cropped.size
    pad = int(max(w, h) * padding_ratio)
    side = max(w, h) + pad * 2
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    offset = ((side - w) // 2, (side - h) // 2)
    canvas.paste(cropped, offset, cropped)
    return canvas


def extract_symbol(full: Image.Image) -> Image.Image:
    w, h = full.size
    split_y = int(h * SYMBOL_HEIGHT_RATIO)
    symbol_region = full.crop((0, 0, w, split_y))
    return trim_to_square(symbol_region)


def extract_wordmark(full: Image.Image) -> Image.Image:
    w, h = full.size
    split_y = int(h * SYMBOL_HEIGHT_RATIO)
    word_region = full.crop((0, split_y, w, h))
    rgba = word_region.convert("RGBA")
    bbox = content_bbox(rgba)
    if not bbox:
        return rgba
    cropped = rgba.crop(bbox)
    pad_x = int(cropped.width * 0.04)
    pad_y = int(cropped.height * 0.12)
    canvas = Image.new(
        "RGBA",
        (cropped.width + pad_x * 2, cropped.height + pad_y * 2),
        (0, 0, 0, 0),
    )
    canvas.paste(cropped, (pad_x, pad_y), cropped)
    return canvas


def save_png(image: Image.Image, path: Path, size: tuple[int, int] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = image
    if size:
        out = image.resize(size, Image.Resampling.LANCZOS)
    out.save(path, format="PNG", optimize=True)


def save_ico(image: Image.Image, path: Path) -> None:
    sizes = [(16, 16), (32, 32), (48, 48)]
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="ICO", sizes=sizes)


def main() -> None:
    if not SRC_FULL.is_file():
        raise SystemExit(f"Missing source logo: {SRC_FULL}")

    full = remove_near_white_background(Image.open(SRC_FULL))
    symbol = extract_symbol(full)
    wordmark = extract_wordmark(full)

    save_png(symbol, BRAND / "logo-mark.png", (512, 512))
    save_png(wordmark, BRAND / "logo-wordmark.png")
    save_png(full, BRAND / "logo-full.png", (1024, 1024))

    mark_512 = Image.open(BRAND / "logo-mark.png")
    save_ico(mark_512, PUBLIC / "favicon.ico")
    save_png(mark_512, PUBLIC / "apple-touch-icon.png", (180, 180))
    save_png(mark_512, PUBLIC / "icon-192.png", (192, 192))
    save_png(mark_512, PUBLIC / "icon-512.png", (512, 512))

    BACKEND_BRAND.mkdir(parents=True, exist_ok=True)
    save_png(mark_512, BACKEND_BRAND / "logo-mark.png", (512, 512))

    print("Installed brand assets:")
    for path in [
        BRAND / "logo-mark.png",
        BRAND / "logo-wordmark.png",
        BRAND / "logo-full.png",
        PUBLIC / "favicon.ico",
        PUBLIC / "apple-touch-icon.png",
        PUBLIC / "icon-192.png",
        PUBLIC / "icon-512.png",
        BACKEND_BRAND / "logo-mark.png",
    ]:
        print(" ", path.relative_to(ROOT.parent))


if __name__ == "__main__":
    main()
