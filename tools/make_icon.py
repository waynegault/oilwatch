"""Draw OilWatch's icon: a heated house on a navy tile, at every Windows size.

Generated rather than hand-drawn, so the whole icon is one file of geometry and
colour: the shortcut takes the 256px frame, Explorer the small ones, and a colour
change is an edit here plus a rebuild. Pillow is imported by nothing in the
package - it arrives with matplotlib, and this is a build-time tool - so this is
the only file that needs it.

    python tools/make_icon.py            # redraw assets/oilwatch.ico
    python tools/make_icon.py --check    # report the committed file's frames
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ICON = ROOT / "assets" / "oilwatch.ico"

#: Drawn at the largest frame's size and downsampled, which is where the curves
#: get their smooth edges - ImageDraw itself has none.
MASTER = 1024
SIZES = ((16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256))

TILE_TOP = (12, 38, 66)
TILE_BOTTOM = (22, 84, 120)
HOUSE = (246, 249, 252)
FLAME_TIP = (242, 118, 15)
FLAME_BASE = (251, 191, 36)
FLAME_CORE = (254, 243, 199)

INSET = 44
TILE_RADIUS = 196
ROOF = ((512, 292), (168, 486), (856, 486))
CHIMNEY = (628, 246, 712, 400)
BODY = (246, 470, 778, 846)
BODY_RADIUS = 34

#: The flame is a doorway-sized lit window, so it survives being 2 pixels wide in
#: the 16px frame - the reason it is this large against the house.
FLAME_TOP = 540
FLAME_CORE_BOX = (466, 722, 560, 838)
FLAME_CURVES = (
    ((410, 742), (372, 628), (462, 566), (520, 548)),
    ((520, 548), (600, 600), (624, 690), (614, 742)),
    ((614, 742), (606, 842), (548, 866), (512, 866)),
    ((512, 866), (452, 866), (418, 842), (410, 742)),
)


def _bezier(curve: tuple[tuple[int, int], ...], steps: int = 48) -> list[tuple[float, float]]:
    """Sample a cubic Bezier, given its four control points."""
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = curve
    points = []
    for step in range(steps + 1):
        t = step / steps
        u = 1 - t
        points.append(
            (
                u**3 * x0 + 3 * u**2 * t * x1 + 3 * u * t**2 * x2 + t**3 * x3,
                u**3 * y0 + 3 * u**2 * t * y1 + 3 * u * t**2 * y2 + t**3 * y3,
            )
        )
    return points


def _vertical_gradient(height: int, top: tuple[int, int, int], bottom: tuple[int, int, int]):
    """A ``MASTER``-wide strip running from ``top`` at its first row to ``bottom``."""
    strip = Image.new("RGB", (1, height))
    for y in range(height):
        blend = y / max(1, height - 1)
        strip.putpixel(
            (0, y),
            tuple(round(top[channel] + (bottom[channel] - top[channel]) * blend) for channel in range(3)),
        )
    return strip.resize((MASTER, height), Image.Resampling.NEAREST)


def _frame() -> Image.Image:
    """The icon at its drawn size, before the frames are cut from it.

    Built in RGBA with the corners left empty: a rounded tile only reads as one if
    what is outside it is transparent, which also keeps the 16px frame from
    looking like a hard-sided square.
    """
    icon = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))

    tile = Image.new("L", (MASTER, MASTER), 0)
    ImageDraw.Draw(tile).rounded_rectangle(
        (INSET, INSET, MASTER - INSET, MASTER - INSET), radius=TILE_RADIUS, fill=255
    )
    icon.paste(
        _vertical_gradient(MASTER, TILE_TOP, TILE_BOTTOM).convert("RGBA"), (0, 0), tile
    )

    house = Image.new("L", (MASTER, MASTER), 0)
    drawn = ImageDraw.Draw(house)
    drawn.polygon(ROOF, fill=255)
    drawn.rectangle(CHIMNEY, fill=255)
    drawn.rounded_rectangle(BODY, radius=BODY_RADIUS, fill=255)
    icon.paste(Image.new("RGBA", (MASTER, MASTER), HOUSE + (255,)), (0, 0), house)

    outline = [point for curve in FLAME_CURVES for point in _bezier(curve)]
    lit = Image.new("L", (MASTER, MASTER), 0)
    ImageDraw.Draw(lit).polygon(outline, fill=255)
    rising = _vertical_gradient(MASTER - FLAME_TOP, FLAME_TIP, FLAME_BASE).convert("RGBA")
    icon.paste(rising, (0, FLAME_TOP), lit.crop((0, FLAME_TOP, MASTER, MASTER)))

    core = Image.new("L", (MASTER, MASTER), 0)
    ImageDraw.Draw(core).ellipse(FLAME_CORE_BOX, fill=255)
    icon.paste(Image.new("RGBA", (MASTER, MASTER), FLAME_CORE + (255,)), (0, 0), core)
    return icon


def build() -> None:
    ICON.parent.mkdir(parents=True, exist_ok=True)
    _frame().resize((256, 256), Image.Resampling.LANCZOS).save(ICON, format="ICO", sizes=list(SIZES))
    print(f"wrote {ICON.relative_to(ROOT)}: {', '.join(f'{w}x{h}' for w, h in SIZES)}")


def check() -> int:
    if not ICON.exists():
        print(f"missing: {ICON.relative_to(ROOT)} - run this again without --check")
        return 1
    try:
        with Image.open(ICON) as icon:
            have = sorted(icon.info.get("sizes", set()))
    except OSError as exc:
        print(f"{ICON.relative_to(ROOT)} is not a readable icon: {exc}")
        return 1
    want = sorted(SIZES)
    print(f"{ICON.relative_to(ROOT)}: {len(have)} frames, {', '.join(f'{w}x{h}' for w, h in have)}")
    if have != want:
        print(f"expected {', '.join(f'{w}x{h}' for w, h in want)}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Draw the OilWatch icon.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="report the committed icon's frames and write nothing",
    )
    args = parser.parse_args(argv)
    if args.check:
        return check()
    build()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
