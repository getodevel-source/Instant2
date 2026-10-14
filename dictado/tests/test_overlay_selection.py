"""Selección del renderer del overlay: web primero, Tk de respaldo.

Corre sin Qt ni Tk reales: los renderers se reemplazan por dobles, así que
verifica la decisión (y no el dibujo, que cubren test_overlay_web,
test_panel_page y test_regression).
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from instant2_app import overlay as overlay_module
from instant2_app import overlay_web as overlay_web_module


class OverlayRendererSelectionTests(unittest.TestCase):
    def test_prefers_web_everywhere(self):
        with patch.object(overlay_web_module, "_WebOverlay") as web, \
                patch.object(overlay_module, "_TkOverlay") as tk:
            overlay = overlay_module.Overlay("F10", style="orbital")
        web.assert_called_once_with("F10", style="orbital")
        tk.assert_not_called()
        self.assertIs(overlay._renderer, web.return_value)

    def test_falls_back_to_tk_when_web_cannot_open(self):
        with patch.object(overlay_web_module, "_WebOverlay",
                          side_effect=RuntimeError("no webengine")), \
                patch.object(overlay_module, "_TkOverlay") as tk, \
                self.assertLogs("instant2", level="WARNING") as logs:
            overlay = overlay_module.Overlay("F9")
        tk.assert_called_once_with("F9")
        self.assertIs(overlay._renderer, tk.return_value)
        self.assertIn("overlay web no abri", logs.output[0])

    def test_fallback_also_covers_a_missing_pyside6(self):
        with patch.object(overlay_web_module, "_WebOverlay",
                          side_effect=ImportError("No module named PySide6")), \
                patch.object(overlay_module, "_TkOverlay") as tk, \
                self.assertLogs("instant2", level="WARNING"):
            overlay = overlay_module.Overlay("F9", style="classic")
        self.assertIs(overlay._renderer, tk.return_value)


if __name__ == "__main__":
    unittest.main()
