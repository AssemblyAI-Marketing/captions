"""Words in, caption cues out, for one mode (see languages.py).

Romanizing (Hinglish in the English alphabet), translating (the second line of dual captions) and picking key
words (the social style) are small language tasks. With an API key they go through AssemblyAI's LLM Gateway. Without one (for example when the
transcript came from the AssemblyAI MCP server over OAuth), the CLI writes them to <name>.llm-request.json for
the agent to answer in <name>.llm-answers.json, then picks the answers up on the next run.
"""
import json
import os

from . import assemblyai, lines
from .assemblyai import read_json

ROMANIZE = (
    "Each item is one caption line, as an array of words from a Hindi/English (Hinglish) transcript. "
    "Rewrite every Hindi word in the English alphabet the way people casually type Hinglish in chats "
    "(क्या हाल है → kya haal hai, बहुत अच्छा → bahut accha). No diacritics. Leave English words, numbers, "
    "names and punctuation as they are. Return the same array of words for each line: one output word "
    "per input word, so the word count of every line stays the same."
)

TRANSLATE = (
    "Each item is one caption line from a video. Translate each line into natural, short {language} "
    "subtitle text. Lines can be fragments of a longer sentence: translate only what is in that line, "
    "so it stays in sync with the speech. If a line is already in {language}, return it unchanged."
)

KEYWORDS = (
    "Each item is one caption line from a video. Pick the words a viewer should remember from each line: the heart "
    "of what's being said, such as a name, a number, a product or the one word that carries the point. At most "
    "{n} words per line, and none at all for a line with nothing worth highlighting (fillers, greetings, asides). "
    "Copy each word exactly as it is written in the line. Return a list of words for each line."
)
KEY_WORDS = 2       # the most key words highlighted per caption line


class NeedsAgent(Exception):
    """Raised when a language task has no API key to run on: the request file is waiting for answers."""

    def __init__(self, request, answers):
        super().__init__(f"answer {request} in {answers}")
        self.request, self.answers = request, answers


class Llm:
    """Runs a JSON-list task on the LLM Gateway, or hands it to the agent through two files."""

    def __init__(self, key=None, model=assemblyai.LLM_MODEL, base=None, prefer_agent=False):
        self.key, self.model, self.prefer_agent = key, model, prefer_agent
        self.request = f"{base}.llm-request.json" if base else None
        self.answers = f"{base}.llm-answers.json" if base else None

    def run(self, kind, items, instruction, same_shape=None):
        ok = lambda out: (isinstance(out, list) and len(out) == len(items)
                          and (same_shape is None or all(map(same_shape, items, out))))
        if self.answers and os.path.exists(self.answers):
            out = read_json(self.answers).get(kind)
            if out is not None:
                if not ok(out):
                    raise SystemExit(f'{self.answers}: "{kind}" must be a list of {len(items)} items, in order, same shape as the request')
                return out
        if self.key and not self.prefer_agent:
            return assemblyai.map_json(items, instruction, self.key, self.model, same_shape)
        if not self.request:
            raise SystemExit("This mode needs an AssemblyAI API key (LLM Gateway) or an agent to answer its language task.")
        req = read_json(self.request) if os.path.exists(self.request) else {}
        req[kind] = {"instruction": instruction, "answer_format": f'"{kind}": a JSON list of {len(items)} items, same order',
                     "items": items}
        with open(self.request, "w", encoding="utf-8") as f:
            json.dump(req, f, ensure_ascii=False, indent=1)
        raise NeedsAgent(self.request, self.answers)


def romanize(words, llm):
    """Rewrite Devanagari words in Latin script, keeping every word's timing."""
    segs = lines.segments(words)
    out = llm.run("romanize", [[w["text"] for w in s] for s in segs], ROMANIZE,
                  same_shape=lambda a, b: isinstance(b, list) and len(a) == len(b))
    return [dict(w, text=str(t)) for s, ts in zip(segs, out) for w, t in zip(s, ts)]


def translate(cue_list, language, llm):
    """Add a 'translation' line to each cue."""
    out = llm.run("translate", [c["text"] for c in cue_list], TRANSLATE.format(language=language))
    for c, t in zip(cue_list, out):
        c["translation"] = str(t)
    return cue_list


def bare(text):
    return "".join(ch for ch in text.lower() if ch.isalnum())


def keywords(cue_list, llm, per_cue=KEY_WORDS):
    """Mark up to per_cue key words on each cue ("key": True on the word) for the social style."""
    out = llm.run("keywords", [c["text"] for c in cue_list], KEYWORDS.format(n=per_cue),
                  same_shape=lambda a, b: isinstance(b, list))
    for c, picked in zip(cue_list, out):
        wanted = {bare(str(k)) for k in picked} - {""}
        marked = 0
        for w in c["words"]:
            # the budget is enforced here too: a longer answer only highlights its first few words
            if marked < per_cue and bare(w["text"]) in wanted:
                w["key"] = True
                marked += 1
    return cue_list


def build(words, mode, llm=None, glue=frozenset(), max_width=32, profile=None, key_words=False):
    """Words in, caption cues out, for one Mode.

    key_words: also mark key words (one more language task, see keywords()). When the translation and the key
    words both wait for the agent, they go into one request file.
    """
    llm = llm or Llm()
    if mode.romanize:
        words = romanize(words, llm)
    cue_list = lines.cues(words, glue=glue, max_width=max_width, profile=profile)
    waiting = None
    if mode.translate_to:
        from .languages import LANGUAGES
        try:
            translate(cue_list, LANGUAGES[mode.translate_to][1], llm)
        except NeedsAgent as need:
            waiting = need
    if key_words:
        keywords(cue_list, llm)
    if waiting:
        raise waiting
    return cue_list
