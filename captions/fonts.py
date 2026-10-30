"""Caption fonts: your own files in fonts/, or fonts already on the system. No fonts ship with this repo.

    resolve("Atkinson Hyperlegible")   -> Font(family="Atkinson Hyperlegible", path="fonts/Atkinson….ttf")
    resolve(None, script="ja")         -> the platform's default Japanese font (no file)

A file's family name is read from its OpenType name table, so libass, Final Cut and the browser preview all
refer to it the same way.
"""
import glob
import os
import struct
import sys
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = os.path.join(ROOT, "fonts")
EXTS = (".ttf", ".otf", ".ttc", ".otc")

# One system font per script per platform that covers the glyphs (spaces and glyphs then share metrics).
DEFAULTS = {
    "latin":      {"darwin": "Helvetica Neue", "win32": "Arial", "linux": "DejaVu Sans"},
    "cyrillic":   {"darwin": "Helvetica Neue", "win32": "Arial", "linux": "DejaVu Sans"},
    "devanagari": {"darwin": "Kohinoor Devanagari", "win32": "Nirmala UI", "linux": "Noto Sans Devanagari"},
    "zh":         {"darwin": "PingFang SC", "win32": "Microsoft YaHei", "linux": "Noto Sans CJK SC"},
    "ja":         {"darwin": "Hiragino Sans", "win32": "Yu Gothic", "linux": "Noto Sans CJK JP"},
    "ko":         {"darwin": "Apple SD Gothic Neo", "win32": "Malgun Gothic", "linux": "Noto Sans CJK KR"},
    "arabic":     {"darwin": "Geeza Pro", "win32": "Segoe UI", "linux": "Noto Sans Arabic"},
    "hebrew":     {"darwin": "Arial Hebrew", "win32": "Segoe UI", "linux": "Noto Sans Hebrew"},
}
# libass on macOS can't always reach these through CoreText; it can reach the alternatives
LIBASS_SAFE = {"PingFang SC": "Hiragino Sans GB"}
# open-source families worth suggesting (SIL Open Font License): download them into fonts/ yourself
OPEN_SOURCE = {
    "latin": ["Atkinson Hyperlegible", "Inter", "Noto Sans"], "cyrillic": ["Inter", "Noto Sans"],
    "devanagari": ["Noto Sans Devanagari", "Mukta"], "zh": ["Noto Sans SC"], "ja": ["Noto Sans JP"],
    "ko": ["Noto Sans KR", "Pretendard"], "arabic": ["Noto Sans Arabic", "Cairo"], "hebrew": ["Noto Sans Hebrew", "Rubik"],
}


@dataclass(frozen=True)
class Font:
    family: str
    path: str = None           # a file to load (fonts/ or a path you gave); None = installed on the system

    @property
    def dir(self):
        return os.path.dirname(self.path) if self.path else None


def platform_key():
    return "darwin" if sys.platform == "darwin" else "win32" if sys.platform.startswith("win") else "linux"


def default(script):
    return DEFAULTS.get(script, DEFAULTS["latin"])[platform_key()]


def family_name(path):
    """Family name from a font file's 'name' table (prefers the typographic family, ID 16, then ID 1)."""
    with open(path, "rb") as f:
        data = f.read()
    offset = 0
    if data[:4] in (b"ttcf",):                       # collection: first font
        offset = struct.unpack(">I", data[12:16])[0]
    num = struct.unpack(">H", data[offset + 4:offset + 6])[0]
    for i in range(num):
        tag, _, off, _ = struct.unpack(">4sIII", data[offset + 12 + 16 * i:offset + 28 + 16 * i])
        if tag != b"name":
            continue
        count, strings = struct.unpack(">HH", data[off + 2:off + 6])
        names = {}
        for j in range(count):
            pid, eid, lid, nid, length, noff = struct.unpack(">6H", data[off + 6 + 12 * j:off + 18 + 12 * j])
            if nid not in (1, 16):
                continue
            raw = data[off + strings + noff:off + strings + noff + length]
            if pid in (0, 3):
                text = raw.decode("utf-16-be", "ignore")
            elif pid == 1:
                text = raw.decode("mac_roman", "ignore")
            else:
                continue
            rank = (nid == 16, pid == 3 and lid == 0x409)
            if text and rank >= names.get(nid, ((False, False), ""))[0]:
                names[nid] = (rank, text)
        for nid in (16, 1):
            if nid in names:
                return names[nid][1]
    return os.path.splitext(os.path.basename(path))[0]


def local_fonts():
    """[(family, path)] for every font file in fonts/."""
    out = []
    for p in sorted(glob.glob(os.path.join(FONT_DIR, "**", "*"), recursive=True)):
        if p.lower().endswith(EXTS):
            try:
                out.append((family_name(p), p))
            except (OSError, struct.error):
                pass
    return out


def resolve(name=None, script="latin"):
    """A Font from a file path, a family/file name in fonts/, or a system family name (or the default)."""
    if not name:
        return Font(default(script))
    if os.path.isfile(os.path.expanduser(name)):
        p = os.path.abspath(os.path.expanduser(name))
        return Font(family_name(p), p)
    want = name.lower().replace(" ", "")
    for family, path in local_fonts():
        stem = os.path.splitext(os.path.basename(path))[0].lower().replace(" ", "")
        if want in (family.lower().replace(" ", ""), stem) or stem.startswith(want):
            return Font(family, path)
    return Font(name)                                 # an installed system font, by family name


def for_libass(font):
    return font if font.path else Font(LIBASS_SAFE.get(font.family, font.family))


def css_stack(font, script):
    """CSS font-family for the preview: the chosen font, then the script default, then generic."""
    return ", ".join(dict.fromkeys(f'"{f}"' for f in (font.family, default(script)))) + ", system-ui, sans-serif"


def report(scripts):
    """What the agent shows when it asks for a font."""
    lines = []
    local = local_fonts()
    for s in dict.fromkeys(scripts):
        lines.append(f"{s}:")
        lines.append(f"  default on this computer: {default(s)}")
        lines.append(f"  open-source options (download into fonts/): {', '.join(OPEN_SOURCE.get(s, []))}")
    lines.append("in fonts/: " + (", ".join(f"{f} ({os.path.basename(p)})" for f, p in local) or "none"))
    return "\n".join(lines)
