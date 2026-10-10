"""Normalized per-turn file edits (issue #57, D-053): normalize, caps, and capture per provider.

The capture tests run the real adapters against the fake CLIs in ``tests/fixtures``. The fakes emit
edit items only when the prompt contains ``FAKE-EDITS``; plain prompts emit none.
"""

import asyncio
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from adapters import run_native
from agent_service.turn_edits import (
    MAX_DIFF_BYTES,
    MAX_JOB_DIFF_BYTES,
    MAX_RECORDS,
    Budget,
    group_by_path,
    normalize_claude,
    normalize_codex,
)

ROOT = Path("/work/project")
FIXTURES = Path(__file__).parent / "fixtures"
FAKE_CODEX = FIXTURES / "fake-codex" / "codex"
FAKE_CLAUDE = FIXTURES / "fake-claude" / "claude"
MARKER = "FAKE-EDITS"


def codex(path="src/a.py", kind="update", diff="+x\n", **kind_extra):
    return {"path": path, "kind": {"type": kind, **kind_extra}, "diff": diff}


def shapes(records):
    return [(r.get("path"), r["op"], r["diff_state"]) for r in records]


# --- normalize_codex ---------------------------------------------------------------------


def test_codex_kinds_map_to_op_and_diff_state():
    add = normalize_codex(codex("src/new.py", "add", "+print(1)\n"), ROOT)
    update = normalize_codex(codex("src/app.py", "update", "@@ -1 +1 @@\n-a\n+b\n"), ROOT)
    delete = normalize_codex(codex("src/gone.py", "delete", "-old\n"), ROOT)
    assert add == {
        "path": "src/new.py",
        "op": "created",
        "source": "codex",
        "tool": "fileChange",
        "diff": "+print(1)\n",
        "diff_state": "diff",
    }
    assert (update["path"], update["op"], update["diff_state"]) == (
        "src/app.py",
        "modified",
        "diff",
    )
    assert (delete["op"], delete["diff"], delete["diff_state"]) == ("deleted", "-old\n", "diff")
    assert "moved_from" not in update


def test_codex_update_with_move_path_records_new_path_and_old_path():
    moved = normalize_codex(codex("src/old.py", "update", "+x\n", move_path="src/moved.py"), ROOT)
    assert moved["path"] == "src/moved.py"
    assert moved["moved_from"] == "src/old.py"
    assert (moved["op"], moved["diff_state"]) == ("modified", "diff")


def test_codex_delete_with_empty_diff_is_unavailable():
    record = normalize_codex(codex("src/gone.py", "delete", ""), ROOT)
    assert (record["op"], record["diff"], record["diff_state"]) == ("deleted", None, "unavailable")


def test_codex_add_with_empty_diff_keeps_diff_state():
    record = normalize_codex(codex("empty.txt", "add", ""), ROOT)
    assert (record["op"], record["diff"], record["diff_state"]) == ("created", "", "diff")


def test_codex_unknown_kind_and_malformed_change_are_unknown_not_errors():
    unknown = normalize_codex(codex("src/x.py", "rename", "+x"), ROOT)
    assert (unknown["op"], unknown["diff"], unknown["diff_state"]) == (
        "unknown",
        None,
        "unavailable",
    )
    for junk in (None, "x", [], {"kind": "add"}):
        record = normalize_codex(junk, ROOT)
        assert (record["path"], record["op"], record["diff_state"]) == (
            None,
            "unknown",
            "unavailable",
        )


def test_codex_non_string_diff_is_unavailable():
    record = normalize_codex({"path": "a.py", "kind": {"type": "add"}, "diff": 5}, ROOT)
    assert (record["diff"], record["diff_state"]) == (None, "unavailable")


# --- normalize_claude --------------------------------------------------------------------


def test_claude_edit_tools_map_to_op_and_partial_diff():
    edit = normalize_claude(
        "Edit",
        {"file_path": f"{ROOT}/src/app.py", "old_string": "a = 1\n", "new_string": "a = 2\n"},
        ROOT,
    )
    multi = normalize_claude(
        "MultiEdit",
        {
            "file_path": "src/util.py",
            "edits": [
                {"old_string": "x\n", "new_string": "y\n"},
                {"old_string": "p\n", "new_string": "q\n"},
            ],
        },
        ROOT,
    )
    write = normalize_claude("Write", {"file_path": "notes/todo.md", "content": "one\ntwo\n"}, ROOT)
    assert edit == [
        {
            "path": "src/app.py",
            "op": "modified",
            "source": "claude",
            "tool": "Edit",
            "diff": "-a = 1\n+a = 2",
            "diff_state": "partial",
        }
    ]
    assert shapes(multi) == [("src/util.py", "modified", "partial")]
    assert multi[0]["diff"] == "-x\n+y\n-p\n+q"
    assert shapes(write) == [("notes/todo.md", "unknown", "partial")]
    assert write[0]["diff"] == "+one\n+two"


def test_claude_notebook_edit_has_no_diff_and_other_tools_yield_nothing():
    notebook = normalize_claude(
        "NotebookEdit", {"notebook_path": "nb/a.ipynb", "new_source": "x"}, ROOT
    )
    assert shapes(notebook) == [("nb/a.ipynb", "modified", "unavailable")]
    assert notebook[0]["diff"] is None
    assert normalize_claude("Bash", {"command": "echo hi > out.txt"}, ROOT) == []
    assert normalize_claude("Read", {"file_path": "a.py"}, ROOT) == []


def test_claude_malformed_edit_input_is_unknown_and_unavailable():
    record = normalize_claude("Edit", "not a dict", ROOT)[0]
    assert (record["path"], record["op"], record["diff_state"]) == (None, "unknown", "unavailable")
    missing = normalize_claude("Edit", {"file_path": "a.py", "old_string": 1}, ROOT)[0]
    assert (missing["path"], missing["diff"], missing["diff_state"]) == (
        "a.py",
        None,
        "unavailable",
    )


# --- paths -------------------------------------------------------------------------------


def test_paths_are_project_relative_and_escapes_are_null():
    inside = normalize_codex(codex(f"{ROOT}/src/a.py"), ROOT)
    assert inside["path"] == "src/a.py"
    for escaped in (
        "/elsewhere/leak.txt",
        "../outside.txt",
        "src/../../x.txt",
        f"{ROOT}/../x",
        ROOT.as_posix(),
        "",
        None,
    ):
        record = normalize_codex(codex(escaped, "add", "secret body\n"), ROOT)
        assert record["path"] is None, escaped
        assert (record["diff"], record["diff_state"]) == (None, "unavailable"), escaped
        assert record["op"] == "created"


def test_outside_path_for_claude_drops_diff_but_keeps_tool_op():
    record = normalize_claude(
        "Edit", {"file_path": "/elsewhere/x.py", "old_string": "a", "new_string": "b"}, ROOT
    )[0]
    assert (record["path"], record["op"], record["diff"], record["diff_state"]) == (
        None,
        "modified",
        None,
        "unavailable",
    )


def test_paths_are_resolved_lexically_without_touching_the_disk():
    missing_root = Path("/definitely/not/here/project")
    record = normalize_codex(codex(f"{missing_root}/a.py"), missing_root)
    assert record["path"] == "a.py"


def make_symlinked_home(base):
    """A physical ``phys/proj`` and a ``logical`` symlink to ``phys`` (like /home -> /var/home)."""
    (base / "phys" / "proj").mkdir(parents=True)
    (base / "phys" / "other").mkdir()
    (base / "logical").symlink_to(base / "phys", target_is_directory=True)


def test_physical_report_maps_to_relative_when_project_is_stored_logical(tmp_path):
    base = tmp_path.resolve()
    make_symlinked_home(base)
    root = base / "logical" / "proj"
    record = normalize_codex(codex(f"{base}/phys/proj/a.txt", "add", "+x\n"), root)
    assert (record["path"], record["op"], record["diff_state"]) == ("a.txt", "created", "diff")


def test_physical_path_of_a_sibling_stays_outside_the_project(tmp_path):
    base = tmp_path.resolve()
    make_symlinked_home(base)
    root = base / "logical" / "proj"
    record = normalize_codex(codex(f"{base}/phys/other/x.txt", "add", "+x\n"), root)
    assert (record["path"], record["diff"], record["diff_state"]) == (None, None, "unavailable")


def test_logical_report_under_a_physical_stored_root_is_still_shown_outside(tmp_path):
    base = tmp_path.resolve()
    make_symlinked_home(base)
    root = base / "phys" / "proj"
    record = normalize_claude(
        "Write", {"file_path": f"{base}/logical/proj/a.txt", "content": "x\n"}, root
    )[0]
    assert (record["path"], record["diff"], record["diff_state"]) == (None, None, "unavailable")


def test_reported_paths_are_never_resolved_only_the_root_is(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "proj"
    root.mkdir()
    resolved = []
    real_realpath = os.path.realpath

    def spy(path, *args, **kwargs):
        resolved.append(os.fspath(path))
        return real_realpath(path, *args, **kwargs)

    monkeypatch.setattr(os.path, "realpath", spy)
    normalize_codex(codex(f"{root}/a.txt", "add", "+x\n"), root)
    assert resolved == [str(root)]


# --- diff text and caps ------------------------------------------------------------------


def test_binary_text_is_dropped():
    record = normalize_codex(codex("img.bin", "add", "PNG\x00\x01"), ROOT)
    assert (record["diff"], record["diff_state"]) == (None, "binary")


def test_diff_is_redacted_before_it_is_stored():
    record = normalize_codex(codex("cfg.txt", "add", "Authorization: Bearer abc123secret\n"), ROOT)
    assert record["diff_state"] == "diff"
    assert "abc123secret" not in record["diff"]


def test_per_diff_cap_is_64_kib_inclusive():
    fits = normalize_codex(codex("a.txt", "add", "a" * MAX_DIFF_BYTES), ROOT)
    too_big = normalize_codex(codex("b.txt", "add", "a" * (MAX_DIFF_BYTES + 1)), ROOT)
    assert MAX_DIFF_BYTES == 64 * 1024
    assert (fits["diff_state"], len(fits["diff"])) == ("diff", MAX_DIFF_BYTES)
    assert (too_big["diff"], too_big["diff_state"]) == (None, "oversized")


def test_job_budget_caps_stored_diff_text_at_1_mib():
    budget = Budget()
    events = []
    records = [
        normalize_codex(codex(f"f{i}.txt", "add", "a" * MAX_DIFF_BYTES), ROOT) for i in range(17)
    ]
    budget.emit(lambda kind, data: events.append(data), records)
    stored = [data["diff_state"] for data in events]
    assert MAX_JOB_DIFF_BYTES == 1024 * 1024
    assert stored == ["diff"] * 16 + ["oversized"]
    assert events[-1]["diff"] is None


def test_budget_writes_one_truncation_marker_after_200_records_and_stops():
    budget = Budget()
    events = []
    records = [
        normalize_codex(codex(f"f{i}.txt", "add", "+x\n"), ROOT) for i in range(MAX_RECORDS + 5)
    ]
    budget.emit(lambda kind, data: events.append((kind, data)), records)
    assert len(events) == MAX_RECORDS + 1
    assert {kind for kind, _ in events} == {"turn_edit"}
    assert events[-1] == ("turn_edit", {"truncated": True})


# --- group_by_path / file_op -------------------------------------------------------------


def test_file_deleted_then_recreated_in_one_turn_is_modified():
    edits = [
        normalize_codex(codex("src/a.py", "delete", "-old\n"), ROOT),
        normalize_codex(codex("src/a.py", "add", "+new\n"), ROOT),
    ]

    files = group_by_path(edits)

    assert [(f["path"], f["op"]) for f in files] == [("src/a.py", "modified")]


@pytest.mark.parametrize(
    ("kinds", "op"),
    [
        (("add", "update"), "created"),
        (("update", "delete"), "deleted"),
        (("add", "delete", "add"), "created"),
        (("delete", "add", "delete"), "deleted"),
    ],
)
def test_file_op_keeps_outcomes_other_than_delete_then_add(kinds, op):
    edits = [normalize_codex(codex("src/a.py", kind, "+x\n"), ROOT) for kind in kinds]

    assert group_by_path(edits)[0]["op"] == op


# --- capture per provider (fake CLIs) ----------------------------------------------------


def run_turn(provider, binary, prompt, project_dir, model, effort, extra=None):
    events = []

    async def approve(kind, payload):
        return {"approved": False, "behavior": "deny"}

    def record(kind, data):
        events.append((kind, data))

    result = asyncio.run(
        run_native(
            {"binary": str(binary), **(extra or {})},
            prompt,
            record,
            {"root": str(project_dir), "permissions": {"read": True, "write": True}},
            model,
            effort,
            project_dir.parent / "session",
            provider,
            approve,
        )
    )
    return result, [data for kind, data in events if kind == "turn_edit"]


def test_codex_completed_edits_are_captured_and_failed_ones_are_not(
    tmp_path, isolated_provider_homes
):
    project = tmp_path / "project"
    project.mkdir()
    result, edits = run_turn("codex", FAKE_CODEX, f"{MARKER} please", project, "gpt-6-astra", "low")
    assert result["finish_reason"] == "completed"
    assert [e.get("tool_id") for e in edits] == [
        "item-add",
        "item-update",
        "item-move",
        "item-delete",
        "item-binary",
        "item-big",
        "item-outside",
    ]
    assert shapes(edits) == [
        ("src/new.py", "created", "diff"),
        ("src/app.py", "modified", "diff"),
        ("src/moved.py", "modified", "diff"),
        ("src/gone.py", "deleted", "diff"),
        ("src/image.bin", "created", "binary"),
        ("src/big.txt", "created", "oversized"),
        (None, "created", "unavailable"),
    ]
    assert edits[2]["moved_from"] == "src/old.py"
    assert all(e["source"] == "codex" and e["tool"] == "fileChange" for e in edits)
    assert "outside secret" not in str(edits)


def test_codex_plain_prompt_captures_no_edits(tmp_path, isolated_provider_homes):
    project = tmp_path / "project"
    project.mkdir()
    _, edits = run_turn("codex", FAKE_CODEX, "just answer", project, "gpt-6-astra", "low")
    assert edits == []


def test_deepseek_turn_captures_the_same_codex_edits(tmp_path, isolated_provider_homes):
    project = tmp_path / "project"
    project.mkdir()
    key = tmp_path / "deepseek.key"
    key.write_text("fixture-only-api-key")
    home = tmp_path / "providers" / "deepseek"
    home.mkdir(mode=0o700, parents=True)
    (home / "config.toml").write_text(
        'cli_auth_credentials_store = "file"\n'
        "allow_login_shell = false\n"
        "[shell_environment_policy]\n"
        "ignore_default_excludes = false\n"
        'filters = { KEEPHARNESS_API_KEY = "exclude" }\n'
    )
    api = {"api_provider": {"url": "http://127.0.0.1", "key_file": str(key)}}
    result, edits = run_turn(
        "deepseek", FAKE_CODEX, f"{MARKER} please", project, "deepseek-flash", "high", api
    )
    assert result["backend"] == "deepseek"
    assert [e.get("tool_id") for e in edits] == [
        "item-add",
        "item-update",
        "item-move",
        "item-delete",
        "item-binary",
        "item-big",
        "item-outside",
    ]
    assert shapes(edits)[:2] == [
        ("src/new.py", "created", "diff"),
        ("src/app.py", "modified", "diff"),
    ]
    assert all(e["source"] == "codex" for e in edits)


def test_claude_successful_edits_are_captured_and_errors_and_shell_are_not(
    tmp_path, isolated_provider_homes
):
    project = tmp_path / "project"
    project.mkdir()
    with (
        patch("control.integrations.configurations", return_value={"claude": {}}),
        patch("control.integrations.inventory", return_value={"claude": []}),
    ):
        result, edits = run_turn(
            "claude", FAKE_CLAUDE, f"{MARKER} please", project, "sonnet", "configured"
        )
    assert result["answer"]
    assert [e.get("tool_id") for e in edits] == ["tool-edit", "tool-multi", "tool-write"]
    assert shapes(edits) == [
        ("src/app.py", "modified", "partial"),
        ("src/util.py", "modified", "partial"),
        ("notes/todo.md", "unknown", "partial"),
    ]
    assert edits[0]["diff"] == "-a = 1\n+a = 2"
    assert all(e["source"] == "claude" for e in edits)
