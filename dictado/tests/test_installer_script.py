"""Guardas del script de Inno Setup (installer/instant2.iss).

Inno Setup no tiene cobertura automatica en este repo: el script se puede
probar de verdad solo si hay ISCC en la maquina. Por eso estas pruebas tienen
dos capas:

1. Guardas estaticas sobre los invariantes del script (baratas, siempre
   corren). Cubren los errores que rompieron la compilacion una vez:
   la firma del Check, la semantica de su resultado y el uso de ";" como
   comentario dentro del [Code] (en Pascal ";" es separador de sentencia, no
   un comentario, y rompe el parseo con "'BEGIN' expected"). Y cubren el
   contrato nuevo: el instalador NO lleva pesos de modelo (los descarga la
   app), pero SI sigue parando el daemon y el runtime de llama.cpp.
2. Una compilacion real del script cuando ISCC esta disponible. Se saltea
   si no lo hay (en CI solo lo instala el job de release).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolation  # noqa: F401  (aisla el estado real del usuario)


ROOT = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", ".."))
ISS = os.path.join(ROOT, "installer", "instant2.iss")
ICON = os.path.join(ROOT, "dictado", "build", "instant2.ico")

ISCC_CANDIDATES = (
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
    # Con el %LOCALAPPDATA% REAL: el aislamiento de los tests redirige esa
    # variable a un temporal, y con la redireccion ISCC no se encuentra y esta
    # prueba se salta en silencio, dejando el instalador sin compilar en la
    # suite. Inno Setup instalado por usuario vive en el perfil real.
    os.path.join(_isolation.ORIGINAL_ENV.get("LOCALAPPDATA", ""),
                 "Programs", "Inno Setup 6", "ISCC.exe"),
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


class InstallerPayloadTests(unittest.TestCase):
    """El instalador no lleva pesos de modelo: los descarga la app."""

    def test_the_files_section_has_no_models_entry(self):
        entry = _section("Files")
        models = [line for line in entry.splitlines()
                  if line.startswith("Source:") and "ModelDir" in line]
        self.assertEqual(models, [],
                         "el instalador ya no copia pesos de modelo: %r"
                         % models)

    def test_the_script_never_mentions_the_model_directory(self):
        # Ni la define ni la interpola: con la entrada presente y el
        # directorio vacio, ISCC aborta con "No files found matching".
        text = _script()
        self.assertNotIn("{#ModelDir}", text)
        self.assertNotIn("ModelDir", text)

    def test_no_source_line_ships_a_model_payload(self):
        for line in _section("Files").splitlines():
            if line.startswith("Source:"):
                self.assertNotIn("model", line.lower(),
                                 "el instalador no debe copiar pesos: %r" % line)

    def test_the_code_section_has_no_model_helpers(self):
        code = _section("Code")
        for name in ("ReuseLegacyModels", "ShouldInstallModel", "FindModelFile"):
            self.assertNotIn(name, code)

    def test_the_installer_still_stops_the_daemon(self):
        code = _section("Code")
        prepare = re.search(
            r"function PrepareToInstall\(.*?\nend;", code, re.S)
        self.assertIsNotNone(prepare)
        self.assertIn("'stop'", prepare.group(0))

    def test_the_installer_still_stops_the_llama_runtime(self):
        code = _section("Code")
        prepare = re.search(
            r"function PrepareToInstall\(.*?\nend;", code, re.S)
        self.assertIsNotNone(prepare)
        body = prepare.group(0)
        self.assertIn("stop-bundled-runtime.ps1", body)
        self.assertIn("llm-runtime", body)

    def test_no_function_signature_uses_a_type_that_does_not_exist(self):
        # La firma (Installer: TInstaller; WizardForm: TWizardForm; Param:
        # String) no compila: TInstaller no existe en el dialecto de Inno
        # Setup 6 y el error es "Unknown type 'TInstaller'".
        code = _section("Code")
        signatures = re.findall(r"function\s+\w+\s*\(([^)]*)\)", code)
        self.assertTrue(signatures)
        for arguments in signatures:
            self.assertNotIn("TInstaller", arguments)

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
            out_dir = os.path.join(work, "out")
            for path in (app_dir, out_dir):
                os.makedirs(path)
            with open(os.path.join(app_dir, "Instant2.exe"), "w") as handle:
                handle.write("stub")
            # Sin directorio de modelos: el instalador no lleva pesos y el
            # script no puede referenciar uno que no existe.
            result = subprocess.run(
                [iscc,
                 "/DAppVersion=0.1.5",
                 "/DSourceDir=%s" % app_dir,
                 "/DIconFile=%s" % ICON,
                 "/O%s" % out_dir,
                 ISS],
                capture_output=True, text=True, cwd=ROOT)
            self.assertEqual(
                result.returncode, 0,
                "el script de Inno Setup no compila:\n%s" % result.stdout)


if __name__ == "__main__":
    unittest.main()
