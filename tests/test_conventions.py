"""P5 §11 (CI guards) / P9: exercise ``scripts/check_conventions.py`` directly.

The script is standard-library only and already wired into CI (``ci.yml``'s
``conventions`` job): file/dir name lint, the Portuguese stop-word scan (now excluding
the whole ``README.pt-BR.md`` file rather than a marker-delimited section of a single
bilingual ``README.md``), and a README.md/README.pt-BR.md heading-structure parity check.
"""

import subprocess
import sys
from pathlib import Path

from scripts import check_conventions as conventions

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def test_this_repository_passes_its_own_convention_check_today():
    """Mirrors the CI ``conventions`` job (``ci.yml``): a live guard against regressions."""
    files = conventions.list_tracked_files()
    name_errors, _ = conventions.check_names(files, verbose=False)
    assert name_errors == 0

    word_hits = conventions.check_words(files)
    assert word_hits == 0


def test_main_as_a_subprocess_matches_the_ci_invocation():
    """``ci.yml`` runs exactly ``python scripts/check_conventions.py``; exit code carries the verdict."""
    result = subprocess.run(
        [sys.executable, "scripts/check_conventions.py"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 name errors" in result.stdout
    assert "0 portuguese hits" in result.stdout


def test_python_file_names_must_be_snake_case():
    errors, _ = conventions.check_names(["control/BadName.py"], verbose=False)
    assert errors == 1


def test_kebab_case_required_for_shell_js_css_html_extensions():
    errors, _ = conventions.check_names(["scripts/BadName.sh"], verbose=False)
    assert errors == 1
    errors, _ = conventions.check_names(["agent_service/Weird_Name.js"], verbose=False)
    assert errors == 1
    errors, _ = conventions.check_names(["scripts/good-name.sh"], verbose=False)
    assert errors == 0


def test_grandfathered_basenames_are_silently_allowed_at_any_depth():
    errors, warnings = conventions.check_names(
        ["deeply/nested/folder/AGENTS.md", "another/place/README.md"], verbose=False
    )
    assert (errors, warnings) == (0, 0)


def test_directory_names_must_be_snake_or_kebab_case():
    errors, _ = conventions.check_names(["Weird Dir/file.py"], verbose=False)
    assert errors >= 1


def test_docs_markdown_with_an_uppercase_stem_is_grandfathered():
    errors, warnings = conventions.check_names(["docs/ACCESS-MENU-20260919.md"], verbose=False)
    assert (errors, warnings) == (0, 0)


def test_pt_word_scan_flags_a_line_with_a_stop_word(tmp_path, monkeypatch):
    monkeypatch.setattr(conventions, "REPO_ROOT", tmp_path)
    target = tmp_path / "notes.py"
    target.write_text("# comentario: aguardando o usuário confirmar\n")  # conventions: allow-pt
    hits = conventions.check_words(["notes.py"])
    assert hits >= 1


def test_pt_word_scan_is_silent_on_english_text(tmp_path, monkeypatch):
    monkeypatch.setattr(conventions, "REPO_ROOT", tmp_path)
    target = tmp_path / "notes.py"
    target.write_text("# waiting for the user to confirm\n")
    hits = conventions.check_words(["notes.py"])
    assert hits == 0


def test_sem_projeto_sentinel_does_not_trigger_the_projeto_stop_word(tmp_path, monkeypatch):
    monkeypatch.setattr(conventions, "REPO_ROOT", tmp_path)
    target = tmp_path / "notes.py"
    target.write_text('scope_id = "sem-projeto"\n')
    hits = conventions.check_words(["notes.py"])
    assert hits == 0


def test_allow_pt_marker_suppresses_a_flagged_line(tmp_path, monkeypatch):
    monkeypatch.setattr(conventions, "REPO_ROOT", tmp_path)
    target = tmp_path / "notes.py"
    target.write_text("# nao traduzido ainda  # conventions: allow-pt\n")
    hits = conventions.check_words(["notes.py"])
    assert hits == 0


def test_readme_pt_br_file_is_excluded_from_the_word_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(conventions, "REPO_ROOT", tmp_path)
    readme_pt = tmp_path / "README.pt-BR.md"
    readme_pt.write_text("# Título\n\nConteúdo não sinalizado.\n")  # conventions: allow-pt
    hits = conventions.check_words(["README.pt-BR.md"])
    assert hits == 0


def test_readme_english_file_is_still_scanned(tmp_path, monkeypatch):
    monkeypatch.setattr(conventions, "REPO_ROOT", tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text("# Title\n\nSee the attached arquivo, please.\n")  # conventions: allow-pt
    hits = conventions.check_words(["README.md"])
    assert hits >= 1


def test_readme_pt_br_basename_is_a_silently_allowed_file_name():
    errors, warnings = conventions.check_names(["README.pt-BR.md"], verbose=False)
    assert (errors, warnings) == (0, 0)


def test_readme_heading_parity_passes_for_matching_structures(tmp_path, monkeypatch):
    monkeypatch.setattr(conventions, "REPO_ROOT", tmp_path)
    (tmp_path / "README.md").write_text("# Title\n\n## First\n\n### Sub\n\n## Second\n")
    pt_text = "# Título\n\n## Primeiro\n\n### Sub\n\n## Segundo\n"  # conventions: allow-pt
    (tmp_path / "README.pt-BR.md").write_text(pt_text)
    assert conventions.check_readme_parity(verbose=False) == 0


def test_readme_heading_parity_fails_for_a_missing_heading(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(conventions, "REPO_ROOT", tmp_path)
    (tmp_path / "README.md").write_text("# Title\n\n## First\n\n## Second\n")
    (tmp_path / "README.pt-BR.md").write_text("# Título\n\n## Primeiro\n")  # conventions: allow-pt
    assert conventions.check_readme_parity(verbose=False) == 1
    assert "heading structure differs" in capsys.readouterr().out


def test_readme_heading_parity_ignores_headings_inside_fenced_code_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(conventions, "REPO_ROOT", tmp_path)
    body = "# Title\n\n## Section\n\n```md\n# not a real heading\n```\n"
    (tmp_path / "README.md").write_text(body)
    pt_body = body.replace("Title", "Cabecalho").replace("Section", "Bloco")
    (tmp_path / "README.pt-BR.md").write_text(pt_body)
    assert conventions.check_readme_parity(verbose=False) == 0


def test_readme_heading_parity_is_a_noop_when_either_file_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(conventions, "REPO_ROOT", tmp_path)
    (tmp_path / "README.md").write_text("# Title\n")
    assert conventions.check_readme_parity(verbose=False) == 0


def test_personas_shared_harness_file_is_allowed_despite_its_leading_underscore():
    """`tests/personas/_harness.cjs` is a shared fixture module, not a spec (see AGENTS.md)."""
    errors, warnings = conventions.check_names(["tests/personas/_harness.cjs"], verbose=False)
    assert (errors, warnings) == (0, 0)


def test_personas_spec_file_names_already_satisfy_the_kebab_case_rule():
    """`tests/personas/<id>-<slug>.spec.cjs` needs no allowlist entry: it is already kebab-case."""
    errors, _ = conventions.check_names(
        ["tests/personas/h00-helper-smoke.spec.cjs", "tests/personas/c01-example.spec.cjs"],
        verbose=False,
    )
    assert errors == 0


def test_docs_markdown_with_a_lowercase_stem_is_not_grandfathered():
    # Only an uppercase-letter stem is grandfathered (matching the real dated docs);
    # a lowercase-stem name with an invalid character (a space) is still flagged.
    errors, _ = conventions.check_names(["docs/oddly named.md"], verbose=False)
    assert errors == 1


def test_json_output_summary_line_is_machine_parseable_enough_for_ci():
    result = subprocess.run(
        [sys.executable, "scripts/check_conventions.py"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    summary = result.stdout.strip().splitlines()[-1]
    assert summary.startswith("conventions: ")
    counts = [int(part) for part in summary.replace("conventions: ", "").split() if part.isdigit()]
    assert len(counts) == 3
