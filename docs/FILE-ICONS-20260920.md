# File icons and twenty attachments

The file tree uses Material Icon Theme 5.38.1 (MIT), with 1,126 referenced SVG
icons, 1,377 extension associations, 2,135 filename associations and 4,654 folder
name associations. Recognized folders use the theme's colors and distinct open
and closed icons. Unknown names retain generic file/folder icons.

Exact filenames take precedence over compound extensions and simple extensions.
Matching is case-insensitive and uses the basename. Shell, JSON, JavaScript, PHP,
Photoshop, Illustrator, raster images and SVG have their upstream associations.
Non-image attachments also use the same icons in the composer and message history;
image thumbnails remain available. An icon does not add a content reader for a
previously unsupported binary format.

The generated sprite and mapping are served from `/assets/`, with no runtime CDN,
Node dependency or filesystem names sent to an external icon service. The existing
package-data wildcard includes the assets and upstream license in the wheel.

The composer, restored drafts, selection modifiers, drag/drop, folder selection
and API submission now share a maximum of 20 attachments per message. A 21st
attachment is rejected. The attachment area scrolls at a bounded height to keep
the prompt and send control visible on desktop and mobile. File size, permissions and extraction limits are unchanged.
The Python limit lives in `workspaces.MAX_ATTACHMENTS`; the UI mirrors it in
`MAX_ATTACHMENTS`.

## Upstream and regeneration

- [Material Icon Theme](https://github.com/material-extensions/vscode-material-icon-theme)
- [Official npm package](https://www.npmjs.com/package/material-icon-theme)
- [Alternative evaluated: vscode-icons](https://github.com/vscode-icons/vscode-icons)

The pinned npm tarball is `material-icon-theme-5.38.1.tgz`, SHA-512 integrity:
`sha512-14cFM4NJGdbuo68rIZTq9TSX0f5BtA6VF+eNX3zq23Z5NoEeVtM4Zn4PpZ0ERl++50jCBrywKFWXmOjbxf0xTA==`.
Verify this against the downloaded bytes, extract the package into a temporary
directory, then use its `dist/module/index.cjs` export `generateManifest()` to
write a JSON manifest. Run:

```sh
python scripts/vendor-file-icons.py /absolute/package /absolute/manifest.json
```

The generator preserves SVG view boxes/colors and namespaces internal paint/clip
IDs to avoid collisions in the shared sprite. It includes only icons referenced
by the default file/folder associations. The complete source package is not a
runtime dependency.

## Validation

- `tests/test_attachment_count.py`: both selection helpers accept 20 and reject
  an invalid cap of 21; folder attachment defaults to 20; submission accepts 20
  and rejects 21 without starting an inference worker.
- `tests/test_project_browser.py`: existing traversal, permission and attachment
  regressions pass alongside the new count tests (11 tests).
- `tests/harness-files-panel.spec.cjs`: real local sprite loading, required file
  associations, folder open/closed state, selection, upload overflow and reload.
- `tests/test_file_icon_assets.py`: complete symbol coverage, unique SVG IDs and
  the explicit static asset allowlist.

JEV selected the full static catalog with confidence 0.90: 592 input tokens,
40 output tokens, 817 ms. No decision rework or token savings were measured. The catalog was verified against the
downloaded package before integration.

## Missing-catalog recovery

A previously running Python process retained the old static asset allowlist and
returned 404 for the new catalog while serving updated HTML/JavaScript from disk.
Restarting the service loaded the new allowlist. The icon renderer now falls back
to inline generic SVGs when the catalog is absent, keeping folders and attachments
usable. A browser regression removes the catalog, renders the tree, then restores
it and verifies the normal associations. Live catalog and sprite requests returned
HTTP 200 after restart.
