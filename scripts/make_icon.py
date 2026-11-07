"""Generate matching SVG/PNG/ICNS/ICO artwork without fonts or online assets."""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'assets'
OUT.mkdir(exist_ok=True)
svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
<title>题库转换</title>
<rect x="96" y="96" width="832" height="832" rx="186" fill="#4365de"/>
<path d="M330 224H582L704 346V738Q704 774 668 774H330Q294 774 294 738V260Q294 224 330 224Z" fill="#fff"/>
<path d="M582 224V318Q582 346 610 346H704Z" fill="#c9d4fb"/>
<path d="M374 426H604M374 502H604M374 578H510" stroke="#a6b7ed" stroke-width="28" stroke-linecap="round"/>
<circle cx="688" cy="698" r="125" fill="#304ab0" stroke="#4365de" stroke-width="18"/>
<path d="M623 698H753M705 650L753 698L705 746" fill="none" stroke="#fff" stroke-width="27" stroke-linecap="round" stroke-linejoin="round"/>
</svg>'''
(OUT/'icon.svg').write_text(svg,encoding='utf-8')
scale=2
image=Image.new('RGBA',(1024*scale,1024*scale))
draw=ImageDraw.Draw(image)
def box(v): return tuple(int(n*scale) for n in v)
def line(points,fill,width):
    draw.line([box(p) for p in points],fill=fill,width=width*scale,joint='curve')
    r=width*scale/2
    for x,y in (points[0],points[-1]):
        x*=scale;y*=scale;draw.ellipse((x-r,y-r,x+r,y+r),fill=fill)
draw.rounded_rectangle(box((96,96,928,928)),radius=186*scale,fill='#4365de')
draw.rounded_rectangle(box((294,224,704,774)),radius=36*scale,fill='white')
draw.polygon([box(p) for p in [(582,224),(704,224),(704,346)]],fill='#4365de')
draw.polygon([box(p) for p in [(582,224),(582,346),(704,346)]],fill='#c9d4fb')
for y,end in [(426,604),(502,604),(578,510)]:line([(374,y),(end,y)],'#a6b7ed',28)
draw.ellipse(box((554,564,822,832)),fill='#4365de')
draw.ellipse(box((563,573,813,823)),fill='#304ab0')
line([(623,698),(753,698)],'white',27)
line([(705,650),(753,698),(705,746)],'white',27)
image=image.resize((1024,1024),Image.Resampling.LANCZOS)
image.save(OUT/'icon.png')
image.save(OUT/'icon.icns')
image.save(OUT/'icon.ico',sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
image.resize((256,256),Image.Resampling.LANCZOS).save(OUT/'icon-preview.png')
print('Generated SVG, PNG, ICNS, ICO and preview')
