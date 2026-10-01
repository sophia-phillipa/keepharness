# Compact visual regression evidence

`tests/harness-visual-compact.spec.cjs` generates the complete compact-workspace matrix: four viewports, six themes, and six UI states (144 screenshots). It retains the approved application scale while testing the denser workspace structure. It also writes `summary.json` with geometry, contrast, 14 main/recovery scenarios for the seven simulated profiles, and one separate mobile empty-state scenario.

Run it directly with the repository's approved Playwright installation:

```sh
PLAYWRIGHT_MODULE=/path/to/installed/playwright \
  node tests/harness-visual-compact.spec.cjs
```

By default the suite creates a unique `tail-harness-visual-*` directory under the operating system's temporary directory and prints that path. The images are current-run evidence rather than committed pixel baselines: fonts and browser rasterization are environment-dependent, while the suite makes the layout contract reproducible through geometry, hit-testing, overflow, target-size, and WCAG AA assertions.

For an explicitly requested evidence directory, set `VISUAL_OUTPUT_DIR`. The comparison capture also requires the mock path explicitly and never writes beside that source:

```sh
VISUAL_OUTPUT_DIR=/absolute/evidence/directory \
MOCK_REFERENCE_HTML=/absolute/read-only/mock-4.html \
PLAYWRIGHT_MODULE=/path/to/installed/playwright \
  node tests/visual/capture-compact-comparison.cjs
```

That command writes `compact-desktop-mock-closed.png`, `compact-desktop-mock-open.png`, `compact-desktop-app-closed.png`, and `compact-desktop-app-open.png`. The app pair uses the Porcelain light palette for direct comparison. Normal test runs do not use a repository or private path for screenshots.
