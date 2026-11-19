"""Regenerate the packaged MDL icon frames from the SVG masters."""

from __future__ import annotations

import io
from pathlib import Path

import cairosvg
from PIL import Image

AMBER = "#D89A3A"
TILE_TOP = "#1E1E26"
TILE_BOT = "#2A2A33"

# Nine nodes traced from mdl-icon-chosen-20261117.jpg, mirrored about x=512.
T = (512, 210)
UL = (253, 316)
UR = (771, 316)
IU = (512, 361)
ML = (229, 463)
MR = (795, 463)
IL = (512, 513)
LL = (255, 609)
LR = (769, 609)
NODE_R = 50
STROKE = 24
MARGIN = 41
RADIUS = round(0.18 * (1024 - 2 * MARGIN))
SHAFT = (485, 618, 53, 146)
HEAD = ((438, 764), (586, 764), (512, 854))

# 16-grid small glyph: 6 nodes + arrow, motif about 80% of the tile.
# Half-integers land on pixel centres at 16 px (and even pixels at 32 px).
S_T = (8, 3.5)
S_LT = (3.5, 6.5)
S_RT = (12.5, 6.5)
S_C = (8, 8.5)
S_LB = (3.5, 11.5)
S_RB = (12.5, 11.5)
S_NODE = 1.45
S_STROKE = 0.95


def q(
    p0: tuple[float, float],
    c: tuple[float, float],
    p1: tuple[float, float],
) -> str:
    return f"M{p0[0]},{p0[1]} Q{c[0]},{c[1]} {p1[0]},{p1[1]}"


def mid_bulge(
    p0: tuple[int, int],
    p1: tuple[int, int],
    bulge: float,
    toward: tuple[int, int] | None = None,
) -> tuple[int, int]:
    """Control point at the midpoint, offset by ``bulge`` pixels."""
    mx = (p0[0] + p1[0]) / 2
    my = (p0[1] + p1[1]) / 2
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    nx, ny = -dy, dx
    length = (nx * nx + ny * ny) ** 0.5 or 1
    nx, ny = nx / length, ny / length
    if toward is not None:
        tx, ty = toward[0] - mx, toward[1] - my
        if tx * nx + ty * ny < 0:
            nx, ny = -nx, -ny
    else:
        cx, cy = 512 - mx, 460 - my
        if cx * nx + cy * ny > 0:
            nx, ny = -nx, -ny
    return (round(mx + nx * bulge), round(my + ny * bulge))


def link(
    p0: tuple[int, int],
    p1: tuple[int, int],
    bulge: float = 18,
    toward: tuple[int, int] | None = None,
) -> str:
    return q(p0, mid_bulge(p0, p1, bulge, toward), p1)


def arrow_path() -> str:
    x, y, w, _h = SHAFT
    (lx, ly), (rx, _ry), (tx, ty) = HEAD
    return f"M{x},{y} H{x + w} V{ly} H{rx} L{tx},{ty} L{lx},{ly} H{x} Z"


def full_links() -> list[str]:
    shaft_lo = (485, 736)
    shaft_lm = (485, 718)
    shaft_ro = (538, 736)
    shaft_rm = (538, 718)
    return [
        link(T, UL, 26),
        link(T, UR, 26),
        q(UL, (512, 286), UR),
        link(UL, ML, 14),
        link(UR, MR, 14),
        link(ML, LL, 14),
        link(MR, LR, 14),
        link(UL, IU, 8),
        link(UR, IU, 8),
        link(UL, IL, 12, toward=(360, 450)),
        link(UR, IL, 12, toward=(664, 450)),
        link(ML, IU, 12, toward=(320, 400)),
        link(MR, IU, 12, toward=(704, 400)),
        link(ML, IL, 10),
        link(MR, IL, 10),
        link(LL, IL, 14, toward=(360, 580)),
        link(LR, IL, 14, toward=(664, 580)),
        q(LL, (322, 688), shaft_lo),
        q(LR, (702, 688), shaft_ro),
        q(LL, (400, 670), shaft_lm),
        q(LR, (624, 670), shaft_rm),
    ]


def tile_svg(margin: float, side: float, radius: float) -> str:
    return (
        "  <defs>\n"
        '    <linearGradient id="tile" x1="0" y1="0" x2="0" y2="1">\n'
        f'      <stop offset="0" stop-color="{TILE_TOP}"/>\n'
        f'      <stop offset="1" stop-color="{TILE_BOT}"/>\n'
        "    </linearGradient>\n"
        "  </defs>\n"
        f'  <rect x="{margin}" y="{margin}" width="{side}" height="{side}" '
        f'rx="{radius}" ry="{radius}" fill="url(#tile)"/>'
    )


def wrap(body: str, size: int) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {size} {size}" width="{size}" height="{size}">\n'
        f"{body}\n</svg>\n"
    )


def write_full(path: Path) -> None:
    links = "\n".join(f'    <path d="{d}"/>' for d in full_links())
    nodes = "\n".join(
        f'    <circle cx="{x}" cy="{y}" r="{NODE_R}"/>'
        for x, y in (T, UL, UR, IU, ML, MR, IL, LL, LR)
    )
    side = 1024 - 2 * MARGIN
    body = f"""{tile_svg(MARGIN, side, RADIUS)}
  <g fill="none" stroke="{AMBER}" stroke-width="{STROKE}" \
stroke-linecap="round" stroke-linejoin="round">
{links}
  </g>
  <g fill="{AMBER}">
{nodes}
    <path d="{arrow_path()}"/>
  </g>"""
    path.write_text(wrap(body, 1024), encoding="utf-8")


def small_links() -> list[str]:
    # Star to the centre + roof + arcs to the arrow. No side columns:
    # those close a hexagon that reads as a ring at 16 px.
    return [
        q(S_T, (5.2, 3.7), S_LT),
        q(S_T, (10.8, 3.7), S_RT),
        f"M{S_LT[0]},{S_LT[1]} L{S_C[0]},{S_C[1]}",
        f"M{S_RT[0]},{S_RT[1]} L{S_C[0]},{S_C[1]}",
        f"M{S_LB[0]},{S_LB[1]} L{S_C[0]},{S_C[1]}",
        f"M{S_RB[0]},{S_RB[1]} L{S_C[0]},{S_C[1]}",
        q(S_LB, (5.2, 13.0), (7, 13.3)),
        q(S_RB, (10.8, 13.0), (9, 13.3)),
    ]


def small_arrow() -> str:
    return "M7.1,11.6 H8.9 V13.2 H10.6 L8,15.2 L5.4,13.2 H7.1 Z"


def write_small(path: Path) -> None:
    margin = 0.65
    side = 16 - 2 * margin
    radius = round(0.18 * side, 2)
    links = "\n".join(f'    <path d="{d}"/>' for d in small_links())
    nodes = "\n".join(
        f'    <circle cx="{x}" cy="{y}" r="{S_NODE}"/>'
        for x, y in (S_T, S_LT, S_RT, S_C, S_LB, S_RB)
    )
    body = f"""{tile_svg(margin, side, radius)}
  <g fill="none" stroke="{AMBER}" stroke-width="{S_STROKE}" \
stroke-linecap="round" stroke-linejoin="round">
{links}
  </g>
  <g fill="{AMBER}">
{nodes}
    <path d="{small_arrow()}"/>
  </g>"""
    path.write_text(wrap(body, 16), encoding="utf-8")


def render_svg(svg: Path, size: int, dest: Path, supersample: int = 1) -> Image.Image:
    big = size * supersample
    png = cairosvg.svg2png(url=str(svg), output_width=big, output_height=big)
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    if supersample > 1:
        img = img.resize((size, size), Image.Resampling.BOX)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest)
    return img


def main() -> None:
    root = Path(__file__).resolve().parents[1] / "src/voxprint_common/mdl/data/icon"
    frames = root / "frames"
    full = root / "voxprint-mdl.svg"
    small = root / "voxprint-mdl-small.svg"
    write_full(full)
    write_small(small)
    images: dict[int, Image.Image] = {}
    for size in (48, 64, 128, 256, 1024):
        ss = 1 if size >= 256 else 4
        images[size] = render_svg(full, size, frames / f"{size}.png", supersample=ss)
    # Pixel-snap: 16 and 32 are 1x and 2x of the 16-grid SVG; 24 is 1.5x.
    images[16] = render_svg(small, 16, frames / "16.png", supersample=1)
    images[24] = render_svg(small, 24, frames / "24.png", supersample=2)
    images[32] = render_svg(small, 32, frames / "32.png", supersample=1)
    images[1024].save(root / "voxprint-mdl.png")
    ico_sizes = (16, 24, 32, 48, 64, 128, 256)
    images[256].save(
        root / "voxprint-mdl.ico",
        format="ICO",
        append_images=[images[n] for n in ico_sizes if n != 256],
        sizes=[(n, n) for n in ico_sizes],
    )
    print("wrote", root)


if __name__ == "__main__":
    main()
