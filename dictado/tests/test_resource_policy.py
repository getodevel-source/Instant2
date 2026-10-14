"""Resource residency profiles shared by ASR, LLM and Kokoro."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from instant2_app.resource_policy import idle_unload_seconds, resource_mode


class ResourcePolicyTests(unittest.TestCase):
    def test_profiles_select_expected_residency_window(self):
        self.assertEqual(idle_unload_seconds({"resource_mode": "speed"}), 0)
        self.assertEqual(idle_unload_seconds({"resource_mode": "balanced"}), 300)
        self.assertEqual(idle_unload_seconds({"resource_mode": "memory"}), 90)

    def test_missing_or_invalid_profile_uses_balanced_default(self):
        self.assertEqual(resource_mode({}), "balanced")
        self.assertEqual(idle_unload_seconds({"resource_mode": "invalid"}), 300)


if __name__ == "__main__":
    unittest.main()
