"""Instant2 model paths stay separate from cwd and sibling product installs."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolation  # noqa: F401  (aisla el estado real del usuario)

from instant2_app import paths

MARKER = os.path.join(paths.PARAKEET_SUBDIR, "encoder.int8.onnx")


def _touch(root):
    p = os.path.join(root, MARKER)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb"):
        pass
    return p


def _same_path(got, want):
    """Compara rutas resolviendo symlinks."""
    if not isinstance(got, str) or not isinstance(want, str):
        return got == want
    return os.path.realpath(got) == os.path.realpath(want)


class DataDirPrecedenceTests(unittest.TestCase):
    def setUp(self):
        self.old_env = os.environ.get("INSTANT2_DATA")
        self.old_cwd = os.getcwd()
        self.old_frozen = getattr(sys, "frozen", None)
        self.old_executable = sys.executable

    def tearDown(self):
        os.chdir(self.old_cwd)
        if self.old_env is None:
            os.environ.pop("INSTANT2_DATA", None)
        else:
            os.environ["INSTANT2_DATA"] = self.old_env
        if self.old_frozen is None:
            if hasattr(sys, "frozen"):
                del sys.frozen
        else:
            sys.frozen = self.old_frozen
        sys.executable = self.old_executable

    def test_env_wins_over_cwd_and_user_dir(self):
        with tempfile.TemporaryDirectory() as env_d, tempfile.TemporaryDirectory() as cwd_d:
            _touch(env_d)
            _touch(os.path.join(cwd_d, "models"))
            os.environ["INSTANT2_DATA"] = env_d
            os.chdir(cwd_d)
            try:
                got = paths.resolve_data_dir()
            finally:
                os.chdir(self.old_cwd)
            self.assertTrue(_same_path(got, env_d))

    def test_cwd_models_are_ignored_to_keep_products_isolated(self):
        os.environ.pop("INSTANT2_DATA", None)
        with tempfile.TemporaryDirectory() as cwd_d:
            _touch(os.path.join(cwd_d, "models"))
            os.chdir(cwd_d)
            try:
                got = paths.resolve_data_dir()
            finally:
                os.chdir(self.old_cwd)
            self.assertTrue(_same_path(got, paths.user_data_dir()))

    def test_frozen_dist_ignores_sibling_models(self):
        with tempfile.TemporaryDirectory() as checkout:
            dist = os.path.join(checkout, "dist")
            os.makedirs(dist)
            _touch(os.path.join(checkout, "models"))
            os.environ.pop("INSTANT2_DATA", None)
            sys.frozen = True
            sys.executable = os.path.join(dist, "Instant2.exe")
            with tempfile.TemporaryDirectory() as cwd_d:
                os.chdir(cwd_d)
                try:
                    got = paths.resolve_data_dir()
                finally:
                    os.chdir(self.old_cwd)
                self.assertTrue(_same_path(got, paths.user_data_dir()))

    def test_cwd_without_marker_falls_back_to_user_data_dir(self):
        os.environ.pop("INSTANT2_DATA", None)
        with tempfile.TemporaryDirectory() as cwd_d:
            os.chdir(cwd_d)
            try:
                got = paths.resolve_data_dir()
            finally:
                os.chdir(self.old_cwd)
            self.assertTrue(_same_path(got, paths.user_data_dir()))

    def test_default_model_directory_is_scoped_to_instant2(self):
        self.assertTrue(paths.user_data_dir().endswith(
            os.path.join("instant2", "models")))
        self.assertTrue(paths.config_dir().endswith("instant2"))

    def test_nonexistent_env_is_preserved_literally(self):
        ghost = os.path.join(tempfile.gettempdir(), "instant2-ghost-noexiste")
        shutil.rmtree(ghost, ignore_errors=True)
        os.environ["INSTANT2_DATA"] = ghost
        self.assertEqual(paths.resolve_data_dir(), ghost)

    def test_env_with_tilde_and_vars_is_expanded(self):
        os.environ["FOO_TEST_DATA"] = "models-dir"
        os.environ["INSTANT2_DATA"] = "~/$FOO_TEST_DATA" if os.name != "nt" else "~/%FOO_TEST_DATA%"
        expanded = paths.resolve_data_dir()
        self.assertNotIn("~", expanded)
        self.assertNotIn("%", expanded)
        self.assertNotIn("$", expanded)
        os.environ.pop("FOO_TEST_DATA", None)


if __name__ == "__main__":
    unittest.main()
