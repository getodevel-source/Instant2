"""El arbol de trabajo tiene que ser el mismo en las tres plataformas.

Sin `.gitattributes`, git deja el arbol de trabajo con CRLF en Windows (por
`core.autocrlf`) y con LF en Linux y macOS: el mismo archivo tiene bytes
distintos en cada sistema y los hashes congelados del benchmark dejan de
coincidir con el archivo (31 errores y 3 fallos en el CI de Linux y macOS).

Las reglas fijadas son:

- `* text=auto eol=lf`: todo el texto sale con LF en el arbol de trabajo, en
  los tres sistemas.
- `*.bat`, `*.cmd`, `*.ps1` y `*.iss` con CRLF: `cmd` deja de encontrar la
  etiqueta `:say` de `scripts/instant2-update.bat` si el archivo tiene LF, y
  los mensajes del helper dejan de escribirse sin que nada lo avise.
- `*.sh` con LF: `bash` no tolera CRLF.

Estas pruebas miran el arbol de trabajo de verdad (no una copia) porque lo
que se quiere garantizar es justamente el estado en disco: el `.bat` con LF
solo se rompe cuando el helper corre.
"""
import hashlib
import json
import os
import subprocess
import unittest


ROOT = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", ".."))
GITATTRIBUTES = os.path.join(ROOT, ".gitattributes")

# Rutas relativas con "/": es como git las escribe en `ls-files --eol`.
CRLF_FILES = (
    "scripts/instant2-update.bat",
    "installer/instant2.iss",
)

# Archivos de texto que tienen que quedar con LF en todas las plataformas.
LF_FILES = (
    "dictado/bench/v2p_cases.json",
    "dictado/bench/v2p_rubric.json",
    "dictado/bench/llm_cases_expanded_2026-10-16.json",
    "dictado/src/instant2_app/gui_lifecycle.py",
    "scripts/install.sh",
)


def _local(relative):
    return os.path.join(ROOT, *relative.split("/"))


def _read(path):
    with open(path, "rb") as handle:
        return handle.read()


def _canonical(data):
    """Los bytes canonicos del repositorio: el mismo texto con LF."""
    return data.replace(b"\r\n", b"\n")


def _git(*args):
    return subprocess.run(
        ("git",) + args, cwd=ROOT, capture_output=True, text=True)


class LineEndingRulesTests(unittest.TestCase):
    """El arbol de trabajo lleva los finales de linea que fijan las reglas."""

    def test_the_gitattributes_file_pins_the_endings(self):
        self.assertTrue(os.path.isfile(GITATTRIBUTES),
                        "falta .gitattributes: los finales de linea dependen "
                        "de la maquina")
        rules = _read(GITATTRIBUTES).decode("utf-8")
        for expected in ("* text=auto eol=lf",
                         "*.bat text eol=crlf",
                         "*.cmd text eol=crlf",
                         "*.ps1 text eol=crlf",
                         "*.iss text eol=crlf",
                         "*.sh text eol=lf"):
            with self.subTest(rule=expected):
                self.assertIn(expected, rules)

    def test_the_windows_scripts_keep_crlf_in_the_working_tree(self):
        """`cmd` necesita CRLF: con LF no encuentra la etiqueta `:say`."""
        for relative in CRLF_FILES:
            with self.subTest(file=relative):
                data = _read(_local(relative))
                self.assertIn(b"\r\n", data,
                              f"{relative} tiene que quedar con CRLF")
                self.assertNotIn(b"\n", data.replace(b"\r\n", b""))

    def test_the_text_files_are_checked_out_with_lf(self):
        """Los archivos de texto tienen que ser los mismos en los tres SO."""
        for relative in LF_FILES:
            with self.subTest(file=relative):
                data = _read(_local(relative))
                self.assertNotIn(b"\r\n", data,
                                 f"{relative} tiene que quedar con LF")

    def test_the_llm_corpus_hash_and_the_preregistration_agree(self):
        """El `.sha256` del corpus LLM y el pre-registro dicen lo mismo."""
        bench = _local("dictado/bench")
        corpus_name = "llm_cases_expanded_2026-10-16.json"
        canonical = hashlib.sha256(
            _canonical(_read(os.path.join(bench, corpus_name)))).hexdigest()
        with open(os.path.join(bench, "llm_cases_expanded_2026-10-16.sha256"),
                  encoding="utf-8") as handle:
            declared = handle.read().split()[0]
        self.assertEqual(declared, canonical)
        prereg_path = os.path.join(bench, "correction-eval-prereg-2026-10-16.json")
        with open(prereg_path, encoding="utf-8") as handle:
            prereg = json.load(handle)
        self.assertEqual(prereg["corpus"], corpus_name)
        self.assertEqual(prereg["corpus_sha256"], canonical)


class GitEolTests(unittest.TestCase):
    """`git ls-files --eol` tiene que estar de acuerdo con el disco."""
    @classmethod
    def setUpClass(cls):
        cls.available = (_git("rev-parse", "--is-inside-work-tree").stdout
                         .strip() == "true")

    def setUp(self):
        if not self.available:
            self.skipTest("no hay un checkout de git aqui")

    def _eol_rows(self):
        result = _git("ls-files", "--eol")
        rows = {}
        for line in result.stdout.splitlines():
            info, _, path = line.partition("\t")
            fields = info.split()
            if len(fields) < 2:
                continue
            # fields[0] es el indice ("i/lf") y fields[1] el arbol de trabajo
            # ("w/crlf"): de ahi sale el final de linea que se ve en disco.
            rows[path.strip()] = fields[1].split("/", 1)[-1]
        return rows

    def test_git_reports_crlf_for_the_windows_scripts(self):
        rows = self._eol_rows()
        for relative in CRLF_FILES:
            with self.subTest(file=relative):
                self.assertIn(relative, rows)
                self.assertEqual(rows[relative], "crlf")

    def test_git_reports_lf_for_the_text_files(self):
        rows = self._eol_rows()
        for relative in LF_FILES:
            with self.subTest(file=relative):
                self.assertIn(relative, rows)
                self.assertEqual(rows[relative], "lf")

    def test_the_attributes_match_the_rules(self):
        for relative in CRLF_FILES + LF_FILES:
            with self.subTest(file=relative):
                result = _git("check-attr", "eol", "--", relative)
                self.assertIn("crlf" if relative in CRLF_FILES else "lf",
                              result.stdout)


if __name__ == "__main__":
    unittest.main()
