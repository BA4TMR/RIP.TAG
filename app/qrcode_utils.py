"""QR code rendering helpers (server-side, no third-party CDN)."""

from __future__ import annotations

import base64
import io

import qrcode
from qrcode.constants import ERROR_CORRECT_M


def make_qr(content: str, *, box_size: int = 10, border: int = 4) -> qrcode.QRCode:
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(content)
    qr.make(fit=True)
    return qr


def qr_png_data_uri(content: str, *, box_size: int = 12) -> str:
    image = make_qr(content, box_size=box_size).make_image(fill_color="#111111", back_color="#ffffff")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def qr_svg_markup(content: str, *, box_size: int = 8) -> str:
    import qrcode.image.svg

    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=box_size,
        border=2,
        image_factory=qrcode.image.svg.SvgPathImage,
    )
    qr.add_data(content)
    qr.make(fit=True)
    image = qr.make_image()
    buffer = io.BytesIO()
    image.save(buffer)
    return buffer.getvalue().decode("utf-8")
