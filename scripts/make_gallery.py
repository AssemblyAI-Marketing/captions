"""Render docs/gallery/<mode>.png from examples/*.json: one caption line per style, mid-line so the word-by-word
reveal shows. Needs ffmpeg built with libass.

    python3 scripts/make_gallery.py
"""
import glob
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from captions import fonts, languages, writers  # noqa: E402

OUT = os.path.join(ROOT, "docs", "gallery")
W, H, TOP, CROP = 1920, 1080, 790, 270


def pick(cues):
    """The fullest line in the first few, preferring one with a second line."""
    pool = cues[:4]
    return max(pool, key=lambda c: (bool(c.get("translation")), len(c["text"])))


def render(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    mode = languages.parse(data["mode"])
    c = pick(data["cues"])
    font = fonts.for_libass(fonts.resolve(None, mode.script)).family
    font2 = fonts.for_libass(fonts.resolve(None, mode.script2 or mode.script)).family
    png = os.path.join(OUT, f"{mode.name}.png")
    with tempfile.TemporaryDirectory() as d:
        ass = os.path.join(d, "g.ass")
        writers.ass([c], ass, (W, H), not mode.rtl, font, font2)
        label = f"{mode.label.upper()}   ·   /{mode.name}"
        with open(ass, encoding="utf-8") as f:
            text = f.read()
        # a small grey label in the corner: its style goes with the others, above [Events]
        text = text.replace("\n[Events]", "\nStyle: Label,Helvetica Neue,26,&H00A0A0A0,&H00A0A0A0,&H00000000,&H00000000,"
                                          "1,0,0,0,100,100,2,0,1,0,0,7,0,0,0,1\n\n[Events]")
        text += f"Dialogue: 1,0:00:00.00,0:10:00.00,Label,,0,0,0,,{{\\pos(64,{TOP + 22})}}{label}\n"
        with open(ass, "w", encoding="utf-8") as f:
            f.write(text)
        t = c["start"] + (c["end"] - c["start"]) * 0.72           # most words final, the last few still landing
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x202022:s={W}x{H}:d={c['end'] + 1:.2f}",
                        "-vf", f"{writers.ass_filter(ass)},crop={W}:{CROP}:0:{TOP},scale=1280:-2", "-ss", f"{t:.2f}",
                        "-frames:v", "1", "-update", "1", png], cwd=d, check=True)
    return png


def main():
    if not writers.has_libass():
        raise SystemExit("needs ffmpeg built with libass")
    os.makedirs(OUT, exist_ok=True)
    for p in sorted(glob.glob(os.path.join(ROOT, "examples", "*.json"))):
        print(render(p))


if __name__ == "__main__":
    main()
