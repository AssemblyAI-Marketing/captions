"""Rough cut a talking-head recording from its transcript: drop retakes, false starts and warm-up chatter, cut in the
pauses, and hand the cut to Final Cut Pro, DaVinci Resolve or Premiere Pro.

    python3 -m captions cut take1.mov take2.mov [--editor all] [--render] [--transcript t1.json --transcript t2.json]

1. Each clip is transcribed (or a transcript is given, from the API or the AssemblyAI MCP with include_words).
2. The words become sentences, split at sentence ends, long pauses and clip changes.
3. Which sentences to keep is a small language task: LLM Gateway with an API key, or the agent through
   <name>.cut.llm-request.json (exit code 3). With --llm none, a simple offline rule drops a sentence when one of the
   next few starts the same way (a retake) and drops sentences that are only filler.
4. Kept sentences become segments. Each in and out point is padded, kept clear of the dropped words around it, and
   moved to the quietest 10 ms of audio nearby, so no cut lands on a syllable.
5. Writes <name>.cut.json (the decisions), <name>.cut.transcript.json (the kept words on the cut's clock, so the
   captions command can run on the cut), editor projects, and with --render an MP4.
"""
import array
import json
import math
import os
import re
import shutil
import statistics
import subprocess
from xml.sax.saxutils import escape, quoteattr

from . import exports, lines

PAD_IN, PAD_OUT = 0.12, 0.18     # breathing room before the first word and after the last
SNAP = 0.12                      # search this far either side for the quietest moment
MERGE_GAP = 1.0                  # kept sentences closer than this stay one segment; longer pauses are cut out
SPLIT_GAP = 0.7                  # a pause this long ends a sentence
LOUD = 0.35                      # a cut point louder than this × the clip's median is mid-speech: flagged to check
FADE = 0.01                      # 10 ms audio fades at each cut in the rendered MP4, so no clicks
FILLERS = {"um", "uh", "erm", "hmm", "mm", "ah", "okay", "ok", "alright", "all", "right", "so", "yeah"}
CONNECTIVES = {"and", "but", "now", "well", "then", "like"}   # ignored when comparing how two sentences open

DECIDE = (
    "Each item is one sentence from a talking-head recording, in order, as '<number>. [take N m:ss] text'. The speaker "
    "sometimes repeats or restarts a line: keep only the LAST complete take of each line and drop earlier attempts and "
    "false starts. Also drop warm-up and wrap-up chatter not meant for the audience (\"All right. Okay.\", \"Let me try "
    "that again\") and sentences that are only filler words. Keep everything else, in order, even if it's informal. "
    "Answer true to keep a sentence, false to drop it."
)


# ---------- sentences and decisions ----------

def sentences(clip_words):
    """[[words of clip 0], [words of clip 1], …] → [{id, clip, start, end, text, words}], in order."""
    groups = []
    for clip, words in enumerate(clip_words):
        cur = []
        for w in words:
            if cur and (cur[-1]["text"].endswith(lines.ENDS) or w["start"] - cur[-1]["end"] > SPLIT_GAP):
                groups.append((clip, cur))
                cur = []
            cur.append(w)
        if cur:
            groups.append((clip, cur))
    return [{"id": i, "clip": clip, "start": ws[0]["start"], "end": ws[-1]["end"], "text": lines.join(ws), "words": ws}
            for i, (clip, ws) in enumerate(groups)]


def _norm(text):
    return [t for t in re.sub(r"[^\w\s]", " ", text.lower()).split() if t]


def _content(text):
    return [w for w in _norm(text) if w not in FILLERS and w not in CONNECTIVES]


def heuristic(sents, lookahead=5):
    """Offline keep/drop: a sentence is a retake if one of the next few says the same opening words; filler goes.

    Opening words skip fillers and connectives ("and so, uh, so what style…" matches "so what style…").
    """
    keep = []
    for i, s in enumerate(sents):
        head = _content(s["text"])[:5]
        if not head:
            keep.append(False)
            continue
        retake = False
        for t in sents[i + 1:i + 1 + lookahead]:
            later = _content(t["text"])[:len(head) + 3]
            if later and sum(w in later for w in head) / len(head) >= 0.6 and len(later) >= 0.6 * len(head):
                retake = True
                break
        keep.append(not retake)
    return keep


def stamp(clip, t):
    return f"take {clip + 1} {int(t // 60)}:{t % 60:04.1f}"


def decide(sents, llm):
    """[bool] per sentence, from LLM Gateway or the agent (llm), or the offline heuristic (llm is None)."""
    if llm is None:
        return heuristic(sents)
    items = [f"{s['id']}. [{stamp(s['clip'], s['start'])}] {s['text']}" for s in sents]
    out = llm.run("cut", items, DECIDE, same_shape=lambda a, b: isinstance(b, bool))
    return [bool(x) for x in out]


# ---------- audio and cut points ----------

def envelope(path, rate=8000, hop=0.01):
    """RMS per 10 ms window of a clip's audio (mono), as a list; empty if it has no audio."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
                         capture_output=True).stdout
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) // 2 * 2])
    n = int(rate * hop)
    return [math.sqrt(sum(x * x for x in samples[i:i + n]) / n) for i in range(0, len(samples) - n + 1, n)]


def quietest(env, t, lo, hi, hop=0.01):
    """The time in [max(lo, t - SNAP), min(hi, t + SNAP)] whose 10 ms window is quietest (nearest t on ties)."""
    a, b = max(lo, t - SNAP), min(hi, t + SNAP)
    if not env or b - a < hop:
        return min(max(t, lo), hi)
    best = min(range(math.ceil(a / hop - 1e-9), int(b / hop + 1e-9) + 1),
               key=lambda i: (env[i] if i < len(env) else 0, abs(i * hop - t)))
    return round(best * hop, 3)


def segments(sents, keep, envs, durations=None):
    """Kept sentences → [{clip, in, out, words}] in source seconds, cut in quiet spots.

    Neighbouring kept sentences in the same clip stay one segment unless the pause between them is long; each in and
    out point stays clear of the words around it that were dropped, and inside the clip.
    """
    segs = []
    for s, k in zip(sents, keep):
        if not k:
            continue
        last = segs[-1] if segs else None
        if last and last["clip"] == s["clip"] and last["next"] == s["id"] and s["start"] - last["words"][-1]["end"] < MERGE_GAP:
            last["words"] += s["words"]
            last["next"] = s["id"] + 1
        else:
            segs.append({"clip": s["clip"], "words": list(s["words"]), "next": s["id"] + 1})
    medians = {i: statistics.median(e) for i, e in enumerate(envs) if e}
    loud = lambda clip, t: clip in medians and envs[clip][min(int(t / 0.01), len(envs[clip]) - 1)] > LOUD * medians[clip]
    clip_words = {}
    for s in sents:
        clip_words.setdefault(s["clip"], []).extend(s["words"])
    for seg in segs:
        ws = clip_words[seg["clip"]]
        where = {id(w): i for i, w in enumerate(ws)}
        first, final = seg["words"][0], seg["words"][-1]
        i0, i1 = where[id(first)], where[id(final)]
        end = (durations or {}).get(seg["clip"], final["end"] + 1.0)
        lo = ws[i0 - 1]["end"] + 0.02 if i0 > 0 else 0.0                     # never into a neighbouring word
        hi = ws[i1 + 1]["start"] - 0.02 if i1 + 1 < len(ws) else end
        lo, hi = min(lo, first["start"]), max(min(hi, end), final["end"])
        env = envs[seg["clip"]]
        seg["in"] = quietest(env, max(first["start"] - PAD_IN, lo), lo, first["start"])
        seg["out"] = quietest(env, min(final["end"] + PAD_OUT, hi), final["end"], hi)
        seg["check"] = [side for side in ("in", "out") if loud(seg["clip"], seg[side])]   # no pause here to cut in
        del seg["next"]
    return segs


def timeline(segs):
    """Each segment's place on the cut's clock, and its words moved there (AssemblyAI shape, milliseconds)."""
    t, words = 0.0, []
    for seg in segs:
        seg["start"], seg["end"] = round(t, 3), round(t + seg["out"] - seg["in"], 3)
        off = seg["start"] - seg["in"]
        words += [{"text": w["text"], "start": round((w["start"] + off) * 1000), "end": round((w["end"] + off) * 1000),
                   "speaker": w.get("speaker")} for w in seg["words"]]
        t = seg["end"]
    return {"status": "completed", "text": " ".join(w["text"] for w in words), "words": words}


# ---------- outputs ----------

def fcpxml(sources, segs, out, version="1.11", bundle=True, project="Rough cut"):
    """A spine of clips from the sources, one per segment, in order: a real cut you can trim in the editor."""
    ms = [exports.probe(p) for p in sources]
    rate = ms[0]["rate"]
    rt = lambda s: exports._rt(s, rate)
    frame = 1 / rate
    res = [f'<format id="r1"{exports._format_name(ms[0])} frameDuration="{frame.numerator}/{frame.denominator}s" '
           f'width="{ms[0]["width"]}" height="{ms[0]["height"]}" colorSpace="1-1-1 (Rec. 709)"/>']
    for i, (p, m) in enumerate(zip(sources, ms)):
        audio = ' hasAudio="1" audioSources="1" audioChannels="2" audioRate="48000"' if m["audio"] else ""
        res.append(f'<asset id="a{i + 1}" name={quoteattr(os.path.splitext(os.path.basename(p))[0])} start="0s" '
                   f'duration="{rt(m["duration"])}" hasVideo="1" format="r1" videoSources="1"{audio}>\n'
                   f'      <media-rep kind="original-media" src={quoteattr(exports.file_url(p))}/>\n    </asset>')
    clips = []
    for seg in segs:
        name = " ".join(w["text"] for w in seg["words"])[:48]
        marks = "".join(f'\n              <marker start="{rt(seg["in"] if side == "in" else seg["out"] - frame)}" duration="{rt(frame)}" '
                        f'value="Check this cut: no pause to cut in"/>' for side in seg.get("check", ()))
        clips.append(f'<asset-clip ref="a{seg["clip"] + 1}" offset="{rt(seg["start"])}" name={quoteattr(name)} '
                     f'start="{rt(seg["in"])}" duration="{rt(seg["out"] - seg["in"])}" format="r1" tcFormat="NDF"'
                     + (f'>{marks}\n            </asset-clip>' if marks else "/>"))
    total = rt(segs[-1]["end"]) if segs else "0s"
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>

<fcpxml version="{version}">
  <resources>
    {chr(10).join("    " + r for r in res).strip()}
  </resources>
  <library>
    <event name={quoteattr(project)}>
      <project name={quoteattr(project)}>
        <sequence format="r1" duration="{total}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="48k">
          <spine>
            {(chr(10) + "            ").join(clips)}
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
"""
    if bundle:
        if os.path.isdir(out) and os.path.exists(os.path.join(out, "Info.fcpxml")):
            shutil.rmtree(out)
        os.makedirs(out)
        path = os.path.join(out, "Info.fcpxml")
    else:
        path = out
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml)
    return path


def premiere_xml(sources, segs, out, name="Rough cut"):
    """xmeml v4: one clipitem per segment on V1 and A1/A2, in order."""
    ms = [exports.probe(p) for p in sources]
    rate_f = ms[0]["rate"]
    tb, ntsc = round(rate_f), rate_f.denominator == 1001
    fr = lambda s: round(s * float(rate_f))
    rate = f"<rate><timebase>{tb}</timebase><ntsc>{'TRUE' if ntsc else 'FALSE'}</ntsc></rate>"
    chars = (f"<samplecharacteristics>{rate}<width>{ms[0]['width']}</width><height>{ms[0]['height']}</height>"
             f"</samplecharacteristics>")
    defined = set()

    def file_ref(clip):
        fid = f"file-{clip + 1}"
        if fid in defined:
            return f'<file id="{fid}"/>'
        defined.add(fid)
        p, m = sources[clip], ms[clip]
        a = ("<audio><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics>"
             "<channelcount>2</channelcount></audio>") if m["audio"] else ""
        return (f'<file id="{fid}"><name>{escape(os.path.basename(p))}</name><pathurl>'
                f'{escape(exports.file_url(p).replace("file://", "file://localhost", 1))}</pathurl>{rate}'
                f'<duration>{fr(m["duration"])}</duration><media><video>{chars}</video>{a}</media></file>')

    def item(kind, n, seg, extra=""):
        return (f'<clipitem id="{kind}{n}"><name>{escape(os.path.basename(sources[seg["clip"]]))}</name>{rate}'
                f'<start>{fr(seg["start"])}</start><end>{fr(seg["end"])}</end><in>{fr(seg["in"])}</in><out>{fr(seg["out"])}</out>'
                f'{file_ref(seg["clip"])}{extra}</clipitem>')

    video = "".join(item("v", n, s) for n, s in enumerate(segs))
    audio = "".join(f"<track>{''.join(item(f'a{ch}-', n, s, f'<sourcetrack><mediatype>audio</mediatype><trackindex>{ch}</trackindex></sourcetrack>') for n, s in enumerate(segs))}</track>"
                    for ch in (1, 2)) if any(m["audio"] for m in ms) else ""
    total = fr(segs[-1]["end"]) if segs else 0
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4"><sequence id="seq-1">'
           f'<name>{escape(name)}</name><duration>{total}</duration>{rate}<media><video><format>{chars}</format>'
           f'<track>{video}</track></video><audio>{audio}</audio></media></sequence></xmeml>\n')
    with open(out, "w", encoding="utf-8") as f:
        f.write(xml)
    return out


def render(sources, segs, out):
    """The cut as an H.264 MP4 at the first clip's size and frame rate, with 10 ms audio fades at every cut."""
    m = exports.probe(sources[0])
    has_audio = [exports.probe(p)["audio"] for p in sources]
    rate = f"{m['rate'].numerator}/{m['rate'].denominator}"
    inputs, parts, labels = [], [], []
    for p in sources:
        inputs += ["-i", p]
    for i, s in enumerate(segs):
        d = s["out"] - s["in"]
        parts.append(f"[{s['clip']}:v]trim={s['in']:.4f}:{s['out']:.4f},setpts=PTS-STARTPTS,fps={rate},"
                     f"scale={m['width']}:{m['height']}:force_original_aspect_ratio=decrease,"
                     f"pad={m['width']}:{m['height']}:-1:-1,setsar=1[v{i}]")
        audio = (f"[{s['clip']}:a]atrim={s['in']:.4f}:{s['out']:.4f},asetpts=PTS-STARTPTS,aresample=48000,"
                 f"aformat=channel_layouts=stereo," if has_audio[s['clip']]
                 else f"aevalsrc=0|0:s=48000:d={d:.4f},")                       # a take with no sound: silence
        parts.append(audio + f"afade=t=in:d={FADE},afade=t=out:st={max(d - FADE, 0):.4f}:d={FADE}[a{i}]")
        labels.append(f"[v{i}][a{i}]")
    graph = ";".join(parts) + f";{''.join(labels)}concat=n={len(segs)}:v=1:a=1[v][a]"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out], check=True)
    return out


# ---------- the whole job ----------

def run(sources, transcripts, base, llm, editors=(), make_mp4=False, log=print):
    """transcripts: one transcript JSON per source. Returns the cut's summary (also written to <base>.cut.json)."""
    from .assemblyai import words as transcript_words
    sents = sentences([transcript_words(t) for t in transcripts])
    if not sents:
        raise SystemExit("No speech found to cut.")
    keep = decide(sents, llm)
    envs = [envelope(p) for p in sources]
    durations = {i: exports.probe(p)["duration"] for i, p in enumerate(sources)}
    segs = segments(sents, keep, envs, durations)
    if not segs:
        raise SystemExit("Every sentence was dropped; nothing left to cut.")
    retimed = timeline(segs)
    summary = {
        "sources": [os.path.abspath(p) for p in sources],
        "segments": [{k: seg[k] for k in ("clip", "in", "out", "start", "end", "check")} | {"text": lines.join(seg["words"])}
                     for seg in segs],
        "dropped": [{"clip": s["clip"], "start": s["start"], "end": s["end"], "text": s["text"]}
                    for s, k in zip(sents, keep) if not k],
        "duration": segs[-1]["end"],
        "source_duration": round(sum(durations.values()), 3),
    }
    with open(base + ".cut.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    with open(base + ".cut.transcript.json", "w", encoding="utf-8") as f:
        json.dump(retimed, f, ensure_ascii=False)
    log(f"Rough cut: {len(segs)} segments, {summary['duration']:.1f} s from {summary['source_duration']:.1f} s; "
        f"dropped {len(summary['dropped'])} sentences -> {base}.cut.json")
    for d in summary["dropped"]:
        log(f"  - [{stamp(d['clip'], d['start'])}] {d['text']}")
    tight = [(seg, side) for seg in segs for side in seg["check"]]
    if tight:
        log(f"  {len(tight)} cut(s) fall mid-speech (no pause there), marked in the projects to check: "
            + ", ".join(f"{stamp(seg['clip'], seg[side])} ({side})" for seg, side in tight))
    for ed in editors:
        if ed == "finalcut":
            path = fcpxml(sources, segs, base + ".cut.fcpxmld", "1.11", True)
            log(f"  Final Cut Pro -> {base}.cut.fcpxmld" + (" (valid FCPXML 1.11)" if exports.validate_fcpxml(path, "1.11") else ""))
        elif ed == "resolve":
            path = fcpxml(sources, segs, base + ".cut.resolve.fcpxml", "1.10", False)
            log(f"  DaVinci Resolve -> {path}" + (" (valid FCPXML 1.10)" if exports.validate_fcpxml(path, "1.10") else ""))
        elif ed == "premiere":
            log(f"  Premiere Pro -> {premiere_xml(sources, segs, base + '.cut.premiere.xml')}")
    if make_mp4:
        log(f"  MP4 -> {render(sources, segs, base + '.cut.mp4')}")
    return summary

