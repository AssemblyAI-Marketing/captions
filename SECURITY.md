# Security

## Reporting a vulnerability

Please don't open a public issue. Use GitHub's private vulnerability reporting (Security → Report a
vulnerability) on this repository, with steps to reproduce. You'll hear back within a few business days.

## How this tool handles your data

- **API key.** Read from `ASSEMBLYAI_API_KEY` or a local `.env` (git-ignored), sent only to `api.assemblyai.com`
  and `llm-gateway.assemblyai.com` over HTTPS, and never written to any output file or log.
- **Audio.** Your video's audio is uploaded to your own AssemblyAI account for transcription. Through the AssemblyAI
  MCP server, the agent gets a short-lived upload link (10 minutes) that uploads into your account; the captions
  skill tells the agent never to print or store it.
- **Transcripts and language tasks.** Transcripts (`*.transcript.json`, `*.mcp-transcript.json`) and the agent
  hand-off files (`*.llm-request.json`, `*.llm-answers.json`) hold what was said in your video. They stay next to
  your video and are git-ignored. Delete them when you're done if the content is sensitive.
- **No third parties.** Apart from AssemblyAI, nothing leaves your machine: no analytics, no telemetry, no other
  network calls. The browser preview loads only local files.
- **Fonts.** No font files ship with this repo. Anything you put in `fonts/` stays local and is git-ignored.
- **Dependencies.** Python standard library only. `ffmpeg`/`ffprobe` (and optionally `xmllint`) are run as
  subprocesses with argument lists, never through a shell.
