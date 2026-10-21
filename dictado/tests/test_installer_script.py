"""Guardas del script de Inno Setup (installer/instant2.iss).

Inno Setup no tiene cobertura automatica en este repo: el script se puede
probar de verdad solo si hay ISCC en la maquina. Por eso estas pruebas tienen
dos capas:

1. Guardas estaticas sobre los invariantes del script (baratas, siempre
   corren). Cubren los errores que rompieron la compilacion una vez:
   la firma del Check, la semantica de su resultado y el uso de ";" como
   comentario dentro del [Code] (en Pascal ";" es separador de sentencia, no
   un comentario, y rompe el parseo con "'BEGIN' expected").
2. Una compilacion real del script cuando ISCC esta disponible. Se saltea
   si no lo hay (en CI solo lo instala el job de release).
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", ".."))
ISS = os.path.join(ROOT, "installer", "instant2.iss")
ICON = os.path.join(ROOT, "dictado", "build", "instant2.ico")

ISCC_CANDIDATES = (
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs",
                 "Inno Setup 6", "ISCC.exe"),
)


def _iscc():
    for candidate in ISCC_CANDIDATES:
        if candidate and os.path.isfile(candidate):
            return candidate
    return shutil.which("ISCC")


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
        self.assertIn("Check: ShouldInstallModel", models[0])

    def test_the_check_looks_for_the_file_in_the_models_directory(self):
        code = _section("Code")
        self.assertIn("function ShouldInstallModel(", code)
        # La funcion tiene que mirar el archivo que Setup esta procesando
        # (CurrentFileName) y buscarlo en el directorio de modelos.
        self.assertIn("CurrentFileName", code)
        self.assertIn("ExtractFileName", code)
        self.assertIn("FindModelFile(ExpandConstant(ModelsDir)", code)

    def test_the_check_returns_true_to_install(self):
        # En Inno Setup, Check devuelve True para INSTALAR el archivo. Como
        # aca el nombre dice "deberia instalarse", tiene que devolver la
        # negacion de "ya esta en el destino". Sin el "not" el instalador
        # saltea justo los modelos que faltan y copia los que ya estan.
        code = _section("Code")
        self.assertIn("Result := not FindModelFile(", code)

    def test_the_check_signature_has_no_parameters(self):
        # La firma (Installer: TInstaller; WizardForm: TWizardForm; Param:
        # String) no compila: TInstaller no existe en el dialecto de Inno
        # Setup 6 y el error es "Unknown type 'TInstaller'". Se mira solo la
        # firma, porque el comentario explica justamente ese error.
        code = _section("Code")
        signatures = re.findall(r"function\s+\w+\s*\(([^)]*)\)", code)
        self.assertTrue(signatures)
        for arguments in signatures:
            self.assertNotIn("TInstaller", arguments)
        signature = re.search(r"function ShouldInstallModel\(([^)]*)\)",
                               code)
        self.assertIsNotNone(signature)
        self.assertEqual(signature.group(1).strip(), "")

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

    def test_the_code_section_has_no_semicolon_comments(self):
        # En el [Code] el ";" no es un comentario: en Pascal es separador de
        # sentencia. Un comentario asi rompe la compilacion con
        # "'BEGIN' expected" y el script entero deja de compilar.
        code = _section("Code")
        offenders = [line for line in code.splitlines()
                     if line.lstrip().startswith(";")]
        self.assertEqual(offenders, [],
                         "comentarios con ';' dentro del [Code]: %r" % offenders)

    def test_the_code_section_uses_slash_comments(self):
        code = _section("Code")
        self.assertIn("// ", code)


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


class InstallerCompilesTests(unittest.TestCase):
    """El script tiene que compilar de verdad (si hay Inno Setup)."""

    def test_the_installer_script_compiles(self):
        iscc = _iscc()
        if not iscc:
            self.skipTest("Inno Setup no esta instalado en esta maquina")
        if not os.path.isfile(ICON):
            self.skipTest("falta el icono %s" % ICON)
        with tempfile.TemporaryDirectory() as work:
            app_dir = os.path.join(work, "app")
            model_dir = os.path.join(work, "models")
            out_dir = os.path.join(work, "out")
            for path in (app_dir, model_dir, out_dir):
                os.makedirs(path)
            with open(os.path.join(app_dir, "Instant2.exe"), "w") as handle:
                handle.write("stub")
            for sub in ("parakeet-v3-int8", "silero-vad", "kokoro-onnx", "llm"):
                os.makedirs(os.path.join(model_dir, sub))
                with open(os.path.join(model_dir, sub, "model.bin"), "w") as h:
                    h.write("stub")
            result = subprocess.run(
                [iscc,
                 "/DAppVersion=0.1.5",
                 "/DSourceDir=%s" % app_dir,
                 "/DModelDir=%s" % model_dir,
                 "/DIconFile=%s" % ICON,
                 "/O%s" % out_dir,
                 ISS],
                capture_output=True, text=True, cwd=ROOT)
            self.assertEqual(
                result.returncode, 0,
                "el script de Inno Setup no compila:\n%s" % result.stdout)


if __name__ == "__main__":
    unittest.main()
