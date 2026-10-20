"""The fork starts its own public product version and app identity."""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from instant2_app import __version__
from instant2_app.identity import APP_COMMAND, APP_NAME, APP_SLUG

CHANGELOG = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "CHANGELOG.md"))


def _changelog_version():
    """La version de la primera seccion publicada del CHANGELOG.

    La seccion de arriba del todo es "[Sin publicar]"; la siguiente es la
    ultima version publicada. Las secciones del historial heredado de Instant
    quedan mas abajo y no cuentan.
    """
    with open(CHANGELOG, encoding="utf-8") as handle:
        text = handle.read()
    for match in re.finditer(r"^## \[(\d+\.\d+\.\d+)\]", text, re.M):
        return match.group(1)
    return None


class ProductIdentityTests(unittest.TestCase):
    def test_instant2_has_independent_name_command_and_slug(self):
        self.assertEqual(APP_NAME, "Instant2")
        self.assertEqual(APP_COMMAND, "instant2")
        self.assertEqual(APP_SLUG, "instant2")

    def test_the_version_is_the_one_published_in_the_changelog(self):
        # La version no se escribe a mano en el test: se compara con el
        # CHANGELOG, que es donde queda registrada la release. Asi un bump de
        # version sin CHANGELOG (o al reves) se ve al correr la suite, en vez
        # de romper el test con un valor hardcodeado que nadie actualiza.
        self.assertEqual(__version__, _changelog_version())

    def test_the_version_is_semantic(self):
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
