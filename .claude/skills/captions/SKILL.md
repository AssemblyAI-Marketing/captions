---
name: captions
description: Caption a video with AssemblyAI in a language or a language pair (english, japanese+english, hinglish, english+spanish…) in the user's chosen font, and hand it to Final Cut Pro, DaVinci Resolve or Premiere Pro. Use when the user asks for captions or subtitles, bilingual or dual captions, Hinglish, or runs a caption slash command such as /japanese+english or /hinglish.
---

# Captions

Work from the repo root. Everything runs through `python3 -m captions` (standard library only; `ffmpeg`/`ffprobe` for editor projects).

## 1. Mode and video

- The mode comes from the slash command (`/mandarin+english` → `mandarin+english`) or from the user. Any language, or two joined with `+` (first line + translated second line), plus `hinglish` and `hinglish-roman`. List them with `python3 -m captions modes`.
- The video is the command's argument. If there isn't one, ask for a local path or a public URL.

## 2. Ask for the font (always, before rendering)

Run `python3 -m captions fonts --mode <mode>`, then ask with AskUserQuestion:

- the default it prints for this computer (recommended),
- each font already in `fonts/`,
- one open-source option it lists (if picked, the user downloads it into `fonts/`; never download, commit or bundle font files yourself),
- "Other" takes any installed family name or a font file path.

For a dual mode whose second line is a different script (`japanese+english`), ask for the second line's font too (`--font2`). If both lines share a script, one font covers both.

## 3. Ask which editor

AskUserQuestion, multiSelect: Final Cut Pro (`finalcut`), DaVinci Resolve (`resolve`), Adobe Premiere Pro (`premiere`), or just caption files. If `ffmpeg -filters | grep libass` lists it, also offer the styled overlay (`--overlay`, word-by-word captions with alpha on the track above the video) and a burned-in copy (`--burn`). If they pick either, ask for the style: `default` (white on a dark box, word by word), `high-contrast` (yellow on black, no motion, for accessibility) or `social` (bold, key words highlighted; good for vertical clips). If more than one person speaks, offer speaker names on screen (`--speaker-names change`). Line length, timing and placement follow the video's shape on their own (vertical video gets short lines set higher up); don't ask.

## 4. Transcribe

**With an API key** (`ASSEMBLYAI_API_KEY` in the environment or `.env`): skip to step 5; the CLI transcribes.

**With the AssemblyAI MCP server** (tools `submit_transcript`, `get_transcript`, and `create_upload_url` for local files):

1. `python3 -m captions plan VIDEO --mode <mode>` prints `upload_file` (mono MP3 under the 4.5 MB upload limit) and the `submit_transcript` parameters for each mode.
2. Local file: call `create_upload_url`, run its `curl` with `@<upload_file>`, and take the `upload_url` it prints. Public URL: use it directly. If `create_upload_url` isn't available, ask the user for a public URL or an API key. The link `create_upload_url` returns uploads into the user's account: never print it, echo it back or write it to a file.
3. Call `submit_transcript` with `audio_url` and that mode's parameters (keep `speaker_labels: true`: the timings come from its utterances).
4. Poll `get_transcript` every ~3 s until `status` is `completed`. Save its structured result (`transcript_id`, `text`, `speakers` with millisecond `start`/`end`) as JSON next to the video, e.g. `<name>.mcp-transcript.json`.
5. Tell the user: the MCP server returns utterance timings, not word timings, so captions are timed per utterance with the words spread across it. An API key gives word-exact timing.

**Neither:** ask for an API key (https://www.assemblyai.com/dashboard) or to connect the MCP server.

## 5. Render

```
python3 -m captions VIDEO --mode <mode> --font "<font>" [--font2 "<font>"] [--editor finalcut --editor resolve --editor premiere] [--overlay] [--burn] [--style default|high-contrast|social] [--speaker-names never|change|always] [--transcript <name>.mcp-transcript.json] [--llm agent]
```

Use `--llm agent` when there is no API key.

**Exit code 3** means a translation, romanization or key-word pick (`--style social`) is waiting for you. For each `<name>.<mode>.llm-request.json` it names: do the task yourself, following its `instruction` exactly (translate line by line so captions stay in sync; romanize keeps the same number of words per line; keywords: at most two words per line, copied exactly from it, often none), write `<name>.<mode>.llm-answers.json` as `{"<kind>": [...]}` with one answer per item, in order, and run the same command again.

## 6. Hand over

Report what was written, then how to open it:

- **Final Cut Pro:** `open -a "Final Cut Pro" "<name>.<mode>.fcpxmld"`, pick a library. Captions are on an SRT caption role (View > Show Captions); the overlay, if any, is on lane 1.
- **DaVinci Resolve:** File > Import > Timeline… → `<name>.<mode>.resolve.fcpxml`. Then File > Import > Subtitle… → `<name>.<mode>.srt`, and drag it onto the timeline's subtitle track.
- **Premiere Pro:** File > Import… → `<name>.<mode>.premiere.xml` (a sequence with the video and the overlay, if any). Then import `<name>.<mode>.srt` and drag it onto the sequence: Premiere makes a caption track.
- **Preview:** `<name>.preview.html` plays the video with every mode's captions in a browser.

Offer to open it in the editor. Don't commit transcripts, renders, `*.llm-*.json` or font files (they're git-ignored), and never print or store the API key.
