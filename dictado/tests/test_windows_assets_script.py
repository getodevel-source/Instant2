"""El instalador de Windows ya no lleva pesos de modelo.

El instalador lleva la app y el runtime de llama.cpp; los componentes de voz
(ASR, TTS y el LLM local opcional) los descarga la app. Estas guardas fijan
ese contrato en los dos lados que lo sostienen: el script que prepara los
recursos del instalador y el workflow de release que lo invoca.
"""
import os
import subprocess
import sys
import unittest


ROOT = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", ".."))
SCRIPT = os.path.join(ROOT, "scripts", "prepare_windows_assets.py")
OLD_SCRIPT = os.path.join(ROOT, "scripts", "prepare_offline_bundle.py")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "release.yml")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class AssetsScriptTests(unittest.TestCase):
    """El script prepara el runtime, no los pesos de modelo."""

    def test_the_script_was_renamed(self):
        self.assertTrue(os.path.isfile(SCRIPT),
                        "falta %s" % SCRIPT)
        self.assertFalse(os.path.exists(OLD_SCRIPT),
                         "el nombre viejo del script ya no describes lo que hace")

    def test_the_script_does_not_stage_model_weights(self):
        text = _read(SCRIPT)
        for gone in ("offline-model-bundle.json", "Instant2-Models",
                     "LLM_MODEL_URL", "LLM_MODEL_NAME", "LLM_MODEL_EXPECTED",
                     "PARAKEET_SHA256", "download_models", "ensure_model",
                     "check_integrity", "--payload-dir", "Kokoro"):
            with self.subTest(token=gone):
                self.assertNotIn(gone, text)

    def test_the_script_still_stages_the_llama_runtime(self):
        text = _read(SCRIPT)
        for kept in ("llm-runtime", "llama-server.exe",
                     "THIRD-PARTY-NOTICES.txt", "stop-bundled-runtime.ps1",
                     "licenses"):
            with self.subTest(token=kept):
                self.assertIn(kept, text)

    def test_the_command_line_only_takes_the_app_directory(self):
        result = subprocess.run(
            [sys.executable, SCRIPT, "--help"],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--app-dir", result.stdout)
        self.assertNotIn("--payload-dir", result.stdout)


class ReleaseWorkflowTests(unittest.TestCase):
    """El job de Windows construye el instalador sin modelos."""

    def test_the_workflow_calls_the_renamed_script(self):
        text = _read(WORKFLOW)
        self.assertIn("scripts/prepare_windows_assets.py", text)
        self.assertNotIn("prepare_offline_bundle", text)

    def test_the_workflow_passes_no_model_directory(self):
        text = _read(WORKFLOW)
        for gone in ("--payload-dir", "/DModelDir", "Instant2-Models"):
            with self.subTest(token=gone):
                self.assertNotIn(gone, text)

    def test_the_workflow_does_not_call_the_installer_offline(self):
        text = _read(WORKFLOW)
        self.assertNotIn("Instalador offline", text)


if __name__ == "__main__":
    unittest.main()
