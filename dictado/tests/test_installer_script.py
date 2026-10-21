"""Guardas del script de Inno Setup (installer/instant2.iss).

Inno Setup no tiene cobertura automatica en este repo: el script se puede
probar de verdad solo si hay ISCC en la maquina. Por eso estas pruebas tienen
dos capas:

1. Guardas estaticas sobre los invariantes del script (baratas, siempre
   corren). Cubren los errores que rompieron la instalacion una vez:
   la firma del Check, la semantica de su resultado, el uso de ";" como
   comentario dentro del [Code] (en Pascal ";" es separador de sentencia, no
   un comentario) y, sobre todo, el instalador partido en volumenes: con
   DiskSpanning=yes Inno genera Setup.exe + Setup-1.bin y quien baja solo el
   .exe se queda con un instalador que pide "el disco".
2. Una compilacion real del script cuando ISCC esta disponible. Se saltea
   si no lo hay (en CI solo lo instala el job de release). Compila las dos
   variantes: el instalador completo (con modelos de voz) y el de
   actualizacion (solo app).
"""
import importlib.util
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
ASSETS_SCRIPT = os.path.join(ROOT, "scripts", "prepare_windows_assets.py")

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


def _function(name):
    code = _section("Code")
    match = re.search(r"function\s+" + re.escape(name) + r"\(.*?\nend;",
                      code, re.S)
    return match.group(0) if match else ""


def _assets_module():
    spec = importlib.util.spec_from_file_location(
        "prepare_windows_assets", ASSETS_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InstallerPayloadTests(unittest.TestCase):
    """El instalador completo lleva la voz; el LLM de 3 GB nunca."""

    def test_the_files_section_has_a_conditional_models_entry(self):
        entry = _section("Files")
        match = re.search(r"#ifdef ModelDir(.*?)#endif", entry, re.S)
        self.assertIsNotNone(
            match, "falta la entrada de modelos bajo #ifdef ModelDir")
        block = match.group(1)
        self.assertIn('Source: "{#ModelDir}\\*"', block)
        self.assertIn('DestDir: "{localappdata}\\instant2\\models"', block)
        self.assertIn("Check: ShouldInstallModel", block)
        self.assertIn("uninsneveruninstall", block)

    def test_the_models_entry_has_no_llm(self):
        module = _assets_module()
        self.assertTrue(module.VOICE_MODEL_FILES)
        llm = [path for path in module.VOICE_MODEL_FILES
               if path.startswith("llm/")]
        self.assertEqual(llm, [], "el LLM de 3 GB no se empaqueta: %r" % llm)
        self.assertIn("kokoro-onnx/kokoro-v1.0.onnx", module.VOICE_MODEL_FILES)
        self.assertIn("parakeet-v3-int8/encoder.int8.onnx",
                      module.VOICE_MODEL_FILES)

    def test_the_installer_is_always_a_single_file(self):
        setup = _section("Setup")
        directives = [line.strip() for line in setup.splitlines()
                      if line.strip() and not line.lstrip().startswith(";")]
        # Con DiskSpanning=yes Inno parte el setup en .exe + .bin y el que baja
        # solo el .exe no puede instalar: pide "el disco".
        self.assertIn("DiskSpanning=no", directives)
        self.assertFalse([line for line in directives
                          if line.startswith("DiskSpanning=")
                          and line != "DiskSpanning=no"])
        self.assertFalse([line for line in directives
                          if line.startswith("DiskSliceSize=")])

    def test_the_output_name_comes_from_a_define(self):
        self.assertIn("OutputBaseFilename={#OutputBase}", _section("Setup"))
        self.assertIn('#define OutputBase "Instant2-Setup"', _script())
        # El release compila dos variantes con nombres distintos.
        self.assertIn("Instant2-Update.exe", _script())

    def test_the_code_section_skips_existing_and_legacy_models(self):
        body = _function("ShouldInstallModel")
        self.assertTrue(body, "falta ShouldInstallModel")
        # Check devuelve True para INSTALAR: "ya esta" va invertido.
        self.assertIn("Result := not (", body)
        self.assertIn("ExpandConstant(ModelsDir)", body)
        self.assertIn("ExpandConstant(LegacyModelsDir)", body)
        self.assertIn("ExtractFileName(CurrentFileName)", body)

    def test_the_installer_never_deletes_legacy_models(self):
        # La app es la que mueve los modelos del slug viejo, y solo borra
        # cuando verificó el destino. El instalador no borra ni mueve nada.
        code = _section("Code")
        self.assertNotIn("DelTree", code)
        self.assertNotIn("ReuseLegacyModels", code)

    def test_the_installer_still_stops_the_daemon(self):
        body = _function("PrepareToInstall")
        self.assertIn("'stop'", body)

    def test_the_installer_still_stops_the_llama_runtime(self):
        body = _function("PrepareToInstall")
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

    def _compile(self, out_dir, extra_defines):
        iscc = _iscc()
        app_dir = os.path.join(os.path.dirname(out_dir), "app")
        models_dir = os.path.join(os.path.dirname(out_dir), "models")
        shutil.rmtree(app_dir, ignore_errors=True)
        shutil.rmtree(models_dir, ignore_errors=True)
        os.makedirs(os.path.join(models_dir, "parakeet-v3-int8"))
        os.makedirs(app_dir)
        with open(os.path.join(app_dir, "Instant2.exe"), "w") as handle:
            handle.write("stub")
        with open(os.path.join(models_dir, "parakeet-v3-int8", "tokens.txt"),
                  "w") as handle:
            handle.write("stub")
        os.makedirs(out_dir, exist_ok=True)
        result = subprocess.run(
            [iscc,
             "/DAppVersion=0.1.8",
             "/DSourceDir=%s" % app_dir,
             "/DIconFile=%s" % ICON,
             "/O%s" % out_dir,
             *extra_defines,
             ISS],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(
            result.returncode, 0,
            "el script de Inno Setup no compila:\n%s" % result.stdout)
        return sorted(os.listdir(out_dir))

    def _check_single_file(self, produced, expected):
        self.assertIn(expected, produced)
        volumes = [name for name in produced if name.endswith(".bin")]
        self.assertEqual(
            volumes, [],
            "el instalador se partió en volúmenes (.bin): %r" % volumes)

    def test_the_full_installer_compiles_to_a_single_file(self):
        iscc = _iscc()
        if not iscc:
            self.skipTest("Inno Setup no esta instalado en esta maquina")
        if not os.path.isfile(ICON):
            self.skipTest("falta el icono %s" % ICON)
        with tempfile.TemporaryDirectory() as work:
            out_dir = os.path.join(work, "out")
            models = os.path.join(work, "models")
            produced = self._compile(out_dir, [
                "/DModelDir=%s" % models,
                "/DOutputBase=Instant2-Setup",
            ])
            self._check_single_file(produced, "Instant2-Setup.exe")

    def test_the_update_installer_compiles_without_models(self):
        iscc = _iscc()
        if not iscc:
            self.skipTest("Inno Setup no esta instalado en esta maquina")
        if not os.path.isfile(ICON):
            self.skipTest("falta el icono %s" % ICON)
        with tempfile.TemporaryDirectory() as work:
            out_dir = os.path.join(work, "out")
            produced = self._compile(out_dir, [
                "/DOutputBase=Instant2-Update",
            ])
            self._check_single_file(produced, "Instant2-Update.exe")


if __name__ == "__main__":
    unittest.main()
