"""CI supply-chain guards (SEC-RC-15): pinned actions and tools, OSV and gitleaks jobs."""

import re
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
