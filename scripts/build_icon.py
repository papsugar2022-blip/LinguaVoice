"""Render the original LinguaVoice speech-wave mark with Pillow (build only)."""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]


def main():
    target = ROOT / 'assets' / 'branding'
    target.mkdir(parents=True, exist_ok=True)
    scale = 4
    image = Image.new('RGBA', (256 * scale, 256 * scale))
    draw = ImageDraw.Draw(image)

    def box(bounds, radius, color):
        draw.rounded_rectangle(tuple(int(v * scale) for v in bounds), radius=radius * scale, fill=color)

    box((8, 8, 248, 248), 58, '#12383D')
    box((42, 49, 214, 190), 48, '#E2FFF5')
    draw.polygon([(v[0]*scale, v[1]*scale) for v in [(65, 166), (65, 214), (112, 181)]], fill='#E2FFF5')
    for x, top, bottom, color in [(72, 104, 136, '#147E78'), (96, 86, 154, '#147E78'),
                                  (120, 74, 166, '#147E78'), (144, 92, 148, '#147E78'),
                                  (168, 105, 135, '#147E78')]:
        box((x, top, x+16, bottom), 8, color)
    image = image.resize((256, 256), Image.Resampling.LANCZOS)
    image.save(target / 'linguavoice.png')
    image.save(target / 'linguavoice.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    print('Original speech-wave icon generated.')


if __name__ == '__main__':
    main()
