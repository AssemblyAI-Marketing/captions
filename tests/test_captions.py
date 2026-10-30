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
from captions import assemblyai, cli, exports, fonts, languages, lines, modes, profiles, writers  # noqa: E402

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


def timed(text, speakers="A", step=0.3, gap_after=None):
    """Words spoken one every step seconds; gap_after={index: seconds} adds a pause after a word; speakers cycles
    per sentence."""
    out, t, sentence = [], 0.0, 0
    for i, tok in enumerate(text.split()):
        out.append({"text": tok, "start": round(t, 3), "end": round(t + step * 0.8, 3),
                    "speaker": speakers[sentence % len(speakers)]})
        t += step + (gap_after or {}).get(i, 0)
        sentence += tok.endswith((".", "?", "!"))
    return out


def clip(path, rate="30000/1001", seconds=12, size="1920x1080"):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=gray:s={size}:r={rate}:d={seconds}",
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


class Profiles(unittest.TestCase):
    def test_landscape_and_vertical(self):
        self.assertEqual(profiles.for_size(1920, 1080)[1:], profiles.HORIZONTAL[1:])
        self.assertEqual(profiles.for_size(1080, 1920)[1:], profiles.VERTICAL[1:])
        self.assertEqual((profiles.HORIZONTAL.width, profiles.HORIZONTAL.cps), (42, 17.0))
        self.assertEqual((profiles.VERTICAL.width, profiles.VERTICAL.cps), (22, 15.0))

    def test_in_between_and_beyond(self):
        square = profiles.for_size(1080, 1080)
        self.assertEqual(square.name, "1:1")
        self.assertTrue(profiles.VERTICAL.width < square.width < profiles.HORIZONTAL.width)
        self.assertTrue(profiles.VERTICAL.margin > square.margin > profiles.HORIZONTAL.margin)
        self.assertEqual(profiles.for_size(2560, 1080).width, 42)          # wider than 16:9 stays at 16:9
        self.assertEqual(profiles.for_size(1918, 1080).name, "16:9")       # snaps within 2%

    def test_parse(self):
        self.assertEqual(profiles.parse("vertical"), profiles.VERTICAL)
        self.assertEqual(profiles.parse("9x16")[1:], profiles.VERTICAL[1:])
        self.assertEqual(profiles.parse("4:5").name, "4:5")
        for bad in ("tall", "0:9", "16"):
            with self.assertRaises(ValueError):
                profiles.parse(bad)

    def test_vertical_lines_are_short(self):
        for name in ("english.json", "mandarin+english.json"):
            for c in lines.cues(words(name), max_width=22, profile=profiles.VERTICAL):
                self.assertLessEqual(lines.width(c["text"]), 25, c["text"])


class Timing(unittest.TestCase):
    P = profiles.HORIZONTAL

    def test_held_for_reading_time(self):
        # a fast 38-character line, then a long silence: it stays up long enough to read at 17 characters a second
        ws = timed("Reading speed matters for every viewer.", step=0.2) + timed("Next.", step=0.3)
        for w in ws[-1:]:
            w["start"], w["end"] = 6.0, 6.3
        c = lines.cues(ws, max_width=42, profile=self.P)[0]
        self.assertGreaterEqual(c["end"] - c["start"], lines.width(c["text"]) / self.P.cps - 1e-3)

    def test_min_duration(self):
        ws = timed("Hi.") + [{"text": "Later.", "start": 5.0, "end": 5.4, "speaker": "A"}]
        c = lines.cues(ws, profile=self.P)[0]
        self.assertGreaterEqual(c["end"] - c["start"], self.P.min_duration - 1e-3)

    def test_hold_never_covers_the_next_line_or_a_pause(self):
        ws = timed("One two three four. Five six seven eight.", gap_after={3: 1.2})
        a, b = lines.cues(ws, max_width=42, profile=self.P)
        self.assertGreaterEqual(b["start"] - a["end"], lines.BLANK - 1e-3)       # the pause shows as a blank screen
        ws = timed("A quick line that keeps going. And the next one right away.", step=0.1)
        cs = lines.cues(ws, max_width=42, profile=self.P)
        for x, y in zip(cs, cs[1:]):
            self.assertLessEqual(x["end"], y["start"] + 1e-9)

    def test_max_duration_splits_slow_speech(self):
        ws = timed("so we we waited and waited and waited", step=1.2)
        cs = lines.cues(ws, max_width=42, profile=self.P)
        self.assertGreater(len(cs), 1)
        for c in cs:
            self.assertLessEqual(c["end"] - c["start"], self.P.max_duration + 1e-9, c["text"])

    def test_real_transcripts_follow_the_rules(self):
        for name in ("english.json", "hindi.json", "hinglish.json", "mandarin+english.json"):
            cs = lines.cues(words(name), max_width=42, profile=self.P)
            for c in cs:
                self.assertLessEqual(c["end"] - c["start"], self.P.max_duration + 1e-9)
                self.assertLess(c["start"], c["end"])
            for a, b in zip(cs, cs[1:]):
                self.assertLessEqual(a["end"], b["start"] + 1e-9)


class Styles(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cues = lines.cues(timed("We shipped Universal-3 Pro today. It is fast. Thanks for having me.", speakers="AB"),
                               max_width=42, profile=profiles.HORIZONTAL)

    def tearDown(self):
        shutil.rmtree(self.d)

    def ass(self, **kw):
        p = os.path.join(self.d, "a.ass")
        writers.ass(self.cues, p, kw.pop("size", (1920, 1080)), kw.pop("karaoke", True), "Arial", **kw)
        return read(p)

    def dialogues(self, ass):
        return [l for l in ass.splitlines() if l.startswith("Dialogue:")]

    def test_default_unchanged(self):
        ass = self.ass()
        self.assertIn("Style: Main,Arial,54,&H00FFFFFF,&H00999999,", ass)
        self.assertIn("\\k", self.dialogues(ass)[0])

    def test_high_contrast_yellow_on_black_without_motion(self):
        ass = self.ass(style="high-contrast")
        self.assertIn(",&H003BEBFF,&H003BEBFF,&H00000000,&H00000000,1,", ass)
        self.assertNotIn("\\k", "".join(self.dialogues(ass)))
        self.assertNotIn("\\t(", ass)

    def test_social_highlights_key_words(self):
        self.cues[0]["words"][2]["key"] = True
        ass = self.ass(style="social")
        first = self.dialogues(ass)[0]
        self.assertIn("\\3c&H0000D4FF", first)
        self.assertEqual(first.count("\\3c&H0000D4FF"), 1)
        self.assertIn("Universal-3{\\rMain}", first)
        self.assertNotIn("\\3c&H0000D4FF", self.ass(style="default"))      # only social draws key words

    def test_vertical_fits_and_sits_higher(self):
        ass = self.ass(size=(1080, 1920), profile=profiles.VERTICAL, style="social")
        main = next(l for l in ass.splitlines() if l.startswith("Style: Main,")).split(",")
        fs, ml, mv = int(main[2]), int(main[19]), int(main[21])
        self.assertLessEqual(fs * 0.58 * profiles.VERTICAL.width, 1080 - 2 * ml)
        self.assertEqual(mv, round(1920 * profiles.VERTICAL.margin))
        self.assertGreater(mv, round(1080 * profiles.HORIZONTAL.margin))


class SpeakerNames(unittest.TestCase):
    def setUp(self):
        self.cues = lines.cues(timed("Hello there. Hi! How are you? Fine thanks. Great.", speakers="AABBA"),
                               max_width=42, profile=profiles.HORIZONTAL)

    def test_when(self):
        self.assertEqual(writers.speaker_labels(self.cues, "never"), [None] * 5)
        self.assertEqual(writers.speaker_labels(self.cues, "always"),
                         ["Speaker A", "Speaker A", "Speaker B", "Speaker B", "Speaker A"])
        self.assertEqual(writers.speaker_labels(self.cues, "change"), ["Speaker A", None, "Speaker B", None, "Speaker A"])
        one = lines.cues(timed("Just me. Still me."), profile=profiles.HORIZONTAL)
        self.assertEqual(writers.speaker_labels(one, "change"), [None, None])        # a monologue needs no names

    def test_real_names_kept(self):
        self.assertEqual(writers.speaker_name("Alice Chen"), "Alice Chen")
        self.assertEqual(writers.speaker_name("B"), "Speaker B")

    def test_on_screen_coloured_per_speaker(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.ass")
            writers.ass(self.cues, p, speakers="change")
            ds = [l for l in read(p).splitlines() if l.startswith("Dialogue:")]
            self.assertIn("{\\rSpeaker\\c" + writers.SPEAKER_COLORS[0] + "}Speaker A\\N{\\rMain}", ds[0])
            self.assertIn("{\\rSpeaker\\c" + writers.SPEAKER_COLORS[1] + "}Speaker B", ds[2])
            self.assertNotIn("Speaker", ds[1])
            writers.vtt(self.cues, os.path.join(d, "a.vtt"), "change")
            self.assertIn("<v Speaker B>How are you?", read(os.path.join(d, "a.vtt")))


class KeyWords(unittest.TestCase):
    def test_budget_and_one_request_with_the_translation(self):
        with tempfile.TemporaryDirectory() as d:
            base = os.path.join(d, "clip.english+spanish")
            llm = modes.Llm(None, base=base)
            mode = languages.parse("english+spanish")
            with self.assertRaises(modes.NeedsAgent):
                modes.build(words("english.json"), mode, llm, profile=profiles.HORIZONTAL, key_words=True)
            req = json.loads(read(base + ".llm-request.json"))
            self.assertEqual(set(req), {"translate", "keywords"})
            items = req["keywords"]["items"]
            # three picks for the first line (one over budget, one in another case), none for the rest
            picks = [[w.strip(".,").upper() for w in items[0].split()[:3]]] + [[] for _ in items[1:]]
            write(base + ".llm-answers.json", {"translate": ["es"] * len(items), "keywords": picks})
            cs = modes.build(words("english.json"), mode, llm, profile=profiles.HORIZONTAL, key_words=True)
            self.assertEqual(sum(bool(w.get("key")) for w in cs[0]["words"]), modes.KEY_WORDS)
            self.assertFalse(any(w.get("key") for c in cs[1:] for w in c["words"]))


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

    def test_vertical_social_with_speaker_names(self):
        with tempfile.TemporaryDirectory() as d:
            video = clip(os.path.join(d, "tall.mp4"), rate="30", size="1080x1920")
            args = (video, "--transcript", os.path.join(EX, "english.json"), "--mode", "english", "--llm", "agent",
                    "--style", "social", "--speaker-names", "always", "--out", d)
            code, out = self.run_cli(*args)
            self.assertEqual(code, cli.AGENT_EXIT, out)
            self.assertIn("Profile: 9:16 (vertical): 22-column lines", out)
            req = json.loads(read(os.path.join(d, "tall.english.llm-request.json")))
            first = req["keywords"]["items"][0].split()[0]
            write(os.path.join(d, "tall.english.llm-answers.json"),
                  {"keywords": [[first]] + [[] for _ in req["keywords"]["items"][1:]]})
            code, out = self.run_cli(*args)
            self.assertEqual(code, 0, out)
            ass = read(os.path.join(d, "tall.english.ass"))
            self.assertIn("PlayResX: 1080", ass)
            self.assertIn("}Speaker A\\N", ass)
            self.assertIn("\\3c&H0000D4FF", ass)
            cs = json.loads(read(os.path.join(d, "tall.english.json")))["cues"]
            self.assertTrue(all(lines.width(c["text"]) <= 25 for c in cs))

    def test_bad_aspect_refused(self):
        code, _ = self.run_cli("https://example.com/a.mp4", "--transcript", os.path.join(EX, "english.json"),
                               "--aspect", "tall")
        self.assertNotEqual(code, 0)

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
