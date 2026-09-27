"""The tool sandbox exposes the /etc pieces the dynamic loader needs, and nothing else."""

import subprocess
from pathlib import Path

import pytest

from agent_service import tools

LOADER_PATHS = ("/etc/ld.so.cache", "/etc/alternatives")


def ro_binds(argv):
    return {argv[i + 1] for i, arg in enumerate(argv) if arg == "--ro-bind"}


@pytest.mark.parametrize("present", [set(), {"/etc/ld.so.cache"}, set(LOADER_PATHS)])
def test_sandbox_binds_loader_paths_only_when_they_exist(monkeypatch, tmp_path, present):
    real_exists = Path.exists
    monkeypatch.setattr(
        tools.Path,
        "exists",
        lambda self: str(self) in present if str(self) in LOADER_PATHS else real_exists(self),
    )
    binds = ro_binds(tools.sandbox(tmp_path, ["true"]))
    for path in LOADER_PATHS:
        assert (path in binds) == (path in present), path
    # Never the whole of /etc: it holds credentials and machine secrets.
    assert "/etc" not in binds


def alternatives_libraries():
    """Shared libraries under /usr that resolve through /etc/alternatives."""
    found = []
    for root in ("/usr/lib", "/usr/lib64", "/usr/lib/x86_64-linux-gnu"):
        for link in sorted(Path(root).glob("*.so*")) if Path(root).is_dir() else []:
            if link.is_symlink() and str(link.readlink()).startswith("/etc/alternatives/"):
                if link.resolve().is_relative_to("/usr") and link.exists():
                    found.append(str(link))
    return found


@pytest.mark.host_tools("bwrap", "prlimit")
def test_alternatives_libraries_resolve_inside_the_sandbox(tmp_path):
    # Ubuntu's ffmpeg loads libblas.so.3 through /etc/alternatives; a dangling link
    # there makes it exit 127 and every image upload fail as image_validation_unavailable.
    libraries = alternatives_libraries()
    if not libraries:
        pytest.skip("no /usr library resolves through /etc/alternatives here")
    probe = subprocess.run(
        tools.sandbox(tmp_path, ["/usr/bin/ls", "-L", *libraries]),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr
