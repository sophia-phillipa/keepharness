# Model-aware attachments

## Supported paths

- UTF-8 text, source code, CSV and TSV: decoded as text without an extension allowlist.
- PDF: local selectable-text extraction (scanned pages are not automatically OCRed).
- EPUB: archive container, package manifest and spine order; chapter markup stripped; no scripts or external resources executed. DRM is not supported.
- DOCX, PPTX, XLSX, ODT, ODS and ODP: bounded ZIP/XML text extraction. Spreadsheet cell coordinates and shared strings are retained; layout, embedded images and recalculation are not provided.
- PNG, JPEG, GIF and WebP: original image bytes sent as native image content. Upload and execution verify the selected service. Local llama.cpp must report `modalities.vision=true`; an upload permission alone does not imply vision.
- WAV, MP3, M4A, OGG, FLAC, WEBM, AAC and OPUS: offline speech transcription using whisper.cpp base, followed by text input. This is speech recognition, not native sound/music understanding or speech synthesis.

Documents are limited to 50 MiB, images to 5 MiB, audio to two hours / 256 MiB. Office/EPUB archives have a 20 MiB uncompressed and 2,000-entry limit. Parsers do not extract archive paths to disk or load external XML entities. Media decoding/transcription runs in a bounded, network-isolated sandbox.

Large extracted documents are stored in the conversation's private attachments directory. The prompt includes at most 6,000 characters per file, explicitly labeled as an excerpt, plus the complete text path for bounded tool reads. If tools are disabled, the agent must request a smaller excerpt rather than claim full access. Conversation/project ownership and upload grants still apply. Raw uploads are preserved.

## Local deployment verified

The Qwen3.6 F16 vision projector was checksum verified and enabled using `--mmproj` with `--no-mmproj-offload`, retaining the existing language model GPU/CPU settings. The original launch profile was backed up privately. The runtime reports vision and video support, but this interface currently forwards still images only.

Whisper.cpp was built locally for CPU transcription; its base model checksum was verified. Runtime and model weights remain outside the repository. The optional runtime directory defaults to `~/.local/share/tail-harness/whisper.cpp` and can be changed using `TAIL_HARNESS_WHISPER_DIR`. This optional runtime is not bundled with the Python distribution.

Local integration job `227884aa980c49ea99c298493cd2c190` completed: an uploaded red PNG was identified correctly and a WAV speech sample was transcribed and quoted. No cloud inference was used. Native Codex/Claude image serialization is implemented but cloud execution was not tested.

## Limitations

This is not an arbitrary binary-file interpreter. Legacy DOC/XLS/PPT, DRM books, encrypted documents, video-frame extraction and native audio reasoning need separate adapters. Unsupported formats must not be represented as readable. Validate upstream model capabilities rather than infer them from marketing names.

Local document integration job `6b7c9017ec074fedbff5dd34b8365ff0` completed: an EPUB control phrase was recovered, and a 657,809-byte CSV was read with a real terminal tool to retrieve its last-line marker beyond the 6,000-character excerpt. The first run exposed a symlink/canonical-path mismatch; the path was corrected and the successful rerun required no approval escalation. Eight format/recovery regression tests were added; the full Python suite passes 112 tests plus two subtests.

## Expanded limits (not load-tested)

Audio duration is capped at 7,200 seconds, uploads at 256 MiB, decoding output
at 512 MiB, transcription address space at 4 GiB and wall time at two hours.
These bounds do not certify two-hour transcription performance.

## Suggested GPU profile

The author-suggested [Qwen profile](../profiles/qwen-author-profile.json) records
the approved example configuration, including context size, CPU/GPU placement
and vision projector settings. Review these parameters for the target machine;
the release regressions do not include GPU or full-context stress benchmarks.
