# KeepHarness brand assets

App icon set and desktop splash. `desktop/main.cjs` uses them (window icon and splash window), and `scripts/package-desktop-linux.sh` copies them into the Linux package.

| File | What it is |
| --- | --- |
| `icon.png` | Master icon, 1024x1024 RGBA. The squircle fills the canvas edge to edge, corners transparent. |
| `icons/<n>x<n>.png` | Linux / electron-builder set: 16, 24, 32, 48, 64, 128, 256, 512. |
| `icon.ico` | Windows icon, PNG-compressed entries 16, 24, 32, 48, 64, 128, 256. |
| `icon.icns` | macOS icon, PNG entries `ic07` 128, `ic08` 256, `ic09` 512, `ic10` 1024. |
| `splash.jpg` | Splash image, 1600x1000 JPEG (under 400 KB). |
| `../splash.html` | Static splash page: the image full-window, no scripts, no remote resources, strict CSP. |

The same script also writes the web icons that both apps serve from `harness_ui/assets/`: `favicon.ico` (16, 32, 48) and `apple-touch-icon.png` (180, opaque and full-bleed because iOS rounds the corners and paints transparency black).

## How they were made

```sh
node scripts/brand-assets.cjs <logo.jpeg> <splash.jpeg>   # Node 22.2+, ffmpeg and ffprobe on PATH
```

- The logo JPEG has no alpha: the checkerboard and the drop shadow around the squircle are painted in. The script uses the squircle's measured outline (a rounded rectangle fitted to the sub-pixel edge, under 0.5 px RMS error), replaces everything outside it and the 4 px band inside it with colors from just inside, then draws the shape with an analytic one-pixel anti-aliased alpha edge. No background color can reach the edge, so there is no checkerboard or light fringe.
- Every size is rendered straight from the 2048 px source with a Lanczos-3 resampler, not by chaining downscales.
- Up to 32 px the symbol is enlarged by 1.2 inside the same outline and sharpened, so the loops and carabiner stay legible.
- The splash is scaled to 1600x1000 (centre-cropping the 1.608 aspect to 1.6). The icon Gemini drew in the splash is a different drawing of the symbol, so the final icon is composited over it, a few pixels larger, before scaling.
- The measured constants (squircle outline, position of the splash icon) are at the top of the script. They apply to these two inputs only.

## Provenance

Generated with Google Gemini image generation for Sophia Phillipa on 2026-10-03, and approved by her. The two original JPEGs are not committed.
