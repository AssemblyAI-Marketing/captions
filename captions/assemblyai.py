"""The three AssemblyAI calls this tool makes: upload, transcribe, and LLM Gateway.

Standard library only, so there's nothing to install.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

from . import lines

API = "https://api.assemblyai.com/v2"
GATEWAY = "https://llm-gateway.assemblyai.com/v1/chat/completions"
SPEECH_MODELS = ["universal-3-5-pro", "universal-2"]   # languages outside 3.5 Pro fall back to Universal-2
LLM_MODEL = "claude-sonnet-4-6"


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def api_key(required=True):
    key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not key and os.path.exists(".env"):
        with open(".env", encoding="utf-8") as f:
            for line in f:
                if line.startswith("ASSEMBLYAI_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip("\"'")
    if not key and required:
        raise SystemExit("Set ASSEMBLYAI_API_KEY (or put it in .env), get one at https://www.assemblyai.com/dashboard. "
                         "Or transcribe through the AssemblyAI MCP server: python3 -m captions plan VIDEO --mode ...")
    return key


def _request(url, key, body=None, data=None, method=None):
    headers = {"authorization": key}
    if body is not None:
        data = json.dumps(body).encode()
        headers["content-type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"AssemblyAI {e.code} from {url}: {e.read().decode(errors='replace')}")


def upload(path, key):
    with open(path, "rb") as f:
        return _request(f"{API}/upload", key, data=f.read(), method="POST")["upload_url"]


def transcribe(source, key, mode, log=print, keyterms=()):
    """Transcript JSON for a local file or URL, with the language settings for a Mode."""
    url = source if re.match(r"https?://", source) else None
    if not url:
        log(f"Uploading {os.path.basename(source)}…")
        url = upload(source, key)
    body = {"audio_url": url, **submit_params(mode, keyterms)}
    job = _request(f"{API}/transcript", key, body)
    log(f"Transcribing ({job['id']})…")
    while job["status"] not in ("completed", "error"):
        time.sleep(3)
        job = _request(f"{API}/transcript/{job['id']}", key)
    if job["status"] == "error":
        raise SystemExit(f"Transcription failed: {job.get('error')}")
    return job


def words(transcript):
    """Word list in seconds from a transcript JSON.

    Takes a full AssemblyAI transcript (word timings), or the structured result of the AssemblyAI MCP
    server's get_transcript, which has per-speaker utterances ("speakers": [{speaker, text, start, end}])
    but no word timings. Then each utterance's words are spread across its span by character width.
    """
    if transcript.get("words"):
        return [{"text": w["text"], "start": w["start"] / 1000, "end": w["end"] / 1000, "speaker": w.get("speaker")}
                for w in transcript["words"]]
    utts = transcript.get("speakers") or transcript.get("utterances")
    if not utts:
        if not transcript.get("text") or not transcript.get("audio_duration"):
            raise SystemExit("Transcript has no word or utterance timings. With the MCP server, submit with "
                             "speaker_labels=true and save get_transcript's structured result.")
        utts = [{"speaker": "A", "text": transcript["text"], "start": 0, "end": transcript["audio_duration"] * 1000}]
    out = []
    for u in utts:
        toks = tokens(u["text"])
        widths = [lines.width(t) + 1 for t in toks]
        span, t = (u["end"] - u["start"]) / 1000, u["start"] / 1000
        for tok, w in zip(toks, widths):
            d = span * w / sum(widths)
            out.append({"text": tok, "start": round(t, 3), "end": round(t + d * 0.9, 3), "speaker": u.get("speaker")})
            t += d
    return out


def tokens(text):
    """Words of an utterance: split on spaces, and Chinese/Japanese runs into two-character chunks
    (punctuation stays on the chunk before it)."""
    punct = lambda ch: 0x3000 <= ord(ch) <= 0x303F or 0xFF00 <= ord(ch) <= 0xFFEF
    out = []
    for word in text.split():
        latin = ""
        for ch in word:
            if not lines.is_cjk(ch):
                latin += ch
                continue
            if latin:
                out.append(latin)
                latin = ""
            if punct(ch) and out:
                out[-1] += ch
            elif out and lines.is_cjk(out[-1][-1]) and len(out[-1]) == 1 and not punct(out[-1][-1]):
                out[-1] += ch
            else:
                out.append(ch)
        if latin:
            out.append(latin)
    return out


def submit_params(mode, keyterms=()):
    """The submit_transcript (MCP) / POST /v2/transcript parameters for a mode.

    keyterms: names and product terms to get right ("AssemblyAI", a speaker's name), up to 1,000.
    """
    body = {"speech_models": SPEECH_MODELS, "speaker_labels": True}
    if keyterms:
        body["keyterms_prompt"] = list(keyterms)
    if mode.language:
        body["language_code"] = mode.language
    elif mode.languages:
        body["language_codes"] = list(mode.languages)
    else:
        body["language_detection"] = True
    return body


def audio_for_upload(source, out_dir, max_bytes=4_500_000):
    """Mono MP3 of the audio, small enough for the MCP upload link (4.5 MB). Returns its path."""
    import subprocess
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", source],
                               capture_output=True, text=True, check=True).stdout)
    kbps = max(16, min(64, int(max_bytes * 8 / dur / 1000 * 0.92)))
    out = os.path.join(out_dir, os.path.splitext(os.path.basename(source))[0] + ".upload.mp3")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", source, "-vn", "-ac", "1", "-ar", "16000",
                    "-b:a", f"{kbps}k", out], check=True)
    if os.path.getsize(out) > max_bytes:
        raise SystemExit(f"{dur / 60:.0f} min of audio doesn't fit in 4.5 MB even at 16 kbps: use an API key or a public URL.")
    return out


def chat(prompt, key, model=LLM_MODEL, max_tokens=4000):
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens}
    return _request(GATEWAY, key, body)["choices"][0]["message"]["content"]


def map_json(items, instruction, key, model=LLM_MODEL, same_shape=None, batch=40):
    """Send a JSON list through the LLM Gateway in batches; get back a list of the same length.

    same_shape(original, result) can check each item further (e.g. same word count).
    """
    out = []
    for b in range(0, len(items), batch):
        chunk = items[b:b + batch]
        prompt = (f"{instruction}\n\nInput is a JSON array of {len(chunk)} items. Reply with only a JSON array "
                  f"of exactly {len(chunk)} items, in the same order. No commentary.\n\n"
                  + json.dumps(chunk, ensure_ascii=False))
        for attempt in range(3):
            reply = chat(prompt, key, model)
            try:
                result = json.loads(reply[reply.index("["):reply.rindex("]") + 1])
            except ValueError:
                continue
            if len(result) == len(chunk) and (same_shape is None or all(map(same_shape, chunk, result))):
                out.extend(result)
                break
        else:
            raise SystemExit("LLM Gateway didn't return a matching list after 3 tries; try again or use --llm-model")
    return out
