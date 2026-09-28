"""Raster assets for the deck. Regenerate with:

    python make-assets.py

pptxgenjs has no gradient fills, no repeating background and no rounded image corners, so
everything that needs one of those ships as a picture. The HTML deck uses the same cover
background, so both formats open on an identical slide.

Sizes are part of the design here. The repository refuses files over 1000 KB, and
pptxgenjs embeds an image once *per slide that uses it* - the cover background alone goes
in twice. So backgrounds are JPEG and screenshots are palette PNGs.
"""

import random
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

MAX_BLUE, MAX_VIOLET = (0, 119, 255), (123, 44, 255)
SPACE, SPACE2, SPACE3 = (5, 7, 15), (10, 17, 35), (7, 12, 26)
INK = "#0D1117"

# The bot's public link - what the QR on the closing slide opens.
BOT_URL = "https://max.ru/t403_hakaton_max_bot"


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def vertical(img, stops):
    """Top-to-bottom gradient through (position, colour) stops."""
    draw = ImageDraw.Draw(img)
    for y in range(img.height):
        t = y / (img.height - 1)
        for (t0, c0), (t1, c1) in zip(stops, stops[1:], strict=False):
            if t0 <= t <= t1:
                draw.line([(0, y), (img.width, y)], fill=lerp(c0, c1, (t - t0) / (t1 - t0)))
                break


def stars(img, count, seed, sizes=(1, 1, 2, 2, 3), alpha=(70, 215), calm=None):
    """Scatter stars. ``calm`` is a (x0, y0, x1, y1) box - fractions of the image - where
    they are dimmed to a third, so none of them lands on a letter of the copy above it."""
    rnd = random.Random(seed)
    draw = ImageDraw.Draw(img, "RGBA")
    for _ in range(count):
        x, y = rnd.randrange(0, img.width), rnd.randrange(0, img.height)
        r = rnd.choice(sizes)
        a = rnd.randint(*alpha)
        if (
            calm
            and calm[0] * img.width <= x <= calm[2] * img.width
            and (calm[1] * img.height <= y <= calm[3] * img.height)
        ):
            a //= 3
        draw.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, a))


def bloom(size, centre, radii, colour, alpha, blur):
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    cx, cy = centre
    rx, ry = radii
    ImageDraw.Draw(layer).ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=(*colour, alpha))
    return layer.filter(ImageFilter.GaussianBlur(blur))


def cover():
    """AIRuntime's deep space with the MAX gradient as light behind the product.

    The previous cover split the slide into a MAX half and an AIRuntime half; the dark half
    had nothing on it. Here the two brands share one field - the space is AIRuntime's, the
    light is MAX blue running into violet - and the product sits where they meet. Used by
    the title slide and the closing slide alike, so the deck opens and ends on one image.
    """
    w, h = 1920, 1080  # 1.5x of 1280x720: crisp on a 13.3in slide, small as JPEG
    img = Image.new("RGB", (w, h))
    vertical(img, [(0.0, SPACE), (0.55, SPACE2), (1.0, SPACE3)])
    base = img.convert("RGBA")
    base = Image.alpha_composite(
        base, bloom((w, h), (0.08 * w, 0.02 * h), (0.36 * w, 0.30 * h), (35, 136, 255), 60, 170)
    )
    base = Image.alpha_composite(
        base, bloom((w, h), (0.69 * w, 0.38 * h), (0.19 * w, 0.29 * h), MAX_BLUE, 150, 150)
    )
    base = Image.alpha_composite(
        base, bloom((w, h), (0.81 * w, 0.64 * h), (0.19 * w, 0.27 * h), MAX_VIOLET, 145, 160)
    )
    img = base.convert("RGB")
    # Finer and fainter than on the dark slides: here they sit behind 112px type.
    stars(img, 260, 11, sizes=(1, 1, 1, 2), alpha=(45, 170), calm=(0.0, 0.08, 0.5, 0.97))
    img.save(HERE / "bg-cover.jpg", quality=88, optimize=True, progressive=True)


def dark():
    w, h = 2560, 1440
    img = Image.new("RGB", (w, h))
    dd = ImageDraw.Draw(img)
    for y in range(h):
        dd.line([(0, y), (w, y)], fill=lerp(SPACE, SPACE2, min(1.0, y / (h * 0.85))))
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    db = ImageDraw.Draw(glow)
    for i in range(90, 0, -1):
        db.ellipse(
            [w * 0.5 - i * 22, -420 - i * 10, w * 0.5 + i * 22, 300 + i * 10],
            fill=(35, 136, 255, 2),
        )
        db.ellipse(
            [w * 0.12 - i * 15, h * 0.2 - i * 9, w * 0.12 + i * 15, h * 0.2 + i * 9],
            fill=(124, 108, 255, 2),
        )
    img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")
    stars(img, 520, 7)
    img.save(HERE / "bg-dark.png", optimize=True)


def rounded_mask(size, radius, tight_corner=None, scale=4):
    """Anti-aliased rounded-rectangle mask, drawn large and scaled down.

    ``tight_corner`` gives the bottom-right corner its own, smaller radius - the corner a
    chat bubble points from.
    """
    w, h = size
    big_w, big_h, big_r = w * scale, h * scale, radius * scale
    big = Image.new("L", (big_w, big_h), 0)
    ImageDraw.Draw(big).rounded_rectangle([0, 0, big_w - 1, big_h - 1], radius=big_r, fill=255)
    if tight_corner is not None:
        tight = Image.new("L", (big_w, big_h), 0)
        ImageDraw.Draw(tight).rounded_rectangle(
            [0, 0, big_w - 1, big_h - 1], radius=tight_corner * scale, fill=255
        )
        box = (big_w - big_r, big_h - big_r, big_w, big_h)
        big.paste(tight.crop(box), box[:2])
    return big.resize(size, Image.LANCZOS)


# Every mini-app screen the deck shows, owner's path first, then the customer's.
SCREENS = (
    "owner-compose",
    "owner-building",
    "owner-ready",
    "storefront",
    "booking",
    "customer-done",
    "gallery-coffee",
    "gallery-barber",
    "gallery-tutor",
    "gallery-nails",
    "gallery-lawyer",
)


def palette_png(im, path):
    im.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE).save(
        path, optimize=True
    )


def screens():
    """The mini-app screenshots with rounded corners, for the pptx phone frames.

    pptxgenjs can only crop an image to an ellipse, so the rounding is baked in here and
    the bezel is a rounded shape drawn behind it. 600px wide is ~290 dpi at the size the
    cover slide uses - plenty for UI text - and a palette PNG keeps each one a fraction of
    the original.
    """
    for name in SCREENS:
        src = Image.open(HERE / f"shot-{name}.png").convert("RGB")
        width = 600
        im = src.resize((width, round(src.height * width / src.width)), Image.LANCZOS)
        # 16/268 of the frame width is the corner the CSS phone uses; keep them identical.
        im.putalpha(rounded_mask(im.size, round(width * 16 / 254)))
        palette_png(im, HERE / f"screen-{name}.png")


def lead_card():
    """The owner's lead card on slide 10, cut from the real screen: rounded like .lead-card img
    (14px on a 318px-wide image) so the pptx can lay it on the same pale frame."""
    im = Image.open(HERE / "shot-owner-lead-card.png").convert("RGB")
    im.putalpha(rounded_mask(im.size, round(im.width * 14 / 318)))
    palette_png(im, HERE / "card-owner-lead.png")


def bubble():
    """The owner's message on the cover: a MAX-gradient bubble with one tight corner.

    Exactly 2x the 272x110 box .bubble occupies in deck.html, so the pptx can place it on
    the same coordinates; the text sits on top as an editable text box.
    """
    w, h = 544, 220
    grad = Image.new("RGB", (w, h))
    px = grad.load()
    for y in range(h):
        for x in range(w):
            t = min(1.0, max(0.0, (x / w) * 0.72 + (y / h) * 0.28))
            px[x, y] = lerp(MAX_BLUE, MAX_VIOLET, t)
    grad.putalpha(rounded_mask((w, h), 40, tight_corner=10))
    grad.save(HERE / "bubble-owner.png", optimize=True)


def qr():
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=14, border=1)
    code.add_data(BOT_URL)
    code.make(fit=True)
    code.make_image(fill_color=INK, back_color="white").convert("RGB").save(
        HERE / "qr-bot.png", optimize=True
    )


def mark():
    """The real AIRuntime mark, shrunk: it shows at under half an inch."""
    src = Image.open(REPO / "frontend" / "public" / "brand" / "logo-mark.png").convert("RGBA")
    src.resize((160, 160), Image.LANCZOS).save(HERE / "mark-airuntime.png", optimize=True)


if __name__ == "__main__":
    cover()
    dark()
    screens()
    lead_card()
    bubble()
    qr()
    mark()
    print(
        "wrote bg-cover.jpg, bg-dark.png, screen-*.png, card-owner-lead.png, bubble-owner.png, qr-bot.png, "
        "mark-airuntime.png"
    )
