"""Dependency diagnostics respect the Python minimum declared by the package."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from instant2_app import deps


class DependencyVersionTests(unittest.TestCase):
    def test_minimum_python_matches_package_requirement(self):
        self.assertEqual(deps.MIN_PYTHON, (3, 12))
        with patch.object(deps.sys, "version_info", (3, 11, 9)), \
                patch.object(deps.sys, "version", "3.11.9 test"):
            result = deps.check()
        self.assertFalse(result["python"]["ok"])
        self.assertIn("3.12+", result["python"]["hint"])

    def test_auto_install_stops_before_pip_on_unsupported_python(self):
        with patch.object(deps, "check", return_value={
                "python": {"ok": False, "hint": "3.11", "auto": False},
                "PySide6": {"ok": False, "hint": "pip install PySide6.",
                            "auto": True}}), \
                patch.object(deps, "_install_one") as install:
            result = deps.ensure(auto=True)
        install.assert_not_called()
        self.assertIn("requiere Python 3.12+", result["PySide6"]["hint"])

    def test_has_net_recognizes_lowercase_proxy(self):
        with patch.dict(os.environ, {"http_proxy": "http://127.0.0.1:8080"}, clear=False):
            self.assertTrue(deps._has_net())


if __name__ == "__main__":
    unittest.main()
