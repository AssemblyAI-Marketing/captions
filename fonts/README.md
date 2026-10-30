# Your caption fonts

Put `.ttf`, `.otf` or `.ttc` files here and pick them with `--font "Family Name"` (or the file name). The family
name is read from the file, so Final Cut, the browser preview and burn-in all use the same font.

No fonts ship with this repo, and everything in this folder except this README is git-ignored: only use fonts
whose licence allows it. Open-source picks (SIL Open Font License) from fonts.google.com:

| Script | Fonts |
|---|---|
| Latin, Cyrillic | Atkinson Hyperlegible, Inter, Noto Sans |
| Devanagari (Hindi) | Noto Sans Devanagari, Mukta |
| Chinese | Noto Sans SC |
| Japanese | Noto Sans JP |
| Korean | Noto Sans KR, Pretendard |
| Arabic | Noto Sans Arabic, Cairo |
| Hebrew | Noto Sans Hebrew, Rubik |

`python3 -m captions fonts --mode <mode>` shows what's here and the default for your computer.
