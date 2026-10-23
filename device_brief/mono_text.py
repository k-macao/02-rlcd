#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Native 1bpp text rasterization for the 400×300 RLCD panel.

FreeType's FT_LOAD_TARGET_MONO selects monochrome-optimized hinting;
FT_LOAD_FORCE_AUTOHINT is required for fonts such as Alibaba PuHuiTi that
ship without small-size TrueType instructions.  Every glyph is rendered
straight to a 1-bit image at an integer pen position: no grayscale image,
threshold, dithering, or subpixel positioning is involved.

References:
- https://github.com/freetype/freetype/blob/master/include/freetype/freetype.h
- https://github.com/rougier/freetype-py/blob/master/examples/glyph-monochrome.py
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import freetype
from PIL import Image, ImageDraw


@dataclass(frozen=True)
class MonoGlyph:
    index: int
    advance: int
    left: int
    top: int
    image: Image.Image | None


@dataclass(frozen=True)
class MonoPlacement:
    glyph: MonoGlyph
    x: int
    y: int


@dataclass(frozen=True)
class MonoLayout:
    advance: int
    placements: tuple[MonoPlacement, ...]
    # Bounding box of actual ink, relative to the Pillow-style ascender origin.
    bbox: tuple[int, int, int, int] | None


class MonoFont:
    """One fixed-size FreeType face with a shared native-1bpp glyph cache."""

    def __init__(self, font_path: str | Path, size: int):
        self.path = Path(font_path)
        self.size = int(size)
        try:
            self.face = freetype.Face(str(self.path))
            self.face.set_pixel_sizes(0, self.size)
        except (OSError, ValueError, freetype.FT_Exception) as exc:
            raise RuntimeError(f"无法加载单色字体：{self.path}") from exc
        self.ascender = int(round(self.face.size.ascender / 64))
        self._glyphs: dict[str, MonoGlyph] = {}
        self._kerning: dict[tuple[int, int], int] = {}

    def getname(self) -> tuple[str, str]:
        """Match the small part of Pillow's font API used by regression tests."""
        def decode(value) -> str:
            return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
        return decode(self.face.family_name), decode(self.face.style_name)

    def _glyph(self, char: str) -> MonoGlyph:
        cached = self._glyphs.get(char)
        if cached is not None:
            return cached
        glyph_index = self.face.get_char_index(char)
        flags = (
            freetype.FT_LOAD_DEFAULT
            | freetype.FT_LOAD_RENDER
            | freetype.FT_LOAD_TARGET_MONO
            | freetype.FT_LOAD_FORCE_AUTOHINT
        )
        self.face.load_glyph(glyph_index, flags)
        slot = self.face.glyph
        bitmap = slot.bitmap
        if bitmap.pixel_mode != freetype.FT_PIXEL_MODE_MONO:
            raise RuntimeError(f"FreeType 未返回 1bpp 字形：{self.path} U+{ord(char):04X}")
        image = None
        if bitmap.width and bitmap.rows:
            if bitmap.pitch <= 0:
                raise RuntimeError(f"不支持的字形行序：{self.path} U+{ord(char):04X}")
            data = bytes(bitmap.buffer[:bitmap.pitch * bitmap.rows])
            # Both FreeType MONO and Pillow raw mode 1 use MSB-first rows.
            image = Image.frombytes(
                "1", (bitmap.width, bitmap.rows), data,
                "raw", "1", bitmap.pitch, 1,
            )
        glyph = MonoGlyph(
            index=int(glyph_index),
            advance=int(round(slot.advance.x / 64)),
            left=int(slot.bitmap_left),
            top=int(slot.bitmap_top),
            image=image,
        )
        self._glyphs[char] = glyph
        return glyph

    def _kern(self, previous: int, current: int) -> int:
        key = (previous, current)
        if key not in self._kerning:
            value = 0
            if previous and current and self.face.has_kerning:
                value = int(round(self.face.get_kerning(
                    previous, current, freetype.FT_KERNING_DEFAULT,
                ).x / 64))
            self._kerning[key] = value
        return self._kerning[key]

    @lru_cache(maxsize=256)
    def layout(self, text: str) -> MonoLayout:
        """Return integer advances and pixel placements shared by measure/draw."""
        pen_x = 0
        previous = 0
        placements = []
        bbox = None
        for char in str(text):
            glyph = self._glyph(char)
            pen_x += self._kern(previous, glyph.index)
            if glyph.image is not None:
                x = pen_x + glyph.left
                y = self.ascender - glyph.top
                placements.append(MonoPlacement(glyph, x, y))
                rect = (x, y, x + glyph.image.width, y + glyph.image.height)
                if bbox is None:
                    bbox = rect
                else:
                    bbox = (
                        min(bbox[0], rect[0]), min(bbox[1], rect[1]),
                        max(bbox[2], rect[2]), max(bbox[3], rect[3]),
                    )
            pen_x += glyph.advance
            previous = glyph.index
        return MonoLayout(pen_x, tuple(placements), bbox)

    def measure(self, text: str) -> int:
        return self.layout(str(text)).advance

    def render(self, draw: ImageDraw.ImageDraw, x: int, y: int, text: str,
               fill: int) -> tuple[int, tuple[int, int, int, int] | None]:
        """Draw only 0/255 pixels and return the same advance used by measure()."""
        origin_x = int(round(x))
        origin_y = int(round(y))
        layout = self.layout(str(text))
        for placement in layout.placements:
            draw.bitmap(
                (origin_x + placement.x, origin_y + placement.y),
                placement.glyph.image,
                fill=fill,
            )
        bbox = None
        if layout.bbox is not None:
            bbox = (
                origin_x + layout.bbox[0], origin_y + layout.bbox[1],
                origin_x + layout.bbox[2], origin_y + layout.bbox[3],
            )
        return layout.advance, bbox


_FONT_CACHE: dict[tuple[str, int], MonoFont] = {}


def get_mono_font(font_path: str | Path, size: int) -> MonoFont:
    """Share faces and glyph bitmaps across all pages and pagination retries."""
    path = str(Path(font_path).resolve())
    key = (path, int(size))
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = MonoFont(path, size)
    return _FONT_CACHE[key]
