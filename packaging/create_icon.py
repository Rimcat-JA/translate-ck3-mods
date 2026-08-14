from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in ("arialbd.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 8, 248, 248), radius=48, fill="#27364b", outline="#d9b45b", width=8)
    draw.ellipse((151, 38, 220, 107), fill="#d6534d")
    draw.polygon([(44, 72), (69, 38), (95, 72), (128, 30), (161, 72), (188, 38), (214, 72), (203, 105), (55, 105)], fill="#d9b45b")
    draw.rounded_rectangle((52, 94, 204, 117), radius=7, fill="#f4df9d")
    font = load_font(92)
    label = "TR"
    box = draw.textbbox((0, 0), label, font=font, stroke_width=2)
    width = box[2] - box[0]
    draw.text(((256 - width) / 2, 115), label, font=font, fill="white", stroke_width=2, stroke_fill="#172231")
    image.save(output, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"Icon: {output}")


if __name__ == "__main__":
    main()
