"""
One-off dev script: renders Mochi's actual PixelFaceWidget offscreen to
produce the theme/expression reference images used in README.md.

Not part of the app itself and not covered by the test suite - it's a
documentation tool. Run with:

    QT_QPA_PLATFORM=offscreen python scripts/render_readme_assets.py

Output goes to assets/readme/*.png (git-tracked, small vector-drawn PNGs -
these are what README.md embeds, not screenshots of a running app, so they
stay perfectly in sync with the real renderer instead of going stale).
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtWidgets import QApplication

from app.character.pixel_face import PixelFaceWidget
from app.character.state_machine import CharacterState
from app.character.theme import THEME_ORDER, THEMES

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "readme")
FACE_SIZE = 220
PADDING = 28
LABEL_H = 34
BG = (24, 24, 28, 255)  # a neutral dark canvas so every theme's glow reads clearly

# The 16-expression reference sheet (spec section 9 / pixel_face.py docstring),
# in the same order the module's own docstring lists them.
EXPRESSIONS = [
    ("Idle", CharacterState.IDLE),
    ("Happy", CharacterState.HAPPY),
    ("Sad", CharacterState.SAD),
    ("Angry", CharacterState.ANGRY),
    ("Confused", CharacterState.CONFUSED),
    ("Surprised", CharacterState.SURPRISED),
    ("Thinking", CharacterState.THINKING),
    ("Sleepy", CharacterState.SLEEPY),
    ("Sleeping", CharacterState.SLEEP),
    ("Talking", CharacterState.TALKING),
    ("Excited", CharacterState.EXCITED),
    ("Alert", CharacterState.ALERT),
    ("Blush", CharacterState.BLUSH),
    ("Shy", CharacterState.SHY),
    ("Heart", CharacterState.HEART),
    ("Wink", CharacterState.WINK),
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if os.path.exists(candidate):
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def render_face(state: CharacterState, theme_key: str) -> Image.Image:
    """Render one PixelFaceWidget frame to a PIL Image (RGBA)."""
    widget = PixelFaceWidget(theme_key=theme_key)
    widget.resize(FACE_SIZE, FACE_SIZE)
    widget.set_state(state)
    widget.set_cursor_hint(None, None)  # centered pupils, not mid-cursor-follow

    # Advance the spring-smoothed animation to its settled target without
    # crossing the ~2.5s minimum blink interval, so every render is a clean
    # eyes-open frame rather than an arbitrary mid-blink one.
    for _ in range(40):
        widget.tick(0.02)

    pixmap = widget.grab()
    buffer = QBuffer()
    buffer.open(QIODevice.ReadWrite)
    pixmap.save(buffer, "PNG")
    data = bytes(buffer.data())
    buffer.close()

    from io import BytesIO

    return Image.open(BytesIO(data)).convert("RGBA")


def _labeled_tile(face_img: Image.Image, label: str) -> Image.Image:
    tile = Image.new("RGBA", (FACE_SIZE + PADDING, FACE_SIZE + PADDING + LABEL_H), BG)
    tile.alpha_composite(face_img, (PADDING // 2, PADDING // 2))
    draw = ImageDraw.Draw(tile)
    font = _font(18)
    bbox = draw.textbbox((0, 0), label, font=font)
    text_w = bbox[2] - bbox[0]
    draw.text(
        ((tile.width - text_w) // 2, FACE_SIZE + PADDING // 2 + 4),
        label,
        font=font,
        fill=(230, 230, 235, 255),
    )
    return tile


def build_theme_grid() -> Image.Image:
    tiles = [_labeled_tile(render_face(CharacterState.IDLE, key), THEMES[key].label) for key in THEME_ORDER]
    grid = Image.new("RGBA", (tiles[0].width * len(tiles), tiles[0].height), BG)
    for i, tile in enumerate(tiles):
        grid.alpha_composite(tile, (i * tile.width, 0))
    return grid


def build_expression_grid(theme_key: str = "purple", columns: int = 4) -> Image.Image:
    tiles = [_labeled_tile(render_face(state, theme_key), label) for label, state in EXPRESSIONS]
    rows = (len(tiles) + columns - 1) // columns
    tw, th = tiles[0].size
    grid = Image.new("RGBA", (tw * columns, th * rows), BG)
    for i, tile in enumerate(tiles):
        x, y = (i % columns) * tw, (i // columns) * th
        grid.alpha_composite(tile, (x, y))
    return grid


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv)

    themes_path = os.path.join(OUT_DIR, "themes.png")
    build_theme_grid().save(themes_path)
    print(f"wrote {themes_path}")

    expressions_path = os.path.join(OUT_DIR, "expressions.png")
    build_expression_grid().save(expressions_path)
    print(f"wrote {expressions_path}")

    del app


if __name__ == "__main__":
    main()
