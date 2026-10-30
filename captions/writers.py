"""Caption files: SRT and WebVTT for players and uploads, ASS for burn-in, JSON for the web player.

Dual captions (a 'translation' on the cue) go on a second line underneath. The ASS file carries the look: a style
(STYLES), the profile's placement for the video's shape, and speaker names.
"""
import json
import os
import shutil
import subprocess

from . import profiles
from .lines import needs_space

# ASS colours are &HAABBGGRR (alpha first, 00 = opaque)
STYLES = {
    # ours: white on a dark box; each word is grey until it's spoken
    "default": dict(color="&H00FFFFFF", unspoken="&H00999999", sub="&H00E0E0E0", edge="&H00141414", box=True,
                    bold=0, scale=1.0, karaoke=True),
    # accessibility: yellow on solid black (17:1), bigger and bold, whole lines with no motion; nothing on screen
    # depends on colour (speaker names are written out)
    "high-contrast": dict(color="&H003BEBFF", unspoken="&H003BEBFF", sub="&H00FFFFFF", edge="&H00000000", box=True,
                          bold=1, scale=1.15, karaoke=False),
    # vertical clips: heavy white type with an outline instead of a box, upcoming words faint, a few key words
    # (modes.keywords) in dark type on a yellow highlight
    "social": dict(color="&H00FFFFFF", unspoken="&H90FFFFFF", sub="&H00FFFFFF", edge="&H00000000", box=False,
                   bold=1, scale=1.1, karaoke=True, key="&H0000D4FF", key_text="&H00111111"),
}
# speaker names, in order of first appearance: light colours that read on a dark box and on video
SPEAKER_COLORS = ["&H00C8F37C", "&H00FFD48A", "&H00CF9EFF", "&H006BB8FF", "&H00FFA2C8", "&H008DF6C6"]
SPEAKER_NAMES = ("never", "change", "always")


def _ts(s, sep):
    ms = round(max(s, 0) * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02}{sep}{ms % 1000:03}"


def _lines(c):
    return c["text"] + (f"\n{c['translation']}" if c.get("translation") else "")


def srt(cue_list, path):
    with open(path, "w", encoding="utf-8") as f:
        for n, c in enumerate(cue_list, 1):
            f.write(f"{n}\n{_ts(c['start'], ',')} --> {_ts(c['end'], ',')}\n{_lines(c)}\n\n")


def vtt(cue_list, path, speakers="never"):
    """WebVTT; with speaker names, a cue that names its speaker starts with a voice tag, <v Speaker A>."""
    names = speaker_labels(cue_list, speakers)
    with open(path, "w", encoding="utf-8") as f:
        f.write("WEBVTT\n\n")
        for c, name in zip(cue_list, names):
            voice = f"<v {name}>" if name else ""
            f.write(f"{_ts(c['start'], '.')} --> {_ts(c['end'], '.')}\n{voice}{_lines(c)}\n\n")


def speaker_name(speaker):
    """AssemblyAI labels speakers A, B, C…; speaker identification can give real names, which stay as they are."""
    s = str(speaker).strip()
    return f"Speaker {s}" if len(s) <= 2 else s


def speaker_labels(cue_list, when="never"):
    """The speaker name to show on each cue, or None. 'change': when the speaker changes, and on the first cue if
    more than one person speaks; 'always': on every cue. Cues without a speaker get none."""
    who = [c["words"][0].get("speaker") if c.get("words") else None for c in cue_list]
    many = len({w for w in who if w is not None}) > 1
    out = []
    for i, w in enumerate(who):
        show = w is not None and (when == "always" or when == "change" and (who[i - 1] != w if i else many))
        out.append(speaker_name(w) if show else None)
    return out


def ass(cue_list, path, size=(1920, 1080), karaoke=True, font="Arial", font2=None, style="default", profile=None,
        speakers="never"):
    """Styled subtitles for burn-in. With karaoke, each word turns from grey to white as it's spoken.

    font is the first line's family, font2 the second line's (dual captions); libass shapes Devanagari,
    Arabic and Hebrew (right to left) itself. style is one of STYLES; profile (profiles.py, default: the one for
    size) sets the margins and keeps a full line inside the frame; speakers is one of SPEAKER_NAMES.
    """
    W, H = size
    font2 = font2 or font
    st = STYLES[style]
    karaoke = karaoke and st["karaoke"]
    p = profile or profiles.for_size(W, H)
    # 5% of the height, scaled by the style, but never so big that a full line (about 0.58 em a column in bold
    # Latin type) runs past the side margins: on a phone that's the width that binds
    fs = min(round(H * 0.05 * st["scale"]), round(W * (1 - 2 * p.side) / (p.width * 0.58)))
    ml, mv = round(W * p.side), round(H * p.margin)
    # box (BorderStyle 3): the outline colour fills a box around each line; otherwise a thin outline and a shadow
    border, outline, shadow = (3, round(fs * 0.25), 0) if st["box"] else (1, max(round(fs * 0.07), 2), max(round(fs * 0.05), 1))
    back = st["edge"] if st["box"] else "&H80000000"
    names = speaker_labels(cue_list, speakers)
    colors = {}
    for c, name in zip(cue_list, names):
        if name:
            colors.setdefault(name, SPEAKER_COLORS[len(colors) % len(SPEAKER_COLORS)])
    cs = lambda s: max(round(s * 100), 0)            # ASS karaoke counts in centiseconds
    esc = lambda t: t.replace("\\", "\\\\").replace("{", "(").replace("}", ")")
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{font},{fs},{st["color"]},{st["unspoken"]},{st["edge"]},{back},{st["bold"]},0,0,0,100,100,0,0,{border},{outline},{shadow},2,{ml},{ml},{mv},1
Style: Sub,{font2},{round(fs * 0.72)},{st["sub"]},{st["sub"]},{st["edge"]},{back},{st["bold"]},0,0,0,100,100,0,0,{border},{round(outline * 0.8)},{shadow},2,{ml},{ml},{mv},1
Style: Speaker,{font},{round(fs * 0.6)},&H00FFFFFF,&H00FFFFFF,{st["edge"]},{back},1,0,0,0,100,100,0,0,{border},{round(outline * 0.6)},{shadow},2,{ml},{ml},{mv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(head)
        for c, name in zip(cue_list, names):
            ws = c["words"]
            keyed = "key" in st and any(w.get("key") for w in ws)
            if karaoke or keyed:
                text = f"{{\\k{cs(ws[0]['start'] - c['start'])}}}" if karaoke else ""     # silence before the first word
                for i, w in enumerate(ws):
                    nxt = ws[i + 1]["start"] if i + 1 < len(ws) else c["end"]
                    sp = " " if i and needs_space(ws[i - 1]["text"], w["text"]) else ""
                    k = f"\\k{cs(nxt - w['start'])}" if karaoke else ""
                    if keyed and w.get("key"):
                        # dark type on a highlight: a thick outline in the key colour, no shadow, back after the word
                        mark = f"\\1c{st['key_text']}\\2c{st['key_text']}\\3c{st['key']}\\bord{round(fs * 0.16)}\\shad0"
                        text += f"{sp}{{{k}{mark}}}{esc(w['text'])}{{\\rMain}}"
                    else:
                        text += (f"{{{k}}}" if k else "") + f"{sp}{esc(w['text'])}"
            else:
                text = esc(c["text"])
            if name:
                text = f"{{\\rSpeaker\\c{colors[name]}}}{esc(name)}\\N{{\\rMain}}" + text
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
