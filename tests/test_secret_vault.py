import pytest

from agent_service.errors import APIError
from agent_service.secret_vault import SecretVault


def test_private_atomic_store_and_write_only_status(tmp_path):
    store = SecretVault(tmp_path / "vault.json")
    store.set("demo", {"token": "fake-private-value"})
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.get("demo") == {"token": "fake-private-value"}
    assert store.status() == [{"binding": "demo", "fields": ["token"]}]
    assert "fake-private-value" not in str(store.status())
    store.delete("demo")
    assert store.status() == []


def test_store_refuses_symlink_and_public_file(tmp_path):
    target = tmp_path / "target"
    target.write_text("{}")
    store = SecretVault(tmp_path / "vault")
    store.path.symlink_to(target)
    with pytest.raises(APIError):
        store.set("demo", {"token": "fake-private-value"})
    store.path.unlink()
    store.path.write_text("{}")
    store.path.chmod(0o644)
    with pytest.raises(APIError):
        store.get("demo")


@pytest.mark.parametrize("value", [{}, {"token": ""}, {"token": "x\n"}, {"token": 1}])
def test_invalid_values_fail_without_writing(tmp_path, value):
    store = SecretVault(tmp_path / "vault")
    with pytest.raises(APIError):
        store.set("demo", value)
    assert not store.path.exists()
