"""Cue profiles: line length, reading speed and caption placement for the video's shape.

A 16:9 video gets 42-column lines read at up to 17 characters a second, each shown 1–7 s. A 9:16 phone video gets
22-column lines at 15 characters a second, 0.8–5 s, set higher up, clear of the app's buttons. Shapes in between
(1:1, 4:5, 21:9…) blend the two on a log scale of the aspect. Captions show one line at a time; a dual mode's
translation is the second line.

    p = profiles.for_size(1080, 1920)      # Profile(name='9:16', width=22, cps=15.0, ...)

The two profiles and the blending are adapted from Overtone (lonthq), MIT.
"""
import math
import re
from collections import namedtuple

# width: line length in columns (Chinese, Japanese, Korean count 2); cps: most characters a second to read;
# min_duration/max_duration: seconds a caption stays up; margin: bottom margin as a share of the height;
# side: left and right margins as a share of the width
Profile = namedtuple("Profile", "name width cps min_duration max_duration margin side")

HORIZONTAL = Profile("16:9", 42, 17.0, 1.0, 7.0, 0.08, 0.03)
VERTICAL = Profile("9:16", 22, 15.0, 0.8, 5.0, 0.22, 0.08)

# a picture within 2% of one of these is named after it
COMMON = ["16:9", "3:2", "8:5", "4:3", "5:4", "1:1", "4:5", "5:8", "3:4", "2:3", "9:16"]


def ratio(aspect):
    """'16:9' or '16x9' -> 1.777…"""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*[:x/]\s*(\d+(?:\.\d+)?)\s*", aspect or "")
    if not m or not float(m[1]) or not float(m[2]):
        raise ValueError(f"not an aspect ratio: {aspect!r} (try 16:9 or 9:16)")
    return float(m[1]) / float(m[2])


def name(width, height):
    r = width / height
    near = [a for a in COMMON if abs(ratio(a) / r - 1) <= 0.02]
    if near:
        return near[0]
    g = math.gcd(width, height)
    return f"{width // g}:{height // g}"


def for_aspect(r, label=None):
    """The profile for a picture that is r times as wide as it is tall."""
    lo, hi = math.log(ratio(VERTICAL.name)), math.log(ratio(HORIZONTAL.name))
    t = min(1.0, max(0.0, (math.log(r) - lo) / (hi - lo)))
    mix = lambda a, b: a + t * (b - a)
    return Profile(label or f"{r:.2f}:1",
                   round(mix(VERTICAL.width, HORIZONTAL.width)),
                   *(round(mix(a, b), 2) for a, b in zip(VERTICAL[2:], HORIZONTAL[2:])))


def for_size(width, height):
    return for_aspect(width / height, name(width, height))


def parse(aspect):
    """--aspect: 'horizontal', 'vertical', or a ratio such as 4:5."""
    named = {"horizontal": HORIZONTAL, "landscape": HORIZONTAL, "vertical": VERTICAL, "portrait": VERTICAL}
    if aspect.lower() in named:
        return named[aspect.lower()]
    return for_aspect(ratio(aspect), aspect.replace("x", ":"))


def describe(p):
    shape = "vertical" if p.width <= VERTICAL.width else "horizontal" if p.width >= HORIZONTAL.width else "in between"
    return (f"{p.name} ({shape}): {p.width}-column lines, up to {p.cps:g} characters a second, "
            f"{p.min_duration:g}–{p.max_duration:g} s a caption")
