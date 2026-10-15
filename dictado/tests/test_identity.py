"""The fork starts its own public product version and app identity."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from instant2_app import __version__
from instant2_app.identity import APP_COMMAND, APP_NAME, APP_SLUG


class ProductIdentityTests(unittest.TestCase):
    def test_instant2_has_independent_name_command_slug_and_version(self):
        self.assertEqual(APP_NAME, "Instant2")
        self.assertEqual(APP_COMMAND, "instant2")
        self.assertEqual(APP_SLUG, "instant2")
        self.assertEqual(__version__, "0.1.4")


if __name__ == "__main__":
    unittest.main()
