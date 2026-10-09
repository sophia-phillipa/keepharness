"""Release check (#46, design section 2.6): nothing reads the 0.15 provider homes any more.

The 0.15 folders under ``<state>/providers/home`` stay on disk for rollback and are ignored.
Only DeepSeek's own home, ``<state>/providers/deepseek``, is still used.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_FOLDERS = ("control", "agent_service", "adapters")
SUFFIXES = {".py", ".js", ".html", ".css", ".json", ".md", ".toml", ".cjs"}
# ``providers/home``, ``"providers" / "home"`` and ``providers", "home`` — not ``providers/deepseek``.
RETIRED = re.compile(r"""providers["']?\s*[/,]\s*["']?home\b|homes_root|provider_homes""")


def test_no_source_file_references_the_015_provider_homes():
    hits = [
        f"{path.relative_to(ROOT)}:{number}: {line.strip()[:100]}"
        for folder in SOURCE_FOLDERS
        for path in sorted((ROOT / folder).rglob("*"))
        if path.is_file() and path.suffix in SUFFIXES and "node_modules" not in path.parts
        for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1)
        if RETIRED.search(line)
    ]
    assert not hits, "\n".join(hits)
