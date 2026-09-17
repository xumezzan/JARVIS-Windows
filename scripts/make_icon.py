"""Draw the application icon. Pure standard library, so it can be reviewed and rerun.

The icon is committed as a binary, which is only honest if the thing that produced it is in
the repository next to it. Run it after changing anything here:

    python scripts/make_icon.py

It writes `src/jarvis/ui/assets/jarvis.ico` with every size Windows asks for, and a PNG
preview next to it for looking at the result.
"""

import struct
import zlib
from pathlib import Path

SIZES = (16, 24, 32, 48, 64, 128, 256)
BACKGROUND = (10, 15, 28)
RING = (107, 182, 255)
GLOW = (45, 91, 158)


Colour = tuple[float, float, float]


def _mix(bottom: Colour, top: tuple[int, int, int], alpha: float) -> Colour:
    blended = [bottom[index] * (1 - alpha) + top[index] * alpha for index in range(3)]
    return blended[0], blended[1], blended[2]


def _band(distance: float, centre: float, width: float, softness: float) -> float:
    """One luminous ring: full inside its width, fading over `softness` on both sides."""
    offset = abs(distance - centre)
    if offset <= width:
        return 1.0
    if offset >= width + softness:
        return 0.0
    return 1.0 - (offset - width) / softness


def render(size: int) -> bytes:
    """The sphere from the dashboard: a dark disc with thin luminous rings around it.

    Small sizes drop the inner rings. At sixteen pixels a wireframe becomes a smudge, and a
    smudge is what the owner would be hunting for in their taskbar.
    """
    rows = bytearray()
    radius = size / 2
    edge = 1.2 / radius  # anti-aliasing of the outer circle, in normalised units
    detailed = size >= 32
    thin = 0.012 if size >= 64 else 0.02
    soft = 0.035 if size >= 64 else 0.06
    tilt = (0.9063, 0.4226)  # cos/sin of 25 degrees, for the tilted ring of the sphere
    for y in range(size):
        rows.append(0)  # PNG filter type for the row
        for x in range(size):
            dx = (x + 0.5 - radius) / radius
            dy = (y + 0.5 - radius) / radius
            distance = (dx * dx + dy * dy) ** 0.5
            disc = 1.0 if distance <= 1 - edge else max(0.0, (1 - distance) / edge)
            if disc <= 0:
                rows.extend((0, 0, 0, 0))
                continue
            colour = _mix((0.0, 0.0, 0.0), BACKGROUND, 1.0)
            colour = _mix(colour, GLOW, 0.30 * max(0.0, 1 - (distance / 0.55) ** 2))
            colour = _mix(colour, RING, _band(distance, 0.84, thin, soft))
            if detailed:
                # Two ellipses read as a globe: one lying flat, one tilted across it.
                flat = ((dx / 0.66) ** 2 + (dy / 0.26) ** 2) ** 0.5
                rx = dx * tilt[0] + dy * tilt[1]
                ry = -dx * tilt[1] + dy * tilt[0]
                crossing = ((rx / 0.30) ** 2 + (ry / 0.66) ** 2) ** 0.5
                colour = _mix(colour, RING, _band(flat, 1.0, thin * 1.4, soft * 1.6) * 0.75)
                colour = _mix(colour, RING, _band(crossing, 1.0, thin * 1.4, soft * 1.6) * 0.55)
            rows.extend(
                (
                    int(colour[0] + 0.5),
                    int(colour[1] + 0.5),
                    int(colour[2] + 0.5),
                    int(disc * 255 + 0.5),
                )
            )
    return bytes(rows)


def png(size: int, pixels: bytes) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(pixels, 9))
        + chunk(b"IEND", b"")
    )


def ico(images: list[tuple[int, bytes]]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries = bytearray()
    payloads = bytearray()
    for size, data in images:
        side = size if size < 256 else 0  # 0 means 256 in the directory entry
        entries.extend(struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(data), offset))
        payloads.extend(data)
        offset += len(data)
    return header + bytes(entries) + bytes(payloads)


def main() -> int:
    assets = Path(__file__).resolve().parent.parent / "src" / "jarvis" / "ui" / "assets"
    images = [(size, png(size, render(size))) for size in SIZES]
    (assets / "jarvis.ico").write_bytes(ico(images))
    (assets / "jarvis.png").write_bytes(dict(images)[256])
    print("wrote", assets / "jarvis.ico")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
