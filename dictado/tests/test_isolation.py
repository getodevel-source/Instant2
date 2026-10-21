"""La suite no toca el estado real del usuario.

La app escribe en dos lugares del disco del usuario: la configuración
(`%APPDATA%\\instant2\\config.json`) y los modelos (`%LOCALAPPDATA%\\instant2\\models`,
más el directorio del producto anterior). Un test que escriba ahí deja la
máquina rota, así que la suite se aísla antes de correr nada.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolation  # noqa: F401  (aisla el estado real del usuario)

from instant2_app import config, paths


class TestIsolationTests(unittest.TestCase):
    def test_the_config_and_the_models_live_in_the_temporary_directory(self):
        root = os.environ[_isolation.HOME_ENV]
        self.assertTrue(config.config_path().startswith(root))
        self.assertTrue(paths.config_dir().startswith(root))
        self.assertTrue(paths.resolve_data_dir().startswith(root))
        self.assertTrue(paths.user_data_dir().startswith(root))
        self.assertTrue(paths.legacy_user_data_dir().startswith(root))

    def test_the_config_path_honours_the_environment_override(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {paths.CONFIG_DIR_ENV: directory}):
                self.assertEqual(paths.config_dir(), directory)
                self.assertEqual(config.config_path(),
                                 os.path.join(directory, "config.json"))

    def test_the_data_dir_honours_the_environment_override(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"INSTANT2_DATA": directory}):
                self.assertEqual(paths.resolve_data_dir(), directory)


if __name__ == "__main__":
    unittest.main()
