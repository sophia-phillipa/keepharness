# Portable local installation

This flow supports Linux servers and keeps the runtime, models and key inside the project, in paths ignored by Git:

```text
local_ai/runtime/llama-b11003/
local_ai/models/
local_ai/config/api-key
```

It does not install system dependencies, does not download weights together with the runtime, and does not start the server during installation.

## 0. Install the application

From the checkout root, run `./setup.sh` to create `.venv` and install the Python dependencies. On Linux/systemd, `./install.sh` also installs the admin service, without running the test suite. The module commands below use `.venv/bin/python`; if you used `install.sh`, use `~/.local/share/keepharness/venv/bin/python` instead.

### Logs and environment checks

The product state directory contains `logs/harness.log` and `logs/admin.log`.
The default state directory is `~/.local/share/keepharness`; an explicit admin
`--state` directory or harness configuration determines the location for that
instance. Each process keeps its own log so that rotations do not compete.
Logs use UTC ISO timestamps and the same secret redaction as stderr.
`KEEPHARNESS_LOG_LEVEL` controls both outputs (default `WARNING`).

Each file is capped at 1 MiB (`MAX_LOG_BYTES`), with three rotated backups
(`LOG_BACKUP_COUNT`), for at most 4 MiB per process. Oversized records are
truncated. The separate `audit.jsonl` retains its JSON Lines format and the
newest 1,000 entries; older entries are trimmed by an atomic replacement.

The admin provides a read-only tail of `harness.log`, limited to 200 lines and
a 64 KiB read. The tail is redacted again and available only to the local
owner session. The status also shows the harness's last exit alongside startup
errors. **Check environment** reports whether `ffmpeg`, `bwrap` and `prlimit`
are on PATH, with installation hints for Fedora/Bazzite, Debian/Ubuntu and
Arch. Hints do not install packages. Document extraction that needs an absent
`bwrap` reports `document_tools_unavailable` and names the missing tool.

## 1. Build llama.cpp

Have `python3`, `git`, a C/C++ compiler and `cmake` already installed. Build on each Linux server: a Linux binary built on one machine is not a universal package for other servers. Other operating systems are explicitly rejected by the current installer.

From the project root, the most portable CPU option is:

```sh
python3 control/install_runtime.py --root . --backend cpu
```

On a Linux machine that already has the Vulkan SDK:

```sh
python3 control/install_runtime.py --root . --backend vulkan
```

On a machine that already has the CUDA toolkit:

```sh
python3 control/install_runtime.py --root . --backend cuda
```

The installer clones the official `ggml-org/llama.cpp` source, uses tag `b11003`, verifies commit `7d6f5d02bb40fca0ab29e65fe4eb86eab6886f19`, builds with CMake and only runs `llama-server --help`. If the runtime already exists, it refuses to overwrite it.

The setup follows the official llama.cpp documentation: [CPU, Vulkan and CUDA builds](https://github.com/ggml-org/llama.cpp/blob/b11003/docs/build.md).

## 2. Download the verified model

The existing downloader uses pinned revisions and SHA-256 hashes. For the catalog's Qwen3.6 UD-Q3_K_M, with an internal destination:

```sh
.venv/bin/python -m control.download_model qwen36 local_ai/models
```

For the other registered model:

```sh
.venv/bin/python -m control.download_model gemma4 local_ai/models
```

The file is written as a temporary file, only receives its final name after the SHA-256 check, and never replaces an already-existing model.

## 3. Start the server

The generic CPU profile avoids assuming a GPU and serves as a starting point for another server:

```sh
.venv/bin/python -m control.start_local \
  --root . \
  --profile profiles/local-cpu-profile.json \
  --port 8091 \
  --alias qwen-local \
  --key-file local_ai/config/api-key
```

The `profiles/qwen-vulkan-profile.json` profile is an author-suggested example for Vulkan, with a specific multimodal projector and CPU affinity. It is distributed as an authorized example, with no credentials. Review its parameters and point to its path with `--profile`; it is not a universal configuration.

To only check the command without starting the runtime or creating the key, add `--check`.

## Latest validation

On 2026-09-19, the CPU installation completed in `local-ai/portable-check` with an empty root and `--jobs 2`. A private CMake 4.4.3 in `local-ai/build-tools` was used, with no system dependency installed. The official `b11003` source was checked at commit `7d6f5d02bb40fca0ab29e65fe4eb86eab6886f19`; the final destination received `llama-server`, libraries, `LICENSE` and `runtime.json`. The final `RUNPATH` is `$ORIGIN` and `llama-server --help` passed after publishing. No weights were downloaded and no GPU was initialized.

## Installation from a wheel

The wheel includes the profiles under `share/keepharness/profiles` in the Python environment's prefix. Copy the chosen profile into a `profiles/` folder in your data root and pass that root via `--root` to the installer/launcher. For a panel installed from a wheel, set `KEEPHARNESS_ROOT` to the same root: runtime, downloads and the key will use `<root>/local_ai`. The package does not ship this server's private configuration or weights.

The default `qwen36` text downloader only downloads the UD-Q3_K_M weights. For images, explicitly configure a compatible multimodal projector in a local profile; the distributed CPU profile does not include a projector.
