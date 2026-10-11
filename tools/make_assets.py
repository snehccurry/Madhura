"""Render Madhura's app icon into ``madhura/assets``.

The mark is the "logo" glyph of ``madhura/ui/icons.py`` — a ring with an off-centre dot, read
straight from its SVG markup — drawn as an app icon: an indigo ring (accent of the dark theme,
lit from above) and a lighter dot on a dark, slightly indigo rounded square with a soft glow and
a hairline rim. Every size is painted natively with QPainter rather than scaled down from the
largest one, and the small sizes drop the glow and rim and keep a ring of at least 2 px, so the
icon stays crisp in a 16 px title bar as well as on a 256 px desktop::

    python tools/make_assets.py                 # icon.png (512), icon.ico (16–256), icon.icns
    python tools/make_assets.py --out DIR       # write somewhere else
    python tools/make_assets.py --preview a.png # also a contact sheet of every size (for review)

Writes ``icon.png`` (512×512), ``icon.ico`` (16, 20, 24, 32, 40, 48, 64, 128, 256; 32-bit DIB
frames, PNG for 256) and, when Qt can write ICNS here, ``icon.icns`` (16–1024, PNG frames).
Output is deterministic; re-run after changing the logo or the indigo accent.
"""

from __future__ import annotations

import argparse
import os
import re
import struct
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "madhura" / "assets"

PNG_SIZE = 512
ICO_SIZES: tuple[int, ...] = (16, 20, 24, 32, 40, 48, 64, 128, 256)
#: ICNS element type → pixel size: the set iconutil writes (PNG payloads; "@2x" types repeat a 1x size).
ICNS_TYPES: tuple[tuple[bytes, int], ...] = (
    (b"icp4", 16),
    (b"icp5", 32),
    (b"ic11", 32),
    (b"ic12", 64),
    (b"ic07", 128),
    (b"ic08", 256),
    (b"ic13", 256),
    (b"ic09", 512),
    (b"ic14", 512),
    (b"ic10", 1024),
)

# Background: the dark theme's page colour, lifted towards indigo at the top.
BG_TOP = "#262A45"
BG_BOTTOM = "#0E0F13"
# Ring: the indigo accent, lighter at the top; dot: a paler tint of it.
RING_TOP = "#A3ABFF"
RING_BOTTOM = "#5D69EE"
DOT_LIGHT = "#E6E9FF"
DOT_DARK = "#A6AEFF"

#: The mark's ring (centre-line) radius as a fraction of the tile, and its stroke width.
RING_RADIUS = 0.272
RING_STROKE = 0.092


def _prepare_environment() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))


# =========================================================================== the logo


def logo_geometry() -> tuple[float, float, float, float]:
    """``(ring_r, dot_dx, dot_dy, dot_r)`` of the "logo" icon, relative to its ring radius.

    Parsed from the SVG markup in ``madhura/ui/icons.py`` (two circles on the 24×24 grid: the
    ring, then the filled dot) so the app icon follows the in-app logo."""
    from madhura.ui import icons

    circles = [
        {k: float(v) for k, v in re.findall(r'\b(cx|cy|r)="([\d.]+)"', match)}
        for match in re.findall(r"<circle\b[^>]*>", icons.svg("logo"))
    ]
    if len(circles) != 2 or any(set(c) != {"cx", "cy", "r"} for c in circles):
        raise SystemExit(f"Unexpected 'logo' icon markup: {circles!r}")
    ring, dot = circles
    r = ring["r"]
    return r, (dot["cx"] - ring["cx"]) / r, (dot["cy"] - ring["cy"]) / r, dot["r"] / r


def render_icon(size: int) -> object:
    """The app icon at *size*×*size* px (a premultiplied ARGB32 ``QImage``)."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QBrush, QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient

    _ring_r, dot_dx, dot_dy, dot_r = logo_geometry()
    s = float(size)
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    small = size <= 24

    # Tile: full bleed when small (every pixel counts), a 1/32 margin from 32 px up.
    margin = 0.0 if size < 32 else s / 32.0
    tile = QRectF(margin, margin, s - 2 * margin, s - 2 * margin)
    t = tile.width()
    radius = t * (0.2 if small else 0.225)
    shape = QPainterPath()
    shape.addRoundedRect(tile, radius, radius)
    bg = QLinearGradient(tile.topLeft(), tile.bottomLeft())
    bg.setColorAt(0.0, QColor(BG_TOP))
    bg.setColorAt(1.0, QColor(BG_BOTTOM))
    p.fillPath(shape, QBrush(bg))

    centre = tile.center()
    ring_radius = t * RING_RADIUS
    stroke = max(2.0, t * RING_STROKE)
    if small:
        # Keep a visible hole: the ring's inner edge must clear the dot by about a pixel.
        ring_radius = max(ring_radius, t * 0.29)

    if not small:
        # A soft indigo glow behind the mark, clipped to the tile.
        glow = QRadialGradient(centre, t * 0.5)
        inner = QColor(RING_BOTTOM)
        inner.setAlphaF(0.30)
        outer = QColor(RING_BOTTOM)
        outer.setAlphaF(0.0)
        glow.setColorAt(0.0, inner)
        glow.setColorAt(1.0, outer)
        p.save()
        p.setClipPath(shape)
        p.fillRect(tile, QBrush(glow))
        p.restore()
        # Hairline rim, brighter along the top edge.
        rim_w = max(1.0, s / 256.0)
        rim = QLinearGradient(tile.topLeft(), tile.bottomLeft())
        top = QColor("#FFFFFF")
        top.setAlphaF(0.16)
        bottom = QColor("#FFFFFF")
        bottom.setAlphaF(0.04)
        rim.setColorAt(0.0, top)
        rim.setColorAt(1.0, bottom)
        p.setPen(QPen(QBrush(rim), rim_w))
        p.setBrush(Qt.BrushStyle.NoBrush)
        half = rim_w / 2.0
        p.drawRoundedRect(tile.adjusted(half, half, -half, -half), radius - half, radius - half)

    # The ring.
    ring_box = QRectF(centre.x() - ring_radius, centre.y() - ring_radius, 2 * ring_radius, 2 * ring_radius)
    ring = QLinearGradient(ring_box.topLeft(), ring_box.bottomRight())
    ring.setColorAt(0.0, QColor(RING_TOP))
    ring.setColorAt(1.0, QColor(RING_BOTTOM))
    p.setPen(QPen(QBrush(ring), stroke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(centre, ring_radius, ring_radius)

    # The dot (up and to the right, as in the logo).
    dot_centre = QPointF(centre.x() + dot_dx * ring_radius, centre.y() + dot_dy * ring_radius)
    radius_dot = dot_r * ring_radius
    if small:
        radius_dot = max(radius_dot, 1.6)
        p.setBrush(QColor(DOT_DARK).lighter(112))
    else:
        dot = QRadialGradient(dot_centre - QPointF(radius_dot * 0.35, radius_dot * 0.35), radius_dot * 1.4)
        dot.setColorAt(0.0, QColor(DOT_LIGHT))
        dot.setColorAt(1.0, QColor(DOT_DARK))
        p.setBrush(QBrush(dot))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(dot_centre, radius_dot, radius_dot)
    p.end()
    return image


# =========================================================================== containers


def png_bytes(image: object) -> bytes:
    """*image* encoded as PNG by Qt."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buf, "PNG"):  # type: ignore[attr-defined]
        raise SystemExit("Qt could not encode a PNG")
    buf.close()
    return bytes(data.data())


def dib_bytes(image: object) -> bytes:
    """*image* as an ICO frame: BITMAPINFOHEADER, 32-bit BGRA rows bottom-up, then the AND mask."""
    from PySide6.QtGui import QImage

    img = image.convertToFormat(QImage.Format.Format_RGBA8888)  # type: ignore[attr-defined]
    w, h = img.width(), img.height()
    raw = bytes(img.constBits())[: img.sizeInBytes()]
    stride = img.bytesPerLine()
    rows = [bytearray(raw[y * stride : y * stride + w * 4]) for y in range(h)]
    for row in rows:  # RGBA → BGRA
        row[0::4], row[2::4] = row[2::4], row[0::4]
    pixels = b"".join(bytes(row) for row in reversed(rows))
    mask_stride = ((w + 31) // 32) * 4
    mask = bytearray()
    for row in reversed(rows):
        bits = bytearray(mask_stride)
        for x in range(w):
            if row[x * 4 + 3] == 0:  # fully transparent → set in the AND mask
                bits[x // 8] |= 0x80 >> (x % 8)
        mask += bits
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, len(pixels) + len(mask), 0, 0, 0, 0)
    return header + pixels + bytes(mask)


def ico_bytes(images: Sequence[object]) -> bytes:
    """A multi-size .ico: DIB frames below 256 px (most compatible), PNG at 256 px."""
    frames = []
    for image in images:
        size = image.width()  # type: ignore[attr-defined]
        frames.append((size, png_bytes(image) if size >= 256 else dib_bytes(image)))
    out = bytearray(struct.pack("<HHH", 0, 1, len(frames)))
    offset = 6 + 16 * len(frames)
    for size, data in frames:
        dim = 0 if size >= 256 else size
        out += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    for _size, data in frames:
        out += data
    return bytes(out)


def icns_bytes(images: dict[int, object]) -> bytes:
    """An .icns with a PNG element for every type in :data:`ICNS_TYPES`."""
    body = bytearray()
    for kind, size in ICNS_TYPES:
        data = png_bytes(images[size])
        body += kind + struct.pack(">I", len(data) + 8) + data
    return b"icns" + struct.pack(">I", len(body) + 8) + bytes(body)


def can_write_icns() -> bool:
    """True when this Qt has an ICNS image plugin (Qt Image Formats)."""
    from PySide6.QtGui import QImageWriter

    return b"icns" in {bytes(f).lower() for f in QImageWriter.supportedImageFormats()}


def _check_readable(path: Path, expected: int) -> None:
    """Read *path* back with Qt and insist on *expected* frames."""
    from PySide6.QtGui import QImageReader

    reader = QImageReader(str(path))
    count = reader.imageCount()
    if count != expected:
        raise SystemExit(f"{path.name}: Qt reads {count} frames, expected {expected}")
    for _ in range(count):
        if reader.read().isNull():
            raise SystemExit(f"{path.name}: Qt could not decode a frame ({reader.errorString()})")
        reader.jumpToNextImage()


# =========================================================================== preview


def contact_sheet(path: Path) -> None:
    """Every ICO size at 1× and 4× (nearest neighbour) on a dark and a light strip."""
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QColor, QImage, QPainter

    zoom = 4
    gap = 16
    width = gap + sum(s * zoom + gap for s in ICO_SIZES[:6]) + PNG_SIZE // 2 + gap
    strip = gap + max(ICO_SIZES[5] * zoom, PNG_SIZE // 2) + gap + ICO_SIZES[-1] + gap
    sheet = QImage(width, strip * 2, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(sheet)
    for band, colour in enumerate(("#202225", "#F3F3F3")):
        top = band * strip
        p.fillRect(QRectF(0, top, width, strip), QColor(colour))
        x = float(gap)
        for size in ICO_SIZES[:6]:
            img = render_icon(size)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            p.drawImage(QRectF(x, top + gap, size * zoom, size * zoom), img)
            x += size * zoom + gap
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawImage(QRectF(x, top + gap, PNG_SIZE // 2, PNG_SIZE // 2), render_icon(PNG_SIZE // 2))
        x = float(gap)
        y = top + strip - gap - ICO_SIZES[-1]
        for size in ICO_SIZES:
            p.drawImage(QPointF(x, y + ICO_SIZES[-1] - size), render_icon(size))
            x += size + gap
    p.end()
    sheet.save(str(path))


# =========================================================================== CLI


def write_assets(out: Path) -> list[Path]:
    """Render and write every icon file into *out*; returns the paths written."""
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    png = out / "icon.png"
    png.write_bytes(png_bytes(render_icon(PNG_SIZE)))
    _check_readable(png, 1)
    written.append(png)

    ico = out / "icon.ico"
    ico.write_bytes(ico_bytes([render_icon(s) for s in ICO_SIZES]))
    _check_readable(ico, len(ICO_SIZES))
    written.append(ico)

    icns = out / "icon.icns"
    if can_write_icns():
        icns.write_bytes(icns_bytes({s: render_icon(s) for s in sorted({s for _k, s in ICNS_TYPES})}))
        _check_readable(icns, len(ICNS_TYPES))
        written.append(icns)
    else:
        print("Qt has no ICNS writer here; skipped icon.icns", file=sys.stderr)
    return written


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="make_assets.py", description="Render Madhura's app icon (PNG, ICO, ICNS).")
    default_out = DEFAULT_OUT.relative_to(REPO_ROOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"output directory (default: {default_out})")
    parser.add_argument("--preview", type=Path, default=None, help="also write a contact sheet of every size to this PNG")
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    _prepare_environment()
    from PySide6.QtGui import QGuiApplication

    _app = QGuiApplication.instance() or QGuiApplication([sys.argv[0]])
    for path in write_assets(args.out.expanduser().resolve()):
        print(f"{path} ({path.stat().st_size:,} bytes)")
    if args.preview is not None:
        contact_sheet(args.preview.expanduser().resolve())
        print(args.preview)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
