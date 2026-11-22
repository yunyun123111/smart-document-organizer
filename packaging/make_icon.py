# -*- coding: utf-8 -*-
"""生成应用图标 app.ico（多尺寸），供 exe 与安装包使用。"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent / "app.ico"
SIZE = 256
BG = (32, 110, 240, 255)
BG_DARK = (22, 78, 190, 255)
PAPER = (255, 255, 255, 255)
LINE = (196, 212, 240, 255)

img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# 圆角底板：上浅下深的竖向渐变
for y in range(SIZE):
    t = y / (SIZE - 1)
    color = tuple(int(BG[i] * (1 - t) + BG_DARK[i] * t) for i in range(3)) + (255,)
    d.line([(0, y), (SIZE, y)], fill=color)
mask = Image.new("L", (SIZE, SIZE), 0)
ImageDraw.Draw(mask).rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=56, fill=255)
img.putalpha(mask)

# 纸张 + 折角
left, top, right, bottom = 66, 52, 190, 204
d.rounded_rectangle([left, top, right, bottom], radius=10, fill=PAPER)
d.polygon([(right - 40, top), (right, top + 40), (right - 40, top + 40)], fill=(214, 226, 246, 255))
d.polygon([(right - 40, top), (right, top + 40), (right - 40, top + 40)], outline=(160, 186, 230, 255))

# 正文横线
for i, y in enumerate((92, 120, 148)):
    x2 = right - 28 if i < 2 else right - 56
    d.rounded_rectangle([left + 22, y, x2, y + 10], radius=5, fill=LINE)

# 放大绘制后缩到各尺寸，保证小图标清晰
sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
img.save(OUT, format="ICO", sizes=sizes)
print(f"saved {OUT} ({OUT.stat().st_size} bytes)")
