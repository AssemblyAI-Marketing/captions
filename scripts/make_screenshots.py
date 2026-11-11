"""Screenshots for the README: docs/preview.png (the browser preview, from demo.html) and docs/live.png (the live
captions page). Dev-only: needs Playwright and Chrome (pip install playwright), unlike the tool itself.

    python3 scripts/make_screenshots.py

The live page runs in dev mode on a free local port. It gets two sample turns, written for this repo, through its own
onTurn(), so the shot shows real partial and final caption styling without a microphone or any network call.
"""
import os
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")

SAMPLE_TURNS = [   # what onTurn() receives from AssemblyAI streaming: a finished turn, then one still landing
    {"end_of_turn": True, "transcript": "Live captions work in any app you can share a window from,"},
    {"end_of_turn": False, "words": [{"text": w, "word_is_final": i < 4} for i, w in
                                     enumerate("like Zoom, Meet, Teams or OBS.".split())]},
]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    from playwright.sync_api import sync_playwright
    port = free_port()
    env = {**os.environ, "LIVE_PORT": str(port), "ASSEMBLYAI_API_KEY": os.environ.get("ASSEMBLYAI_API_KEY", "screenshot-only")}
    server = subprocess.Popen([sys.executable, os.path.join(ROOT, "live", "server.py")], env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://localhost:{port}/", timeout=1).close()
                break
            except OSError:
                time.sleep(0.1)
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome")
            page = browser.new_page(viewport={"width": 1200, "height": 860}, device_scale_factor=2, color_scheme="dark")

            # the browser preview: dual captions, part-way through a line so the word-by-word reveal shows
            page.goto("file://" + os.path.join(ROOT, "demo.html"))
            page.evaluate("""() => {
                const t = DATA.tracks.find(t => t.mode === "japanese+english") || DATA.tracks[0];
                setTrack(t);
                const pool = t.cues.filter(c => c.translation);
                const c = (pool.length ? pool : t.cues).reduce((a, b) => (b.text.length > a.text.length ? b : a));
                seek(c.start + (c.end - c.start) * 0.7);
            }""")
            page.wait_for_timeout(300)
            page.screenshot(path=os.path.join(DOCS, "preview.png"))

            # the live page, captions on
            page.goto(f"http://localhost:{port}/?size=40")
            page.wait_for_timeout(500)
            page.evaluate("() => setOn(true)")          # the "on" state the page shows while listening
            for turn in SAMPLE_TURNS:
                page.evaluate("t => onTurn(t)", turn)
            page.wait_for_timeout(200)
            page.screenshot(path=os.path.join(DOCS, "live.png"))
            browser.close()
    finally:
        server.terminate()
        server.wait()
    for name in ("preview.png", "live.png"):
        print(os.path.join(DOCS, name))


if __name__ == "__main__":
    main()
