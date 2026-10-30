"""Languages and caption modes.

A mode is a language, or two joined with "+":

    english            English captions
    japanese           Japanese captions
    japanese+english   Japanese on top, English translation underneath (dual captions)
    english+spanish    English on top, Spanish translation underneath

Two presets cover Hindi/English code-switching:

    hinglish           Hindi and English as spoken, each in its own script
    hinglish-roman     Hinglish written entirely in the English alphabet
"""
from dataclasses import dataclass

# name: (AssemblyAI language code, label, script). Universal-3.5 Pro covers 18 of these natively;
# the rest fall back to Universal-2 (see assemblyai.SPEECH_MODELS).
LANGUAGES = {
    "english": ("en", "English", "latin"),
    "spanish": ("es", "Spanish", "latin"),
    "french": ("fr", "French", "latin"),
    "german": ("de", "German", "latin"),
    "italian": ("it", "Italian", "latin"),
    "portuguese": ("pt", "Portuguese", "latin"),
    "dutch": ("nl", "Dutch", "latin"),
    "danish": ("da", "Danish", "latin"),
    "swedish": ("sv", "Swedish", "latin"),
    "norwegian": ("no", "Norwegian", "latin"),
    "finnish": ("fi", "Finnish", "latin"),
    "turkish": ("tr", "Turkish", "latin"),
    "vietnamese": ("vi", "Vietnamese", "latin"),
    "indonesian": ("id", "Indonesian", "latin"),
    "polish": ("pl", "Polish", "latin"),
    "russian": ("ru", "Russian", "cyrillic"),
    "ukrainian": ("uk", "Ukrainian", "cyrillic"),
    "hindi": ("hi", "Hindi", "devanagari"),
    "mandarin": ("zh", "Mandarin", "zh"),
    "japanese": ("ja", "Japanese", "ja"),
    "korean": ("ko", "Korean", "ko"),
    "arabic": ("ar", "Arabic", "arabic"),
    "hebrew": ("he", "Hebrew", "hebrew"),
}
ALIASES = {"chinese": "mandarin", "en": "english", "hinglish-english": "hinglish"}
RTL = {"arabic", "hebrew"}


@dataclass(frozen=True)
class Mode:
    name: str                  # "japanese+english"
    label: str                 # "Japanese + English"
    language: str = None       # one AssemblyAI code, e.g. "ja"
    languages: tuple = ()      # a code-switching set, e.g. ("hi", "en")
    script: str = "latin"      # script of the first line: picks fonts and line rules
    script2: str = None        # script of the second line, for dual captions
    translate_to: str = None   # language name of the second line
    romanize: bool = False     # rewrite Devanagari in the English alphabet (Hinglish)
    rtl: bool = False

    @property
    def needs_llm(self):
        return bool(self.translate_to or self.romanize)

    @property
    def transcript_key(self):
        """Modes that transcribe the same way share one transcript."""
        return self.language or "-".join(self.languages)


PRESETS = {
    "hinglish": Mode("hinglish", "Hinglish", languages=("hi", "en"), script="devanagari"),
    "hinglish-roman": Mode("hinglish-roman", "Hinglish (Roman)", languages=("hi", "en"), script="latin", romanize=True),
}


def parse(name, translate_to=None):
    """Mode for "japanese", "japanese+english", "hinglish", … (case-insensitive, "-" also joins a pair)."""
    key = name.strip().lower().replace(" ", "")
    key = ALIASES.get(key, key)
    if key in PRESETS and not translate_to:
        return PRESETS[key]
    if key in PRESETS:
        p = PRESETS[key]
        t = _lang(translate_to)
        return Mode(f"{key}+{t}", f"{p.label} + {LANGUAGES[t][1]}", p.language, p.languages, p.script,
                    LANGUAGES[t][2], t, p.romanize, p.rtl)
    first, _, second = key.replace("-", "+").partition("+")
    first, second = _lang(first), _lang(second or translate_to) if (second or translate_to) else None
    code, label, script = LANGUAGES[first]
    if second == first:
        second = None
    # speakers mix English into most languages; a non-English pair with English uses code-switching
    switch = first != "english" and second == "english"
    return Mode(
        name=first if not second else f"{first}+{second}",
        label=label if not second else f"{label} + {LANGUAGES[second][1]}",
        language=None if switch else code,
        languages=(code, "en") if switch else (),
        script=script,
        script2=LANGUAGES[second][2] if second else None,
        translate_to=second,
        rtl=first in RTL,
    )


def _lang(name):
    n = ALIASES.get(name.strip().lower(), name.strip().lower())
    if n not in LANGUAGES:
        raise ValueError(f'unknown language "{name}". Known: {", ".join(sorted(LANGUAGES))}, hinglish, hinglish-roman')
    return n


# The ones with a ready slash command (scripts/make_commands.py writes .claude/commands/<name>.md)
POPULAR = [
    "english", "spanish", "spanish+english", "english+spanish", "french", "french+english", "german",
    "german+english", "portuguese", "portuguese+english", "italian", "japanese", "japanese+english",
    "korean", "korean+english", "mandarin", "mandarin+english", "english+mandarin", "hindi", "hindi+english",
    "hinglish", "hinglish-roman", "arabic", "arabic+english", "vietnamese", "russian", "turkish",
]
