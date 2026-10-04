# Model-aware attachments

## Supported paths

- UTF-8 text, source code, CSV and TSV: decoded as text without an extension allowlist.
- PDF: local selectable-text extraction (scanned pages are not automatically OCRed).
- EPUB: archive container, package manifest and spine order; chapter markup stripped; no scripts or external resources executed. DRM is not supported.
- DOCX, PPTX, XLSX, ODT, ODS and ODP: bounded ZIP/XML text extraction. Spreadsheet cell coordinates and shared strings are retained; layout, embedded images and recalculation are not provided.
- PNG, JPEG, GIF and WebP: original image bytes sent as native image content. Upload and execution verify the selected service. Local llama.cpp must report `modalities.vision=true`; an upload permission alone does not imply vision.
- MP4: model-aware admission, four sampled JPEG frames with timestamps and local speech transcription when an audio track exists. Frames represent 0%, 25%, 50% and 75% of duration; motion, intervening events and the full visual timeline are not reconstructed.
- WAV, MP3, M4A, OGG, FLAC, WEBM, AAC and OPUS: offline speech transcription using whisper.cpp base, followed by text input. This is speech recognition, not native sound/music understanding or speech synthesis.

All individual attachments are limited to 100 MiB (104,857,600 bytes), inclusive. Audio and MP4 duration is limited to four hours. Office/EPUB archives have a 20 MiB uncompressed and 2,000-entry limit. Parsers do not extract archive paths to disk or load external XML entities. Media decoding/transcription runs in a bounded, network-isolated sandbox.

Large extracted documents are stored in the conversation's private attachments directory. The prompt includes at most 6,000 characters per file, explicitly labeled as an excerpt, plus the complete text path for bounded tool reads. If tools are disabled, the agent must request a smaller excerpt rather than claim full access. Conversation/project ownership and upload grants still apply. Raw uploads are preserved.

## Local deployment verified

The Qwen3.6 F16 vision projector was checksum verified and enabled using `--mmproj` with `--no-mmproj-offload`, retaining the existing language model GPU/CPU settings. The original launch profile was backed up privately. The runtime reports vision and video support, but this interface currently forwards still images only.

Whisper.cpp was built locally for CPU transcription; its base model checksum was verified. Runtime and model weights remain outside the repository. The optional runtime directory defaults to `~/.local/share/keepharness/whisper.cpp` and can be changed using `KEEPHARNESS_WHISPER_DIR`. This optional runtime is not bundled with the Python distribution.

Local integration job `227884aa980c49ea99c298493cd2c190` completed: an uploaded red PNG was identified correctly and a WAV speech sample was transcribed and quoted. No cloud inference was used. Native Codex/Claude image serialization is implemented but cloud execution was not tested.

## Limitations

This is not an arbitrary binary-file interpreter. Legacy DOC/XLS/PPT, DRM books, encrypted documents, video-frame extraction and native audio reasoning need separate adapters. Unsupported formats must not be represented as readable. Validate upstream model capabilities rather than infer them from marketing names.

Local document integration job `6b7c9017ec074fedbff5dd34b8365ff0` completed: an EPUB control phrase was recovered, and a 657,809-byte CSV was read with a real terminal tool to retrieve its last-line marker beyond the 6,000-character excerpt. The first run exposed a symlink/canonical-path mismatch; the path was corrected and the successful rerun required no approval escalation. Eight format/recovery regression tests were added; the full Python suite passes 112 tests plus two subtests.

## Expanded limits (not load-tested)

Audio duration is capped at 14,400 seconds, uploads at 256 MiB, decoding output
at 512 MiB, transcription address space at 4 GiB and wall time at two hours.
The two-hour wall timeout is a processing budget, separate from the four-hour
input duration limit. These bounds do not certify four-hour transcription
performance.

## Suggested GPU profile

The author-suggested [Qwen profile](../profiles/qwen-vulkan-profile.json) records
the approved example configuration, including context size, CPU/GPU placement
and vision projector settings. Review these parameters for the target machine;
the release regressions do not include GPU or full-context stress benchmarks.

## Explicit unsupported-attachment notices

The chat displays an assistant notice naming each rejected file when direct
upload or folder selection encounters an unsupported image or binary format.
These pre-submission notices remain in the current view; they are not stored as
conversation turns. Importer limitations are identified as missing readers,
not incorrectly attributed to the model.

At inference time, images unsupported by DeepSeek, local runtimes reporting
vision disabled, or scoped execution are excluded while supported extracted
text remains available. This also covers images inherited when changing the
conversation model. The harness emits the explanation in the response stream
and prepends it to the saved answer, independently of the model's wording.
Native Codex, Claude and Gemini retain their existing image handling, as do
local runtimes whose capability probe confirms vision. Probe/network failures
remain errors rather than being treated as evidence of incompatibility.

Validation: 23 targeted Python attachment tests and the mocked Playwright
attachment-notice scenario passed. Provider execution was mocked; no real
provider inference was performed.

## Parser hardening — 2026-09-21

Image admission now decodes the first frame with FFmpeg before returning image
metadata. This requires the existing local `ffmpeg`, `bwrap` and `prlimit`
executables; the Python package does not install them. The decoder has no
network, uses only file/pipe protocols and runs with a 30-second wall timeout,
2 GiB address-space bound and 60-second CPU bound. The 5 MiB upload limit does
not bound decoded pixel dimensions. First-frame validation does not certify
all animation frames. Missing tools return `image_validation_unavailable`,
timeouts return `image_validation_timeout`, and rejected data returns
`invalid_image`. These are local importer results, independent of the model's
vision capability. Upload bytes remain unchanged.

Office/EPUB XML rejects DTDs at parse time, including UTF-16 input and EPUB
container/package metadata. Valid XML without a DTD retains encoding support.
EPUB reading-order expansion also counts repeated chapter references against
the existing 20 MiB/2,000-page budget. Spreadsheet shared-string indices must
be nonnegative, and ODF paragraph text preserves inline formatting text and
tails. Audio dispatch no longer loads the whole file before invoking the
existing transcription path.

Validation: 50 targeted parser, attachment-format and private-preview tests
passed, including actual sandboxed image decoding and mocked audio dispatch.
No speech/model inference or full-suite run was performed in this campaign.
See [parser evidence](../docs/validation/2026-09-21-attachment-parsers.md).

EPUBs with missing reading-order references recover the readable chapters in
order and prepend an explicit incomplete-extraction warning with the missing
reference count. If no chapter can be read, extraction fails with
`document_text_unavailable`. A read-only authorized real-document check
confirmed this recovery; three synthetic regressions were added, bringing the
targeted parser/format/preview result to 53 passing tests. Extracted sections
are not equivalent to printed pages or proof of complete book content.

## Four-hour audio admission and cancellation

The audio duration limit is now 14,400 seconds. The byte limit remains 256 MiB
and decoded PCM remains capped at 512 MiB: four hours of 16 kHz mono 16-bit
audio occupy approximately 460.8 MB before the WAV header. Video ingestion was
not added. Existing decode/transcription timeouts and CPU bounds remain in
force; a full four-hour transcription was not run by this contract campaign.

Synthetic tests exercise the full transcription control flow with mocked
probe/decode/speech tools, accepting 14,225.536 and 14,400 seconds, rejecting
longer/nonfinite/invalid durations, and checking cleanup during cancellation
of decode and transcription. Actual short subprocess tests verify process
group cancellation, including a descendant whose parent has already exited.
The shared process helper now terminates an incomplete operation's group even
when its leader already has a return code. Thirteen new direct audio/process
tests and the directly affected attachment tests passed: 66 tests total.


## MP4 and uniform attachment admission — 2026-09-22

The current per-file limit is 100 MiB across direct uploads, project-file imports
and the CLI upload helper. Older dated validation sections above describe their
original limits. Expansion, project storage, duration and inference context
budgets are independent of this upload limit. Native providers can impose their
own input limits.

The CLI upload helper does not send a model selection, so it cannot admit
MP4 or image attachments. Use the composer or the upload API with explicit
`backend`, `model` and a compatible `execution_mode` for those formats.

`/v1/models` exposes `capabilities.video`, `video_execution_modes` and
`video_transcription`. The composer reads these capabilities and refreshes its
help when the model or mode changes. Upload permission remains required. The
server checks capability again when admitting MP4, including imported project
files. Missing capability evidence does not grant MP4 support.

Decoding runs through the existing network-isolated FFmpeg sandbox. Generated
frames are bounded JPEGs, with timestamps and a sampling limitation in the
model context. Original MP4 bytes are not sent as image content or returned
through the raster preview endpoint. A speech track uses the existing local
Whisper pipeline; missing transcription dependencies or failed processing
produce an explicit error instead of claiming full interpretation. Silent
videos can be interpreted from frames without Whisper.

Validation: 253 focused backend tests, seven MP4 browser profiles, existing
attachment/preview/permission browser regressions and a real short local
FFmpeg/Whisper smoke check passed. See the [validation record](../docs/validation/2026-09-22-mp4-attachments.md)
for commands, capability sources, service verification and limitations.
