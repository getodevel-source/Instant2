"""Atomic user-config persistence without touching real user data."""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from instant2_app import config


class ConfigPersistenceTests(unittest.TestCase):
    def test_voice_detection_cli_uses_public_choices_and_keeps_old_aliases(self):
        self.assertEqual(config.parse_vad_mode("liviana"), "silero")
        self.assertEqual(config.parse_vad_mode("más precisa"), "ten")
        self.assertEqual(config.parse_vad_mode("silero"), "silero")
        self.assertEqual(config.parse_vad_mode("ten"), "ten")
        with self.assertRaisesRegex(ValueError, "liviana.*más precisa"):
            config.parse_vad_mode("otro")

    def test_new_configuration_manages_local_llm_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            with patch("instant2_app.config.config_path", return_value=path), \
                    patch.dict(os.environ, {}, clear=True):
                cfg = config.load()
        self.assertTrue(cfg["llm_managed_enabled"])
        self.assertEqual(cfg["resource_mode"], "balanced")

    def test_offline_bundle_sets_local_llm_defaults_once(self):
        with tempfile.TemporaryDirectory() as directory:
            app_dir = os.path.join(directory, "Instant2")
            runtime = os.path.join(app_dir, "llm-runtime")
            data_dir = os.path.join(directory, "models")
            os.makedirs(runtime)
            os.makedirs(os.path.join(data_dir, "llm"))
            server = os.path.join(runtime, "llama-server.exe")
            model = os.path.join(data_dir, "llm",
                                 "Qwen_Qwen3.5-4B-Q4_K_M.gguf")
            open(server, "wb").close()
            open(model, "wb").close()
            config_path = os.path.join(directory, "config.json")
            executable = os.path.join(app_dir, "Instant2.exe")

            with (patch("instant2_app.config.config_path", return_value=config_path),
                  patch("instant2_app.paths.resolve_data_dir", return_value=data_dir),
                  patch.object(config.sys, "platform", "win32"),
                  patch.object(config.sys, "executable", executable),
                  patch.object(config.sys, "frozen", True, create=True)):
                cfg = config.load()
                self.assertEqual(cfg["llm_url"], "http://127.0.0.1:8080/v1")
                self.assertEqual(cfg["llm_model"], "qwen3.5:4b-q4_K_M")
                self.assertEqual(cfg["llm_server_path"], server)
                self.assertEqual(cfg["llm_model_path"], model)
                self.assertTrue(cfg["llm_bundle_initialized"])

                # A user can clear the endpoint later without it returning on
                # every launch just because the packaged files still exist.
                with open(config_path, encoding="utf-8") as handle:
                    disk = json.load(handle)
                disk["llm_url"] = ""
                with open(config_path, "w", encoding="utf-8") as handle:
                    json.dump(disk, handle)
                cfg = config.load()
                self.assertEqual(cfg["llm_url"], "")

    def test_offline_bundle_does_not_replace_user_llm_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            app_dir = os.path.join(directory, "Instant2")
            runtime = os.path.join(app_dir, "llm-runtime")
            data_dir = os.path.join(directory, "models")
            os.makedirs(runtime)
            os.makedirs(os.path.join(data_dir, "llm"))
            open(os.path.join(runtime, "llama-server.exe"), "wb").close()
            open(os.path.join(data_dir, "llm",
                              "Qwen_Qwen3.5-4B-Q4_K_M.gguf"), "wb").close()
            path = os.path.join(directory, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"llm_url": "https://llm.example/v1",
                           "llm_model": "my-remote-model"}, handle)
            executable = os.path.join(app_dir, "Instant2.exe")

            with (patch("instant2_app.config.config_path", return_value=path),
                  patch("instant2_app.paths.resolve_data_dir", return_value=data_dir),
                  patch.object(config.sys, "platform", "win32"),
                  patch.object(config.sys, "executable", executable),
                  patch.object(config.sys, "frozen", True, create=True)):
                cfg = config.load()

            self.assertEqual(cfg["llm_url"], "https://llm.example/v1")
            self.assertEqual(cfg["llm_model"], "my-remote-model")
            self.assertTrue(cfg["llm_bundle_initialized"])

    def test_offline_bundle_migrates_the_previous_managed_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            app_dir = os.path.join(directory, "Instant2")
            data_dir = os.path.join(directory, "models")
            runtime = os.path.join(app_dir, "llm-runtime")
            os.makedirs(runtime)
            os.makedirs(os.path.join(data_dir, "llm"))
            server = os.path.join(runtime, "llama-server.exe")
            model = os.path.join(data_dir, "llm",
                                 "Qwen_Qwen3.5-4B-Q4_K_M.gguf")
            open(server, "wb").close()
            open(model, "wb").close()
            old_model = os.path.join(directory, "PreviousModels", "llm",
                                     "Qwen_Qwen3.5-4B-Q4_K_M.gguf")
            old_server = os.path.join(
                directory, "AppData", "Local", "instant2", "llm-runtime",
                "llama", "llama-server.exe")
            config_path = os.path.join(directory, "config.json")
            with open(config_path, "w", encoding="utf-8") as handle:
                json.dump({"llm_url": "http://127.0.0.1:8080/v1",
                           "llm_model": "qwen3.5:4b-q4_K_M",
                           "llm_server_path": old_server,
                           "llm_model_path": old_model}, handle)
            executable = os.path.join(app_dir, "Instant2.exe")

            with (patch("instant2_app.config.config_path", return_value=config_path),
                  patch("instant2_app.paths.resolve_data_dir", return_value=data_dir),
                  patch.object(config.sys, "platform", "win32"),
                  patch.object(config.sys, "executable", executable),
                  patch.object(config.sys, "frozen", True, create=True)):
                cfg = config.load()

            self.assertEqual(cfg["llm_server_path"], server)
            self.assertEqual(cfg["llm_model_path"], model)
            self.assertEqual(cfg["llm_url"], "http://127.0.0.1:8080/v1")

    def test_resource_mode_rejects_unknown_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"resource_mode": "unexpected"}, handle)
            with patch("instant2_app.config.config_path", return_value=path), \
                    patch.dict(os.environ, {}, clear=True):
                cfg = config.load()
        self.assertEqual(cfg["resource_mode"], "balanced")

    def test_save_replaces_config_and_keeps_previous_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('{"key": "old"}')

            with patch("instant2_app.config.config_path", return_value=path):
                config.save({**config.DEFAULTS, "key": "f10"})

            with open(path, encoding="utf-8") as handle:
                current = json.load(handle)
            with open(path + ".bak", encoding="utf-8") as handle:
                backup = json.load(handle)
            self.assertEqual(current["key"], "f10")
            self.assertEqual(backup, {"key": "old"})
            self.assertCountEqual(os.listdir(directory),
                                  ["config.json", "config.json.bak"])

    def test_failed_serialization_preserves_config_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            previous = '{"key": "old"}'
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(previous)

            with patch("instant2_app.config.config_path", return_value=path), \
                    patch("instant2_app.config.json.dump",
                          side_effect=TypeError("cannot serialize")):
                with self.assertRaisesRegex(TypeError, "cannot serialize"):
                    config.save({**config.DEFAULTS, "key": "f10"})

            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), previous)
            self.assertEqual(os.listdir(directory), ["config.json"])

    def test_corrupt_config_recovers_from_previous_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{ truncated")
            with open(path + ".bak", "w", encoding="utf-8") as handle:
                json.dump({**config.DEFAULTS, "key": "f10"}, handle)

            with patch("instant2_app.config.config_path", return_value=path), \
                    patch.dict(os.environ, {}, clear=True), \
                    self.assertLogs("instant2", level="ERROR") as logs:
                restored = config.load()

            self.assertIn("config corrupta", logs.output[0])
            self.assertEqual(restored["key"], "f10")
            with open(path, encoding="utf-8") as handle:
                repaired = json.load(handle)
            self.assertEqual(repaired["key"], "f10")

    def test_numeric_types_and_sound_variants_parsed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"threads": "invalid", "max_seg": "bad", "sound": False}, handle)
            with patch("instant2_app.config.config_path", return_value=path), \
                    patch.dict(os.environ, {"INSTANT2_SOUND": "si"}, clear=True):
                cfg = config.load()
            self.assertEqual(cfg["threads"], config.DEFAULTS["threads"])
            self.assertEqual(cfg["max_seg"], config.DEFAULTS["max_seg"])
            self.assertTrue(cfg["sound"])

    def test_llm_token_and_update_mode_env(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({}, handle)
            with patch("instant2_app.config.config_path", return_value=path), \
                    patch.dict(os.environ, {"INSTANT2_LLM_TOKEN": "tok-env",
                                            "INSTANT2_LLM_MODEL": "qwen3.5:2b-q4_K_M",
                                            "INSTANT2_LLM_NORMAL": "false",
                                            "INSTANT2_UPDATE": "auto"}, clear=True):
                cfg = config.load()
            self.assertEqual(cfg["llm_token"], "tok-env")
            self.assertEqual(cfg["llm_model"], "qwen3.5:2b-q4_K_M")
            self.assertFalse(cfg["llm_normal_enabled"])
            self.assertEqual(cfg["update_mode"], "auto")
            with patch("instant2_app.config.config_path", return_value=path), \
                    patch.dict(os.environ, {"INSTANT2_UPDATE": "xxx"}, clear=True):
                cfg = config.load()
            self.assertEqual(cfg["update_mode"], "notify")



    def test_local_llm_runtime_settings_are_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"llm_managed_enabled": True,
                           "llm_server_path": "  C:/tools/llama-server.exe  ",
                           "llm_model_path": "C:/models/qwen.gguf",
                           "llm_acceleration": "broken"}, handle)
            with patch("instant2_app.config.config_path", return_value=path), \
                    patch.dict(os.environ, {}, clear=True):
                cfg = config.load()
            self.assertTrue(cfg["llm_managed_enabled"])
            self.assertEqual(cfg["llm_server_path"], "C:/tools/llama-server.exe")
            self.assertEqual(cfg["llm_model_path"], "C:/models/qwen.gguf")
            self.assertEqual(cfg["llm_acceleration"], "auto")
            with patch("instant2_app.config.config_path", return_value=path), \
                    patch.dict(os.environ, {}, clear=True):
                saved = config.save(cfg)
                with open(saved, encoding="utf-8") as handle:
                    disk = json.load(handle)
            self.assertTrue(disk["llm_managed_enabled"])
            self.assertEqual(disk["llm_server_path"], "C:/tools/llama-server.exe")
            self.assertEqual(disk["llm_acceleration"], "auto")

    def test_llm_token_persists_and_stays_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            with patch("instant2_app.config.config_path", return_value=path):
                saved = config.save({**config.DEFAULTS, "llm_token": "tok-1",
                                     "llm_model": "qwen3.5:2b-q4_K_M",
                                     "llm_normal_enabled": False})
            with open(saved, encoding="utf-8") as handle:
                disk = json.load(handle)
            self.assertEqual(disk["llm_token"], "tok-1")
            self.assertEqual(disk["llm_model"], "qwen3.5:2b-q4_K_M")
            self.assertIs(disk["llm_normal_enabled"], False)
            with patch("instant2_app.config.config_path", return_value=path):
                cfg = config.load()
            self.assertEqual(cfg["llm_token"], "tok-1")
            self.assertEqual(cfg["llm_model"], "qwen3.5:2b-q4_K_M")
            self.assertIs(cfg["llm_normal_enabled"], False)

if __name__ == "__main__":
    unittest.main()
