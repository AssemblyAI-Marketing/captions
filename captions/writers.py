"""Caption files: SRT and WebVTT for players and uploads, ASS for burn-in, JSON for the web player.

Dual captions (a 'translation' on the cue) go on a second line underneath.
"""
import json
import os
import shutil
import subprocess

from .lines import needs_space


def _ts(s, sep):
    ms = round(max(s, 0) * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02}{sep}{ms % 1000:03}"


def _lines(c):
    return c["text"] + (f"\n{c['translation']}" if c.get("translation") else "")


def srt(cue_list, path):
    with open(path, "w", encoding="utf-8") as f:
        for n, c in enumerate(cue_list, 1):
            f.write(f"{n}\n{_ts(c['start'], ',')} --> {_ts(c['end'], ',')}\n{_lines(c)}\n\n")


def vtt(cue_list, path):
    with open(path, "w", encoding="utf-8") as f:
        f.write("WEBVTT\n\n")
        for c in cue_list:
            f.write(f"{_ts(c['start'], '.')} --> {_ts(c['end'], '.')}\n{_lines(c)}\n\n")


def ass(cue_list, path, size=(1920, 1080), karaoke=True, font="Arial", font2=None):
    """Styled subtitles for burn-in. With karaoke, each word turns from grey to white as it's spoken.

    font is the first line's family, font2 the second line's (dual captions); libass shapes Devanagari,
    Arabic and Hebrew (right to left) itself.
    """
    W, H = size
    font2 = font2 or font
    fs = round(H * 0.05)
    cs = lambda s: max(round(s * 100), 0)            # ASS karaoke counts in centiseconds
    esc = lambda t: t.replace("\\", "\\\\").replace("{", "(").replace("}", ")")
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{font},{fs},&H00FFFFFF,&H00999999,&H00141414,&H00141414,0,0,0,0,100,100,0,0,3,{round(fs * 0.25)},0,2,60,60,{round(H * 0.08)},1
Style: Sub,{font2},{round(fs * 0.72)},&H00E0E0E0,&H00E0E0E0,&H00141414,&H00141414,0,0,0,0,100,100,0,0,3,{round(fs * 0.2)},0,2,60,60,{round(H * 0.08)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(head)
        for c in cue_list:
            if karaoke:
                ws = c["words"]
                text = f"{{\\k{cs(ws[0]['start'] - c['start'])}}}"     # silence before the first word
                for i, w in enumerate(ws):
                    nxt = ws[i + 1]["start"] if i + 1 < len(ws) else c["end"]
                    sp = " " if i and needs_space(ws[i - 1]["text"], w["text"]) else ""
                    text += f"{{\\k{cs(nxt - w['start'])}}}{sp}{esc(w['text'])}"
            else:
                text = esc(c["text"])
            if c.get("translation"):
                text += f"\\N{{\\rSub}}{esc(c['translation'])}"
            ts = lambda s: _ts(s, ".")[1:-1]          # ASS wants H:MM:SS.cc
            f.write(f"Dialogue: 0,{ts(c['start'])},{ts(c['end'])},Main,,0,0,0,,{text}\n")


def cues_json(cue_list, path, mode, label):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"mode": mode, "label": label, "cues": cue_list}, f, ensure_ascii=False, indent=1)


def has_libass():
    if not shutil.which("ffmpeg"):
        return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    return " ass " in out


def filter_path(p):
    """A path for an ffmpeg filter argument (single-quoted): refuse characters that would end the quoting."""
    if "'" in p or "\\" in p or "\n" in p:
        raise SystemExit(f"Rename or move {p!r}: ffmpeg filter arguments can't contain quotes, backslashes or newlines")
    return p


def ass_filter(ass_path, fontsdir=None, alpha=False):
    """ffmpeg's libass filter with complex shaping (HarfBuzz): Arabic ligatures and Devanagari conjuncts join."""
    f = f"ass='{filter_path(os.path.basename(ass_path))}':shaping=complex"
    if fontsdir:
        f += f":fontsdir='{filter_path(fontsdir)}'"
    return f + (":alpha=1" if alpha else "")


def burn(video, ass_path, out, crf=18, fontsdir=None):
    """Burn the ASS captions into the video. Audio is copied untouched. Needs ffmpeg built with libass.

    fontsdir: a folder of font files (fonts/) for libass to load before looking at the system's fonts.
    """
    if not has_libass():
        raise SystemExit("Burn-in needs ffmpeg built with libass (check: ffmpeg -filters | grep libass). "
                         "Your SRT/VTT files and the browser preview work without it.")
    # run next to the .ass file so its path needs no filter escaping
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", os.path.abspath(video),
                    "-vf", ass_filter(ass_path, fontsdir),
                    "-c:v", "libx264", "-crf", str(crf),
                    "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", os.path.abspath(out)],
                   cwd=os.path.dirname(os.path.abspath(ass_path)), check=True)
