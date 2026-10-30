"""Turn timed words into short, balanced caption lines.

Works for Latin, Cyrillic, Devanagari, Arabic, Hebrew, Chinese, Japanese and Korean text. Chinese, Japanese and
Korean characters count as two columns; Chinese and Japanese are joined without spaces, so a 32-column line holds
about 16 characters.

    cues = lines.cues(words, glue=lines.glue_pairs(["Voice Agent"]), profile=profiles.HORIZONTAL)

With a profile (profiles.py), each caption stays up long enough to read at its characters-a-second limit and for
its minimum duration, as long as the silence after it allows, never longer than its maximum, and a longer pause
leaves the screen blank. The timing rules are adapted from Overtone (lonthq), MIT.
"""

ENDS = tuple(".?!。？！।؟")         # sentence ends, including Chinese/Japanese, Devanagari and Arabic
PAUSES = tuple(",，、;；،")
DANGLERS = {
    # English
    "a", "an", "the", "on", "in", "of", "to", "and", "with", "for", "at", "or",
    # Hindi postpositions and conjunctions, in both scripts
    "का", "की", "के", "को", "से", "में", "और", "पर", "ने",
    "ka", "ki", "ke", "ko", "se", "mein", "aur", "par", "ne",
    # Spanish, French, German, Portuguese, Italian articles and prepositions
    "el", "la", "los", "las", "un", "una", "de", "del", "y", "en", "con", "por", "para",
    "le", "les", "des", "du", "et", "au", "der", "die", "das", "und", "mit", "o", "os", "e", "il", "di",
}
PAUSE = 1.0          # a silence this long (s) shows as a blank screen…
BLANK = 0.4          # …for at least this long
MAX_HOLD = 1.5       # most a caption stays up after its last word, to be read
# a line shouldn't start on these: Japanese particles and closing punctuation
LEADERS = {"は", "が", "を", "に", "で", "と", "の", "も", "へ", "や", "、", "。", "，", "！", "？", "」", "』", "）"}


def is_cjk(ch):
    o = ord(ch)
    return 0x2E80 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF or 0xFF00 <= o <= 0xFFEF or 0x3000 <= o <= 0x303F


def is_kana_ending(left, right):
    """Japanese hiragana right after a kanji usually finishes that word (付ける): don't break between them."""
    return bool(left and right) and 0x4E00 <= ord(left[-1]) <= 0x9FFF and 0x3041 <= ord(right[0]) <= 0x309F


def is_wide(ch):
    """Takes two columns: Chinese, Japanese, Korean (Hangul) and fullwidth forms."""
    return is_cjk(ch) or 0xAC00 <= ord(ch) <= 0xD7AF or 0x1100 <= ord(ch) <= 0x11FF


def width(text):
    return sum(2 if is_wide(c) else 1 for c in text)


def needs_space(left, right):
    """No space between two Chinese words, or before Chinese punctuation."""
    return not (left and right and is_cjk(left[-1]) and is_cjk(right[0]))


def join(words):
    out = ""
    for w in words:
        t = w["text"]
        out += (" " if out and needs_space(out, t) else "") + t
    return out


def glue_pairs(phrases):
    """{("Voice", "Agent"), ...} from ["Voice Agent", ...]: adjacent words never split across lines."""
    pairs = set()
    for p in phrases:
        parts = p.split()
        pairs.update(zip(parts, parts[1:]))
    return pairs


def segments(words, max_gap=0.45):
    """Hard breaks: speaker changes, pauses and sentence ends."""
    out, cur = [], []
    for w in words:
        if cur:
            prev = cur[-1]
            if (w.get("speaker") != prev.get("speaker") or w["start"] - prev["end"] > max_gap
                    or prev["text"].endswith(ENDS)):
                out.append(cur)
                cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def split_balanced(seg, max_width=32, glue=frozenset(), max_duration=None):
    """Fewest, most even lines for one segment (small DP over break points), none spoken over max_duration s."""
    n = len(seg)
    # prefix sums of word widths and of the spaces between them, so a line's width is O(1)
    pw, ps = [0], [0]
    for k, w in enumerate(seg):
        pw.append(pw[-1] + width(w["text"]))
        ps.append(ps[-1] + (1 if k and needs_space(seg[k - 1]["text"], w["text"]) else 0))
    text_len = lambda i, j: pw[j] - pw[i] + ps[j] - ps[i + 1]
    total = text_len(0, n)
    INF = float("inf")
    for k in range(max(1, -(-total // max_width)), n + 1):
        target = total / k
        cost = [[INF] * (k + 1) for _ in range(n + 1)]
        back = [[0] * (k + 1) for _ in range(n + 1)]
        cost[0][0] = 0
        for j in range(1, n + 1):
            for m in range(1, k + 1):
                for i in range(j):
                    L = text_len(i, j)
                    if cost[i][m - 1] == INF or L > max_width + 3:
                        continue
                    if max_duration and j - i > 1 and seg[j - 1]["end"] - seg[i]["start"] > max_duration:
                        continue
                    c = (L - target) ** 2
                    if j - i == 1 and n > 1 and L < 8:
                        c += 150                                        # orphan
                    if j < n and (seg[j - 1]["text"], seg[j]["text"]) in glue:
                        c += 1000                                       # split pair
                    if j < n and seg[j - 1]["text"].endswith(PAUSES):
                        c -= 15                                         # natural pause
                    if j < n and seg[j - 1]["text"].lower() in DANGLERS:
                        c += 80                                         # line ends on "the", "का"…
                    if j < n and seg[j]["text"] in LEADERS:
                        c += 120                                        # next line starts on "は", "。"…
                    if j < n and is_kana_ending(seg[j - 1]["text"], seg[j]["text"]):
                        c += 120                                        # 付|ける: kana still belongs to the kanji
                    if cost[i][m - 1] + c < cost[j][m]:
                        cost[j][m], back[j][m] = cost[i][m - 1] + c, i
        if cost[n][k] < INF:
            lines, j = [], n
            for m in range(k, 0, -1):
                i = back[j][m]
                lines.append(seg[i:j])
                j = i
            return lines[::-1]
    return [seg]


def cues(words, glue=frozenset(), max_width=32, lead=0.05, hold=0.6, profile=None):
    """[{start, end, text, words}]: each line shows from just before its first word until the next line.

    profile (profiles.Profile) adds the reading rules: held into the silence after it until it's readable at
    profile.cps and up for profile.min_duration (at most MAX_HOLD past its last word), cut at profile.max_duration,
    and a pause of PAUSE or more stays blank for at least BLANK. Starts and word timings never move.
    """
    max_duration = profile.max_duration if profile else None
    lines = [l for s in segments(words) for l in split_balanced(s, max_width, glue, max_duration)]
    out, prev_end = [], 0
    for i, line in enumerate(lines):
        text = join(line)
        start = max(line[0]["start"] - lead, prev_end, 0)
        last = line[-1]["end"]
        end = last + hold
        if profile:
            needed = max(profile.min_duration, width(text) / profile.cps)
            end = min(max(end, start + needed), last + MAX_HOLD, max(start + profile.max_duration, last))
        if i + 1 < len(lines):
            nxt = lines[i + 1][0]["start"]
            end = min(end, nxt - (BLANK if nxt - last >= PAUSE else lead))
        end = max(end, start + 0.001)
        prev_end = end
        out.append({"start": round(start, 3), "end": round(end, 3), "text": text, "words": line})
    return out
