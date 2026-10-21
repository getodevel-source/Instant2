"""Migración de los modelos del directorio anterior ("instant").

El instalador de Windows ya no lleva los pesos: la app los reutiliza desde el
directorio del slug viejo. Estos tests fijan la misma semántica que tenía
`ReuseLegacyModels` en el Inno Setup: renombrar, fusionar, no dejar
duplicados, no tocar el override del usuario y no propagar fallos.
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolation  # noqa: F401  (aisla el estado real del usuario)

from instant2_app import paths


def _make_tree(root, *relative):
    """Crea un archivo (o carpeta) dentro de `root` y devuelve su ruta."""
    path = os.path.join(root, *relative)
    if relative[-1].endswith(".onnx") or "." in relative[-1]:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"pesos")
    else:
        os.makedirs(path, exist_ok=True)
    return path


class LegacyMigrationTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.current = os.path.join(self.root, "instant2", "models")
        self.legacy = os.path.join(self.root, "instant", "models")
        self.old_env = os.environ.pop("INSTANT2_DATA", None)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self.old_env is None:
            os.environ.pop("INSTANT2_DATA", None)
        else:
            os.environ["INSTANT2_DATA"] = self.old_env

    def test_moves_the_whole_legacy_directory_when_the_current_one_is_missing(self):
        _make_tree(self.legacy, "parakeet-v3-int8", "encoder.int8.onnx")
        _make_tree(self.legacy, "silero-vad", "silero_vad.onnx")
        result = paths.migrate_legacy_models(self.current, self.legacy)
        self.assertTrue(result["moved"])
        self.assertFalse(os.path.exists(self.legacy))
        self.assertTrue(os.path.isfile(
            os.path.join(self.current, "parakeet-v3-int8", "encoder.int8.onnx")))
        self.assertTrue(os.path.isfile(
            os.path.join(self.current, "silero-vad", "silero_vad.onnx")))
        self.assertEqual(
            sorted(os.listdir(os.path.join(self.current, "parakeet-v3-int8"))),
            ["encoder.int8.onnx"])
        # El contenido es el mismo: un rename no copia nada.
        with open(os.path.join(self.current, "parakeet-v3-int8",
                               "encoder.int8.onnx"), "rb") as handle:
            self.assertEqual(handle.read(), b"pesos")
        self.assertTrue(os.path.isfile(
            os.path.join(self.current, "silero-vad", "silero_vad.onnx")))

    def test_merges_when_both_directories_exist_and_drops_the_duplicates(self):
        _make_tree(self.current, "parakeet-v3-int8", "encoder.int8.onnx")
        _make_tree(self.legacy, "parakeet-v3-int8", "encoder.int8.onnx")
        _make_tree(self.legacy, "silero-vad", "silero_vad.onnx")
        result = paths.migrate_legacy_models(self.current, self.legacy)
        self.assertFalse(result["moved"])
        self.assertTrue(result["merged"])
        self.assertEqual(result["removed_duplicates"], 1)
        self.assertEqual(result["moved_entries"], 1)
        self.assertTrue(os.path.isfile(
            os.path.join(self.current, "silero-vad", "silero_vad.onnx")))
        self.assertTrue(os.path.isfile(
            os.path.join(self.current, "parakeet-v3-int8", "encoder.int8.onnx")))
        # El directorio anterior no queda como un duplicado viejo.
        self.assertFalse(os.path.exists(self.legacy))

    def test_is_idempotent(self):
        _make_tree(self.legacy, "parakeet-v3-int8", "encoder.int8.onnx")
        paths.migrate_legacy_models(self.current, self.legacy)
        second = paths.migrate_legacy_models(self.current, self.legacy)
        self.assertFalse(second["moved"])
        self.assertFalse(second["merged"])
        self.assertEqual(second["moved_entries"], 0)
        self.assertTrue(os.path.isfile(
            os.path.join(self.current, "parakeet-v3-int8", "encoder.int8.onnx")))

    def test_the_explicit_override_wins_and_the_legacy_directory_is_untouched(self):
        _make_tree(self.legacy, "parakeet-v3-int8", "encoder.int8.onnx")
        override = os.path.join(self.root, "otro")
        os.environ["INSTANT2_DATA"] = override
        with patch.object(paths, "user_data_dir", return_value=self.current), \
                patch.object(paths, "legacy_user_data_dir", return_value=self.legacy):
            got = paths.resolve_data_dir()
        self.assertEqual(got, override)
        self.assertTrue(os.path.isfile(
            os.path.join(self.legacy, "parakeet-v3-int8", "encoder.int8.onnx")))
        self.assertFalse(os.path.exists(self.current))

    def test_resolve_data_dir_migrates_the_legacy_directory(self):
        _make_tree(self.legacy, "parakeet-v3-int8", "encoder.int8.onnx")
        with patch.object(paths, "user_data_dir", return_value=self.current), \
                patch.object(paths, "legacy_user_data_dir", return_value=self.legacy):
            got = paths.resolve_data_dir()
        self.assertEqual(os.path.realpath(got), os.path.realpath(self.current))
        self.assertFalse(os.path.exists(self.legacy))
        self.assertTrue(os.path.isfile(
            os.path.join(self.current, "parakeet-v3-int8", "encoder.int8.onnx")))

    def test_a_failure_does_not_propagate(self):
        _make_tree(self.legacy, "parakeet-v3-int8", "encoder.int8.onnx")
        with patch.object(paths.os, "rename", side_effect=OSError("bloqueado")):
            result = paths.migrate_legacy_models(self.current, self.legacy)
        self.assertFalse(result["moved"])
        self.assertTrue(os.path.isfile(
            os.path.join(self.legacy, "parakeet-v3-int8", "encoder.int8.onnx")))

    def test_a_failure_does_not_break_resolve_data_dir(self):
        _make_tree(self.legacy, "parakeet-v3-int8", "encoder.int8.onnx")
        with patch.object(paths, "user_data_dir", return_value=self.current), \
                patch.object(paths, "legacy_user_data_dir", return_value=self.legacy), \
                patch.object(paths.os, "rename", side_effect=OSError("bloqueado")):
            got = paths.resolve_data_dir()
        self.assertEqual(os.path.realpath(got), os.path.realpath(self.current))

    def test_a_missing_legacy_directory_is_not_an_error(self):
        result = paths.migrate_legacy_models(self.current, self.legacy)
        self.assertFalse(result["moved"])
        self.assertFalse(result["merged"])
        self.assertFalse(os.path.exists(self.current))


if __name__ == "__main__":
    unittest.main()
