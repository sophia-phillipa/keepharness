# KeepHarness brand assets

App icon set and desktop splash. `desktop/main.cjs` uses them (window icon and splash window), and `scripts/package-desktop-linux.sh` copies them into the Linux package.

The mark is "Design 2": a deep-navy squircle, a thick coral ring broken where three solid teal nodes sit, the nodes joined in a triangle by thick teal bars (navy `#071f43`, teal `#3ecdbe`, coral `#fc785d`).

| File | What it is |
| --- | --- |
| `keepharness-mark.svg` | The full icon as vector art: squircle plus mark, viewBox 1024. |
| `keepharness-mark-only.svg` | The mark without the squircle, for in-app use. |
| `icon.png` | Master icon, 1024x1024 RGBA. The squircle fills the canvas edge to edge, corners transparent. |
| `icons/<n>x<n>.png` | Linux / electron-builder set: 16, 24, 32, 48, 64, 128, 256, 512. |
| `icon.ico` | Windows icon, PNG-compressed entries 16, 24, 32, 48, 64, 128, 256. |
| `icon.icns` | macOS icon, PNG entries `ic07` 128, `ic08` 256, `ic09` 512, `ic10` 1024. |
| `splash.jpg` | Splash image, 1600x1000 JPEG (under 400 KB). |
| `../splash.html` | Static splash page: the image full-window, no scripts, no remote resources, strict CSP. |

The same script also writes what both apps serve from `harness_ui/assets/`: `favicon.ico` (16, 32, 48), `apple-touch-icon.png` (180, opaque and full-bleed because iOS rounds the corners and paints transparency black) and the `keepharness` symbol of `icons.svg`, which is the in-app mark (rail of the harness, header of the admin).

## How they were made

```sh
node scripts/brand-assets.cjs <splash.jpeg>   # Node 22.2+, ffmpeg and ffprobe on PATH, nothing else
```

- The geometry (ring, bars, nodes, the gap cut around each node, corner radius) is one description at the top of the script, measured on Sophia's reference image in squircle widths. The script writes the two SVG files and the sprite symbol from it and rasterizes the same description with an analytic one-pixel anti-aliased edge, so the SVG and the PNGs agree (checked in Chrome: 0.05/255 mean difference at 1024).
- Up to 32 px the ring and bars are thickened and every edge snaps to the pixel grid, so the ring and the nodes stay crisp at 16, 24 and 32. The in-app symbol is thickened too, but not snapped.
- The splash is Gemini's original splash image, scaled to 1600x1000 (centre-cropping the 1.608 aspect to 1.6). The icon Gemini drew in it is covered by the final one, 2.5 px larger on every side, before scaling; the background, wordmark and tagline stay as generated.

## Provenance

Generated with Google Gemini image generation for Sophia Phillipa on 2026-10-03, and approved by her (decision D51: "Design 2", chosen because the first mark read as a frog at 16 to 32 px). The reference image and the original splash image are not committed.
