# Vendored fonts

Both faces ship in the image and are served from `/static/fonts/` — **never from a CDN.**
`app/__init__.py` sends `Content-Security-Policy: frame-ancestors 'none'` today, but the
no-CDN rule is about the app being self-contained (and about not handing every page view to
a third party), not about what the current CSP happens to block.

| File | Family | Style | Source |
|---|---|---|---|
| `space-grotesk-latin-var.woff2` | Space Grotesk | variable, 400–700 | [floriankarsten/space-grotesk](https://github.com/floriankarsten/space-grotesk) |
| `instrument-sans-latin-var.woff2` | Instrument Sans | variable, 400–700 | [Instrument/instrument-sans](https://github.com/Instrument/instrument-sans) |

Both are **SIL Open Font License 1.1** — the full text sits beside them in
`OFL-Space-Grotesk.txt` and `OFL-Instrument-Sans.txt`, which is what the licence requires of
anyone redistributing the binaries. Keep those files next to the fonts.

⚠️ **These are the `latin` subset only** (`U+0000-00FF` plus the usual punctuation), which is
what keeps the pair at ~52 KB combined. A category or account name containing Cyrillic, Greek
or Vietnamese renders from the fallback stack instead — visibly a different face, but readable.
If that ever matters, fetch the `latin-ext` subset from the same source and add a second
`@font-face` per family with the matching `unicode-range`; do **not** swap in the full font.

To refresh a face, re-fetch from the upstream repo above rather than from a `fonts.googleapis.com`
CSS URL — those URLs are versioned and rotate.
