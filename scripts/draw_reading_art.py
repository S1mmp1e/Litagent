"""Build the local, decorative paper illustration used by the empty reader."""
from pathlib import Path
from PIL import Image, ImageDraw

scale = 3
image = Image.new('RGBA', (360 * scale, 230 * scale))
d = ImageDraw.Draw(image)

def box(rect, fill, outline=None, radius=3):
    d.rounded_rectangle(tuple(int(v * scale) for v in rect), radius=radius * scale,
                        fill=fill, outline=outline, width=scale)

def line(points, fill, width=1):
    d.line([(int(x * scale), int(y * scale)) for x, y in points], fill=fill, width=width * scale)

for x in range(25, 350, 16):
    for y in range(20, 220, 16):
        box((x, y, x+1, y+1), '#dce5e2', radius=0)
box((78, 32, 246, 212), '#dce5e2', radius=5)
box((72, 24, 240, 204), '#eff3f1', '#cbd6d1', 5)
box((100, 13, 269, 199), '#ffffff', '#c4d2cc', 5)
box((119, 35, 160, 40), '#267c66', radius=1)
box((119, 54, 244, 61), '#394941', radius=1)
box((119, 68, 215, 73), '#394941', radius=1)
for y, length in [(90,125),(99,119),(108,123),(117,93)]:
    box((119,y,119+length,y+2), '#d4dcd8', radius=1)
box((119,136,175,178), '#e7f1ec', radius=3)
for y, length in [(138,55),(148,52),(158,57),(168,41)]:
    box((186,y,186+length,y+2), '#d4dcd8', radius=1)
line([(130,164),(141,151),(153,160),(166,146)], '#438873', 2)
box((245,91,298,118), '#faedce', '#d8bc7b', 4)
for x in (256,266,276):
    line([(x,101),(x+4,101),(x+4,108)], '#a98334', 2)
path = Path(__file__).resolve().parents[1] / 'lit_agent/static/reading-paper.png'
image.save(path)
print(path)
