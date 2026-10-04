"""Per-model private hardware profiles; all binaries/process starts are fixtures."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient
from test_configuration import INVENTORY

from control.local_models import load_profile, load_profiles, save_profile
from control.server import create_app
from tests.owner_session import sign_in


def profiles(tmp_path):
    binary = tmp_path / "llama-server"
    binary.touch()
    result = []
    for name, layers in [("qwen", "44"), ("gemma", "12")]:
        model = tmp_path / (name + ".gguf")
        model.touch()
        result.append(
            {
                "binary": str(binary),
                "model_file": str(model),
                "performance": {"n-gpu-layers": layers, "threads": "8"},
            }
        )
    return result


def test_legacy_read_and_save_upsert_preserve_other_models(tmp_path):
    qwen, gemma = profiles(tmp_path)
    (tmp_path / "local-profile.json").write_text(json.dumps(qwen))
    assert load_profile(tmp_path, qwen["model_file"]) == qwen
    assert load_profile(tmp_path, gemma["model_file"]) == {}
    save_profile(tmp_path, gemma)
    assert load_profiles(tmp_path) == {p["model_file"]: p for p in [qwen, gemma]}
    assert load_profile(tmp_path) == gemma
    save_profile(tmp_path, {**qwen, "performance": {"n-gpu-layers": "0"}})
    assert load_profile(tmp_path, gemma["model_file"]) == gemma
    assert load_profile(tmp_path, qwen["model_file"])["performance"]["n-gpu-layers"] == "0"
    for name in ["local-profile.json", "local-profiles.json"]:
        assert (tmp_path / name).stat().st_mode & 0o777 == 0o600


def test_profiles_api_import_exact_model_start_and_export(tmp_path):
    qwen, gemma = profiles(tmp_path)
    qwen["description"] = "Exportable Qwen profile"
    with (
        patch("control.discovery.scan", AsyncMock(return_value=INVENTORY)),
        patch("control.local_models.processes", return_value=[]) as running,
        patch("control.routes.socket"),
        patch("control.operations.Operations.launch", return_value={"id": "fixture"}) as launch,
    ):
        with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8094") as client:
            sign_in(client).get("/")
            headers = {"X-Harness-Admin": "1"}

            def post(path, data):
                return client.post("/api/" + path, json=data, headers=headers)

            assert post("local-profile", qwen).status_code == 200
            assert (
                post("local-start", {"file": gemma["model_file"], "use_profile": True}).status_code
                == 400
            )
            launch.assert_not_called()
            running.return_value = [qwen, gemma]
            assert post("local-import", {"file": gemma["model_file"]}).json() == gemma
            assert post("local-import", {}).status_code == 400
            running.return_value = []
            result = post("local-start", {"file": gemma["model_file"], "use_profile": True})
            assert result.status_code == 200, result.text
            args = launch.call_args.args[0]
            assert args[0] == gemma["binary"]
            assert args[args.index("--model") + 1] == gemma["model_file"]
            assert args[args.index("--n-gpu-layers") + 1] == "12"
            expected = {p["model_file"]: p for p in [qwen, gemma]}
            assert client.get("/api/state").json()["local_profiles"] == expected
            bundle = post("settings-export", {}).json()
            assert bundle["local_profiles"] == expected
            bundle["local_profiles"][qwen["model_file"]]["performance"]["threads"] = "6"
            preview = post("settings-import", {"bundle": bundle})
            assert preview.status_code == 200 and preview.json()["local_profiles"] == 2
            assert load_profile(tmp_path, qwen["model_file"])["performance"]["threads"] == "8"
            assert post("settings-import", {"bundle": bundle, "apply": True}).status_code == 200
            assert load_profile(tmp_path, qwen["model_file"])["performance"]["threads"] == "6"
            legacy = {k: v for k, v in bundle.items() if k != "local_profiles"}
            legacy["local_profile"] = qwen
            assert post("settings-import", {"bundle": legacy, "apply": True}).status_code == 200
            assert load_profile(tmp_path, gemma["model_file"]) == gemma


def test_invalid_profile_catalog_does_not_partially_import(tmp_path):
    qwen, gemma = profiles(tmp_path)
    with patch("control.discovery.scan", AsyncMock(return_value=INVENTORY)):
        with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8094") as client:
            sign_in(client).get("/")
            headers = {"X-Harness-Admin": "1"}
            bundle = client.post("/api/settings-export", json={}, headers=headers).json()
            bundle["local_profiles"] = {qwen["model_file"]: qwen, gemma["model_file"]: qwen}
            result = client.post(
                "/api/settings-import", json={"bundle": bundle, "apply": True}, headers=headers
            )
            assert result.status_code == 400
            assert load_profiles(tmp_path) == {}
            assert not (tmp_path / "settings.json").exists()


def test_permissions_survive_reload_and_import_of_runtime_settings(tmp_path):
    from control.local_models import runtime_permissions

    qwen, gemma = profiles(tmp_path)
    qwen.update(
        permissions={"upload": True, "read": True, "shell": True}, capabilities={"tools": True}
    )
    save_profile(tmp_path, qwen)
    save_profile(tmp_path, gemma)
    # Hardware discovery must not erase a previously authorized model policy.
    save_profile(tmp_path, {k: qwen[k] for k in ("binary", "model_file", "performance")})
    runtime = [{"id": "qwen-local", "model_file": qwen["model_file"]}]
    permissions = runtime_permissions(Path(str(tmp_path)), runtime, ["qwen-local", "unknown"])
    assert permissions["qwen-local"]["upload"] is True
    assert permissions["qwen-local"]["shell"] is True
    assert not any(permissions["unknown"].values())
    assert load_profile(tmp_path, gemma["model_file"]) == gemma


def test_sampling_roundtrip_and_invalid_hardware_values(tmp_path):
    import pytest

    from control.local_models import launch_options, validate_profile

    qwen, _ = profiles(tmp_path)
    sampling = {
        "temp": "0.7",
        "top-k": "40",
        "top-p": "0.95",
        "min-p": "0.05",
        "repeat-penalty": "1.1",
        "seed": "-1",
        "cpu-range": "0-3,8-11",
    }
    profile = validate_profile({**qwen, "performance": sampling})
    save_profile(tmp_path, profile)
    assert load_profile(tmp_path, qwen["model_file"]) == profile
    assert "--top-p" in launch_options(profile)
    for name, value in [
        ("temp", "nan"),
        ("temp", "inf"),
        ("top-p", "1.1"),
        ("min-p", "-1"),
        ("threads", "0"),
        ("parallel", "0"),
        ("n-gpu-layers", "-1"),
        ("threads", "2.5"),
        ("cpu-range", "8-2"),
        ("cpu-range", "1;true"),
        ("seed", "4294967296"),
        ("api-key", "credential"),
        ("host", "0.0.0.0"),
    ]:
        with pytest.raises(ValueError):
            validate_profile({**qwen, "performance": {name: value}})


def test_multimodal_description_flags_roundtrip_and_launch_command(tmp_path):
    import pytest

    from control.local_models import launch_command, validate_profile

    qwen, _ = profiles(tmp_path)
    mmproj = tmp_path / "qwen-mmproj.gguf"
    mmproj.touch()
    profile = validate_profile(
        {
            **qwen,
            "description": "Test profile",
            "mmproj_file": str(mmproj),
            "flags": ["no-mmproj-offload", "kv-offload"],
        }
    )
    save_profile(tmp_path, profile)
    assert load_profile(tmp_path, qwen["model_file"]) == profile
    command = launch_command(profile, key_file=tmp_path / "key", port=8091, alias="qwen-local")
    assert command[command.index("--mmproj") + 1] == str(mmproj)
    assert "--no-mmproj-offload" in command and "--kv-offload" in command and "--jinja" in command
    for invalid in (
        {**qwen, "description": 1},
        {**qwen, "description": "x" * 2001},
        {**qwen, "mmproj_file": str(tmp_path / "missing.gguf")},
        {**qwen, "flags": ["api-key"]},
    ):
        with pytest.raises(ValueError):
            validate_profile(invalid)


def test_panel_uses_project_internal_runtime_models_and_key(tmp_path):
    internal = tmp_path / "local_ai"
    binary = internal / "runtime/llama-b11003/llama-server"
    binary.parent.mkdir(parents=True)
    binary.touch()
    model = internal / "models/example.gguf"
    model.parent.mkdir()
    model.touch()
    with (
        patch("control.env.LOCAL_AI", internal),
        patch("control.discovery.scan", AsyncMock(return_value=INVENTORY)),
        patch("control.local_models.processes", return_value=[]),
        patch("control.routes.socket"),
        patch("shutil.disk_usage") as disk,
        patch("control.operations.Operations.launch", return_value={"id": "fixture"}) as launch,
    ):
        disk.return_value.free = 100 * 1024**3
        with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8094") as client:
            sign_in(client).get("/")
            headers = {"X-Harness-Admin": "1"}
            r = client.post(
                "/api/local-start", json={"file": str(model), "gpu_layers": 0}, headers=headers
            )
            assert r.status_code == 200, r.text
            args = launch.call_args.args[0]
            assert args[0] == str(binary)
            assert args[args.index("--api-key-file") + 1] == str(internal / "config/api-key")
            assert (internal / "config/api-key").stat().st_mode & 0o777 == 0o600
            r = client.post(
                "/api/model-install", json={"model": "qwen36", "accepted": True}, headers=headers
            )
            assert r.status_code == 200, r.text
            assert launch.call_args.args[0][-1] == str(internal / "models")


def test_import_keeps_saved_description(tmp_path):
    qwen, _ = profiles(tmp_path)
    save_profile(tmp_path, {**qwen, "description": "Author-suggested profile"})
    save_profile(tmp_path, qwen)
    assert load_profile(tmp_path, qwen["model_file"])["description"] == "Author-suggested profile"


def test_local_ai_directory_migrates_once_and_rewrites_profile_paths(tmp_path):
    from control.server import migrate_local_ai_directory

    root = tmp_path / "root"
    legacy = root / "local-ai"
    (legacy / "models").mkdir(parents=True)
    (legacy / "models" / "Qwen.gguf").touch()
    state = tmp_path / "state"
    state.mkdir()
    stored = {
        str(legacy / "models" / "Qwen.gguf"): {
            "binary": str(legacy / "runtime/llama-b11003/llama-server"),
            "model_file": str(legacy / "models" / "Qwen.gguf"),
            "performance": {},
        }
    }
    (state / "local-profiles.json").write_text(json.dumps(stored, indent=2))

    migrate_local_ai_directory(root, state)

    assert not legacy.exists()
    assert (root / "local_ai" / "models" / "Qwen.gguf").is_file()
    migrated = json.loads((state / "local-profiles.json").read_text())
    entry = next(iter(migrated.values()))
    assert "local-ai/" not in entry["binary"] and "local-ai/" not in entry["model_file"]
    assert entry["binary"].endswith("local_ai/runtime/llama-b11003/llama-server")
    assert entry["model_file"].endswith("local_ai/models/Qwen.gguf")

    # Idempotent: a second call is a no-op, not an error, even though local-ai is now gone.
    migrate_local_ai_directory(root, state)
    assert (root / "local_ai" / "models" / "Qwen.gguf").is_file()
