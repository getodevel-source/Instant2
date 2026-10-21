"""Migración de los modelos del directorio anterior ("instant").

El instalador de Windows ya no lleva los pesos: la app los reutiliza desde el
directorio del slug viejo. Estos tests fijan la misma semántica que tenía
`ReuseLegacyModels` en el Inno Setup, con dos diferencias que importan:

- la migración es un paso explícito del arranque (`migrate_legacy_models_once`),
  no un efecto secundario de "donde están mis datos";
- una entrada con el mismo nombre en el destino NO es un duplicado verificado
  (el destino puede ser una descarga parcial): la copia anterior se conserva
  con otro nombre en vez de borrarse.
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


def _write(root, content, *relative):
    """Crea un archivo con contenido explícito y devuelve su ruta."""
    path = os.path.join(root, *relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(content)
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

    def test_resolve_data_dir_does_not_move_anything(self):
        """`resolve_data_dir()` es una consulta: no toca el disco."""
        _make_tree(self.legacy, "parakeet-v3-int8", "encoder.int8.onnx")
        with patch.object(paths, "user_data_dir", return_value=self.current), \
                patch.object(paths, "legacy_user_data_dir", return_value=self.legacy):
            got = paths.resolve_data_dir()
        self.assertEqual(os.path.realpath(got), os.path.realpath(self.current))
        # Nada se movió: la migración es un paso aparte.
        self.assertTrue(os.path.isfile(
            os.path.join(self.legacy, "parakeet-v3-int8", "encoder.int8.onnx")))
        self.assertFalse(os.path.exists(self.current))

    def test_the_explicit_step_moves_the_legacy_directory(self):
        _make_tree(self.legacy, "parakeet-v3-int8", "encoder.int8.onnx")
        with patch.object(paths, "user_data_dir", return_value=self.current), \
                patch.object(paths, "legacy_user_data_dir", return_value=self.legacy):
            result = paths.migrate_legacy_models_once()
        self.assertTrue(result.get("moved"))
        self.assertFalse(os.path.exists(self.legacy))
        self.assertTrue(os.path.isfile(
            os.path.join(self.current, "parakeet-v3-int8", "encoder.int8.onnx")))

    def test_the_explicit_step_happens_only_once(self):
        _make_tree(self.legacy, "parakeet-v3-int8", "encoder.int8.onnx")
        with patch.object(paths, "user_data_dir", return_value=self.current), \
                patch.object(paths, "legacy_user_data_dir", return_value=self.legacy):
            paths.migrate_legacy_models_once()
            marker = os.path.join(self.current, paths.MIGRATION_MARKER)
            self.assertTrue(os.path.isfile(marker))
            # La marca queda: la migración no se repite en cada arranque.
            _make_tree(self.legacy, "parakeet-v3-int8", "encoder.int8.onnx")
            again = paths.migrate_legacy_models_once()
        self.assertTrue(again.get("skipped"))
        self.assertEqual(again.get("reason"), "already-done")

    def test_the_explicit_step_respects_the_user_override(self):
        _make_tree(self.legacy, "parakeet-v3-int8", "encoder.int8.onnx")
        override = os.path.join(self.root, "otro")
        os.environ["INSTANT2_DATA"] = override
        with patch.object(paths, "user_data_dir", return_value=self.current), \
                patch.object(paths, "legacy_user_data_dir", return_value=self.legacy):
            result = paths.migrate_legacy_models_once()
        self.assertTrue(result.get("skipped"))
        self.assertEqual(result.get("reason"), "override")
        self.assertTrue(os.path.isfile(
            os.path.join(self.legacy, "parakeet-v3-int8", "encoder.int8.onnx")))

    def test_a_partial_download_does_not_destroy_the_legacy_copy(self):
        """El destino con una descarga parcial no habilita el borrado."""
        # El destino tiene el `llm/` a medio descargar...
        _write(self.current, b"parcial", "llm",
               "Qwen_Qwen3.5-4B-Q4_K_M.gguf.part")
        # ...y el anterior tiene el GGUF que la persona eligió a mano.
        _write(self.legacy, b"completo", "llm", "mi-modelo.gguf")
        result = paths.migrate_legacy_models(self.current, self.legacy)
        self.assertEqual(result["kept_aside"], 1)
        self.assertEqual(result["removed_duplicates"], 0)
        # La copia anterior se conserva (con otro nombre) y el destino intacto.
        kept = os.path.join(self.legacy, "llm" + paths.LEGACY_SUFFIX,
                            "mi-modelo.gguf")
        self.assertTrue(os.path.isfile(kept))
        self.assertTrue(os.path.isfile(
            os.path.join(self.current, "llm", "Qwen_Qwen3.5-4B-Q4_K_M.gguf.part")))

    def test_a_partial_recognition_does_not_destroy_the_legacy_set(self):
        """Un `parakeet-v3-int8` a medias no habilita el borrado del anterior."""
        _write(self.current, b"parcial", "parakeet-v3-int8", "encoder.int8.onnx")
        _make_tree(self.legacy, "parakeet-v3-int8", "encoder.int8.onnx")
        _make_tree(self.legacy, "parakeet-v3-int8", "decoder.int8.onnx")
        result = paths.migrate_legacy_models(self.current, self.legacy)
        self.assertEqual(result["kept_aside"], 1)
        self.assertEqual(result["removed_duplicates"], 0)
        kept = os.path.join(self.legacy, "parakeet-v3-int8" + paths.LEGACY_SUFFIX)
        self.assertTrue(os.path.isfile(
            os.path.join(kept, "encoder.int8.onnx")))
        self.assertTrue(os.path.isfile(
            os.path.join(kept, "decoder.int8.onnx")))

    def test_a_verified_duplicate_is_still_removed(self):
        """Si el destino está verificado, la copia anterior sí sobra."""
        _make_tree(self.current, "parakeet-v3-int8", "encoder.int8.onnx")
        _make_tree(self.legacy, "parakeet-v3-int8", "encoder.int8.onnx")
        result = paths.migrate_legacy_models(self.current, self.legacy)
        self.assertEqual(result["removed_duplicates"], 1)
        self.assertEqual(result["kept_aside"], 0)
        self.assertFalse(os.path.exists(self.legacy))

    def test_two_migrations_at_the_same_time_keep_the_models(self):
        """El lock evita que dos procesos se pisen: nunca se pierde un archivo."""
        _make_tree(self.legacy, "silero-vad", "silero_vad.onnx")
        os.makedirs(self.current, exist_ok=True)
        _write(self.current, b"parcial", "llm", "Qwen.partial")
        with patch.object(paths, "user_data_dir", return_value=self.current), \
                patch.object(paths, "legacy_user_data_dir", return_value=self.legacy):
            first = paths.migrate_legacy_models_once()
            # El segundo proceso ve la marca y no hace nada.
            second = paths.migrate_legacy_models_once()
        self.assertTrue(first.get("skipped") or first.get("merged"))
        self.assertTrue(second.get("skipped"))
        self.assertTrue(os.path.isfile(
            os.path.join(self.current, "silero-vad", "silero_vad.onnx")))
        self.assertTrue(os.path.isfile(
            os.path.join(self.current, "llm", "Qwen.partial")))

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
