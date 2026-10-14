"""Synthetic label used by the built-in reviewer demo."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.verification import CANONICAL_WARNING


def sample_label_image() -> bytes:
    width, height = 900, 1180
    image = Image.new("RGB", (width, height), "#f1eadc")
    draw = ImageDraw.Draw(image)

    def font(size: int, bold: bool = False):
        candidates = (
            [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "C:/Windows/Fonts/arialbd.ttf",
                "C:/Windows/Fonts/segoeuib.ttf",
                "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                "/Library/Fonts/Arial Bold.ttf",
            ]
            if bold
            else [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "C:/Windows/Fonts/arial.ttf",
                "C:/Windows/Fonts/segoeui.ttf",
                "/System/Library/Fonts/Supplemental/Arial.ttf",
                "/Library/Fonts/Arial.ttf",
            ]
        )
        for candidate in candidates:
            if Path(candidate).exists():
                try:
                    return ImageFont.truetype(candidate, size)
                except OSError:
                    pass

        # Pillow/FreeType may also resolve DejaVu by family name.
        family = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        try:
            return ImageFont.truetype(family, size)
        except OSError:
            # Final portable fallback keeps the demo endpoint functional even
            # on hosts without the expected system font packages.
            try:
                return ImageFont.load_default(size=size)
            except TypeError:
                return ImageFont.load_default()

    def centered(text: str, y: int, size: int, bold: bool = False):
        selected = font(size, bold)
        box = draw.textbbox((0, 0), text, font=selected)
        draw.text(
            ((width - (box[2] - box[0])) / 2, y),
            text,
            fill="#111111",
            font=selected,
        )

    draw.rounded_rectangle(
        (30, 30, width - 30, height - 30),
        20,
        outline="#3b3024",
        width=4,
    )
    centered("OLD TOM DISTILLERY", 85, 50, True)
    centered("KENTUCKY STRAIGHT", 175, 32, True)
    centered("BOURBON WHISKEY", 220, 40, True)
    centered("45% Alc./Vol. (90 Proof)", 320, 28)
    centered("750 mL", 365, 27)
    centered("Bottled by Old Tom Distillery", 455, 21)
    centered("Frankfort, KY", 488, 21)

    x, y = 65, 650
    heading_font = font(18, True)
    body_font = font(18)
    draw.text((x, y), "GOVERNMENT WARNING:", fill="#111111", font=heading_font)
    cursor_x = x + draw.textlength("GOVERNMENT WARNING: ", font=heading_font)
    body = CANONICAL_WARNING.split(":", 1)[1].strip()
    for word in body.split():
        token = word + " "
        token_width = draw.textlength(token, font=body_font)
        if cursor_x + token_width > width - 65:
            y += 28
            cursor_x = 65
        draw.text((cursor_x, y), token, fill="#111111", font=body_font)
        cursor_x += token_width

    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()
