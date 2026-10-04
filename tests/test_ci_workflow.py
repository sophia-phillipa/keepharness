"""CI supply-chain guards (SEC-RC-15): pinned actions and tools, OSV and gitleaks jobs."""

import os
import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def workflow():
    return (ROOT / ".github/workflows/ci.yml").read_text()


def test_ci_actions_are_pinned_by_commit_sha():
    uses = re.findall(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$", workflow(), re.M)
    assert uses
    for reference, comment in uses:
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", reference), reference
        assert re.fullmatch(r"\s*# v\d+(\.\d+)+", comment), f"{reference} needs a # vX.Y.Z comment"


def test_ci_scans_dependencies_and_history_and_pins_its_tools():
    text = workflow()
    for job in ("dependency-scan:", "secret-scan:"):
        assert job in text
    assert "fetch-depth: 0" in text
    for lock in (
        "requirements.txt",
        "agent_service/bridge-requirements.txt",
        "desktop/package-lock.json",
    ):
        assert f"-L {lock}" in text
    assert re.search(r"pip install ruff==\d+\.\d+\.\d+\n", text)
    assert re.search(r"prettier@\d+\.\d+\.\d+ ", text)
    assert re.search(r"playwright@\d+\.\d+\.\d+\n", text)
    dependabot = (ROOT / ".github/dependabot.yml").read_text()
    assert "package-ecosystem: github-actions" in dependabot and "interval: weekly" in dependabot


def test_every_osv_ignore_has_a_reason_and_an_expiry():
    config = tomllib.loads((ROOT / "osv-scanner.toml").read_text())
    for ignore in config.get("IgnoredVulns", []):
        assert ignore.get("reason", "").strip() and ignore.get("ignoreUntil"), ignore


def jobs():
    return dict(
        re.findall(
            r"^  ([\w-]+):\n(.*?)(?=^  [\w-]+:|\Z)", workflow().split("jobs:\n", 1)[1], re.M | re.S
        )
    )


def test_ci_runtime_installs_use_the_hashes_and_do_not_resolve_again():
    for name in ("unit", "ui", "package"):
        job = jobs()[name]
        locked = "pip install --require-hashes -r requirements.txt"
        assert locked in job
        if name == "package":
            install = "pip install --no-deps dist/*.whl"
            assert "python -m build --no-isolation" in job
        else:
            install = "pip install --no-deps --no-build-isolation --editable ."
        assert job.index(locked) < job.index(install)
    unit = jobs()["unit"]
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
        "optional-dependencies"
    ]["test"]
    for requirement in extras:
        assert f"'{requirement}'" in unit
    for name in ("unit", "package"):
        assert 'pip install --constraint "$RUNNER_TEMP/runtime-constraints.txt"' in jobs()[name]


def test_ci_tool_constraints_preserve_pins_and_markers_without_enabling_hash_mode(tmp_path):
    lock = tmp_path / "requirements.txt"
    lock.write_text(
        "# Generated lock\n\n"
        "anyio==4.15.1 \\\n"
        "    --hash=sha256:abc \\\n"
        "    --hash=sha256:def\n"
        "    # via httpx\n"
        'typing-extensions==4.15.0 ; python_version < "3.13" \\\n'
        "    --hash=sha256:ghi\n"
    )
    for name in ("unit", "package"):
        job = jobs()[name]
        commands = [line.strip() for line in job.splitlines() if "sed " in line]
        assert len(commands) == 1
        subprocess.run(
            ["sh", "-c", commands[0]],
            cwd=tmp_path,
            env={**os.environ, "RUNNER_TEMP": str(tmp_path)},
            check=True,
        )
        assert (tmp_path / "runtime-constraints.txt").read_text().splitlines() == [
            "anyio==4.15.1",
            'typing-extensions==4.15.0 ; python_version < "3.13"',
        ]
        assert job.index(commands[0]) < job.index("pip install --constraint")


def test_ci_regenerates_both_locks_and_rejects_drift():
    job = jobs()["lock-freshness"]
    commands = (
        "uv pip compile pyproject.toml scripts/build-requirements.in --universal --python-version 3.11 --generate-hashes -o requirements.txt",
        "uv pip compile scripts/bridge-requirements.in --universal --python-version 3.10 --generate-hashes -o agent_service/bridge-requirements.txt",
    )
    check = "git diff --exit-code -- requirements.txt agent_service/bridge-requirements.txt"
    assert check in job
    for command in commands:
        assert command in job
        assert job.index(command) < job.index(check)


def test_ci_has_read_only_permissions_and_does_not_persist_checkout_credentials():
    assert "\npermissions:\n  contents: read\n" in workflow()
    checkouts = re.findall(r"uses: actions/checkout@[^\n]+\n(.*?)(?=      -|\Z)", workflow(), re.S)
    assert checkouts
    assert all("persist-credentials: false" in checkout for checkout in checkouts)


def test_weekly_schedule_runs_only_the_dependency_scan():
    assert re.search(r"schedule:\n\s+- cron: ['\"]\d+ \d+ \* \* [0-6]['\"]", workflow())
    for name, job in jobs().items():
        if name == "dependency-scan":
            assert "github.event_name != 'schedule'" not in job
        else:
            assert "if: github.event_name != 'schedule'" in job
