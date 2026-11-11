---
name: rough-cut
description: Rough cut talking-head recordings with AssemblyAI - drop retakes, false starts and warm-up chatter, cut in the pauses, and hand the cut to Final Cut Pro, DaVinci Resolve or Premiere Pro (plus an optional MP4), ready to caption. Use when the user asks to cut, trim or clean up a recording, remove retakes or mistakes, make a first or rough cut, or runs /rough-cut.
---

# Rough cut

Work from the repo root. Everything runs through `python3 -m captions cut` (standard library; `ffmpeg`/`ffprobe`).

## 1. Takes and editor

- The recordings are the command's arguments, in the order they were shot. If there are none, ask for local paths (the cut needs the media itself, not a URL).
- Ask with AskUserQuestion (multiSelect) which editor to hand the cut to: Final Cut Pro (`finalcut`), DaVinci Resolve (`resolve`), Adobe Premiere Pro (`premiere`), and whether to also render an MP4 (`--render`).
- Ask what language is spoken only if it isn't obvious (default `english`; `hinglish`, `japanese`… as in `python3 -m captions modes`).

## 2. Transcribe

**With an API key:** skip to step 3; the CLI transcribes each take (and keeps the transcript next to it).

**With the AssemblyAI MCP server:** for each take, as in the captions skill: `python3 -m captions plan TAKE --mode <mode>` → `create_upload_url` + its `curl` with `@<upload_file>` (never print or store that link) → `submit_transcript` with the printed parameters → poll `get_transcript`, then once more with `include_words: true` → save it as `<take>.mcp-transcript.json`. Word timings matter here: the cut is made on them. Pass one `--transcript` per take, in the same order.

## 3. Cut

```
python3 -m captions cut TAKE1 [TAKE2 ...] [--transcript T1.json --transcript T2.json] [--mode <mode>] [--editor finalcut --editor resolve --editor premiere] [--render] [--llm agent|none]
```

**Exit code 3** means the keep/drop decision is waiting for you in `<name>.cut.llm-request.json`: one item per sentence, `N. [take T m:ss] text`, in order. Read them all first, then answer `<name>.cut.llm-answers.json` as `{"cut": [true, false, ...]}` (one boolean per item; true keeps it):

- When a line is said more than once, keep the **last complete** take and drop the earlier ones, including a take that ends in a restart ("…or and step number two…").
- Drop warm-up and wrap-up chatter ("All right. Okay.", "Let me try again", "Promise something."), lone fragments ("So,", "Now,") and filler-only sentences.
- Keep everything else in order, even if informal; keep a lead-in question with the answer that follows it.

Then run the same command again. With an API key LLM Gateway decides instead; `--llm none` uses an offline rule (later retakes win, filler goes) that's rougher.

## 4. Check and hand over

Tell the user how long the cut is against the takes, list what was dropped (the CLI prints it), and name any cuts it flagged as **mid-speech** (no pause there to cut in): they carry a "Check this cut" marker in Final Cut and Resolve.

- **Final Cut Pro:** `open -a "Final Cut Pro" "<name>.cut.fcpxmld"`. Every segment is a clip on the spine, so trims are one drag.
- **DaVinci Resolve:** File > Import > Timeline… → `<name>.cut.resolve.fcpxml`.
- **Premiere Pro:** File > Import… → `<name>.cut.premiere.xml`.
- **Next:** captions on the cut. `<name>.cut.transcript.json` is the kept words on the cut's clock, so with `--render`: `python3 -m captions <name>.cut.mp4 --transcript <name>.cut.transcript.json --mode <mode>` (then the captions skill: font, editor). No second transcription.

Don't commit recordings, renders, transcripts or `*.llm-*.json`.
