import tempfile
import unittest
from unittest.mock import patch

from control import install_runtime


class InstallRuntimeTest(unittest.TestCase):
    def test_runtime_path_is_project_local_and_pinned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = install_runtime.project_root(directory)
            self.assertEqual(
                install_runtime.runtime_dir(root), root / "local-ai/runtime/llama-b11003"
            )
        self.assertEqual(install_runtime.COMMIT, "7d6f5d02bb40fca0ab29e65fe4eb86eab6886f19")


    def test_backends_are_explicit(self):
        self.assertEqual(set(install_runtime.BACKEND_OPTIONS), {"cpu", "vulkan", "cuda"})
        self.assertIn("-DGGML_VULKAN=ON", install_runtime.BACKEND_OPTIONS["vulkan"])


    def test_invalid_root_fails_without_creating_a_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = install_runtime.project_root(directory) / "missing"
            self.assertEqual(install_runtime.main(["--root", str(missing)]), 1)
            self.assertFalse((missing / "local-ai").exists())

    def test_non_linux_is_rejected_before_build(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(install_runtime.platform, "system", return_value="Darwin"):
                with self.assertRaisesRegex(RuntimeError, "Linux"):
                    install_runtime.build(install_runtime.project_root(directory), "cpu", 1)


if __name__ == "__main__":
    unittest.main()
