"""Multilingual captions with AssemblyAI.

    python3 -m captions VIDEO --mode japanese+english [--font "Noto Sans JP"] [--editor finalcut] [--style social]
    python3 -m captions modes                  list languages and presets
    python3 -m captions fonts --mode hinglish  fonts to choose from (defaults, fonts/ folder, open-source picks)
    python3 -m captions plan VIDEO --mode ...  for the AssemblyAI MCP server: upload-sized audio + submit params
    python3 -m captions demo                   rebuild demo.html from examples/

A mode is a language ("japanese") or a pair ("japanese+english": Japanese on top, English underneath),
plus "hinglish" and "hinglish-roman". Line length, reading speed and placement follow the video's shape (16:9,
9:16, 1:1…; see profiles.py). Writes, next to the video (or in --out), for each mode:

    <name>.<mode>.srt / .vtt / .ass / .json    captions (any player or editor) / styled burn-in / cues
    <name>.<mode>.fcpxmld                      --editor finalcut
    <name>.<mode>.resolve.fcpxml               --editor resolve
    <name>.<mode>.premiere.xml                 --editor premiere
    <name>.<mode>.overlay.mov                  --overlay: styled captions with alpha, placed in each project
    <name>.preview.html                        the video with every mode's captions, in a browser

Exit code 3: a translation or romanization is waiting for an agent in <name>.<mode>.llm-request.json
(no API key for LLM Gateway; key words for --style social too). Answer it in <name>.<mode>.llm-answers.json and run the same command again.
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys

from . import assemblyai, exports, fonts, languages, lines, modes, profiles, writers
from .assemblyai import read_json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
AGENT_EXIT = 3


def player(tracks, path, title, video=None, faces=()):
    with open(os.path.join(HERE, "player.html"), encoding="utf-8") as f:
        html = f.read()
    data = json.dumps({"title": title, "video": video, "tracks": tracks, "fonts": list(faces)},
                      ensure_ascii=False).replace("</", "<\\/")
    html = re.sub(r"/\*DATA\*/.*?/\*END\*/", lambda _: data, html, count=1, flags=re.S)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def video_size(path):
    if not shutil.which("ffprobe"):
        return 1920, 1080
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        w, h = map(int, r.stdout.strip().split(",")[:2])
        return w, h
    except ValueError:
        return 1920, 1080


def demo(out=None):
    out = out or os.path.join(ROOT, "demo.html")
    tracks = [read_json(p) for p in sorted(glob.glob(os.path.join(ROOT, "examples", "*.json")))]
    order = ["english", "hindi", "hinglish", "hinglish-roman", "mandarin+english", "japanese+english"]
    tracks.sort(key=lambda t: order.index(t["mode"]) if t["mode"] in order else len(order))
    player(tracks, out, "Multilingual captions")
    print(f"{len(tracks)} modes -> {out}")


def list_modes():
    print("Languages (use one, or join two with +, e.g. japanese+english):")
    for name, (code, label, script) in sorted(languages.LANGUAGES.items()):
        print(f"  {name:12} {code:3} {script}")
    print("Presets: hinglish (Hindi + English as spoken), hinglish-roman (Hinglish in the English alphabet)")
    print("Popular (slash commands): " + ", ".join(languages.POPULAR))


def parse_modes(names, translate=None):
    try:
        return [languages.parse(n, translate) for n in (names or ["english"])]
    except ValueError as e:
        raise SystemExit(str(e))


def plan(argv):
    p = argparse.ArgumentParser(prog="python3 -m captions plan")
    p.add_argument("video")
    p.add_argument("--mode", action="append")
    p.add_argument("--out")
    p.add_argument("--keyterm", action="append", default=[])
    a = p.parse_args(argv)
    ms = parse_modes(a.mode)
    out = a.out or os.path.dirname(os.path.abspath(a.video))
    upload = None if re.match(r"https?://", a.video) else assemblyai.audio_for_upload(a.video, out)
    print(json.dumps({
        "upload_file": upload,
        "upload_bytes": os.path.getsize(upload) if upload else None,
        "submit_transcript": {m.name: assemblyai.submit_params(m, a.keyterm) for m in ms},
        "next": ["create_upload_url, then curl --data-binary @upload_file to it (skip for a public URL)",
                 "submit_transcript with audio_url = the upload_url, plus the params above",
                 "poll get_transcript until completed; save its structured result as JSON",
                 "python3 -m captions VIDEO --mode ... --transcript that.json"],
    }, indent=1, ensure_ascii=False))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    sub = argv[:1]
    if sub == ["demo"]:
        return demo(*argv[1:2])
    if sub == ["modes"]:
        return list_modes()
    if sub == ["plan"]:
        return plan(argv[1:])
    if sub == ["fonts"]:
        p = argparse.ArgumentParser(prog="python3 -m captions fonts")
        p.add_argument("--mode", action="append")
        ms = parse_modes(p.parse_args(argv[1:]).mode)
        return print(fonts.report([s for m in ms for s in (m.script, m.script2) if s]))

    p = argparse.ArgumentParser(prog="python3 -m captions", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("video", help="local video/audio file, or a public URL")
    p.add_argument("--mode", action="append", help="caption mode, e.g. english, japanese+english (repeatable)")
    p.add_argument("--translate", metavar="LANGUAGE", help="add a translated second line to every mode, e.g. english")
    p.add_argument("--transcript", help="AssemblyAI transcript JSON to use instead of transcribing (API or MCP)")
    p.add_argument("--font", help="first-line font: a family name, a file, or a font in fonts/")
    p.add_argument("--font2", help="second-line font for dual captions (default: --font if it's the same script)")
    p.add_argument("--editor", action="append", choices=exports.EDITORS + ("all",),
                   help="also write a project for finalcut, resolve or premiere (repeatable, or all)")
    p.add_argument("--overlay", action="store_true", help="render styled captions with alpha into each project (libass)")
    p.add_argument("--burn", action="store_true", help="also burn captions into a copy of the video (libass)")
    p.add_argument("--no-karaoke", action="store_true", help="burn whole lines instead of word-by-word")
    p.add_argument("--style", choices=tuple(writers.STYLES), default="default",
                   help="look of the overlay and burn-in: default, high-contrast (yellow on black, no motion) or "
                        "social (bold, key words highlighted: one more LLM task)")
    p.add_argument("--speaker-names", choices=writers.SPEAKER_NAMES, default="never",
                   help="show who's speaking on screen (and in the .vtt): never, on each change, or always")
    p.add_argument("--glue", action="append", default=[], metavar="PHRASE", help='keep a phrase on one line (repeatable)')
    p.add_argument("--keyterm", action="append", default=[], metavar="TERM",
                   help="a name or term to transcribe exactly, e.g. AssemblyAI (repeatable; also kept on one line)")
    p.add_argument("--aspect", help="profile for this shape instead of the video's: 16:9, 9:16, 1:1, vertical…")
    p.add_argument("--max-width", type=int, help="line length in columns; CJK counts 2 (default: from the video's "
                                                 "shape, 42 for 16:9, 22 for 9:16)")
    p.add_argument("--out", help="output folder (default: next to the video)")
    p.add_argument("--llm", choices=("auto", "gateway", "agent"), default="auto",
                   help="who translates/romanizes: LLM Gateway (needs a key) or the agent (default: gateway if a key is set)")
    p.add_argument("--llm-model", default=assemblyai.LLM_MODEL, help="LLM Gateway model")
    a = p.parse_args(argv)

    chosen = parse_modes(a.mode, a.translate)
    is_url = bool(re.match(r"https?://", a.video))
    editors = list(exports.EDITORS) if a.editor and "all" in a.editor else (a.editor or [])
    if (editors or a.burn or a.overlay) and is_url:
        raise SystemExit("--editor, --overlay and --burn need a local video file")
    if (a.burn or a.overlay) and not writers.has_libass():
        print("Skipping --burn/--overlay: they need ffmpeg built with libass (check: ffmpeg -filters | grep libass).")
        a.burn = a.overlay = False
    stem = re.sub(r"[^\w.-]+", "-", os.path.splitext(os.path.basename(a.video.split("?")[0]))[0]).strip("-") or "captions"
    out = a.out or (os.getcwd() if is_url else os.path.dirname(os.path.abspath(a.video)))
    os.makedirs(out, exist_ok=True)
    glue = lines.glue_pairs(a.glue + a.keyterm)
    # the key is needed to transcribe (unless there's a transcript or a cached one) and for LLM Gateway;
    # without one, translating and romanizing go to the agent
    key = None if a.llm == "agent" and a.transcript else assemblyai.api_key(required=False)
    if a.llm == "gateway" and not key:
        raise SystemExit("--llm gateway needs ASSEMBLYAI_API_KEY")
    size = video_size(a.video) if not is_url else (1920, 1080)
    try:
        profile = profiles.parse(a.aspect) if a.aspect else profiles.for_size(*size)
    except ValueError as e:
        raise SystemExit(str(e))
    if a.max_width:
        profile = profile._replace(width=a.max_width)
    print(f"Profile: {profiles.describe(profile)}")

    transcripts, tracks, faces, waiting = {}, [], {}, []
    for mode in chosen:
        if a.transcript:
            transcripts[mode.transcript_key] = read_json(a.transcript)
        elif mode.transcript_key not in transcripts:
            cache = os.path.join(out, f"{stem}.{mode.transcript_key}.transcript.json")
            if os.path.exists(cache):
                transcripts[mode.transcript_key] = read_json(cache)
            else:
                transcripts[mode.transcript_key] = assemblyai.transcribe(a.video, key or assemblyai.api_key(), mode,
                                                                         keyterms=a.keyterm)
                with open(cache, "w", encoding="utf-8") as f:
                    json.dump(transcripts[mode.transcript_key], f, ensure_ascii=False)
        base = os.path.join(out, f"{stem}.{mode.name}")
        llm = modes.Llm(key, a.llm_model, base, prefer_agent=a.llm == "agent")
        try:
            cue_list = modes.build(assemblyai.words(transcripts[mode.transcript_key]), mode, llm, glue, profile.width,
                                   profile, key_words=a.style == "social" and not mode.rtl)
        except modes.NeedsAgent as need:
            waiting.append(need)
            continue

        font = fonts.resolve(a.font, mode.script)
        same = not mode.script2 or mode.script2 == mode.script
        font2 = fonts.resolve(a.font2 or (a.font if same else None), mode.script2 or mode.script)
        fontsdir = font.dir or font2.dir
        writers.srt(cue_list, base + ".srt")
        writers.vtt(cue_list, base + ".vtt", a.speaker_names)
        # right-to-left scripts reveal whole lines and get no key words: splitting a line into per-word runs breaks
        # Arabic letter joining
        writers.ass(cue_list, base + ".ass", size, not a.no_karaoke and not mode.rtl,
                    fonts.for_libass(font).family, fonts.for_libass(font2).family, a.style, profile, a.speaker_names)
        writers.cues_json(cue_list, base + ".json", mode.name, mode.label)
        for f in (font, font2):
            if f.path:
                faces[f.family] = os.path.relpath(f.path, out)
        tracks.append({"mode": mode.name, "label": mode.label, "cues": cue_list, "rtl": mode.rtl,
                       "font": fonts.css_stack(font, mode.script), "font2": fonts.css_stack(font2, mode.script2 or mode.script)})
        print(f"{mode.label}: {len(cue_list)} captions -> {base}.srt/.vtt/.ass/.json  (font: {font.family}"
              + (f", second line: {font2.family}" if mode.translate_to else "") + ")")

        overlay = exports.overlay(a.video, base + ".ass", base + ".overlay.mov", font if font.path else font2) if a.overlay else None
        for ed in editors:
            if ed == "finalcut":
                path = exports.fcpxml(a.video, [(mode.name, cue_list)], font, base + ".fcpxmld", "1.11", True, overlay, f"{stem} {mode.label}")
                ok = exports.validate_fcpxml(path, "1.11")
                print(f"  Final Cut Pro -> {base}.fcpxmld" + (" (valid FCPXML 1.11)" if ok else ""))
            elif ed == "resolve":
                path = exports.fcpxml(a.video, [(mode.name, cue_list)], font, base + ".resolve.fcpxml", "1.10", False, overlay, f"{stem} {mode.label}")
                ok = exports.validate_fcpxml(path, "1.10")
                print(f"  DaVinci Resolve -> {path}" + (" (valid FCPXML 1.10)" if ok else "") + f", subtitles: {base}.srt")
            elif ed == "premiere":
                exports.premiere_xml(a.video, base + ".premiere.xml", overlay, f"{stem} {mode.label}")
                print(f"  Premiere Pro -> {base}.premiere.xml, captions: {base}.srt")
        if a.burn:
            ext = os.path.splitext(a.video)[1].lower()
            dst = base + (ext if ext in (".mp4", ".mov") else ".mp4")
            writers.burn(a.video, base + ".ass", dst, fontsdir=fontsdir)
            print(f"  burned in -> {dst}")

    if tracks:
        preview = os.path.join(out, f"{stem}.preview.html")
        src = a.video if is_url else os.path.relpath(os.path.abspath(a.video), out)
        player(tracks, preview, os.path.basename(a.video), src, [{"family": k, "url": v} for k, v in faces.items()])
        print(f"Preview -> {preview}")
    if waiting:
        for w in waiting:
            print(f"Waiting for the agent: answer {w.request} in {w.answers}, then run this command again.")
        sys.exit(AGENT_EXIT)
