# -*- coding: utf-8 -*-
"""Собирает icon.ico со всеми нужными размерами из assets/spark.png.

Исходник — искра Gemini 256×256 с прозрачным фоном. Windows берёт из .ico
размер под задачу (16 — панель задач, 32 — ярлык, 256 — крупные плитки),
и если нужного нет, масштабирует сам, что даёт мыло. Поэтому кладём все.
"""
import os

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "assets", "spark.png")
OUT = os.path.join(HERE, "icon.ico")
SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]


def main():
    img = Image.open(SRC).convert("RGBA")
    # каждый размер пережимаем отдельно с LANCZOS — резче, чем встроенное
    # масштабирование Pillow при сохранении .ico
    frames = [img.resize(s, Image.LANCZOS) for s in SIZES]
    frames[0].save(OUT, format="ICO", sizes=SIZES, append_images=frames[1:])
    print("icon ->", OUT, "| размеры:", [s[0] for s in SIZES])


if __name__ == "__main__":
    main()
