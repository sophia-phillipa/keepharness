import pytest
import tempfile
import unittest
from unittest.mock import patch

from control import install_runtime


class InstallRuntimeTest(unittest.TestCase):
    def test_runtime_path_is_project_local_and_pinned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = install_runtime.project_root(directory)
            self.assertEqual(
                install_runtime.runtime_dir(root), root / "local_ai/runtime/llama-b11003"
            )
        self.assertEqual(install_runtime.COMMIT, "7d6f5d02bb40fca0ab29e65fe4eb86eab6886f19")

    def test_backends_are_explicit(self):
        self.assertEqual(set(install_runtime.BACKEND_OPTIONS), {"cpu", "vulkan", "cuda"})
        self.assertIn("-DGGML_VULKAN=ON", install_runtime.BACKEND_OPTIONS["vulkan"])

    def test_invalid_root_fails_without_creating_a_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = install_runtime.project_root(directory) / "missing"
            self.assertEqual(install_runtime.main(["--root", str(missing)]), 1)
            self.assertFalse((missing / "local_ai").exists())

    def test_non_linux_is_rejected_before_build(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(install_runtime.platform, "system", return_value="Darwin"):
                with self.assertRaisesRegex(RuntimeError, "Linux"):
                    install_runtime.build(install_runtime.project_root(directory), "cpu", 1)


if __name__ == "__main__":
    unittest.main()


def test_install_py_never_writes_browser_entry_with_or_without_desktop(tmp_path):
    from control import install
    home = tmp_path
    version = home / '.local/opt/keepharness-0.15.1'
    version.mkdir(parents=True)
    (version / 'VERSION').write_text('0.15.1\n')
    (version / 'build-manifest.json').write_text('{"version":"0.15.1","product":"keepharness"}')
    (version / 'keepharness-bin').write_text('binary')
    (version / 'keepharness').write_text('launcher')
    links = home / '.local/opt/keepharness'
    links.mkdir()
    (links / 'current').symlink_to(version)
    assert not any(p.suffix == '.desktop' for p in install.files(home, '/python'))
    (links / 'current').unlink()
    assert not any(p.suffix == '.desktop' for p in install.files(home, '/python'))


def test_install_py_rollback_removes_only_exact_legacy_browser_entry(tmp_path, monkeypatch):
    from control import install
    monkeypatch.setattr(install, 'rollback_refusal', lambda home: None)
    monkeypatch.setattr(install, 'rollback_state', lambda home: home)
    entry = tmp_path / '.local/share/applications/keepharness.desktop'
    entry.parent.mkdir(parents=True)
    legacy = f'Exec="{tmp_path}/.local/bin/keepharness-open"'
    for lines, removed in [(legacy + '\nIcon=utilities-terminal\n', True),
                           (legacy + ' --foreign\nIcon=utilities-terminal\n', False),
                           (legacy + '\nIcon=keepharness\n', False),
                           (legacy + '\n', False),
                           (f'Exec="{tmp_path}/.local/opt/keepharness/current/keepharness"\nIcon=utilities-terminal\n', False)]:
        entry.write_text('[Desktop Entry]\n' + lines)
        install.rollback(tmp_path, run=lambda *a, **kw: None)
        assert entry.exists() is not removed


def test_install_py_atomic_entry_does_not_follow_symlink(tmp_path):
    from control import install
    foreign = tmp_path / 'foreign'
    foreign.write_text('unchanged')
    entry = tmp_path / 'keepharness-browser.desktop'
    entry.symlink_to(foreign)
    install.atomic_write(entry, 'owned entry', 0o644)
    assert foreign.read_text() == 'unchanged'
    assert not entry.is_symlink()
    assert entry.read_text() == 'owned entry'


def test_install_py_register_removes_only_exact_stale_browser(tmp_path, monkeypatch):
    from control import install
    from types import SimpleNamespace
    from pathlib import Path
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(install, 'remove_legacy_service', lambda home: None)
    monkeypatch.setattr(install.subprocess, 'run', lambda *a, **kw: None)
    monkeypatch.setattr(install, 'wait_ready', lambda *a: None)
    apps = tmp_path / '.local/share/applications'
    apps.mkdir(parents=True)
    desktop = apps / 'keepharness.desktop'
    desktop_text = f'Exec="{tmp_path}/.local/opt/keepharness/current/keepharness"\nIcon=keepharness\n'
    desktop.write_text(desktop_text)
    browser = apps / 'keepharness-browser.desktop'
    for extra in (' --foreign', ''):
        text = f'Exec="{tmp_path}/.local/bin/keepharness-open"{extra}\nIcon=utilities-terminal\n'
        browser.write_text(text)
        install.register(SimpleNamespace(port=0, dev=False, boot=False))
        assert browser.exists() == bool(extra)
        if extra:
            assert browser.read_text() == text
        assert desktop.read_text() == desktop_text


@pytest.mark.parametrize('operation', ['register', 'rollback'])
@pytest.mark.parametrize('kind', ['exact', 'other_icon', 'extra_exec', 'edited_exec', 'foreign', 'symlink'])
def test_legacy_browser_desktop_entry_removed_only_when_exact(tmp_path, monkeypatch, operation, kind):
    from control import install
    from pathlib import Path
    from types import SimpleNamespace
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(install, 'remove_legacy_service', lambda home: None)
    monkeypatch.setattr(install.subprocess, 'run', lambda *a, **kw: None)
    monkeypatch.setattr(install, 'wait_ready', lambda *a: None)
    monkeypatch.setattr(install, 'rollback_refusal', lambda home: None)
    monkeypatch.setattr(install, 'rollback_state', lambda home: home)
    apps = tmp_path / '.local/share/applications'
    apps.mkdir(parents=True)
    exec_line = f'Exec="{tmp_path}/.local/bin/keepharness-open"'
    content = {
        'exact': exec_line + '\nIcon=utilities-terminal\n',
        'other_icon': exec_line + '\nIcon=keepharness\n',
        'extra_exec': exec_line + '\nExec="/foreign"\nIcon=utilities-terminal\n',
        'edited_exec': exec_line + ' --custom\nIcon=utilities-terminal\n',
        'foreign': 'Exec="/foreign"\nIcon=utilities-terminal\n',
        'symlink': exec_line + '\nIcon=utilities-terminal\n',
    }[kind]
    legacy = apps / 'keepharness.desktop'
    if kind == 'symlink':
        target = tmp_path / 'foreign-target'
        target.write_text(content)
        legacy.symlink_to(target)
    else:
        legacy.write_text(content)
    if operation == 'register':
        install.register(SimpleNamespace(port=0, dev=False, boot=False))
    else:
        install.rollback(tmp_path, run=lambda *a, **kw: None)
    assert legacy.is_symlink() or legacy.exists() is (kind != 'exact')
    if kind != 'exact':
        assert legacy.read_text() == content


@pytest.mark.parametrize('operation', ['register', 'rollback', 'remove_browser_entry'])
@pytest.mark.parametrize('kind', ['owned', 'edited', 'foreign', 'symlink', 'duplicate'])
def test_browser_cleanup_preserves_foreign_entries_and_desktop(tmp_path, monkeypatch, operation, kind):
    from control import install
    from pathlib import Path
    from types import SimpleNamespace
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(install, 'remove_legacy_service', lambda home: None)
    monkeypatch.setattr(install.subprocess, 'run', lambda *a, **kw: None)
    monkeypatch.setattr(install, 'wait_ready', lambda *a: None)
    monkeypatch.setattr(install, 'rollback_refusal', lambda home: None)
    monkeypatch.setattr(install, 'rollback_state', lambda home: home)
    apps = tmp_path / '.local/share/applications'
    apps.mkdir(parents=True)
    text = f'Exec="{tmp_path}/.local/bin/keepharness-open"\nIcon=utilities-terminal\n'
    desktop = apps / 'keepharness.desktop'
    desktop_text = f'Exec="{tmp_path}/.local/opt/keepharness/current/keepharness"\nIcon=keepharness\n'
    desktop.write_text(desktop_text)
    browser = apps / 'keepharness-browser.desktop'
    content = text
    if kind == 'edited':
        content = text.replace('open"', 'open" --custom')
    elif kind == 'foreign':
        content = 'Exec="/foreign"\nIcon=utilities-terminal\n'
    elif kind == 'duplicate':
        content += 'Exec="/foreign"\n'
    if kind == 'symlink':
        target = tmp_path / 'foreign-target'
        target.write_text(content)
        browser.symlink_to(target)
    else:
        browser.write_text(content)
    if operation == 'register':
        install.register(SimpleNamespace(port=0, dev=False, boot=False))
        assert (tmp_path / '.local/bin/keepharness-open').is_file()
        assert (tmp_path / '.config/systemd/user' / install.SERVICE).is_file()
    elif operation == 'rollback':
        install.rollback(tmp_path, run=lambda *a, **kw: None)
    else:
        install.remove_browser_entry(browser, tmp_path)
        install.remove_browser_entry(desktop, tmp_path)
    assert desktop.read_text() == desktop_text
    assert browser.exists() == (kind != 'owned')
    if kind != 'owned':
        assert browser.read_text() == content
    if kind == 'symlink':
        assert browser.is_symlink()
        assert target.read_text() == content
