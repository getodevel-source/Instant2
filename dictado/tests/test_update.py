"""Actualizador: versiones, sidecar y ciclo descarga->verifica sin red."""
import hashlib
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolation  # noqa: F401  (aisla el estado real del usuario)

from instant2_app import update as update_module


class VersionTests(unittest.TestCase):
    def test_normalize_tolerates_v_prefix(self):
        self.assertEqual(update_module.normalize("v0.1.0"), "0.1.0")
        self.assertEqual(update_module.normalize("  V2.0  "), "2.0")
        self.assertEqual(update_module.normalize("instant2-v0.1.1"), "0.1.1")
        self.assertEqual(update_module.normalize(" Instant2-V0.1.1 "), "0.1.1")

    def test_is_newer_compares_numbers_not_strings(self):
        self.assertTrue(update_module.is_newer("0.1.0", "0.0.9"))
        self.assertTrue(update_module.is_newer("0.10.0", "0.9.9"))
        self.assertFalse(update_module.is_newer("0.1.0", "0.1.0"))
        self.assertFalse(update_module.is_newer("0.1.0", "0.2.0"))

    def test_check_detects_update_with_mocked_api(self):
        payload = {"tag_name": "instant2-v9.9.9", "body": "notas",
                   "assets": [{"name": "Instant2.exe",
                               "browser_download_url": "https://x/Instant2.exe"}]}
        with patch.object(update_module, "fetch_json", return_value=payload), \
                patch.object(update_module, "current_version", return_value="0.1.0"), \
                patch.object(update_module.sys, "platform", "win32"):
            info = update_module.check()
        self.assertTrue(info["update"])
        self.assertEqual(info["latest"], "9.9.9")
        self.assertEqual(info["asset_url"], "https://x/Instant2.exe")

    def test_check_up_to_date(self):
        payload = {"tag_name": "v0.1.0", "body": "",
                   "assets": [{"name": "Instant2.exe",
                               "browser_download_url": "https://x/Instant2.exe"}]}
        with patch.object(update_module, "fetch_json", return_value=payload), \
                patch.object(update_module, "current_version", return_value="0.1.0"), \
                patch.object(update_module.sys, "platform", "win32"):
            info = update_module.check()
        self.assertFalse(info["update"])

    def test_missing_platform_asset_is_loud(self):
        payload = {"tag_name": "v9.9.9", "body": "", "assets": []}
        with patch.object(update_module, "fetch_json", return_value=payload), \
                patch.object(update_module, "current_version", return_value="0.1.0"), \
                patch.object(update_module.sys, "platform", "win32"):
            with self.assertRaises(LookupError):
                update_module.check()

    def test_api_without_version_raises_instead_of_up_to_date(self):
        with patch.object(update_module, "fetch_json", return_value={}), \
                patch.object(update_module, "current_version", return_value="0.1.0"), \
                patch.object(update_module.sys, "platform", "win32"):
            with self.assertRaises(RuntimeError):
                update_module.check()

    def test_clean_notes_strips_markdown_prefix(self):
        notes = "# Título\n\n- **Cambios** varios\n\nTexto"
        cleaned = update_module.clean_notes(notes)
        self.assertIn("Título", cleaned)
        self.assertNotIn("#", cleaned.splitlines()[0])
        self.assertLessEqual(len(cleaned.splitlines()), 8)


class WindowsUpdaterScriptTests(unittest.TestCase):
    def test_portable_restart_resets_pyinstaller_onefile_environment(self):
        script = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "..", "scripts",
            "instant2-update.bat"))
        with open(script, encoding="utf-8") as handle:
            content = handle.read().casefold()

        reset = content.index('set "pyinstaller_reset_environment=1"')
        # El helper anuncia cada paso con `set "MSG=..."` y lo escribe en el
        # log desde :say; el ancla es el mensaje, no el `echo`.
        stop = content.index('set "msg=[2/6] frenando daemon..."')
        daemon = content.index('start "" "%dst%" run')
        panel = content.index('start "" "%dst%" setup')
        self.assertLess(reset, stop)
        self.assertLess(reset, daemon)
        self.assertLess(reset, panel)


class InstallModeTests(unittest.TestCase):
    def test_source_mode_when_not_frozen(self):
        self.assertEqual(update_module.install_mode(), "source")
        with patch.object(update_module.sys, "platform", "win32"):
            self.assertEqual(update_module.platform_asset("source"),
                             "Instant2.exe")

    def test_installed_mode_requires_uninstaller_next_to_exe(self):
        with tempfile.TemporaryDirectory() as directory:
            exe = os.path.join(directory, "Instant2.exe")
            open(exe, "wb").close()
            with patch.object(update_module.sys, "frozen", True, create=True), \
                    patch.object(update_module.sys, "executable", exe), \
                    patch.object(update_module.sys, "platform", "win32"):
                self.assertEqual(update_module.install_mode(), "portable")
                self.assertEqual(update_module.platform_asset(),
                                 "Instant2.exe")
                open(os.path.join(directory, update_module.UNINSTALLER),
                     "wb").close()
                self.assertEqual(update_module.install_mode(), "installed")
                self.assertEqual(update_module.platform_asset(),
                                 "Instant2-Setup.exe")

    def test_check_uses_the_installer_asset_when_installed(self):
        payload = {"tag_name": "v9.9.9", "body": "",
                   "assets": [{"name": "Instant2-Setup.exe",
                               "browser_download_url": "https://x/Instant2-Setup.exe"}]}
        with tempfile.TemporaryDirectory() as directory:
            exe = os.path.join(directory, "Instant2.exe")
            open(exe, "wb").close()
            open(os.path.join(directory, update_module.UNINSTALLER),
                 "wb").close()
            with patch.object(update_module.sys, "frozen", True, create=True), \
                    patch.object(update_module.sys, "executable", exe), \
                    patch.object(update_module.sys, "platform", "win32"), \
                    patch.object(update_module, "fetch_json", return_value=payload), \
                    patch.object(update_module, "current_version",
                                 return_value="0.1.0"):
                info = update_module.check()
        self.assertEqual(info["asset_url"], "https://x/Instant2-Setup.exe")

    def test_installed_update_lists_all_setup_volumes_in_order(self):
        payload = {"tag_name": "v9.9.9", "body": "", "assets": [
            {"name": "Instant2-Setup-2.bin",
             "browser_download_url": "https://x/Instant2-Setup-2.bin",
             "size": 20},
            {"name": "Instant2-Setup.exe",
             "browser_download_url": "https://x/Instant2-Setup.exe",
             "size": 10},
            {"name": "Instant2-Setup-1.bin",
             "browser_download_url": "https://x/Instant2-Setup-1.bin",
             "size": 15},
            {"name": "Instant2-Setup.exe.sha256",
             "browser_download_url": "https://x/Instant2-Setup.exe.sha256",
             "size": 80},
        ]}
        with tempfile.TemporaryDirectory() as directory:
            exe = os.path.join(directory, "Instant2.exe")
            open(exe, "wb").close()
            open(os.path.join(directory, update_module.UNINSTALLER), "wb").close()
            with patch.object(update_module.sys, "frozen", True, create=True), \
                    patch.object(update_module.sys, "executable", exe), \
                    patch.object(update_module.sys, "platform", "win32"), \
                    patch.object(update_module, "fetch_json", return_value=payload), \
                    patch.object(update_module, "current_version", return_value="0.1.0"):
                info = update_module.check()
        self.assertEqual([item["name"] for item in info["asset_files"]], [
            "Instant2-Setup.exe", "Instant2-Setup-1.bin", "Instant2-Setup-2.bin"])
        self.assertEqual([item["size"] for item in info["asset_files"]], [10, 15, 20])

    def test_installed_update_rejects_missing_setup_volume(self):
        with self.assertRaisesRegex(ValueError, "partes incompletas"):
            update_module._installer_files({
                "Instant2-Setup.exe": "https://x/Instant2-Setup.exe",
                "Instant2-Setup-1.bin": "https://x/Instant2-Setup-1.bin",
                "Instant2-Setup-3.bin": "https://x/Instant2-Setup-3.bin",
            })

    def test_download_release_package_verifies_all_parts_in_same_directory(self):
        bodies = {
            "https://x/Instant2-Setup.exe": b"setup",
            "https://x/Instant2-Setup-1.bin": b"disk-one",
        }
        hashes = {url: hashlib.sha256(body).hexdigest()
                  for url, body in bodies.items()}
        info = {"asset": "Instant2-Setup.exe", "asset_url":
                "https://x/Instant2-Setup.exe", "asset_files": [
                    {"name": "Instant2-Setup.exe",
                     "url": "https://x/Instant2-Setup.exe", "size": 5},
                    {"name": "Instant2-Setup-1.bin",
                     "url": "https://x/Instant2-Setup-1.bin", "size": 8},
                ]}
        progress = []

        def fake_download(url, target, expected_sha256=None, progress=None):
            body = bodies[url]
            self.assertEqual(expected_sha256, hashes[url])
            with open(target, "wb") as handle:
                handle.write(body)
            if progress:
                progress(len(body), len(body))

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(update_module, "fetch_expected_sha256",
                             side_effect=lambda url: hashes[url]), \
                patch.object(update_module, "download", side_effect=fake_download):
            main, expected = update_module.download_release_package(
                info, directory, progress=lambda done, total: progress.append((done, total)))
            self.assertEqual(os.path.basename(main), "Instant2-Setup.exe")
            self.assertEqual(expected, hashes["https://x/Instant2-Setup.exe"])
            self.assertTrue(os.path.isfile(os.path.join(directory,
                                                        "Instant2-Setup-1.bin")))
            self.assertIn((13, 13), progress)

    def test_download_release_package_rejects_path_traversal(self):
        info = {"asset": "Instant2-Setup.exe", "asset_url": "https://x/setup.exe",
                "asset_files": [
                    {"name": "Instant2-Setup.exe", "url": "https://x/setup.exe",
                     "size": 5},
                    {"name": "..\\outside.bin", "url": "https://x/outside.bin",
                     "size": 1},
                ]}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(update_module, "fetch_expected_sha256") as fetch:
            with self.assertRaisesRegex(ValueError, "ruta de asset inválida"):
                update_module.download_release_package(info, directory)
            fetch.assert_not_called()

    def test_frozen_unix_is_portable_with_its_own_asset(self):
        with patch.object(update_module.sys, "frozen", True, create=True), \
                patch.object(update_module.sys, "executable",
                             "/opt/instant2/instant2"), \
                patch.object(update_module.sys, "platform", "linux"):
            self.assertEqual(update_module.install_mode(), "portable")
            self.assertEqual(update_module.platform_asset(), "instant2-linux")


class ApplyUpdateTests(unittest.TestCase):
    def _expected(self, path):
        import hashlib
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    def test_apply_binary_update_replaces_the_target(self):
        with tempfile.TemporaryDirectory() as directory:
            downloaded = os.path.join(directory, "instant2-linux.download")
            target = os.path.join(directory, "instant2")
            with open(downloaded, "wb") as handle:
                handle.write(b"nuevo")
            with open(target, "wb") as handle:
                handle.write(b"viejo")
            with patch.object(update_module.sys, "platform", "linux"):
                applied = update_module.apply_binary_update(
                    downloaded, target,
                    expected_sha256=self._expected(downloaded))
            self.assertEqual(applied, target)
            with open(target, "rb") as handle:
                self.assertEqual(handle.read(), b"nuevo")
            self.assertFalse(os.path.isfile(downloaded))

    def test_binary_is_staged_beside_target_before_atomic_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            target_dir = os.path.join(directory, "mounted-app")
            os.makedirs(target_dir)
            downloaded = os.path.join(directory, "instant2-linux.download")
            target = os.path.join(target_dir, "instant2")
            with open(downloaded, "wb") as handle:
                handle.write(b"nuevo")
            with open(target, "wb") as handle:
                handle.write(b"viejo")
            replace = os.replace
            staged_from = []

            def atomic_replace(source, destination):
                staged_from.append(source)
                self.assertEqual(os.path.dirname(source), os.path.dirname(destination))
                replace(source, destination)

            with patch.object(update_module.sys, "platform", "linux"), \
                    patch.object(update_module.os, "replace", side_effect=atomic_replace):
                update_module.apply_binary_update(
                    downloaded, target,
                    expected_sha256=self._expected(downloaded))

            self.assertEqual(len(staged_from), 1)
            with open(target, "rb") as handle:
                self.assertEqual(handle.read(), b"nuevo")
            self.assertFalse(os.path.isfile(downloaded))

    def test_apply_binary_update_refuses_on_windows(self):
        with patch.object(update_module.sys, "platform", "win32"):
            with self.assertRaises(RuntimeError):
                update_module.apply_binary_update(
                    "descarga", "destino", expected_sha256="a" * 64)

    @unittest.skipIf(os.name == "nt",
                     "la semántica de reemplazo en caliente es de POSIX")
    def test_replace_while_running_keeps_the_old_binary_alive(self):
        """El reemplazo en caliente es seguro: el proceso vivo sigue con el inodo viejo."""
        with tempfile.TemporaryDirectory() as directory:
            downloaded = os.path.join(directory, "nuevo")
            target = os.path.join(directory, "binario")
            with open(downloaded, "wb") as handle:
                handle.write(b"version nueva")
            with open(target, "wb") as handle:
                handle.write(b"version vieja")
            with open(target, "rb") as running:
                update_module.apply_binary_update(
                    downloaded, target,
                    expected_sha256=self._expected(downloaded))
                self.assertEqual(running.read(), b"version vieja")
            with open(target, "rb") as handle:
                self.assertEqual(handle.read(), b"version nueva")

    def test_apply_binary_update_blocks_source_mode_unless_allowed(self):
        with patch.object(update_module.sys, "platform", "linux"), \
                patch("instant2_app.update.install_mode", return_value="source"):
            with self.assertRaisesRegex(RuntimeError, "instalación source"):
                update_module.apply_binary_update("descarga")
            # allow_source solo no alcanza: sin target explícito fuera del
            # intérprete, nunca se sobrescribe sys.executable.
            with self.assertRaisesRegex(RuntimeError, "instalación source"):
                update_module.apply_binary_update(
                    "descarga", allow_source=True, expected_sha256="a" * 64)
            # Con target explícito fuera del prefijo, pasa el guard
            with tempfile.TemporaryDirectory() as directory, \
                    patch.object(update_module.sys, "prefix", "/otro/prefix"), \
                    patch.object(update_module.sys, "executable", "/otro/prefix/bin/python"):
                downloaded = os.path.join(directory, "instant2-linux.download")
                target = os.path.join(directory, "instant2")
                with open(downloaded, "wb") as handle:
                    handle.write(b"nuevo")
                with open(target, "wb") as handle:
                    handle.write(b"viejo")
                applied = update_module.apply_binary_update(
                    downloaded, target, allow_source=True,
                    expected_sha256=self._expected(downloaded))
                self.assertEqual(applied, target)
                with open(target, "rb") as handle:
                    self.assertEqual(handle.read(), b"nuevo")
                self.assertFalse(os.path.isfile(downloaded))

class ApplyHardeningTests(unittest.TestCase):
    def _write(self, directory, name, body):
        path = os.path.join(directory, name)
        with open(path, "wb") as handle:
            handle.write(body)
        digest = hashlib.sha256(body).hexdigest()
        return path, digest

    def test_is_newer_parse_failure_is_not_an_update(self):
        self.assertFalse(update_module.is_newer("raro!!", "0.1.0"))
        self.assertFalse(update_module.is_newer("", "0.1.0"))

    def test_check_rejects_non_semver_tag(self):
        payload = {"tag_name": "v0.1-beta", "body": "",
                   "assets": [{"name": "Instant2.exe",
                               "browser_download_url": "https://x/Instant2.exe"}]}
        with patch.object(update_module, "fetch_json", return_value=payload), \
                patch.object(update_module, "current_version", return_value="0.1.0"), \
                patch.object(update_module.sys, "platform", "win32"):
            with self.assertRaisesRegex(RuntimeError, "semver"):
                update_module.check()

    def test_check_older_release_is_not_an_update_even_with_asset(self):
        payload = {"tag_name": "v0.0.1", "body": "",
                   "assets": [{"name": "Instant2.exe",
                               "browser_download_url": "https://x/Instant2.exe"}]}
        with patch.object(update_module, "fetch_json", return_value=payload), \
                patch.object(update_module, "current_version", return_value="0.2.0"), \
                patch.object(update_module.sys, "platform", "win32"):
            info = update_module.check()
        self.assertFalse(info["update"])
        self.assertEqual(info["latest"], "0.0.1")

    def test_apply_requires_expected_sha256(self):
        with tempfile.TemporaryDirectory() as directory:
            downloaded, _digest = self._write(directory, "nuevo", b"nuevo")
            target, _old = self._write(directory, "bin", b"viejo")
            with patch.object(update_module.sys, "platform", "linux"):
                with self.assertRaisesRegex(RuntimeError, "SHA256 esperado"):
                    update_module.apply_binary_update(downloaded, target)
                with self.assertRaisesRegex(RuntimeError, "SHA256 esperado"):
                    update_module.apply_binary_update(
                        downloaded, target, expected_sha256="corto")
            with open(target, "rb") as handle:
                self.assertEqual(handle.read(), b"viejo")

    @unittest.skipIf(os.name == "nt",
                     "el health-check de ejecutable es de POSIX")
    def test_apply_rehashes_before_replace_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            downloaded, digest = self._write(directory, "nuevo", b"nuevo")
            target, _old = self._write(directory, "bin", b"viejo")
            os.chmod(target, 0o755)
            with patch.object(update_module.sys, "platform", "linux"):
                applied = update_module.apply_binary_update(
                    downloaded, target, expected_sha256=digest)
            self.assertEqual(applied, target)
            with open(target, "rb") as handle:
                self.assertEqual(handle.read(), b"nuevo")
            with open(target + ".bak", "rb") as handle:
                self.assertEqual(handle.read(), b"viejo")
            self.assertFalse(os.path.isfile(downloaded))

    def test_apply_detects_tamper_between_download_and_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            downloaded, digest = self._write(directory, "nuevo", b"nuevo")
            target, _old = self._write(directory, "bin", b"viejo")
            real_sha = update_module._sha256_of
            with patch.object(update_module, "_sha256_of",
                              return_value="0" * 64), \
                    patch.object(update_module.sys, "platform", "linux"):
                with self.assertRaisesRegex(ValueError, "TOCTOU|entre la descarga"):
                    update_module.apply_binary_update(
                        downloaded, target, expected_sha256=digest)
            self.assertIsNotNone(real_sha)
            with open(target, "rb") as handle:
                self.assertEqual(handle.read(), b"viejo")

    def test_apply_restores_backup_when_health_check_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            downloaded, digest = self._write(directory, "nuevo", b"nuevo")
            target, _old = self._write(directory, "bin", b"viejo")
            with patch.object(update_module.sys, "platform", "linux"), \
                    patch.object(update_module, "_posix_health_ok",
                                 return_value=False):
                with self.assertRaisesRegex(RuntimeError, "health-check"):
                    update_module.apply_binary_update(
                        downloaded, target, expected_sha256=digest)
            with open(target, "rb") as handle:
                self.assertEqual(handle.read(), b"viejo")

    @unittest.skipIf(os.name == "nt", "chmod POSIX")
    def test_private_dirs_and_config_files_are_restricted(self):
        from instant2_app import config as config_module
        from instant2_app import paths as paths_module
        with tempfile.TemporaryDirectory() as base:
            directory = os.path.join(base, "cfg")
            self.assertEqual(paths_module.ensure_private_dir(directory), directory)
            self.assertEqual(oct(os.stat(directory).st_mode & 0o777), "0o700")
            with patch.object(paths_module, "config_dir", return_value=directory), \
                    patch.object(config_module, "config_path",
                                 return_value=os.path.join(directory, "config.json")):
                cfg = config_module.load()
                config_module.save(cfg)
            mode = oct(os.stat(os.path.join(directory, "config.json")).st_mode & 0o777)
            self.assertEqual(mode, "0o600")

    def test_llm_blocks_plain_http_off_loopback(self):
        from instant2_app import llm as llm_module
        for ok in ("http://localhost:8080", "http://127.0.0.1:8080",
                   "http://[::1]:8080", "https://llm.ejemplo.com"):
            self.assertTrue(llm_module.check_url(ok))
        with self.assertRaisesRegex(ValueError, "https://"):
            llm_module.check_url("http://192.168.1.10:8080")
        with self.assertRaisesRegex(ValueError, "https://"):
            llm_module.check_url("http://llm.ejemplo.com")

    def test_llm_failure_log_hides_url_and_keeps_class(self):
        from instant2_app import llm as llm_module
        url = "http://127.0.0.1:9/v1/chat/completions?token=secreto-abc"
        with patch("urllib.request.urlopen",
                   side_effect=OSError("caído")) as _urlopen, \
                patch.object(llm_module.log, "warning") as warning:
            out = llm_module.maybe_polish(
                "hola mundo", {"llm_url": url}, conf=0.1)
            _urlopen.assert_called_once()
        self.assertEqual(out, "hola mundo")
        warning.assert_called_once()
        logged = " ".join(str(part) for part in warning.call_args.args)
        self.assertIn("OSError", logged)
        self.assertNotIn("secreto-abc", logged)
        self.assertNotIn("127.0.0.1", logged)

    def test_llm_rejects_non_loopback_http_without_network(self):
        from instant2_app import llm as llm_module
        with patch("urllib.request.urlopen") as _urlopen, \
                patch.object(llm_module.log, "warning") as warning:
            out = llm_module.maybe_polish(
                "hola mundo",
                {"llm_url": "http://192.168.1.10:8080?token=secreto-abc"},
                conf=0.1)
            _urlopen.assert_not_called()
        self.assertEqual(out, "hola mundo")
        warning.assert_called_once()
        logged = " ".join(str(part) for part in warning.call_args.args)
        self.assertIn("ValueError", logged)
        self.assertNotIn("secreto-abc", logged)
        self.assertNotIn("192.168.1.10", logged)

    def test_llm_sends_bearer_without_logging_it(self):
        from instant2_app import llm as llm_module
        import json as _json

        seen = {}

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return _json.dumps(
                    {"choices": [{"message": {"content": "hola mundo"}}]}
                ).encode()

        def fake_urlopen(request, timeout=None):
            seen["auth"] = request.get_header("Authorization")
            return _Response()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            out = llm_module.polish(
                "hola mundo", "http://127.0.0.1:8080",
                token="token-secreto-xyz")
        self.assertEqual(out, "hola mundo")
        self.assertEqual(seen["auth"], "Bearer token-secreto-xyz")


class DownloadVerifyTests(unittest.TestCase):
    def _fixture(self, body):
        directory = tempfile.mkdtemp(prefix="instant2-update-test-")
        source = os.path.join(directory, "asset.bin")
        with open(source, "wb") as handle:
            handle.write(body)
        digest = hashlib.sha256(body).hexdigest()
        with open(source + ".sha256", "w", encoding="utf-8") as handle:
            handle.write(f"{digest}  asset.bin\n")
        return directory, source, digest

    def _file_url(self, path):
        return "file:" + path.replace(os.sep, "/")

    def test_cycle_download_verify_ok(self):
        _dir, source, digest = self._fixture(b"nuevo-exe-" * 1000)
        dest = os.path.join(_dir, "out", "Instant2.exe")
        expected = update_module.fetch_expected_sha256(self._file_url(source))
        self.assertEqual(expected, digest)
        seen = []
        result = update_module.download(
            self._file_url(source), dest,
            progress=lambda done, total: seen.append((done, total)),
            expected_sha256=expected)
        self.assertEqual(result, dest)
        with open(dest, "rb") as handle:
            self.assertEqual(handle.read(), b"nuevo-exe-" * 1000)
        self.assertTrue(seen)

    def test_tampered_download_is_deleted(self):
        _dir, source, digest = self._fixture(b"original")
        dest = os.path.join(_dir, "Instant2.exe")
        with self.assertRaises(ValueError):
            update_module.download(
                self._file_url(source), dest, expected_sha256="0" * 64)
        self.assertFalse(os.path.exists(dest))

    def test_missing_sidecar_refuses(self):
        directory = tempfile.mkdtemp(prefix="instant2-update-test-")
        source = os.path.join(directory, "asset.bin")
        with open(source, "wb") as handle:
            handle.write(b"sin-sidecar")
        with self.assertRaises(RuntimeError):
            update_module.fetch_expected_sha256(self._file_url(source))

    def test_midstream_failure_leaves_no_part(self):
        from unittest.mock import MagicMock
        directory = tempfile.mkdtemp(prefix="instant2-update-test-")
        dest = os.path.join(directory, "Instant2.exe")
        response = MagicMock()
        response.read.side_effect = [b"mitad", ConnectionError("corte")]
        context = MagicMock()
        context.__enter__.return_value = response
        with patch("urllib.request.urlopen", return_value=context):
            with self.assertRaises(ConnectionError):
                update_module.download("https://x/Instant2.exe", dest,
                                       expected_sha256="f" * 64)
        self.assertFalse(os.path.exists(dest))
        self.assertFalse(os.path.exists(dest + ".part"))

    def test_verified_file_lands_at_final_path(self):
        _dir, source, digest = self._fixture(b"final-" * 500)
        dest = os.path.join(_dir, "Instant2.exe")
        result = update_module.download(
            self._file_url(source), dest, expected_sha256=digest)
        self.assertEqual(result, dest)
        self.assertTrue(os.path.exists(dest))
        self.assertFalse(os.path.exists(dest + ".part"))


class SyncTasks:
    """TaskRunner sincrónico para los flujos de actualización."""

    def __init__(self):
        self.pending = 0
        self.on_idle = None
        self.on_error = None

    def submit(self, fn, on_result=None, on_error=None, on_progress=None):
        self.pending += 1
        try:
            value = fn(lambda payload: on_progress and on_progress(payload))
        except Exception as exc:  # noqa: BLE001  (igual que el runner real)
            if on_error is not None:
                on_error(exc)
            value = None
        else:
            if on_result is not None:
                on_result(value)
        finally:
            self.pending -= 1
        return id(fn)

    def drain(self):
        return 0

    def close(self):
        self.pending = 0


class DeferredTasks:
    """Worker manual para comprobar cuándo se ejecutan las operaciones lentas."""

    def __init__(self):
        self.jobs = []
        self.pending = 0
        self.on_idle = None
        self.on_error = None

    def submit(self, fn, on_result=None, on_error=None, on_progress=None):
        self.jobs.append((fn, on_result, on_error, on_progress))
        self.pending += 1
        return len(self.jobs)

    def drain(self):
        return 0

    def close(self):
        self.pending = 0


class UpdateFlowTests(unittest.TestCase):
    """Chequeo, descarga automática verificada y aplicación desde PanelLogic."""

    def _logic(self, cfg=None):
        from types import SimpleNamespace
        from instant2_app import gui
        emitted = []
        base_cfg = {"mic_hint": "", "mic_index": None, "key": "f9",
                    "autostart": False, "update_last_check": 0}
        base_cfg.update(cfg or {})
        with (
            patch("instant2_app.gui.config.load", return_value=base_cfg),
            patch("instant2_app.gui.models.check",
                  return_value={"parakeet": True, "vad": True}),
            patch("instant2_app.gui.resolve_data_dir",
                  return_value=os.path.join(os.getcwd(), "models")),
            patch("instant2_app.gui.daemon_is_running", return_value=False),
            patch("instant2_app.gui.audio.input_choices", return_value=[]),
            patch("instant2_app.gui.autostart.is_enabled", return_value=False),
            patch.dict(sys.modules, {"sounddevice": SimpleNamespace(
                default=SimpleNamespace(device=(-1, -1)))}),
        ):
            logic = gui.PanelLogic(
                emit=lambda kind, payload: emitted.append((kind, payload)),
                tasks=SyncTasks(), schedule=lambda ms, fn: None)
            logic._update_source_mode = False
        return logic, emitted

    def _toasts(self, emitted):
        return [payload for kind, payload in emitted if kind == "toast"]

    def _release(self, version="0.2.0"):
        return {"update": True, "current": "0.1.0", "latest": version,
                "notes": "Novedades", "asset": "Instant2.exe",
                "asset_url": "https://x/Instant2.exe"}

    def _verified_install_file(self, directory, name="Instant2-Setup.exe"):
        import hashlib
        path = os.path.join(directory, name)
        with open(path, "wb") as handle:
            handle.write(b"instalador-verificado-" * 100)
        with open(path, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        return path, digest

    def test_silent_check_downloads_and_verifies_update(self):
        logic, _emitted = self._logic({"update_mode": "auto"})
        info = self._release()
        with (
            patch("instant2_app.update.check", return_value=info),
            patch("instant2_app.update.fetch_expected_sha256",
                  return_value="a" * 64),
            patch("instant2_app.update.download") as download,
            patch("instant2_app.gui.config.save", return_value="config.json"),
        ):
            logic._silent_update_check()

        download.assert_called_once()
        self.assertEqual(download.call_args.kwargs["expected_sha256"], "a" * 64)
        self.assertIn("Reiniciar para aplicar v0.2.0", logic.update_button)
        self.assertEqual(logic.state_payload()["updates"]["action"], "apply_update")

        # El sello diario evita otra consulta, pero no oculta la descarga lista.
        logic._update_silent_done = False
        with patch("instant2_app.update.check") as check:
            logic._silent_update_check()
        check.assert_not_called()

    def test_update_button_check_downloads_in_background_with_progress(self):
        from instant2_app import gui
        logic, emitted = self._logic({"update_mode": "auto"})

        import hashlib
        fake_body = b"nuevo-binario-" * 100
        fake_digest = hashlib.sha256(fake_body).hexdigest()

        def fake_download(_url, _target, **kwargs):
            with open(_target, "wb") as handle:
                handle.write(fake_body)
            kwargs["progress"](512, 1024)

        with (
            patch("instant2_app.update.check", return_value=self._release()),
            patch("instant2_app.update.fetch_expected_sha256",
                  return_value=fake_digest),
            patch("instant2_app.update.download", side_effect=fake_download) as download,
        ):
            logic.check_updates()

        download.assert_called_once()
        progress_states = [
            payload["updates"]["progress"]
            for kind, payload in emitted if kind == "state"
            if payload["updates"]["progress"].get("percent") == 50
        ]
        self.assertTrue(progress_states)
        self.assertIn("Descarga verificada", logic.state_payload()["updates"]["progress"]["text"])
        self.assertEqual(logic.state_payload()["updates"]["action"], "apply_update")
        ready = [toast for toast in self._toasts(emitted)
                 if toast["title"] == "Actualización lista"]
        self.assertTrue(ready)
        self.assertEqual([a["label"] for a in ready[-1]["actions"]],
                         ["Actualizar y reiniciar", "Después"])

        with (
            patch("instant2_app.update.install_mode", return_value="portable"),
            patch("instant2_app.gui.os.path.isfile", return_value=True),
            patch("instant2_app.gui._pending_update_path",
                  return_value=os.path.join(tempfile.gettempdir(),
                                            "instant2-test-pending.json")),
            patch("instant2_app.gui._update_log_path",
                  return_value=os.path.join(tempfile.gettempdir(),
                                            "instant2-test-update.log")),
            patch("instant2_app.gui.subprocess.Popen") as popen,
        ):
            logic.handle({"op": "apply_update"})

        launches = [entry for entry in popen.call_args_list
                    if entry.args and "instant2-update.bat" in str(entry.args[0])]
        self.assertEqual(len(launches), 1)
        self.assertEqual(launches[0].args[0][0], "cmd")
        self.assertEqual(launches[0].args[0][-1],
                         os.path.join(gui._workdir(), "dist", "Instant2.exe"))
        self.assertTrue(logic._closed)

    def test_silent_check_notify_mode_asks_before_downloading(self):
        logic, emitted = self._logic()
        info = self._release()
        with (
            patch("instant2_app.update.check", return_value=info),
            patch("instant2_app.update.download") as download,
            patch("instant2_app.gui.config.save", return_value="config.json"),
        ):
            logic._silent_update_check()
        download.assert_not_called()
        self.assertTrue(any(toast["title"].startswith("Hay versión nueva")
                            for toast in self._toasts(emitted)))
        self.assertIn("Descargar v0.2.0", logic.update_button)

    def test_download_failure_can_be_retried(self):
        logic, emitted = self._logic({"update_mode": "auto"})
        info = self._release()
        with (
            patch("instant2_app.update.check", return_value=info),
            patch("instant2_app.update.fetch_expected_sha256",
                  side_effect=[RuntimeError("sin red"), "c" * 64]),
            patch("instant2_app.update.download"),
        ):
            logic.check_updates()
            self.assertEqual(logic.state_payload()["updates"]["action"], "retry_update")
            self.assertIn("Reintentar descarga", logic.update_button)
            logic.handle({"op": "retry_update"})

        self.assertEqual(logic.state_payload()["updates"]["action"], "apply_update")
        self.assertTrue(any(toast["title"] == "Actualización lista"
                            for toast in self._toasts(emitted)))

    def test_installed_update_waits_for_daemon_in_worker(self):
        logic, _emitted = self._logic()
        logic.tasks = DeferredTasks()
        logic._pending_update = self._release()
        _tmp = tempfile.TemporaryDirectory()
        self.addCleanup(_tmp.cleanup)
        _path, _digest = self._verified_install_file(_tmp.name)
        logic._ready_update = (_path, _digest, "0.2.0")

        with (patch("instant2_app.update.install_mode", return_value="installed"),
              patch("instant2_app.gui.stop_daemon") as stop,
              patch("instant2_app.gui.daemon_is_running", return_value=False),
              patch("instant2_app.gui._pid_value", return_value=4242),
              patch("instant2_app.gui._probe_daemon_process",
                    return_value=("stopped", None)),
              patch("instant2_app.gui._pending_update_path",
                    return_value=os.path.join(_tmp.name, "pending-update.json")),
              patch("instant2_app.gui.subprocess.run",
                    return_value=SimpleNamespace(returncode=0)) as run):
            logic.handle({"op": "apply_update"})

            self.assertTrue(logic._update_install_pending)
            self.assertEqual(len(logic.tasks.jobs), 1)
            stop.assert_not_called()
            run.assert_not_called()

            fn, on_result, on_error, _progress = logic.tasks.jobs.pop()
            result = fn(lambda _payload: None)
            on_result(result)

        stop.assert_called_once_with()
        run.assert_called_once()
        self.assertTrue(logic._closed)

    def test_installed_update_reports_daemon_that_will_not_stop(self):
        logic, emitted = self._logic()
        logic.tasks = DeferredTasks()
        logic._pending_update = self._release()
        _tmp = tempfile.TemporaryDirectory()
        self.addCleanup(_tmp.cleanup)
        _path, _digest = self._verified_install_file(_tmp.name)
        logic._ready_update = (_path, _digest, "0.2.0")

        with (patch("instant2_app.update.install_mode", return_value="installed"),
              patch("instant2_app.gui.stop_daemon") as stop,
              patch("instant2_app.gui._pid_value", return_value=4242),
              patch("instant2_app.gui._probe_daemon_process",
                    return_value=("running", None)),
              patch("instant2_app.gui.time.monotonic", side_effect=[0, 0, 100]),
              patch("instant2_app.gui.time.sleep") as sleep,
              patch("instant2_app.gui._pending_update_path",
                    return_value=os.path.join(_tmp.name, "pending-update.json")),
              patch("instant2_app.gui.subprocess.run",
                    return_value=SimpleNamespace(returncode=0)) as run):
            logic.handle({"op": "apply_update"})
            fn, _on_result, on_error, _progress = logic.tasks.jobs.pop()
            with self.assertRaisesRegex(RuntimeError, "sigue activo") as caught:
                fn(lambda _payload: None)
            on_error(caught.exception)

        stop.assert_called_once_with()
        sleep.assert_not_called()
        run.assert_not_called()
        self.assertFalse(logic._update_install_pending)
        self.assertFalse(logic._closed)
        self.assertTrue(any(toast["title"] == "Actualización sin aplicar"
                            for toast in self._toasts(emitted)))

    def test_portable_update_uses_bundled_helper_and_real_exe_path(self):
        logic, _emitted = self._logic()
        logic._pending_update = self._release()

        with tempfile.TemporaryDirectory() as temp:
            _target, _digest = self._verified_install_file(temp, "Instant2.exe")
            logic._ready_update = (_target, _digest, "0.2.0")
            extraction = os.path.join(temp, "_MEI12345")
            os.makedirs(extraction)
            bundled = os.path.join(extraction, "instant2-update.bat")
            with open(bundled, "w", encoding="utf-8") as handle:
                handle.write("@echo off\n")
            executable = os.path.join(temp, "portable", "Instant2.exe")
            with (
                patch("instant2_app.update.install_mode", return_value="portable"),
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "platform", "win32"),
                patch.object(sys, "_MEIPASS", extraction, create=True),
                patch.object(sys, "executable", executable),
                patch("tempfile.gettempdir", return_value=temp),
                patch("instant2_app.gui._pending_update_path",
                      return_value=os.path.join(temp, "pending-update.json")),
                patch("instant2_app.gui._update_log_path",
                      return_value=os.path.join(temp, "update.log")),
                patch("instant2_app.gui.subprocess.Popen") as popen,
            ):
                logic.handle({"op": "apply_update"})

            helper = os.path.join(
                temp, f"instant2_update_{os.getpid()}", "instant2-update.bat")
            self.assertTrue(os.path.isfile(helper))
            args = popen.call_args.args[0]
            self.assertEqual(args[2], helper)
            self.assertEqual(args[-1], executable)
        self.assertTrue(logic._closed)

    def test_portable_helper_copy_failure_is_visible_and_retryable(self):
        logic, emitted = self._logic()
        logic._pending_update = self._release()

        with tempfile.TemporaryDirectory() as temp:
            _target, _digest = self._verified_install_file(temp, "Instant2.exe")
            logic._ready_update = (_target, _digest, "0.2.0")
            bundled = os.path.join(temp, "instant2-update.bat")
            with open(bundled, "w", encoding="utf-8") as handle:
                handle.write("@echo off\n")
            with (
                patch("instant2_app.update.install_mode", return_value="portable"),
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "platform", "win32"),
                patch.object(sys, "_MEIPASS", temp, create=True),
                patch.object(sys, "executable", os.path.join(temp, "Instant2.exe")),
                patch("tempfile.gettempdir", return_value=temp),
                patch("shutil.copyfile", side_effect=OSError("disco lleno")),
                patch("instant2_app.gui.subprocess.Popen") as popen,
            ):
                logic.handle({"op": "apply_update"})

        self.assertFalse(logic._update_install_pending)
        self.assertEqual(logic.state_payload()["updates"]["action"], "apply_update")
        self.assertEqual(popen.call_count, 0)
        self.assertTrue(any(toast["title"] == "Actualización sin aplicar"
                            for toast in self._toasts(emitted)))

if __name__ == "__main__":
    unittest.main()
