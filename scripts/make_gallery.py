"""Render docs/gallery/<mode>.png from examples/*.json: one caption line per mode, mid-line so the word-by-word
reveal shows. Then docs/gallery/styles/<style>.png and <style>-vertical.png: each --style on a 16:9 and a 9:16 frame,
from examples/transcripts/interview.json with speaker names on. Needs ffmpeg built with libass.

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
from captions import assemblyai, fonts, languages, lines, profiles, writers  # noqa: E402

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


# what an LLM might pick for the interview sample: the gallery stays offline
KEY = {"sound", "off,", "phone,", "captions", "vertical", "Short", "lines,"}


def render_style(style, size, profile, t, png, crop=None):
    with open(os.path.join(ROOT, "examples", "transcripts", "interview.json"), encoding="utf-8") as f:
        ws = assemblyai.words(json.load(f))
    cues = lines.cues(ws, max_width=profile.width, profile=profile)
    if style == "social":
        for c in cues:
            for w in c["words"]:
                w["key"] = w["text"] in KEY
    W, H = size
    font = fonts.for_libass(fonts.resolve(None, "latin")).family
    with tempfile.TemporaryDirectory() as d:
        ass = os.path.join(d, "g.ass")
        writers.ass(cues, ass, size, True, font, style=style, profile=profile, speakers="always")
        vf = writers.ass_filter(ass) + (f",crop={crop}" if crop else "") + (",scale=1280:-2" if W > H else ",scale=360:-2")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"gradients=s={W}x{H}:c0=0x3a6186:c1=0xd8a47f:x0=0:y0=0:x1={W}:y1={H}:speed=0:seed=7:d={t + 1:.2f}",
                        "-vf", vf, "-ss", f"{t:.2f}", "-frames:v", "1", "-update", "1", png], cwd=d, check=True)
    return png


def styles():
    out = os.path.join(OUT, "styles")
    os.makedirs(out, exist_ok=True)
    for style in writers.STYLES:
        # mid-way through "Most people watch with the sound off," (speaker B)
        print(render_style(style, (W, H), profiles.HORIZONTAL, 11.4, os.path.join(out, f"{style}.png"), f"{W}:{CROP}:0:{TOP}"))
        print(render_style(style, (1080, 1920), profiles.VERTICAL, 12.0, os.path.join(out, f"{style}-vertical.png")))


def main():
    if not writers.has_libass():
        raise SystemExit("needs ffmpeg built with libass")
    os.makedirs(OUT, exist_ok=True)
    for p in sorted(glob.glob(os.path.join(ROOT, "examples", "*.json"))):
        print(render(p))
    styles()


if __name__ == "__main__":
    main()
