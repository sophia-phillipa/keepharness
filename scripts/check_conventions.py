#!/usr/bin/env python3
"""Enforce the naming and language conventions from dossier/naming-model.md.

Standard library only. Checks (1) file/directory name lint (snake_case Python, kebab-case for
other text extensions, with a grandfathered allowlist), (2) a Portuguese stop-word scan over
text files, excluding README.pt-BR.md and a handful of documented sentinels, (3) no `guest`
identifier in code and tests, and (4) heading-structure parity between README.md and
README.pt-BR.md.

Usage: python scripts/check_conventions.py [--verbose] [--only names|words|guest|readme]
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SKIP_DIRS = set(".git .venv node_modules graphify-out local-ai local_ai state dist build".split())  # noqa: SIM905

PY_NAME_RE = re.compile(r"^[a-z0-9_]+\.py$")
KEBAB_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*\.[a-z0-9.]+$")
DIR_NAME_RE = re.compile(r"^[a-z0-9_-]+$")

KEBAB_EXTENSIONS = set(".sh .js .cjs .css .html .md .json .svg .toml .yml .yaml .txt".split())  # noqa: SIM905
TEXT_EXTENSIONS = set(".py .js .cjs .css .html .md .sh .toml .yml .yaml .json .txt".split())  # noqa: SIM905

# Bare filenames matched against the basename anywhere in the tree.
SILENT_NAME_BASENAMES = set(
    "README.md README.pt-BR.md AGENTS.md CHANGELOG.md CLAUDE.md LICENSE MANIFEST.in".split()  # noqa: SIM905
)

# Paths (or fnmatch globs, matched against the full relative path) that never count as errors.
SILENT_NAME_ALLOWLIST = set(
    "docs/*-2026*.md dossier/UC-*.md dossier/UX-*.md harness_ui/assets/*LICENSE* agent_service/vendor agent_service/vendor/** .github .github/** .agents .agents/** profiles/*.json adapters/*/specs adapters/*/specs/** tests/personas/_harness.cjs".split()  # noqa: SIM905
)

# Known naming-model violations already tracked in the migration table: counted as warnings only.
# All entries here have been migrated (see dossier/naming-model.md); none remain.
WARNING_NAME_ALLOWLIST: set[str] = set()

ALLOW_FILE = REPO_ROOT / ".conventions-allow"

# Unambiguous Portuguese stop words. Deliberately excludes ambiguous tokens (local, manual, com,
# para, sem, data, nova, novo) which collide with English/proper-name usage in this repo.
PT_WORDS = "não nao você voce também tambem então entao arquivo arquivos pasta pastas projeto projetos conversa conversas mensagem mensagens resposta respostas pergunta rascunho salvar excluir fechar abrir remover adicionar enviar buscar carregando aguardando executando recebendo verificando nenhum nenhuma erro falha servidor provedor provedores usuário usuario configuração configuracao permissão permissao sessão sessao título titulo inválido invalido disponível indisponível obrigatório opcional padrão exemplo ajuda voltar cancelar renovar verificar atualizar iniciar parar reiniciar continuar tentar novamente escolha escolher informe clique digite".split()  # noqa: SIM905
PT_WORD_RE = re.compile(r"\b(" + "|".join(PT_WORDS) + r")\b", re.IGNORECASE)

SENTINEL_EXCLUDE = ("sem-projeto",)
ALLOW_LINE_MARKER = "conventions: allow-pt"
SELF_EXCLUDED_FILES = {
    "dossier/naming-model.md",
    "scripts/check_conventions.py",
    "README.pt-BR.md",
}

# Code and tests carry no `guest` identifier or run class (D-040 items 4 and 8): any spelling counts
# (guest, Guest, GUEST_LOGIN, tailnet-guest, guests). dossier/ and docs/ are history, not scanned.
GUEST_RE = re.compile("guest", re.IGNORECASE)
GUEST_EXTENSIONS = set(".py .js .cjs .html .css .sh".split())  # noqa: SIM905
GUEST_SKIPPED_PREFIXES = ("dossier/", "docs/")
GUEST_ALLOWED_FILES = {
    "scripts/check_conventions.py": "defines the pattern",
    "tests/test_conventions.py": "plants the word to test the scan",
}

HEADING_RE = re.compile(r"^(#{1,6})\s+\S")
README_EN = "README.md"
README_PT = "README.pt-BR.md"


def list_tracked_files() -> list[str]:
    try:
        cmd = ["git", "ls-files", "--cached", "--others", "--exclude-standard"]
        out = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, check=True)
        # --cached --others covers tracked and new/untracked files, honoring .gitignore. A path
        # can still be listed but gone from disk (deleted, not yet staged); skip those.
        return [line for line in out.stdout.splitlines() if line and (REPO_ROOT / line).is_file()]
    except (OSError, subprocess.CalledProcessError):
        files = []
        for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            rel_dir = Path(dirpath).relative_to(REPO_ROOT)
            for name in filenames:
                rel = rel_dir / name if str(rel_dir) != "." else Path(name)
                files.append(str(rel))
        return files


def load_allow_globs() -> set[str]:
    if not ALLOW_FILE.exists():
        return set()
    lines = ALLOW_FILE.read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.strip().startswith("#")}


def matches_any(path: str, globs: set[str]) -> bool:
    return any(fnmatch.fnmatch(path, glob) for glob in globs)


def is_silently_allowed(rel_path: str, extra_allow: set[str]) -> bool:
    p = Path(rel_path)
    if p.name in SILENT_NAME_BASENAMES:
        return True
    if matches_any(rel_path, SILENT_NAME_ALLOWLIST):
        return True
    # docs/*.md whose file name contains an uppercase letter is grandfathered.
    if p.parent.as_posix() == "docs" and p.suffix == ".md" and any(c.isupper() for c in p.stem):
        return True
    return bool(matches_any(rel_path, extra_allow))


def _check_file_name(rel_path: str, extra_allow: set[str], verbose: bool) -> tuple[int, int]:
    p = Path(rel_path)
    if is_silently_allowed(rel_path, extra_allow):
        return 0, 0
    problem = None
    if p.suffix == ".py" and not PY_NAME_RE.match(p.name):
        problem = f"{rel_path}: Python file name must be snake_case (got '{p.name}')"
    elif p.suffix in KEBAB_EXTENSIONS and not KEBAB_NAME_RE.match(p.name):
        problem = f"{rel_path}: file name must be lowercase kebab-case (got '{p.name}')"
    if problem is None:
        return 0, 0
    if matches_any(rel_path, WARNING_NAME_ALLOWLIST):
        if verbose:
            print(f"WARNING {problem}")
        return 0, 1
    print(f"ERROR {problem}")
    return 1, 0


def _check_directories(
    dirs_seen: set[str], extra_allow: set[str], verbose: bool
) -> tuple[int, int]:
    errors = warnings = 0
    reported: set[str] = set()
    for rel_dir in sorted(dirs_seen):
        parts = Path(rel_dir).parts
        for depth, segment in enumerate(parts, start=1):
            if DIR_NAME_RE.match(segment):
                continue
            partial = str(Path(*parts[:depth]))
            if partial in reported:
                break
            reported.add(partial)
            if is_silently_allowed(partial, extra_allow):
                break
            message = f"{partial}: directory name must be snake_case or kebab-case"
            if matches_any(partial, WARNING_NAME_ALLOWLIST):
                warnings += 1
                if verbose:
                    print(f"WARNING {message}")
            else:
                errors += 1
                print(f"ERROR {message}")
            break
    return errors, warnings


def check_names(files: list[str], verbose: bool) -> tuple[int, int]:
    errors = warnings = 0
    extra_allow = load_allow_globs()
    dirs_seen: set[str] = set()
    for rel_path in files:
        for parent in Path(rel_path).parents:
            if str(parent) not in (".", ""):
                dirs_seen.add(str(parent))
        file_errors, file_warnings = _check_file_name(rel_path, extra_allow, verbose)
        errors += file_errors
        warnings += file_warnings
    dir_errors, dir_warnings = _check_directories(dirs_seen, extra_allow, verbose)
    return errors + dir_errors, warnings + dir_warnings


def _scan_file_for_pt_words(rel_path: str) -> int:
    full_path = REPO_ROOT / rel_path
    try:
        text = full_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0

    hits = 0
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if ALLOW_LINE_MARKER in line:
            continue
        search_line = line
        for sentinel in SENTINEL_EXCLUDE:
            search_line = search_line.replace(sentinel, "")
        for match in PT_WORD_RE.finditer(search_line):
            hits += 1
            print(f"{rel_path}:{idx + 1}: {match.group(0)}")
    return hits


def check_words(files: list[str]) -> int:
    hits = 0
    for rel_path in files:
        if rel_path in SELF_EXCLUDED_FILES:
            continue
        p = Path(rel_path)
        if p.suffix not in TEXT_EXTENSIONS:
            continue
        if "min." in p.name or rel_path == "harness_ui/assets/file-icons-data.js":
            continue
        hits += _scan_file_for_pt_words(rel_path)
    return hits


def check_guests(files: list[str]) -> int:
    hits = 0
    for rel_path in files:
        if (
            Path(rel_path).suffix not in GUEST_EXTENSIONS
            or rel_path.startswith(GUEST_SKIPPED_PREFIXES)
            or rel_path in GUEST_ALLOWED_FILES
        ):
            continue
        try:
            lines = (REPO_ROOT / rel_path).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(lines, 1):
            if GUEST_RE.search(line):
                hits += 1
                print(f"{rel_path}:{number}: guest")
    return hits


def heading_levels(rel_path: str) -> list[int]:
    """Return the heading depth of every Markdown heading, in document order."""
    full_path = REPO_ROOT / rel_path
    try:
        text = full_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    levels = []
    in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = HEADING_RE.match(line)
        if match:
            levels.append(len(match.group(1)))
    return levels


def check_readme_parity(verbose: bool) -> int:
    """README.md and README.pt-BR.md must have the same heading structure (levels, in order)."""
    en_path, pt_path = REPO_ROOT / README_EN, REPO_ROOT / README_PT
    if not en_path.is_file() or not pt_path.is_file():
        return 0
    en_levels, pt_levels = heading_levels(README_EN), heading_levels(README_PT)
    if en_levels == pt_levels:
        if verbose:
            print(
                f"OK {README_EN} and {README_PT} have matching heading structure "
                f"({len(en_levels)} headings)"
            )
        return 0
    print(
        f"ERROR {README_EN} and {README_PT} heading structure differs: "
        f"{len(en_levels)} headings {en_levels} vs {len(pt_levels)} headings {pt_levels}"
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--only", choices=("names", "words", "guest", "readme"))
    args = parser.parse_args()

    files = list_tracked_files()

    errors = warnings = hits = guest_hits = readme_errors = 0
    if args.only in (None, "names"):
        errors, warnings = check_names(files, args.verbose)
    if args.only in (None, "words"):
        hits = check_words(files)
    if args.only in (None, "guest"):
        guest_hits = check_guests(files)
    if args.only in (None, "readme"):
        readme_errors = check_readme_parity(args.verbose)
    errors += readme_errors

    print(
        f"conventions: {errors} name errors, {warnings} name warnings, {hits} portuguese hits, "
        f"{guest_hits} guest hits"
    )
    return 0 if errors == 0 and hits == 0 and guest_hits == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
