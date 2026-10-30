"""Editor projects: Final Cut Pro, DaVinci Resolve and Adobe Premiere Pro.

    finalcut   <name>.fcpxmld        FCPXML 1.11 bundle: the video, and the captions as real Final Cut captions
                                      (editable text, your font), both lines for dual captions
    resolve    <name>.resolve.fcpxml FCPXML 1.10 (File > Import > Timeline), plus the .srt for the subtitle track
    premiere   <name>.premiere.xml   Final Cut Pro 7 XML sequence (File > Import), plus the .srt, which Premiere
                                      imports as a caption track

With an overlay (a ProRes 4444 render of the styled, word-by-word captions) it sits on the track above the
video in all three, so the captions look the same as the burn-in.
"""
import json
import os
import shutil
import subprocess
from fractions import Fraction
from urllib.parse import quote
from xml.sax.saxutils import escape, quoteattr

EDITORS = ("finalcut", "resolve", "premiere")
FCP_DTD_DIR = "/Applications/Final Cut Pro.app/Contents/Frameworks/Interchange.framework/Versions/A/Resources"


def probe(path):
    """{width, height, rate: Fraction, duration, audio} for a media file."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=codec_type,width,height,r_frame_rate:format=duration", "-of", "json", path],
                       capture_output=True, text=True, check=True)
    info = json.loads(r.stdout)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    if not v:
        raise SystemExit(f"{path} has no video stream: editor projects need a video")
    num, den = map(int, v["r_frame_rate"].split("/"))
    return {"width": int(v["width"]), "height": int(v["height"]), "rate": Fraction(num, den),
            "duration": float(info["format"]["duration"]), "audio": any(s["codec_type"] == "audio" for s in info["streams"])}


def file_url(path):
    return "file://" + quote(os.path.abspath(path))


# ---------- FCPXML (Final Cut Pro, DaVinci Resolve) ----------

def _rt(seconds, rate):
    """Seconds → rational time on the frame grid, e.g. 1001/30000s steps at 29.97."""
    frames = round(Fraction(seconds).limit_denominator(100000) * rate)
    # written in frameDuration's own units (n × 1001/30000s), the way Final Cut writes it
    return "0s" if frames == 0 else f"{frames * rate.denominator}/{rate.numerator}s"


def _format_name(m):
    rates = {Fraction(24000, 1001): "2398", Fraction(24): "24", Fraction(25): "25", Fraction(30000, 1001): "2997",
             Fraction(30): "30", Fraction(50): "50", Fraction(60000, 1001): "5994", Fraction(60): "60"}
    sizes = {(1920, 1080): "1080p", (3840, 2160): "2160p", (1280, 720): "720p"}
    if (m["width"], m["height"]) in sizes and m["rate"] in rates:
        return f' name="FFVideoFormat{sizes[(m["width"], m["height"])]}{rates[m["rate"]]}"'
    return ""


def fcpxml(video, tracks, font, out, version="1.11", bundle=True, overlay=None, project="Captions"):
    """FCPXML with the video on the spine, captions (one role per mode) and an optional overlay on lane 1."""
    m = probe(video)
    rate = m["rate"]
    rt = lambda s: _rt(s, rate)
    frame = 1 / rate
    dur = rt(m["duration"])
    fmt = (f'<format id="r1"{_format_name(m)} frameDuration="{frame.numerator}/{frame.denominator}s" '
           f'width="{m["width"]}" height="{m["height"]}" colorSpace="1-1-1 (Rec. 709)"/>')
    audio = ' hasAudio="1" audioSources="1" audioChannels="2" audioRate="48000"' if m["audio"] else ""
    res = [fmt, f'<asset id="a1" name={quoteattr(os.path.splitext(os.path.basename(video))[0])} start="0s" duration="{dur}" '
                f'hasVideo="1" format="r1" videoSources="1"{audio}>\n      <media-rep kind="original-media" src={quoteattr(file_url(video))}/>\n    </asset>']
    kids = []
    if overlay:
        res.append(f'<asset id="a2" name={quoteattr(os.path.splitext(os.path.basename(overlay))[0])} start="0s" duration="{rt(probe(overlay)["duration"])}" '
                   f'hasVideo="1" format="r1" videoSources="1">\n      <media-rep kind="original-media" src={quoteattr(file_url(overlay))}/>\n    </asset>')
        kids.append(f'<asset-clip ref="a2" lane="1" offset="0s" name="Captions (styled overlay)" start="0s" duration="{dur}" format="r1"/>')
    size = round(m["height"] * 0.05)
    for k, (mode, cues) in enumerate(tracks):
        lang = _caption_lang(mode)
        style = f"ts{k + 1}"
        for i, c in enumerate(cues):
            if c["end"] - c["start"] < 1 / float(rate):
                continue
            text = c["text"] + (f"\n{c['translation']}" if c.get("translation") else "")
            sdef = (f'\n            <text-style-def id="{style}"><text-style font={quoteattr(font.family)} fontSize="{size}" '
                    f'fontColor="1 1 1 1" backgroundColor="0 0 0 0.8"/></text-style-def>') if i == 0 else ""
            kids.append(f'<caption lane="{k + 2}" offset="{rt(c["start"])}" name={quoteattr(c["text"][:40])} start="{rt(c["start"])}" '
                        f'duration="{rt(c["end"] - c["start"])}" role="SRT?captionFormat=SRT.{lang}">\n'
                        f'            <text placement="bottom"><text-style ref="{style}">{escape(text)}</text-style></text>{sdef}\n          </caption>')
    body = "".join(f"\n          {k}" for k in kids)
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>

<fcpxml version="{version}">
  <resources>
    {chr(10).join("    " + r for r in res).strip()}
  </resources>
  <library>
    <event name={quoteattr(project)}>
      <project name={quoteattr(project)}>
        <sequence format="r1" duration="{dur}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="48k">
          <spine>
        <asset-clip ref="a1" offset="0s" name={quoteattr(os.path.basename(video))} start="0s" duration="{dur}" format="r1" tcFormat="NDF"{' audioRole="dialogue"' if m["audio"] else ''}>{body}
        </asset-clip>
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
"""
    if bundle:
        if os.path.isdir(out) and os.path.exists(os.path.join(out, "Info.fcpxml")):
            shutil.rmtree(out)                                   # our own earlier bundle for this video + mode
        os.makedirs(out)
        path = os.path.join(out, "Info.fcpxml")
    else:
        path = out
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml)
    return path


def _caption_lang(mode):
    from .languages import LANGUAGES, PRESETS
    first = mode.split("+")[0]
    if first in PRESETS:
        return "hi"
    return LANGUAGES.get(first, ("en",))[0]


def validate_fcpxml(path, version):
    """xmllint against Final Cut's own DTD, when Final Cut is installed. Returns None if it couldn't check."""
    dtd = os.path.join(FCP_DTD_DIR, f"FCPXMLv{version.replace('.', '_')}.dtd")
    if not (os.path.exists(dtd) and shutil.which("xmllint")):
        return None
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:               # xmllint can't open a DTD path with spaces
        local = os.path.join(tmp, "fcpxml.dtd")
        shutil.copy(dtd, local)
        r = subprocess.run(["xmllint", "--noout", "--dtdvalid", local, path], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"{path} failed FCPXML {version} validation:\n{r.stderr[-1500:]}")
    return True


# ---------- Final Cut Pro 7 XML (Adobe Premiere Pro) ----------

def premiere_xml(video, out, overlay=None, name="Captions"):
    """xmeml v4 sequence: the video on V1 (+ overlay on V2), its audio on A1/A2. Captions come from the .srt."""
    m = probe(video)
    ntsc = m["rate"].denominator == 1001
    tb = round(m["rate"])
    n = round(m["duration"] * float(m["rate"]))
    rate = f"<rate><timebase>{tb}</timebase><ntsc>{'TRUE' if ntsc else 'FALSE'}</ntsc></rate>"
    chars = f"<samplecharacteristics>{rate}<width>{m['width']}</width><height>{m['height']}</height></samplecharacteristics>"

    def file_el(fid, path, audio):
        a = "<audio><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics><channelcount>2</channelcount></audio>" if audio else ""
        return (f'<file id="{fid}"><name>{escape(os.path.basename(path))}</name><pathurl>{escape(file_url(path).replace("file://", "file://localhost", 1))}</pathurl>'
                f'{rate}<duration>{n}</duration><media><video>{chars}</video>{a}</media></file>')

    def clip(cid, fid, path, audio, first):
        f = file_el(fid, path, audio) if first else f'<file id="{fid}"/>'
        return (f'<clipitem id="{cid}"><name>{escape(os.path.basename(path))}</name><duration>{n}</duration>{rate}'
                f'<start>0</start><end>{n}</end><in>0</in><out>{n}</out>{f}</clipitem>')

    video_tracks = f"<track>{clip('v1', 'file-1', video, m['audio'], True)}</track>"
    if overlay:
        video_tracks += f"<track>{clip('v2', 'file-2', overlay, False, True)}</track>"
    audio_tracks = ""
    if m["audio"]:
        for ch in (1, 2):
            audio_tracks += (f'<track><clipitem id="a{ch}"><name>{escape(os.path.basename(video))}</name><duration>{n}</duration>{rate}'
                             f'<start>0</start><end>{n}</end><in>0</in><out>{n}</out><file id="file-1"/>'
                             f'<sourcetrack><mediatype>audio</mediatype><trackindex>{ch}</trackindex></sourcetrack></clipitem></track>')
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4"><sequence id="seq-1"><name>{escape(name)}</name>'
           f'<duration>{n}</duration>{rate}<media><video><format>{chars}</format>{video_tracks}</video>'
           f'<audio>{audio_tracks}</audio></media></sequence></xmeml>\n')
    with open(out, "w", encoding="utf-8") as f:
        f.write(xml)
    return out


# ---------- the styled overlay ----------

def overlay(video, ass_path, out, font=None):
    """Render the .ass captions alone as ProRes 4444 with alpha, at the video's size, rate and length (libass)."""
    m = probe(video)
    rate = m["rate"]
    from .writers import ass_filter
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"color=c=black@0.0:s={m['width']}x{m['height']}:r={rate.numerator}/{rate.denominator}:d={m['duration']:.3f},format=rgba",
         "-vf", ass_filter(ass_path, font.dir if font else None, alpha=True), "-c:v", "prores_ks", "-profile:v", "4444",
         "-pix_fmt", "yuva444p10le", "-vendor", "apl0", os.path.abspath(out)],
        cwd=os.path.dirname(os.path.abspath(ass_path)), check=True)
    return out
