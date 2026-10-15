"""La página web del overlay repite branding.PALETTE y su contrato.

La página se sirve tal cual (sin motor de plantillas), así que los colores se
repiten a mano. Este test es la red que evita que un cambio de marca deje el
overlay con los colores viejos (o con un literal suelto que nadie sabe de
dónde salió). El contrato del bridge también se ancla acá.
"""
import os
import json
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from instant2_app.branding import PALETTE
from instant2_app.overlay_web import compose_page

WEB_PAGE = os.path.join(
    os.path.dirname(__file__), "..", "src", "instant2_app", "web", "overlay.html")

# Únicos colores que pueden escribirse a mano: blanco y negro puros se usan con
# alfa para la luz de canto y la sombra, no como color de marca.
ALLOWED_LITERALS = {"#ffffff", "#000000"}


class WebOverlayPaletteTests(unittest.TestCase):
    def _source(self):
        with open(WEB_PAGE, encoding="utf-8") as handle:
            return handle.read()

    def test_declared_tokens_mirror_python_palette(self):
        source = compose_page(self._source(), "")
        declared = json.loads(re.search(r"const PALETTE = (\{[^;]+\});", source).group(1))
        for key in ("accent", "working", "green", "amber", "red", "text",
                    "muted", "glass", "glass_alt", "line"):
            self.assertIn(key, declared, f"falta el token {key} en overlay.html")
            self.assertEqual(
                declared[key], PALETTE[key].lower(),
                f"overlay.html: {key} debe seguir a PALETTE['{key}']")

    def test_no_color_escapes_the_palette(self):
        found = {value.lower() for value in re.findall(r"#[0-9a-fA-F]{6}", self._source())}
        allowed = {value.lower() for value in PALETTE.values()} | ALLOWED_LITERALS
        self.assertEqual(found - allowed, set(),
                         "overlay.html: colores fuera de la paleta")

    def test_contract_strings_survive(self):
        source = self._source()
        for expected in ("<!--QWEBCHANNEL-->", "window.pushFrame",
                         "bridge.frame.connect", "bridge.event", "Copiado"):
            self.assertIn(expected, source)

    def test_roundrect_polyfill_survives(self):
        source = self._source()
        self.assertIn("typeof C.roundRect", source)


if __name__ == "__main__":
    unittest.main()
