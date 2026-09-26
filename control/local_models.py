"""Discover local llama.cpp processes without exporting credential contents."""

import asyncio
import math
import os
import re
from pathlib import Path

import httpx


def processes():
    found = []
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            args = (proc / "cmdline").read_bytes().decode().split("\0")
            if Path(args[0]).name != "llama-server":
                continue

            def option(*names, default=""):
                return next((args[args.index(n) + 1] for n in names if n in args), default)

            host = option("--host", default="127.0.0.1")
            if host not in ("127.0.0.1", "localhost", "0.0.0.0"):
                continue
            found.append(
                {
                    "url": "http://127.0.0.1:" + option("--port", default="8080"),
                    "key_file": option("--api-key-file"),
                    "binary": args[0],
                    "model_file": option("--model", "-m"),
                    "mmproj_file": option("--mmproj"),
                    "flags": [flag for flag in PROFILE_FLAGS if "--" + flag in args],
                    "performance": performance(args),
                }
            )
        except (OSError, ValueError, IndexError, UnicodeError):
            continue
    return found


async def discover():
    results = []
    async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
        for server in processes():
            try:
                headers = {}
                if server["key_file"]:
                    headers["Authorization"] = (
                        "Bearer " + Path(server["key_file"]).read_text().strip()
                    )
                r = await client.get(server["url"] + "/v1/models", headers=headers)
                r.raise_for_status()
                for model in r.json().get("data", []):
                    results.append(
                        {
                            **server,
                            "id": model["id"],
                            "runtime": "llama.cpp",
                            "context": model.get("meta", {}).get("n_ctx"),
                        }
                    )
            except (OSError, httpx.HTTPError, ValueError, KeyError):
                continue
    return results


# Only performance options: never import credentials or arbitrary CLI arguments.
PERFORMANCE_FLAGS = (
    "device",
    "n-gpu-layers",
    "ctx-size",
    "parallel",
    "flash-attn",
    "cache-type-k",
    "cache-type-v",
    "cache-ram",
    "reasoning",
    "reasoning-format",
    "reasoning-budget",
    "threads",
    "n-cpu-moe",
    "threads-batch",
    "load-mode",
    "cpu-range",
    "cpu-strict",
    "cpu-range-batch",
    "cpu-strict-batch",
    "temp",
    "top-k",
    "top-p",
    "min-p",
    "repeat-penalty",
    "seed",
    "main-gpu",
    "split-mode",
    "tensor-split",
)
PROFILE_FLAGS = ("no-mmproj-offload", "kv-offload")
MODEL_PERMISSIONS = ("read", "write", "upload", "tests", "internet", "shell", "hooks")


def performance(args):
    return {
        name: args[args.index("--" + name) + 1]
        for name in PERFORMANCE_FLAGS
        if "--" + name in args and args.index("--" + name) + 1 < len(args)
    }


def save_profile(state, server):
    import json

    profile = {
        k: server[k]
        for k in (
            "binary",
            "model_file",
            "description",
            "mmproj_file",
            "flags",
            "performance",
            "permissions",
            "capabilities",
            "allowed_roots",
        )
        if k in server
    }
    if not profile.get("model_file"):
        raise ValueError("Select the weights file for this profile.")
    profile["model_file"] = str(Path(profile["model_file"]).expanduser().resolve())
    profiles = load_profiles(state)
    previous = profiles.get(profile["model_file"], {})
    for key in ("description", "permissions", "capabilities", "allowed_roots"):
        if key not in profile and key in previous:
            profile[key] = previous[key]
    profiles[profile["model_file"]] = profile
    for name, value in [("local-profiles.json", profiles), ("local-profile.json", profile)]:
        target = Path(state) / name
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(value, indent=2))
        temp.chmod(0o600)
        temp.replace(target)
    return profile


def load_profiles(state):
    import json

    try:
        stored = json.loads((Path(state) / "local-profiles.json").read_text())
    except FileNotFoundError:
        stored = {}
    if not isinstance(stored, dict):
        raise ValueError("Invalid local profile catalog.")
    profiles = {}
    for value in stored.values():
        if not isinstance(value, dict) or not value.get("model_file"):
            raise ValueError("Invalid local profile.")
        key = str(Path(value["model_file"]).expanduser().resolve())
        profiles[key] = {**value, "model_file": key}
    try:
        legacy = json.loads((Path(state) / "local-profile.json").read_text())
    except FileNotFoundError:
        legacy = {}
    if not isinstance(legacy, dict):
        raise ValueError("Invalid legacy local profile.")
    if legacy.get("model_file"):
        key = str(Path(legacy["model_file"]).expanduser().resolve())
        profiles.setdefault(key, {**legacy, "model_file": key})
    return profiles


def load_profile(state, model_file=None):
    import json

    profiles = load_profiles(state)
    if model_file is not None:
        return profiles.get(str(Path(model_file).expanduser().resolve()), {})
    try:
        legacy = json.loads((Path(state) / "local-profile.json").read_text())
    except FileNotFoundError:
        legacy = {}
    if legacy.get("model_file"):
        return profiles.get(str(Path(legacy["model_file"]).expanduser().resolve()), {})
    return next(iter(profiles.values()), {})


def launch_options(profile):
    values = profile.get("performance", {})
    return [
        value
        for name in PERFORMANCE_FLAGS
        if name in values
        for value in ("--" + name, str(values[name]))
    ]


def launch_command(profile, *, key_file, port=8096, alias="managed-local"):
    """Build the fixed local-only llama.cpp invocation from a validated profile."""
    profile = validate_profile(profile)
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Invalid port.")
    if not isinstance(alias, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", alias):
        raise ValueError("Invalid alias.")
    key = Path(key_file).expanduser()
    if not key.is_absolute():
        raise ValueError("Key file must use an absolute path.")
    command = [
        profile["binary"],
        "--model",
        profile["model_file"],
        "--alias",
        alias,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--api-key-file",
        str(key),
        "--jinja",
    ]
    if profile.get("mmproj_file"):
        command.extend(["--mmproj", profile["mmproj_file"]])
    command.extend("--" + flag for flag in profile.get("flags", ()))
    return [*command, *launch_options(profile)]


def validate_profile(profile):
    if not profile:
        return {}
    if not isinstance(profile, dict) or set(profile) - {
        "binary",
        "model_file",
        "description",
        "mmproj_file",
        "flags",
        "performance",
        "permissions",
        "capabilities",
        "allowed_roots",
    }:
        raise ValueError("Local profile contains fields that are not allowed.")
    binary = Path(profile.get("binary", ""))
    model = Path(profile.get("model_file", ""))
    if not binary.is_absolute() or binary.name != "llama-server" or not binary.is_file():
        raise ValueError("Profile executable not found on this machine.")
    if not model.is_absolute() or model.suffix != ".gguf" or not model.is_file():
        raise ValueError("Profile model not found on this machine.")
    values = profile.get("performance", {})
    if not isinstance(values, dict) or set(values) - set(PERFORMANCE_FLAGS):
        raise ValueError("Performance options not allowed.")
    if any(
        not isinstance(v, str)
        or not v
        or len(v) > 100
        or v.startswith("--")
        or any(ord(c) < 32 for c in v)
        for v in values.values()
    ):
        raise ValueError("Invalid performance value.")
    integer_ranges = {
        "n-gpu-layers": (0, 999),
        "ctx-size": (0, 10000000),
        "parallel": (1, 256),
        "threads": (1, 4096),
        "threads-batch": (-1, 4096),
        "n-cpu-moe": (0, 100000),
        "reasoning-budget": (-1, 10000000),
        "cache-ram": (-1, 2**40),
        "cpu-strict": (0, 1),
        "cpu-strict-batch": (0, 1),
        "top-k": (-1, 100000),
        "seed": (-1, 2**32 - 1),
        "main-gpu": (0, 255),
    }
    float_ranges = {"temp": (0, 100), "top-p": (0, 1), "min-p": (0, 1), "repeat-penalty": (0, 100)}
    for name, (minimum, maximum) in {**integer_ranges, **float_ranges}.items():
        if name not in values:
            continue
        try:
            value = int(values[name]) if name in integer_ranges else float(values[name])
        except ValueError:
            raise ValueError("Invalid numeric value for " + name + ".") from None
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError("Value out of the allowed range for " + name + ".")
    for name in ("cpu-range", "cpu-range-batch"):
        if name not in values:
            continue
        if not re.fullmatch(r"\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*", values[name]):
            raise ValueError("Provide CPUs like 0-7 or 0-3,8-11.")
        for part in values[name].split(","):
            bounds = [int(value) for value in part.split("-")]
            if bounds[0] > bounds[-1] or bounds[-1] > 4095:
                raise ValueError("Invalid CPU range.")
    if "split-mode" in values and values["split-mode"] not in ("none", "layer", "row"):
        raise ValueError("Invalid GPU split mode.")
    if "tensor-split" in values:
        try:
            ratios = [float(part) for part in values["tensor-split"].split(",")]
        except ValueError:
            raise ValueError("Provide GPU ratios separated by commas.") from None
        if (
            not 1 <= len(ratios) <= 256
            or any(not math.isfinite(value) or value < 0 for value in ratios)
            or sum(ratios) <= 0
        ):
            raise ValueError("Invalid GPU ratios.")
    result = {
        "binary": str(binary.resolve()),
        "model_file": str(model.resolve()),
        "performance": dict(values),
    }
    if "description" in profile:
        description = profile["description"]
        if not isinstance(description, str) or len(description) > 2000:
            raise ValueError("Invalid profile description.")
        result["description"] = description
    if "mmproj_file" in profile:
        mmproj = Path(profile["mmproj_file"]).expanduser()
        if not mmproj.is_absolute() or mmproj.suffix != ".gguf" or not mmproj.is_file():
            raise ValueError("Multimodal projector not found on this machine.")
        result["mmproj_file"] = str(mmproj.resolve())
    if "flags" in profile:
        flags = profile["flags"]
        if (
            not isinstance(flags, list)
            or len(flags) > len(PROFILE_FLAGS)
            or any(flag not in PROFILE_FLAGS for flag in flags)
        ):
            raise ValueError("Profile-specific flags not allowed.")
        result["flags"] = list(dict.fromkeys(flags))
    for key, allowed in [("permissions", MODEL_PERMISSIONS), ("capabilities", ("tools",))]:
        if key not in profile:
            continue
        items = profile[key]
        if (
            not isinstance(items, dict)
            or set(items) - set(allowed)
            or any(type(value) is not bool for value in items.values())
        ):
            raise ValueError("Permissions and capabilities must use allowed boolean values.")
        result[key] = {name: items.get(name, False) for name in allowed}
    if "allowed_roots" in profile:
        roots = profile["allowed_roots"]
        if (
            not isinstance(roots, list)
            or len(roots) > 20
            or any(not isinstance(value, str) for value in roots)
        ):
            raise ValueError("Provide up to 20 local folders per model.")
        home = Path.home().resolve()
        sensitive = [
            home / name
            for name in (
                ".ssh",
                ".codex",
                ".claude",
                ".gemini",
                ".config",
                ".local/share/tail-harness",
            )
        ]
        checked = []
        for value in roots:
            root = Path(value).expanduser()
            if not root.is_absolute() or not root.is_dir():
                raise ValueError("Choose existing, absolute folders.")
            root = root.resolve()
            if root in (Path("/"), home) or any(
                root.is_relative_to(path) or path.is_relative_to(root) for path in sensitive
            ):
                raise ValueError("A broad or credentials folder cannot be shared.")
            checked.append(str(root))
        result["allowed_roots"] = list(dict.fromkeys(checked))
    return result


def runtime_permissions(state, runtimes, models):
    """Bind API aliases to the discovered weights; never inherit another file's access."""
    profiles = load_profiles(state)
    result = {}
    for model in models:
        matches = [item for item in runtimes if item.get("id") == model]
        profile = {}
        if len(matches) == 1 and matches[0].get("model_file"):
            profile = profiles.get(str(Path(matches[0]["model_file"]).expanduser().resolve()), {})
        tools = profile.get("capabilities", {}).get("tools") is True
        result[model] = {
            name: profile.get("permissions", {}).get(name) is True and (name == "upload" or tools)
            for name in MODEL_PERMISSIONS
        }
    return result


def runtime_roots(state, runtimes, models):
    profiles = load_profiles(state)
    result = {}
    for model in models:
        matches = [item for item in runtimes if item.get("id") == model]
        profile = (
            profiles.get(str(Path(matches[0]["model_file"]).expanduser().resolve()), {})
            if len(matches) == 1 and matches[0].get("model_file")
            else {}
        )
        result[model] = list(profile.get("allowed_roots", []))
    return result


async def runtime_details(binary):
    """Inspect fixed informational flags only, without loading weights or starting a server."""
    path = Path(binary).expanduser()
    if not path.is_absolute() or path.name != "llama-server" or not path.is_file():
        raise ValueError("Provide an existing llama-server executable.")

    async def inspect(flag):
        proc = await asyncio.create_subprocess_exec(
            str(path), flag, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        try:
            async with asyncio.timeout(5):
                output = b""
                while chunk := await proc.stdout.read(min(8192, 65537 - len(output))):
                    output += chunk
                    if len(output) > 65536:
                        raise ValueError("The runtime response exceeded the discovery limit.")
                await proc.wait()
            return proc.returncode, output.decode(errors="replace")
        except TimeoutError:
            raise ValueError("The runtime took too long to respond to device discovery.") from None
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    code, output = await inspect("--list-devices")
    if code:
        raise ValueError("Could not list this runtime's devices.")
    help_code, help_text = await inspect("--help")
    devices = []
    for line in output.splitlines():
        match = re.match(r"^\s*([A-Za-z][\w.-]*\d+)\s*:\s*(.+)$", line)
        if match:
            devices.append({"id": match[1], "name": match[2]})
    flags = (
        [
            name
            for name in PERFORMANCE_FLAGS
            if re.search(r"--" + re.escape(name) + r"(?:\s|=|,|$)", help_text)
        ]
        if help_code == 0
        else []
    )
    return {
        "devices": devices,
        "output": output,
        "supported_flags": flags,
        "capabilities_verified": help_code == 0,
    }
