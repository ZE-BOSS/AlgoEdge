# Alphavantiq Capital — brand assets

Open `preview.html` in a browser to see everything at real sizes on both backgrounds.

## The mark

A monogram **A** drawn as two tapered planes — the left shadowed, the right lit — so it
reads as a single folded surface catching light rather than a flat letter. The seam
falls exactly on the apex.

The counter's base **rises left to right**, carried through to the outer notch below it.
That tilt is a trend line living inside the letter. A horizontal bar makes it a plain A;
it is the one detail not to "correct".

Tapered filled planes, not uniform strokes — that is what separates this from a
default. The taper is also why it holds at 16px where an even-weight stroke goes muddy.

## Files

| File | Use |
|---|---|
| `mark.svg` | the mark alone — app headers, anywhere on a dark or light surface |
| `favicon.svg` | mark on a rounded ink tile — browser tabs, app icon |
| `seal.svg` | mark inside a double ring — statements, certificates, PDFs. Min 40px |
| `logo-lockup.svg` | horizontal mark + wordmark — website header, email, documents |
| `splash.svg` | Expo splash, 1284×2778, everything safe inside the centre |
| `loader.svg` | a highlight sweeping across the metal; honours `prefers-reduced-motion` |
| `preview.html` | review sheet — sizes, backgrounds, palette, in-context mockups |

In the React app use `frontend/src/components/Brand.jsx` (`<Mark />`, `<Wordmark />`)
rather than these files: it inlines the SVG so the mark cannot flash in late on a cold
load, and it gives each gradient a unique id. **Two copies of the same gradient id on
one page is a real bug** — the second reuses the first's stops and loses its fill
entirely if the first unmounts.

## Palette

| Token | Value | Use |
|---|---|---|
| Ink | `#0B1220` | primary background, favicon tile |
| Ink raised | `#1B2942` | cards, raised surfaces, splash glow |
| Gold lit | `#F8E6B4` → `#E4C273` → `#C9963A` | the mark's right plane |
| Gold shade | `#CFA245` → `#A97E28` → `#835F1C` | the mark's left plane |
| Gold flat | `#D9AE55` | single-colour gold where a gradient cannot be used |
| Paper | `#F5F7FA` | text on ink, light background |

Gold is the **only** accent in brand contexts. The app's existing green/blue stay for
data — P&L, charts, status — where they carry meaning. Do not add a second brand
accent; the previous mark had a green-to-blue gradient wordmark beside a coloured dot
and neither read as the brand.

## Wordmark

`ALPHAVANTIQ` in a geometric sans at ~650 weight, with `CAPITAL` beneath at ~0.5em and
`0.34em` letter-spacing, 55% opacity.

The lockup SVG uses **live text**, not outlines, with a system font stack. It renders
correctly in every browser. **For print, or a trademark filing, convert the wordmark to
paths** once a licensed typeface is chosen — live text depends on the viewer having the
font, which is fine on the web and not fine on a registration certificate.

## Clear space and minimum size

- Clear space: the height of the mark's crossbar on all sides.
- Minimum: mark 16px, lockup 140px wide. Below that use the mark alone.
- On busy photography use `favicon.svg`'s ink tile, never the bare mark.

## Still to produce

- PNG exports (`icon-512`, `icon-192`, `adaptive-icon` foreground) for the Expo build —
  Expo needs raster, so these get generated when the mobile app is set up.
- `apple-touch-icon.png` at 180×180.
- An OG/social card at 1200×630.
