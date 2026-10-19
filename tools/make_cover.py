"""Render the 1280x640 social preview card for mkdocs-asset-integrity."""

from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 640
OUT = pathlib.Path(__file__).resolve().parent.parent / "assets" / "cover.png"

# Warm editorial palette: espresso ground, amber accent, terracotta support.
BG = (26, 20, 16)
BG_CARD = (38, 30, 24)
BORDER = (66, 53, 43)
CREAM = (245, 236, 226)
MUTED = (150, 134, 120)
AMBER = (217, 152, 66)
TERRA = (194, 65, 12)
GREEN_OK = (138, 168, 120)
GREY_BAD = (120, 104, 92)

FONTS = "C:/Windows/Fonts"
def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(f"{FONTS}/{name}", size)

MONO = "consola.ttf"
MONO_B = "consolab.ttf"
SANS_B = "segoeuib.ttf"
SANS = "segoeui.ttf"


def text_w(draw, s, f) -> int:
    return int(draw.textbbox((0, 0), s, font=f)[2])


def main() -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # Subtle warm vignette on the right so the card does not read as flat.
    for i in range(160):
        alpha = int(10 * (1 - i / 160))
        d.ellipse(
            [W - 420 + i, -180 + i, W + 200 - i, 460 - i],
            outline=(BG[0] + alpha, BG[1] + alpha // 2, BG[2] + alpha // 3),
        )

    # ---- header -------------------------------------------------------
    d.text((64, 58), "MKDOCS PLUGIN", font=font(MONO_B, 14), fill=AMBER)
    d.text((64, 92), "Asset Integrity", font=font(SANS_B, 60), fill=CREAM)
    d.text(
        (64, 172),
        "Content-hash your CSS/JS, and prove the bytes a CDN serves are yours.",
        font=font(SANS, 22),
        fill=MUTED,
    )

    d.line([(64, 230), (W - 64, 230)], fill=BORDER, width=1)

    # ---- two cards ----------------------------------------------------
    card_y, card_h = 268, 232
    gap = 40
    card_w = (W - 128 - gap) // 2
    left_x = 64
    right_x = left_x + card_w + gap

    def card(x: int, label: str, label_color, name: str, name_color,
             detail: str, status: str, status_color, ok: bool) -> None:
        d.rounded_rectangle(
            [x, card_y, x + card_w, card_y + card_h],
            radius=10, fill=BG_CARD, outline=BORDER, width=1,
        )
        # Left accent bar: grey for the broken case, amber for the fixed one.
        d.rounded_rectangle(
            [x, card_y, x + 4, card_y + card_h],
            radius=2, fill=TERRA if ok else GREY_BAD,
        )
        d.text((x + 28, card_y + 26), label, font=font(MONO_B, 13), fill=label_color)

        d.text((x + 28, card_y + 62), "GET", font=font(MONO, 13), fill=MUTED)
        d.text((x + 28, card_y + 88), name, font=font(MONO_B, 23), fill=name_color)

        d.text((x + 28, card_y + 136), detail, font=font(MONO, 15), fill=MUTED)

        # Status pill, bottom-left of the card.
        pw = text_w(d, status, font(MONO_B, 13)) + 28
        d.rounded_rectangle(
            [x + 28, card_y + card_h - 56, x + 28 + pw, card_y + card_h - 26],
            radius=15, fill=(44, 36, 29), outline=status_color, width=1,
        )
        d.text((x + 42, card_y + card_h - 49), status, font=font(MONO_B, 13), fill=status_color)

    card(
        left_x,
        "WITHOUT FINGERPRINT",
        GREY_BAD,
        "assets/custom.css",
        (176, 158, 142),
        "URL never changes  ->  cache never misses",
        "stale",
        GREY_BAD,
        ok=False,
    )
    card(
        right_x,
        "WITH FINGERPRINT + SRI",
        AMBER,
        "assets/custom.901d2a0f.css",
        CREAM,
        'integrity="sha384-kB0qD1j4VG+M7P2K8…"',
        "verified",
        GREEN_OK,
        ok=True,
    )

    # ---- footer -------------------------------------------------------
    footer = "mkdocs 1.6.1 still ships no fingerprint   |   0 of 233 catalog plugins cover it"
    d.text((64, 548), footer, font=font(MONO, 15), fill=MUTED)

    d.text((64, 588), "pip install mkdocs-asset-integrity", font=font(MONO_B, 17), fill=AMBER)

    # Right-aligned tag so the bottom band is not empty on the right.
    tag = "MIT  ·  zero dependencies"
    d.text(
        (W - 64 - text_w(d, tag, font(MONO, 14)), 590),
        tag, font=font(MONO, 14), fill=MUTED,
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "PNG", optimize=True)
    print(f"saved {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
