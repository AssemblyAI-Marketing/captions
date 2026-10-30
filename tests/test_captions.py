"""Offline tests: no API key or network. Run from the repo root:

    python3 -m unittest -v

Tests that need ffmpeg, xmllint or Final Cut's DTDs skip themselves when those aren't installed.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from captions import assemblyai, cli, exports, fonts, languages, lines, modes, writers  # noqa: E402

EX = os.path.join(ROOT, "examples", "transcripts")
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def words(name):
    return assemblyai.words(json.loads(read(os.path.join(EX, name))))


def clip(path, rate="30000/1001", seconds=12):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=gray:s=1920x1080:r={rate}:d={seconds}",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", str(seconds), "-c:v", "libx264",
                    "-c:a", "aac", "-shortest", path], check=True)
    return path


class Languages(unittest.TestCase):
    def test_single(self):
        m = languages.parse("japanese")
        self.assertEqual((m.name, m.language, m.languages, m.script, m.translate_to), ("japanese", "ja", (), "ja", None))

    def test_pair_with_english_code_switches(self):
        m = languages.parse("japanese+english")
        self.assertEqual((m.language, m.languages, m.script, m.script2, m.translate_to),
                         (None, ("ja", "en"), "ja", "latin", "english"))
        self.assertEqual(m.label, "Japanese + English")
        self.assertTrue(m.needs_llm)

    def test_english_audio_translated(self):
        m = languages.parse("english+spanish")
        self.assertEqual((m.language, m.languages, m.translate_to), ("en", (), "spanish"))

    def test_aliases_and_hyphen(self):
        self.assertEqual(languages.parse("Chinese+English").name, "mandarin+english")
        self.assertEqual(languages.parse("mandarin-english").name, "mandarin+english")

    def test_presets(self):
        self.assertEqual(languages.parse("hinglish").languages, ("hi", "en"))
        self.assertTrue(languages.parse("hinglish-roman").romanize)
        self.assertEqual(languages.parse("hinglish", "english").translate_to, "english")

    def test_rtl_and_same_pair(self):
        self.assertTrue(languages.parse("arabic+english").rtl)
        self.assertIsNone(languages.parse("english+english").translate_to)

    def test_unknown(self):
        with self.assertRaises(ValueError):
            languages.parse("klingon")

    def test_every_popular_mode_parses(self):
        for name in languages.POPULAR:
            self.assertEqual(languages.parse(name).name, name)

    def test_transcript_key_shared(self):
        self.assertEqual(languages.parse("hinglish").transcript_key, languages.parse("hinglish-roman").transcript_key)


class Lines(unittest.TestCase):
    def test_width(self):
        self.assertEqual(lines.width("abc"), 3)
        self.assertEqual(lines.width("字幕"), 4)
        self.assertEqual(lines.width("한국어"), 6)

    def test_join(self):
        self.assertEqual(lines.join([{"text": "这个"}, {"text": "demo"}, {"text": "很"}, {"text": "简单。"}]), "这个 demo 很简单。")
        self.assertEqual(lines.join([{"text": "안녕"}, {"text": "하세요"}]), "안녕 하세요")

    def test_cues_fit_and_cover_every_word(self):
        for name in ("english.json", "hindi.json", "hinglish.json", "mandarin+english.json"):
            ws = words(name)
            cs = lines.cues(ws, max_width=32)
            self.assertEqual(sum(len(c["words"]) for c in cs), len(ws), name)
            for c in cs:
                self.assertLessEqual(lines.width(c["text"]), 35, c["text"])
                self.assertLess(c["start"], c["end"])
            for a, b in zip(cs, cs[1:]):
                self.assertLessEqual(a["end"], b["start"] + 1e-9)       # one line at a time

    def test_glue(self):
        ws = [{"text": t, "start": i * 0.3, "end": i * 0.3 + 0.25, "speaker": "A"}
              for i, t in enumerate("We are flying to New Delhi on Monday morning with the whole team.".split())]
        for c in lines.cues(ws, glue=lines.glue_pairs(["New Delhi"]), max_width=20):
            self.assertFalse(c["text"].endswith("New"), c["text"])

    def test_japanese_kanji_keeps_its_kana(self):
        toks = "今日 は 動画 に 字幕 を 付 ける 方法 を 紹介 し ます 。".split()
        ws = [{"text": t, "start": i * 0.25, "end": i * 0.25 + 0.2, "speaker": "A"} for i, t in enumerate(toks)]
        for c in lines.cues(ws, max_width=16):
            self.assertFalse(c["text"].endswith("付"), c["text"])

    def test_japanese_line_never_starts_on_particle(self):
        toks = "今日 は 新しい 字幕 の ツール を みんな に 紹介 し ます 。".split()
        ws = [{"text": t, "start": i * 0.25, "end": i * 0.25 + 0.2, "speaker": "A"} for i, t in enumerate(toks)]
        for c in lines.cues(ws, max_width=14)[1:]:
            self.assertNotIn(c["words"][0]["text"], lines.LEADERS, c["text"])


class Transcripts(unittest.TestCase):
    def test_full_transcript_words(self):
        ws = words("english.json")
        self.assertEqual(ws[0]["text"], "Captions")
        self.assertAlmostEqual(ws[0]["start"], 0.4)

    def test_mcp_utterances(self):
        mcp = {"transcript_id": "x", "status": "completed", "text": "…",
               "speakers": [{"speaker": "A", "text": "Hello there, this is a test.", "start": 1000, "end": 3000},
                            {"speaker": "B", "text": "大家好，今天我们来看看字幕。", "start": 3500, "end": 6000}]}
        ws = assemblyai.words(mcp)
        self.assertEqual([w["text"] for w in ws[:6]], ["Hello", "there,", "this", "is", "a", "test."])
        self.assertAlmostEqual(ws[0]["start"], 1.0)
        self.assertLessEqual(ws[5]["end"], 3.0)
        self.assertGreaterEqual(ws[6]["start"], 3.5)
        self.assertEqual("".join(w["text"] for w in ws[6:]), "大家好，今天我们来看看字幕。")
        self.assertTrue(all(a["end"] <= b["start"] for a, b in zip(ws, ws[1:])))

    def test_mcp_without_timings(self):
        with self.assertRaises(SystemExit):
            assemblyai.words({"text": "hi"})

    def test_submit_params(self):
        self.assertEqual(assemblyai.submit_params(languages.parse("japanese+english"))["language_codes"], ["ja", "en"])
        self.assertEqual(assemblyai.submit_params(languages.parse("korean"))["language_code"], "ko")
        self.assertTrue(assemblyai.submit_params(languages.parse("korean"))["speaker_labels"])


class AgentHandoff(unittest.TestCase):
    def test_request_then_answers(self):
        with tempfile.TemporaryDirectory() as d:
            base = os.path.join(d, "clip.hinglish-roman")
            llm = modes.Llm(None, base=base)
            mode = languages.parse("hinglish-roman")
            with self.assertRaises(modes.NeedsAgent):
                modes.build(words("hinglish.json"), mode, llm)
            req = json.loads(read(base + ".llm-request.json"))
            items = req["romanize"]["items"]
            answer = [[f"w{i}" for i, _ in enumerate(line)] for line in items]
            write(base + ".llm-answers.json", {"romanize": answer})
            cs = modes.build(words("hinglish.json"), mode, llm)
            self.assertEqual(cs[0]["words"][0]["text"], "w0")

    def test_wrong_shape_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            base = os.path.join(d, "clip.english+spanish")
            write(base + ".llm-answers.json", {"translate": ["only one"]})
            with self.assertRaises(SystemExit):
                modes.build(words("english.json"), languages.parse("english+spanish"), modes.Llm(None, base=base))


class Writers(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cues = lines.cues(words("english.json"))
        self.cues[0]["translation"] = "Los subtítulos"

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_srt_and_vtt(self):
        writers.srt(self.cues, os.path.join(self.d, "a.srt"))
        writers.vtt(self.cues, os.path.join(self.d, "a.vtt"))
        srt = read(os.path.join(self.d, "a.srt"))
        self.assertTrue(srt.startswith("1\n00:00:00,350 --> 00:00:01,"))
        self.assertIn("Captions don't have\nLos subtítulos\n", srt)
        self.assertTrue(read(os.path.join(self.d, "a.vtt")).startswith("WEBVTT\n\n00:00:00.350 -->"))

    def test_ass_karaoke_and_fonts(self):
        p = os.path.join(self.d, "a.ass")
        writers.ass(self.cues, p, (1920, 1080), True, "Atkinson Hyperlegible", "Hiragino Sans")
        ass = read(p)
        self.assertIn("Style: Main,Atkinson Hyperlegible,", ass)
        self.assertIn("Style: Sub,Hiragino Sans,", ass)
        first = next(l for l in ass.splitlines() if l.startswith("Dialogue:"))
        ks = sum(int(x) for x in __import__("re").findall(r"\\k(\d+)", first))
        self.assertAlmostEqual(ks / 100, self.cues[0]["end"] - self.cues[0]["start"], delta=0.05)
        self.assertIn("\\N{\\rSub}Los subtítulos", first)


class Hardening(unittest.TestCase):
    def test_filter_paths(self):
        self.assertEqual(writers.filter_path("/a b/fonts"), "/a b/fonts")
        for bad in ("/it's", "a\\b", "a\nb"):
            with self.assertRaises(SystemExit):
                writers.filter_path(bad)

    def test_preview_escapes_closing_script(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "p.html")
            cli.player([{"mode": "english", "label": "x", "cues": [{"start": 0, "end": 1, "text": "</script><b>",
                                                                       "words": [{"text": "</script><b>", "start": 0, "end": 1}]}]}], p, "t")
            html = read(p)
            self.assertEqual(html.count("</script>"), read(os.path.join(ROOT, "captions", "player.html")).count("</script>"))


class Fonts(unittest.TestCase):
    def test_defaults_per_script(self):
        for script in fonts.DEFAULTS:
            self.assertTrue(fonts.default(script))

    def test_system_name_passthrough(self):
        self.assertEqual(fonts.resolve("Some Installed Font"), fonts.Font("Some Installed Font"))

    def test_family_from_file(self):
        found = [p for p in ("/System/Library/Fonts/Helvetica.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf") if os.path.exists(p)]
        if not found:
            self.skipTest("no known system font file")
        self.assertIn(fonts.family_name(found[0]), ("Helvetica", "DejaVu Sans"))
        self.assertEqual(fonts.resolve(found[0]).path, found[0])

    def test_report_mentions_open_source(self):
        self.assertIn("Noto Sans JP", fonts.report(["ja"]))


@unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg/ffprobe")
class Exports(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        cls.video = clip(os.path.join(cls.d, "clip.mp4"))
        cls.cues = lines.cues(words("mandarin+english.json"))
        for c in cls.cues:
            c["translation"] = "English line & <test>"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d)

    def test_probe(self):
        m = exports.probe(self.video)
        self.assertEqual((m["width"], m["height"], str(m["rate"]), m["audio"]), (1920, 1080, "30000/1001", True))

    def test_rational_times_on_frame_grid(self):
        from fractions import Fraction
        self.assertEqual(exports._rt(1.0, Fraction(30000, 1001)), "30030/30000s")
        self.assertEqual(exports._rt(0, Fraction(30)), "0s")

    def test_finalcut_bundle(self):
        font = fonts.Font("Hiragino Sans")
        path = exports.fcpxml(self.video, [("mandarin+english", self.cues)], font, os.path.join(self.d, "c.fcpxmld"), "1.11")
        root = ET.parse(path).getroot()
        caps = root.findall(".//caption")
        self.assertEqual(len(caps), len(self.cues))
        self.assertIn("SRT.zh", caps[0].get("role"))
        self.assertEqual(root.find(".//format").get("name"), "FFVideoFormat1080p2997")
        self.assertIn("English line & <test>", "".join(caps[0].itertext()))
        if exports.validate_fcpxml(path, "1.11") is None:
            self.skipTest("Final Cut's DTD or xmllint not installed; structure checked only")

    def test_resolve_file(self):
        path = exports.fcpxml(self.video, [("japanese+english", self.cues)], fonts.Font("Arial"),
                              os.path.join(self.d, "c.resolve.fcpxml"), "1.10", bundle=False)
        self.assertEqual(ET.parse(path).getroot().get("version"), "1.10")
        exports.validate_fcpxml(path, "1.10")

    def test_premiere_xml(self):
        path = exports.premiere_xml(self.video, os.path.join(self.d, "c.premiere.xml"))
        root = ET.parse(path).getroot()
        self.assertEqual(root.tag, "xmeml")
        self.assertEqual(root.find("./sequence/rate/ntsc").text, "TRUE")
        self.assertEqual(root.find("./sequence/rate/timebase").text, "30")
        self.assertEqual(int(root.find("./sequence/duration").text), 360)
        self.assertEqual(len(root.findall("./sequence/media/audio/track")), 2)
        self.assertTrue(root.find(".//pathurl").text.startswith("file://localhost/"))


@unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg/ffprobe")
class Cli(unittest.TestCase):
    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                cli.main(list(args))
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue()

    def test_agent_round_trip_with_all_editors(self):
        with tempfile.TemporaryDirectory() as d:
            video = clip(os.path.join(d, "clip.mp4"), rate="25")
            args = (video, "--transcript", os.path.join(EX, "english.json"), "--mode", "english",
                    "--mode", "english+spanish", "--llm", "agent", "--editor", "all", "--out", d)
            code, out = self.run_cli(*args)
            self.assertEqual(code, cli.AGENT_EXIT, out)
            self.assertTrue(os.path.exists(os.path.join(d, "clip.english.fcpxmld", "Info.fcpxml")))
            req = json.loads(read(os.path.join(d, "clip.english+spanish.llm-request.json")))
            write(os.path.join(d, "clip.english+spanish.llm-answers.json"),
                  {"translate": [f"es {i}" for i, _ in enumerate(req["translate"]["items"])]})
            code, out = self.run_cli(*args)
            self.assertEqual(code, 0, out)
            for suffix in (".srt", ".vtt", ".ass", ".json", ".resolve.fcpxml", ".premiere.xml"):
                self.assertTrue(os.path.exists(os.path.join(d, "clip.english+spanish" + suffix)), suffix)
            self.assertIn("es 0", read(os.path.join(d, "clip.english+spanish.srt")))
            html = read(os.path.join(d, "clip.preview.html"))
            self.assertIn('"mode": "english+spanish"', html)

    def test_url_with_editor_refused(self):
        code, _ = self.run_cli("https://example.com/a.mp4", "--transcript", os.path.join(EX, "english.json"), "--editor", "finalcut")
        self.assertNotEqual(code, 0)

    def test_modes_and_fonts_commands(self):
        code, out = self.run_cli("modes")
        self.assertIn("japanese", out)
        code, out = self.run_cli("fonts", "--mode", "japanese+english")
        self.assertIn("ja:", out)
        self.assertIn("latin:", out)


class Commands(unittest.TestCase):
    def test_every_popular_mode_has_a_command(self):
        d = os.path.join(ROOT, ".claude", "commands")
        for name in languages.POPULAR:
            text = read(os.path.join(d, f"{name}.md"))
            self.assertIn(f"mode `{name}`", text)
            self.assertTrue(text.startswith('---\ndescription: "'), name)

    def test_skill_frontmatter(self):
        text = read(os.path.join(ROOT, ".claude", "skills", "captions", "SKILL.md"))
        self.assertTrue(text.startswith("---\nname: captions\ndescription: "))


if __name__ == "__main__":
    unittest.main()
