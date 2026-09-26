import json

from control import start_local


def profile(root):
    runtime = root / "local-ai/runtime/llama-b11003"
    runtime.mkdir(parents=True)
    binary = runtime / "llama-server"
    binary.touch()
    models = root / "local-ai/models"
    models.mkdir(parents=True)
    (models / "Qwen.gguf").touch()
    (models / "Qwen-mmproj.gguf").touch()
    return {
        "description": "fixture",
        "binary": "local-ai/runtime/llama-b11003/llama-server",
        "model_file": "local-ai/models/Qwen.gguf",
        "mmproj_file": "local-ai/models/Qwen-mmproj.gguf",
        "flags": ["no-mmproj-offload", "kv-offload"],
        "performance": {"ctx-size": "98304", "threads": "8"},
    }


def test_check_resolves_paths_and_does_not_create_key(tmp_path, capsys):
    source = tmp_path / "profile.json"
    source.write_text(json.dumps(profile(tmp_path)))
    key = "local-ai/config/api-key"
    start_local.main(
        [
            "--profile",
            str(source),
            "--root",
            str(tmp_path),
            "--port",
            "8091",
            "--alias",
            "qwen-local",
            "--key-file",
            key,
            "--check",
        ]
    )
    command = capsys.readouterr().out
    assert str(tmp_path / "local-ai/models/Qwen.gguf") in command
    assert "--no-mmproj-offload" in command and "--kv-offload" in command and "--jinja" in command
    assert not (tmp_path / key).exists()


def test_exec_creates_private_key_once_and_uses_fixed_environment(tmp_path, monkeypatch):
    source = tmp_path / "profile.json"
    source.write_text(json.dumps(profile(tmp_path)))
    called = {}
    monkeypatch.setattr(
        start_local.os, "execv", lambda binary, args: called.update(binary=binary, args=args)
    )
    key = tmp_path / "local-ai/config/api-key"
    start_local.main(["--profile", str(source), "--root", str(tmp_path), "--key-file", str(key)])
    assert key.stat().st_mode & 0o777 == 0o600
    previous = key.read_text()
    start_local.main(["--profile", str(source), "--root", str(tmp_path), "--key-file", str(key)])
    assert key.read_text() == previous
    assert called["args"][called["args"].index("--api-key-file") + 1] == str(key)
    assert start_local.os.environ["MANGOHUD"] == "0"
