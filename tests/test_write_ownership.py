import pytest

from agent_service.write_ownership import WriteOwnership


def test_overlapping_roots_and_work_items_block_other_owners(tmp_path):
    locks = WriteOwnership()
    assert locks.acquire("one", "p", "ITEM1", [tmp_path / "root"]) is None
    assert locks.acquire("two", "q", None, [tmp_path / "root" / "child"]) == "writable_root"
    assert locks.acquire("three", "p", "ITEM1", [tmp_path / "other"]) == "work_item"
    assert locks.acquire("four", "q", "ITEM1", [tmp_path / "other"]) is None
    locks.release("one")
    assert locks.acquire("two", "q", None, [tmp_path / "root" / "child"]) is None


def test_symlink_alias_and_siblings(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "alias").symlink_to(tmp_path / "real")
    locks = WriteOwnership()
    assert locks.acquire("one", "p", None, [tmp_path / "real"]) is None
    assert locks.acquire("two", "q", None, [tmp_path / "alias"]) == "writable_root"
    assert locks.acquire("three", "q", None, [tmp_path / "sibling"]) is None


def test_circular_symlink_is_rejected(tmp_path):
    loop = tmp_path / "loop"
    loop.symlink_to(loop.name)
    with pytest.raises((OSError, RuntimeError)):
        WriteOwnership().acquire("one", "p", None, [loop])


def test_catalog_cwd_shared_between_distinct_projects_is_locked(tmp_path):
    import json
    from types import SimpleNamespace

    from agent_service.services.queue_worker import ownership_roots

    catalog = tmp_path / "catalog"
    catalog.mkdir()
    (catalog / "harness.catalog.json").write_text(json.dumps({"version": 1, "cwd": "."}))
    instance = SimpleNamespace(
        config={
            "state_dir": str(tmp_path / "state"),
            "projects": {
                "p": {"root": str(tmp_path / "p"), "catalogs": ["c"]},
                "q": {"root": str(tmp_path / "q"), "catalogs": ["c"]},
            },
            "catalogs": [{"id": "c", "root": str(catalog), "trusted": True}],
        }
    )
    locks = WriteOwnership()
    assert (
        locks.acquire(
            "one", "p", None, ownership_roots(instance, {"project": "p", "payload": "{}"})
        )
        is None
    )
    assert (
        locks.acquire(
            "two", "q", None, ownership_roots(instance, {"project": "q", "payload": "{}"})
        )
        == "writable_root"
    )
