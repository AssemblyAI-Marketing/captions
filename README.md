# Multilingual Captions (AssemblyAI)

Caption a video in **20+ languages, or two at once** (Japanese on top, English underneath), in your own font, and
open it in **Final Cut Pro, DaVinci Resolve or Premiere Pro**. Powered by [AssemblyAI](https://www.assemblyai.com)
Speech-to-Text (`universal-3-5-pro`, with code-switching for speech that mixes languages). Each word shows up grey as
it's spoken and turns white when it ends.

Every style, frozen mid-line so you can see words landing. Spanish, French, German, Portuguese, Italian, Japanese,
Korean and Mandarin are real AssemblyAI output (transcription and translation) on speech written for this repo;
English, Arabic, Hindi and Hinglish are written samples:

| | |
|---|---|
| <img src="docs/gallery/english.png" alt="english captions" width="420"> | <img src="docs/gallery/spanish+english.png" alt="spanish+english captions" width="420"> |
| <img src="docs/gallery/french+english.png" alt="french+english captions" width="420"> | <img src="docs/gallery/german+english.png" alt="german+english captions" width="420"> |
| <img src="docs/gallery/portuguese+english.png" alt="portuguese+english captions" width="420"> | <img src="docs/gallery/italian+english.png" alt="italian+english captions" width="420"> |
| <img src="docs/gallery/japanese+english.png" alt="japanese+english captions" width="420"> | <img src="docs/gallery/korean+english.png" alt="korean+english captions" width="420"> |
| <img src="docs/gallery/mandarin+english.png" alt="mandarin+english captions" width="420"> | <img src="docs/gallery/arabic+english.png" alt="arabic+english captions" width="420"> |
| <img src="docs/gallery/hindi.png" alt="hindi captions" width="420"> | <img src="docs/gallery/hinglish.png" alt="hinglish captions" width="420"> |
| <img src="docs/gallery/hinglish-roman.png" alt="hinglish-roman captions" width="420"> | |

Try it without a key: open [`demo.html`](demo.html) in a browser.

## In Claude Code

Open this folder in Claude Code and run a command with your video:

```
/japanese+english ~/Movies/talk.mov
```

The agent asks which **font** and which **editor** you want, transcribes, and hands you the project. Ready commands:

| | |
|---|---|
| Single language | `/english` `/spanish` `/french` `/german` `/portuguese` `/italian` `/japanese` `/korean` `/mandarin` `/hindi` `/arabic` `/vietnamese` `/russian` `/turkish` |
| Dual (two lines) | `/spanish+english` `/french+english` `/german+english` `/portuguese+english` `/japanese+english` `/korean+english` `/mandarin+english` `/hindi+english` `/arabic+english` `/english+spanish` `/english+mandarin` |
| Hindi + English | `/hinglish` (each language in its own script) · `/hinglish-roman` (all in the English alphabet) |

Any other pair works too: ask for "captions in Dutch with Turkish underneath". The workflow lives in
[`.claude/skills/captions/SKILL.md`](.claude/skills/captions/SKILL.md); the commands are generated from
`captions/languages.py` by `scripts/make_commands.py`.

## On the command line

Python 3.9+, standard library only. `ffmpeg`/`ffprobe` for editor projects.

```bash
git clone https://github.com/AssemblyAI-Marketing/multilingual-captions-aai.git
```

```bash
cd multilingual-captions-aai && cp .env.example .env
```

Put your [AssemblyAI API key](https://www.assemblyai.com/dashboard) in `.env`, then:

```bash
python3 -m captions talk.mp4 --mode japanese+english --font "Hiragino Sans" --editor finalcut
```

Next to the video, for each mode:

| File | |
|---|---|
| `talk.<mode>.srt`, `.vtt` | Captions for YouTube, X, LinkedIn or any player |
| `talk.<mode>.ass` | Styled, word-by-word captions for burn-in |
| `talk.<mode>.fcpxmld` | `--editor finalcut`: FCPXML 1.11, your video plus real Final Cut captions in your font |
| `talk.<mode>.resolve.fcpxml` | `--editor resolve`: FCPXML 1.10 (File › Import › Timeline), plus the `.srt` (File › Import › Subtitle) |
| `talk.<mode>.premiere.xml` | `--editor premiere`: a sequence (File › Import), plus the `.srt`, which Premiere imports as a caption track |
| `talk.<mode>.overlay.mov` | `--overlay`: the styled captions as ProRes 4444 with alpha, on the track above the video in each project |
| `talk.preview.html` | The video with every mode's captions, in a browser |

| Flag | |
|---|---|
| `--mode` | A language, or two joined with `+`. Repeatable. `python3 -m captions modes` lists them |
| `--font`, `--font2` | First and second line fonts: a family name, a file, or a font in `fonts/` |
| `--editor` | `finalcut`, `resolve`, `premiere` or `all`. Repeatable |
| `--overlay`, `--burn` | Styled captions with alpha in each project, or burned into a copy of the video (needs ffmpeg with libass) |
| `--keyterm` | A name or product to transcribe exactly, e.g. `--keyterm AssemblyAI`. Repeatable |
| `--transcript` | Use an existing transcript (API or MCP) instead of transcribing |
| `--llm agent` | Let your agent do the translation instead of LLM Gateway (see below) |
| `--glue`, `--max-width` | Keep a phrase on one line; line length in columns (Chinese, Japanese and Korean count double) |

The transcript is cached as `talk.<language>.transcript.json`, so adding modes later doesn't transcribe again.

## Fonts

No fonts ship with this repo. Captions use a font already on your computer (a sensible default per script), or one
you drop into [`fonts/`](fonts/) (git-ignored). `python3 -m captions fonts --mode japanese+english` lists the
defaults, what's in `fonts/`, and open-source picks such as Noto Sans JP and Atkinson Hyperlegible (SIL Open Font
License). The family name is read from the font file, so Final Cut, the overlay, burn-in and the preview all match.

## With the AssemblyAI MCP server

No API key on hand? The captions skill can transcribe through the [AssemblyAI MCP server](https://github.com/AssemblyAI/assemblyai-mcp):

1. `python3 -m captions plan talk.mp4 --mode japanese+english` makes an upload-sized audio file (under 4.5 MB) and
   prints the `submit_transcript` parameters.
2. The agent uploads it with `create_upload_url`, calls `submit_transcript`, polls `get_transcript`, and saves the
   result as JSON.
3. `python3 -m captions talk.mp4 --mode japanese+english --transcript talk.mcp-transcript.json --llm agent`

The MCP server returns sentence timings rather than word timings, so captions are timed per sentence with the words
spread across it; an API key gives word-exact timing. Translation and Hinglish romanizing normally use AssemblyAI's
LLM Gateway; with `--llm agent` the CLI writes them to `talk.<mode>.llm-request.json` and exits with code 3, the
agent answers in `talk.<mode>.llm-answers.json`, and the same command finishes the job.

## How it works

1. **Transcribe** with `universal-3-5-pro` (falling back to `universal-2` for languages outside it). A pair with
   English, like `japanese+english`, uses code-switching (`language_codes: ["ja", "en"]`).
2. **Break lines** at speaker changes, pauses and sentence ends, then into the fewest, most even lines that fit.
   Lines don't end on "the", "का" or "de", Japanese lines don't start on a particle, and `--glue`/`--keyterm`
   phrases stay together.
3. **Romanize** (`hinglish-roman`) word for word, so every word keeps its timing, and **translate** each line on
   its own, so the second line changes in step with the first.
4. **Write** captions, editor projects, the overlay and the preview.

## Tests

```bash
python3 -m unittest -v
```

Offline: no key or network. Final Cut and Resolve projects are validated against Final Cut's own FCPXML DTDs when
Final Cut is installed. CI runs the suite on every push.

## Layout

```
captions/
  languages.py   languages, modes ("japanese+english"), popular commands
  assemblyai.py  upload, transcribe, LLM Gateway, MCP transcripts
  lines.py       line breaking for Latin, Cyrillic, Devanagari, Arabic, Hebrew, Chinese, Japanese, Korean
  modes.py       romanizing, translating, the agent hand-off
  fonts.py       font files, system defaults, the font report
  writers.py     SRT, VTT, ASS, burn-in
  exports.py     Final Cut Pro, DaVinci Resolve, Premiere Pro, the overlay
  player.html    preview and demo player
  cli.py         python3 -m captions
.claude/         the captions skill and one command per popular mode
examples/        sample captions; transcripts/ holds sample transcript JSON
```

## Privacy and security

Your audio goes to your own AssemblyAI account and nowhere else; the API key is never written to any file. See
[SECURITY.md](SECURITY.md). Every caption in `examples/`, `docs/` and `demo.html` was written for this repo, with
made-up timings.

Unofficial: a side project by someone who works at AssemblyAI, not an AssemblyAI product.

## License

MIT
