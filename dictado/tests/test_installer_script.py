"""Guardas del script de Inno Setup (installer/instant2.iss).

Inno Setup no tiene cobertura automatica en este repo: el script solo se
puede probar compilando y corriendo el instalador de verdad. Estas pruebas
no comprueban el comportamiento (eso no se puede sin Inno Setup), sino los
invariantes del script: que los modelos que el usuario ya tiene no se
vuelvan a copiar y que la instalacion silenciosa vuelva a abrir la app.
"""
import os
import re
import unittest

ISS = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "installer", "instant2.iss"))


def _script():
    with open(ISS, encoding="utf-8") as handle:
        return handle.read()


def _section(name):
    text = _script()
    match = re.search(r"^\[" + re.escape(name) + r"\]\s*$(.*?)(?=^\[|\Z)",
                      text, re.M | re.S)
    return match.group(1) if match else ""


class InstallerModelsTests(unittest.TestCase):
    """T7: los modelos que el usuario ya tiene no se vuelven a copiar."""

    def test_the_models_entry_skips_the_files_that_are_already_there(self):
        entry = _section("Files")
        models = [line for line in entry.splitlines()
                  if line.startswith("Source:") and "ModelDir" in line]
        self.assertEqual(len(models), 1)
        self.assertIn("Check: ModelAlreadyInstalled", models[0])

    def test_the_check_looks_for_the_file_in_the_models_directory(self):
        code = _section("Code")
        self.assertIn("function ModelAlreadyInstalled(", code)
        # La funcion tiene que mirar el archivo que Setup esta procesando
        # (CurrentFilename) y buscarlo en el directorio de modelos.
        self.assertIn("CurrentFilename", code)
        self.assertIn("ExtractFileName", code)
        self.assertIn("FindModelFile(ExpandConstant(ModelsDir)", code)

    def test_the_legacy_models_are_moved_not_copied(self):
        code = _section("Code")
        self.assertIn("LegacyModelsDir = '{localappdata}\\instant\\models'",
                      code)
        self.assertIn("ModelsDir = '{localappdata}\\instant2\\models'", code)
        # Mover (rename) es lo que evita copiar los 650 MB otra vez.
        self.assertIn("RenameFile(Legacy, Current)", code)
        # Y lo que ya esta en el destino no puede quedar huerfano.
        self.assertIn("DelTree(LegacyItem, True, True, True)", code)

    def test_the_reuse_runs_before_the_files_are_installed(self):
        code = _section("Code")
        prepare = re.search(
            r"function PrepareToInstall\(.*?\nend;", code, re.S)
        self.assertIsNotNone(prepare)
        self.assertIn("ReuseLegacyModels();", prepare.group(0))


class InstallerRunTests(unittest.TestCase):
    """T7b: la instalacion silenciosa tambien reabre la app."""

    def test_the_silent_install_reopens_the_app(self):
        run = _section("Run")
        entries = [line for line in run.splitlines()
                   if line.startswith("Filename:")]
        self.assertEqual(len(entries), 1)
        self.assertNotIn("skipifsilent", entries[0])
        self.assertIn("postinstall", entries[0])

    def test_the_installed_app_is_the_one_that_is_opened(self):
        run = _section("Run")
        self.assertIn('Filename: "{app}\\{#AppExe}"', run)


if __name__ == "__main__":
    unittest.main()
