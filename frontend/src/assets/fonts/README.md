# Self-hosted fonts

`src/index.css` declares `Inter` and `JetBrains Mono` with `local()` first and
then a `url()` pointing here. **Both files are deliberately absent**: until they
are added the `url()` source fails, the stack falls through to the system font,
and nothing is requested from a third-party host.

To get the designed typography back, drop these two files in:

| File | Source |
|---|---|
| `inter-variable.woff2` | the Inter release, `InterVariable.woff2` (SIL Open Font License 1.1) |
| `jetbrains-mono-variable.woff2` | the JetBrains Mono release (SIL Open Font License 1.1) |

Nothing else needs changing — `index.css` already points at these names.

**Do not** restore the Google-hosted `@import`. It sends every admin page view
to Google, which is the exposure this directory exists to remove. `tests/test_no_third_party_assets.py` fails the build if it reappears.
