#!/usr/bin/env python3
"""Render the outbox.cafe social avatar.

The mark is intentionally simple enough to survive round social crops and tiny
favicons: a flat screenprint-style Pancake peeking out of an envelope, with no
AI-ish fake lettering or over-rendered fur.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "favicon.png"
SIZE = 1024
SCALE = 4

INK = "#172027"
PAPER = "#fff4d4"
PAPER_DARK = "#ead19d"
PAPER_SHADOW = "#c58d58"
ORANGE = "#e46f2d"
BLACK = "#101822"
CREAM = "#fff9e7"
PINK = "#e97574"
TEAL = "#49b5a5"
MINT = "#bfe8cf"
RED = "#d94d3d"
GOLD = "#f0b84f"


def sx(value: float) -> int:
    return round(value * SCALE)


def pt(point: tuple[float, float]) -> tuple[int, int]:
    return sx(point[0]), sx(point[1])


def box(values: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    return tuple(sx(v) for v in values)  # type: ignore[return-value]


def polygon(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    *,
    fill: str,
    outline: str | None = INK,
    width: int = 10,
) -> None:
    draw.polygon([pt(p) for p in points], fill=fill)
    if outline and width:
        draw.line([pt(p) for p in [*points, points[0]]], fill=outline, width=sx(width), joint="curve")


def line(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    *,
    fill: str = INK,
    width: int = 8,
) -> None:
    draw.line([pt(p) for p in points], fill=fill, width=sx(width), joint="curve")


def ellipse(
    draw: ImageDraw.ImageDraw,
    bounds: tuple[float, float, float, float],
    *,
    fill: str,
    outline: str | None = INK,
    width: int = 10,
) -> None:
    draw.ellipse(box(bounds), fill=fill)
    if outline and width:
        draw.ellipse(box(bounds), outline=outline, width=sx(width))


def add_paper_grain(img: Image.Image) -> None:
    grain = Image.new("RGBA", img.size, (0, 0, 0, 0))
    g = ImageDraw.Draw(grain)
    for i in range(900):
        x = (i * 73 + 19) % (SIZE * SCALE)
        y = (i * 151 + 47) % (SIZE * SCALE)
        alpha = 11 if i % 5 else 18
        g.rectangle((x, y, x + SCALE, y + SCALE), fill=(23, 32, 39, alpha))
    img.alpha_composite(grain)


def draw_background(draw: ImageDraw.ImageDraw) -> None:
    draw.rectangle((0, 0, sx(SIZE), sx(SIZE)), fill="#f9ebc7")
    ellipse(draw, (56, 56, 968, 968), fill="#15343a", outline=INK, width=16)
    ellipse(draw, (91, 91, 933, 933), fill="#f7dfad", outline="#0f2930", width=6)

    # Offset registration rings, deliberately rough but restrained.
    draw.arc(box((121, 126, 903, 908)), 198, 338, fill=RED, width=sx(7))
    draw.arc(box((116, 112, 914, 910)), 20, 159, fill=TEAL, width=sx(7))

    for angle in range(0, 360, 15):
        r1 = 436
        r2 = 463 if angle % 30 == 0 else 451
        cx = cy = 512
        a = math.radians(angle)
        line(
            draw,
            [
                (cx + math.cos(a) * r1, cy + math.sin(a) * r1),
                (cx + math.cos(a) * r2, cy + math.sin(a) * r2),
            ],
            fill="#254b4e",
            width=3,
        )


def draw_back_flap(draw: ImageDraw.ImageDraw) -> None:
    polygon(draw, [(512, 186), (168, 470), (856, 470)], fill=GOLD, outline=INK, width=13)
    polygon(draw, [(512, 226), (236, 454), (788, 454)], fill=ORANGE, outline=None, width=0)
    line(draw, [(512, 186), (168, 470), (856, 470), (512, 186)], width=13)


def draw_cat(draw: ImageDraw.ImageDraw) -> None:
    # Ears sit behind the head but over the open envelope.
    polygon(draw, [(282, 364), (337, 139), (441, 346)], fill=BLACK, outline=INK, width=12)
    polygon(draw, [(742, 364), (687, 139), (583, 346)], fill=BLACK, outline=INK, width=12)
    polygon(draw, [(318, 327), (344, 209), (403, 330)], fill=PINK, outline=None, width=0)
    polygon(draw, [(706, 327), (680, 209), (621, 330)], fill=PINK, outline=None, width=0)
    polygon(draw, [(341, 284), (357, 219), (390, 298)], fill="#ffd1c0", outline=None, width=0)
    polygon(draw, [(683, 284), (667, 219), (634, 298)], fill="#ffd1c0", outline=None, width=0)

    ellipse(draw, (280, 190, 744, 604), fill=CREAM, outline=INK, width=14)

    # Calico blocks: flat and graphic, intentionally not furry.
    polygon(draw, [(282, 202), (440, 198), (489, 382), (415, 460), (303, 421)], fill=ORANGE, outline=None, width=0)
    polygon(draw, [(353, 194), (488, 190), (496, 352), (438, 342), (406, 268)], fill=BLACK, outline=None, width=0)
    polygon(draw, [(523, 191), (651, 184), (741, 276), (704, 445), (604, 396), (577, 272)], fill=BLACK, outline=None, width=0)
    polygon(draw, [(633, 209), (740, 287), (720, 426), (638, 393), (606, 284)], fill=ORANGE, outline=None, width=0)
    polygon(draw, [(459, 191), (533, 189), (512, 385)], fill=CREAM, outline=None, width=0)

    # Reassert the head contour after the patches.
    draw.ellipse(box((280, 190, 744, 604)), outline=INK, width=sx(14))

    ellipse(draw, (372, 353, 444, 421), fill=MINT, outline=INK, width=7)
    ellipse(draw, (580, 353, 652, 421), fill=MINT, outline=INK, width=7)
    ellipse(draw, (397, 374, 427, 417), fill=INK, outline=None, width=0)
    ellipse(draw, (597, 374, 627, 417), fill=INK, outline=None, width=0)
    ellipse(draw, (402, 375, 414, 389), fill=CREAM, outline=None, width=0)
    ellipse(draw, (602, 375, 614, 389), fill=CREAM, outline=None, width=0)
    line(draw, [(354, 350), (452, 347)], width=7)
    line(draw, [(572, 347), (670, 350)], width=7)

    polygon(draw, [(512, 440), (482, 422), (542, 422)], fill=PINK, outline=INK, width=6)
    line(draw, [(512, 443), (512, 468), (492, 487)], width=6)
    line(draw, [(512, 468), (534, 487)], width=6)

    for y in (447, 477):
        line(draw, [(383, y), (278, y - 18)], width=4)
        line(draw, [(641, y), (746, y - 18)], width=4)
    line(draw, [(383, 493), (289, 528)], width=4)
    line(draw, [(641, 493), (735, 528)], width=4)

    ellipse(draw, (344, 540, 457, 652), fill=CREAM, outline=INK, width=11)
    ellipse(draw, (567, 540, 680, 652), fill=CREAM, outline=INK, width=11)
    for x in (384, 419, 607, 642):
        line(draw, [(x, 606), (x - 4, 637)], width=5)


def draw_envelope_front(draw: ImageDraw.ImageDraw) -> None:
    polygon(draw, [(166, 452), (858, 452), (858, 828), (166, 828)], fill=PAPER, outline=INK, width=14)
    polygon(draw, [(166, 452), (512, 652), (166, 828)], fill="#f4dea9", outline=INK, width=10)
    polygon(draw, [(858, 452), (512, 652), (858, 828)], fill="#f1d39b", outline=INK, width=10)
    polygon(draw, [(166, 828), (512, 594), (858, 828)], fill=CREAM, outline=INK, width=12)

    # Airmail corner stripes read as mail, not fake stamp text.
    for i, color in enumerate((RED, TEAL, RED, TEAL, RED)):
        x = 211 + i * 31
        line(draw, [(x, 490), (x + 78, 442)], fill=color, width=12)

    # Small postmark-like geometry without any bogus letters.
    ellipse(draw, (688, 663, 796, 771), fill="#00000000", outline=INK, width=7)
    line(draw, [(707, 708), (777, 708)], width=5)
    line(draw, [(722, 732), (762, 684)], width=5)

    # A grounded shadow keeps the icon from floating at thumbnail sizes.
    draw.arc(box((229, 821, 795, 889)), 3, 177, fill="#91683f", width=sx(11))


def main() -> None:
    out = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else OUT
    img = Image.new("RGBA", (sx(SIZE), sx(SIZE)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    draw_background(draw)
    draw_back_flap(draw)
    draw_cat(draw)
    draw_envelope_front(draw)
    add_paper_grain(img)

    img = img.filter(ImageFilter.UnsharpMask(radius=sx(1.1), percent=80, threshold=2))
    img = img.resize((SIZE, SIZE), Image.Resampling.LANCZOS)
    img.save(out, "PNG", optimize=True)
    print(out)


if __name__ == "__main__":
    main()
