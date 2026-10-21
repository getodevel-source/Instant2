"""Contrato del payload del instalador de Windows.

El instalador completo lleva la app, el runtime de llama.cpp y los modelos de
voz (ASR + TTS), para que una instalación nueva dicte sin internet. El modelo
de lenguaje local (3 GB) nunca se empaqueta: la app lo baja a pedido. Estas
guardas fijan ese contrato en los dos lados que lo sostienen: el script que
prepara los recursos y el workflow de release que lo invoca.
"""
import os
import subprocess
import sys
import unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolation  # noqa: F401  (aisla el estado real del usuario)


ROOT = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", ".."))
SCRIPT = os.path.join(ROOT, "scripts", "prepare_windows_assets.py")
OLD_SCRIPT = os.path.join(ROOT, "scripts", "prepare_offline_bundle.py")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "release.yml")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class AssetsScriptTests(unittest.TestCase):
    """El script prepara el runtime y, si se le pide, los modelos de voz."""

    def test_the_script_was_renamed(self):
        self.assertTrue(os.path.isfile(SCRIPT),
                        "falta %s" % SCRIPT)
        self.assertFalse(os.path.exists(OLD_SCRIPT),
                         "el nombre viejo del script ya no describe lo que hace")

    def test_the_script_never_stages_the_llm(self):
        text = _read(SCRIPT)
        for gone in ("LLM_MODEL_URL", "LLM_MODEL_NAME", "LLM_MODEL_EXPECTED",
                     "Qwen_Qwen3.5-4B-Q4_K_M.gguf"):
            with self.subTest(token=gone):
                self.assertNotIn(gone, text)

    def test_the_script_stages_the_voice_models_when_asked(self):
        text = _read(SCRIPT)
        for kept in ("VOICE_MODEL_FILES", "download_models", "ensure_model",
                     "check_integrity", "kokoro-onnx", "PARAKEET_SUBDIR",
                     "voice-model-bundle.json", '"llm_bundled": False'):
            with self.subTest(token=kept):
                self.assertIn(kept, text)

    def test_the_script_still_stages_the_llama_runtime(self):
        text = _read(SCRIPT)
        for kept in ("llm-runtime", "llama-server.exe",
                     "THIRD-PARTY-NOTICES.txt", "stop-bundled-runtime.ps1",
                     "licenses"):
            with self.subTest(token=kept):
                self.assertIn(kept, text)

    def test_the_command_line_takes_the_model_directory(self):
        result = subprocess.run(
            [sys.executable, SCRIPT, "--help"],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--app-dir", result.stdout)
        self.assertIn("--models-dir", result.stdout)
        self.assertNotIn("--payload-dir", result.stdout)


class ReleaseWorkflowTests(unittest.TestCase):
    """El job de Windows construye los dos instaladores."""

    def test_the_workflow_calls_the_renamed_script(self):
        text = _read(WORKFLOW)
        self.assertIn("scripts/prepare_windows_assets.py", text)
        self.assertNotIn("prepare_offline_bundle", text)

    def test_the_workflow_builds_the_full_installer_with_models(self):
        text = _read(WORKFLOW)
        self.assertIn("--models-dir dist/Instant2-Models", text)
        self.assertIn("/DModelDir=$MODELS", text)
        self.assertIn("/DOutputBase=Instant2-Setup", text)

    def test_the_workflow_builds_the_app_only_update_installer(self):
        text = _read(WORKFLOW)
        self.assertIn("/DOutputBase=Instant2-Update", text)
        self.assertIn("Instant2-Update.exe", text)

    def test_the_workflow_rejects_a_split_installer(self):
        text = _read(WORKFLOW)
        self.assertIn("Inno partió el instalador en volúmenes", text)


if __name__ == "__main__":
    unittest.main()
