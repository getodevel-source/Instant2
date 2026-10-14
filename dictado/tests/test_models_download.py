"""Descarga de modelos: bytes reales, reanudación, espejo y chequeo de disco.

Todo corre contra un servidor HTTP local con archivos de unos KB: la suite no
toca Hugging Face (antes, cada corrida de CI bajaba los 670 MB reales).
"""
import http.server
import hashlib
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from instant2_app import models

BODY = b"parakeet-de-prueba-" * 100  # ~1.9 KB por archivo falso


class _RangeHandler(http.server.BaseHTTPRequestHandler):
    """Sirve `self.server.payload` y respeta Range con 206."""

    # Loopback de Windows: sin TCP_NODELAY cada request paga el ACK demorado.
    disable_nagle_algorithm = True

    def do_GET(self):
        data = self.server.payload
        rng = self.headers.get("Range")
        self.server.ranges.append(rng)
        if rng and rng.startswith("bytes=") and not self.server.ignore_ranges:
            start = int(rng.split("=", 1)[1].split("-", 1)[0])
            body = data[start:]
            self.send_response(206)
            self.send_header(
                "Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        else:
            body = data
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class _Server:
    def __init__(self, payload=BODY, ignore_ranges=False):
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
        self.httpd.payload = payload
        self.httpd.ranges = []
        self.httpd.ignore_ranges = ignore_ranges
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/archivo"

    @property
    def ranges(self):
        return self.httpd.ranges

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _expected(payload=BODY):
    return (len(payload), hashlib.sha256(payload).hexdigest())


class ModelDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _Server()
        cls.addClassCleanup(cls.server.close)

    def setUp(self):
        self.server.httpd.payload = BODY
        self.server.httpd.ignore_ranges = False
        self.server.ranges.clear()
        self.expected = _expected()
        faux = {name: self.expected for name in models.PARAKEET_FILES}
        patches = [
            patch.object(models, "PARAKEET_SHA256", faux),
            patch.object(models, "VAD_SHA256", self.expected),
            patch.object(models, "_steps", self._steps),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.data_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.data_dir, ignore_errors=True)

    def _steps(self):
        from instant2_app.paths import PARAKEET_SUBDIR, VAD_SUBDIR

        parakeet = [(self.server.url, name, self.expected)
                    for name in models.PARAKEET_FILES]
        return (("parakeet", PARAKEET_SUBDIR, parakeet),
                ("vad", VAD_SUBDIR, [(self.server.url, "silero_vad.onnx",
                                      self.expected)]))

    def _target(self, *parts):
        return os.path.join(self.data_dir, *parts)

    def test_downloads_all_files_with_real_byte_progress(self):
        events = []
        models.download_models(
            self.data_dir, progress=lambda *args: events.append(args))
        self.assertTrue(all(models.check(self.data_dir).values()))
        self.assertTrue(all(
            value is True for value in models.check_integrity(
                self.data_dir, models.PARAKEET_SHA256,
                models.VAD_SHA256).values()))
        parakeet = [e for e in events if e[0] == "parakeet"]
        vad = [e for e in events if e[0] == "vad"]
        total = len(models.PARAKEET_FILES) * self.expected[0]
        self.assertEqual(parakeet[-1][1:], (total, total))
        self.assertEqual(vad[-1][1:], (self.expected[0], self.expected[0]))
        self.assertTrue(all(a <= b for a, b in
                            zip([e[1] for e in parakeet],
                                [e[1] for e in parakeet][1:])))

    def test_resumes_a_partial_download_with_range(self):
        directory = self._target("parakeet-v3-int8")
        os.makedirs(directory)
        encoder = os.path.join(directory, "encoder.int8.onnx")
        with open(encoder + ".part", "wb") as handle:
            handle.write(BODY[: len(BODY) // 2])
        models.download_models(self.data_dir, progress=lambda *a: None)
        self.assertIn(f"bytes={len(BODY) // 2}-", self.server.ranges)
        with open(encoder, "rb") as handle:
            self.assertEqual(handle.read(), BODY)

    def test_downloads_from_scratch_when_server_ignores_range(self):
        self.server.httpd.ignore_ranges = True
        directory = self._target("parakeet-v3-int8")
        os.makedirs(directory)
        encoder = os.path.join(directory, "encoder.int8.onnx")
        with open(encoder + ".part", "wb") as handle:
            handle.write(BODY[: len(BODY) // 2])
        models.download_models(self.data_dir, progress=lambda *a: None)
        with open(encoder, "rb") as handle:
            self.assertEqual(handle.read(), BODY)

    def test_corrupt_file_is_downloaded_again(self):
        directory = self._target("parakeet-v3-int8")
        os.makedirs(directory)
        encoder = os.path.join(directory, "encoder.int8.onnx")
        with open(encoder, "wb") as handle:
            handle.write(b"truncado")
        models.download_models(self.data_dir, progress=lambda *a: None)
        with open(encoder, "rb") as handle:
            self.assertEqual(handle.read(), BODY)

    def test_refuses_to_start_without_disk_space(self):
        class _Usage:
            free = 1024
        with patch("shutil.disk_usage", return_value=_Usage()):
            with self.assertRaises(models.ModelIntegrityError) as caught:
                models.download_models(self.data_dir, progress=lambda *a: None)
        self.assertIn("espacio", str(caught.exception))
        self.assertFalse(os.path.isfile(
            self._target("parakeet-v3-int8", "encoder.int8.onnx")))
        self.assertEqual(self.server.ranges, [])

    def test_preflight_reports_required_and_free_space_before_network(self):
        class _Usage:
            free = 1024

        seen = []
        with patch("shutil.disk_usage", return_value=_Usage()):
            with self.assertRaises(models.ModelIntegrityError):
                models.download_models(
                    self.data_dir, progress=lambda *a: None,
                    preflight=seen.append)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["free_bytes"], 1024)
        self.assertGreater(seen[0]["required_bytes"], 1024)
        self.assertFalse(seen[0]["enough"])
        self.assertEqual(self.server.ranges, [])

    def test_cancel_keeps_partial_file_and_next_run_resumes_it(self):
        from threading import Event

        cancel = Event()

        def stop_after_first_chunk(_step, done, _total):
            if done:
                cancel.set()

        encoder = self._target("parakeet-v3-int8", "encoder.int8.onnx")
        with patch.object(models, "CHUNK", 64):
            with self.assertRaises(models.DownloadCancelled):
                models.download_models(
                    self.data_dir, progress=stop_after_first_chunk,
                    cancel_event=cancel)
        part = encoder + ".part"
        self.assertTrue(os.path.isfile(part))
        partial_size = os.path.getsize(part)
        self.assertGreater(partial_size, 0)
        self.assertLess(partial_size, len(BODY))
        self.assertFalse(os.path.isfile(encoder))

        models.download_models(self.data_dir, progress=lambda *a: None)
        self.assertTrue(all(models.check(self.data_dir).values()))
        self.assertIn(f"bytes={partial_size}-", self.server.ranges)

    def test_space_check_counts_only_bytes_missing_from_resumable_parts(self):
        from instant2_app.paths import PARAKEET_SUBDIR, VAD_SUBDIR

        entries = [(PARAKEET_SUBDIR, name) for name in models.PARAKEET_FILES]
        entries.append((VAD_SUBDIR, "silero_vad.onnx"))
        partial = BODY[: len(BODY) // 2]
        for subdir, name in entries:
            directory = self._target(subdir)
            os.makedirs(directory, exist_ok=True)
            with open(os.path.join(directory, name) + ".part", "wb") as handle:
                handle.write(partial)

        remaining = len(entries) * (len(BODY) - len(partial))

        class _Usage:
            free = models.DISK_MARGIN + remaining

        with patch("shutil.disk_usage", return_value=_Usage()):
            models.download_models(self.data_dir, progress=lambda *a: None)
        self.assertTrue(all(models.check(self.data_dir).values()))

    def test_invalid_content_is_rejected_without_leaving_junk(self):
        encoder = self._target("parakeet-v3-int8", "encoder.int8.onnx")
        os.makedirs(os.path.dirname(encoder))
        with open(encoder, "wb") as handle:
            handle.write(b"truncado")  # corrupto en disco: se vuelve a bajar
        self.server.httpd.payload = b"contenido equivocado"
        with patch.object(models, "DOWNLOAD_ATTEMPTS", 1):
            with self.assertRaises(models.ModelIntegrityError):
                models.download_models(self.data_dir, progress=lambda *a: None)
        self.assertFalse(os.path.isfile(encoder))
        self.assertFalse(os.path.isfile(encoder + ".part"))

    def test_mirror_endpoint_is_honored(self):
        with patch.dict(os.environ, {"HF_ENDPOINT": "https://espejo.local/"},
                        clear=False):
            os.environ.pop("INSTANT2_HF_ENDPOINT", None)
            self.assertEqual(models._endpoint(), "https://espejo.local")
        with patch.dict(os.environ, {"INSTANT2_HF_ENDPOINT": "https://propio.local"},
                        clear=False):
            self.assertEqual(models._endpoint(), "https://propio.local")

    def test_already_verified_files_are_not_downloaded_again(self):
        models.download_models(self.data_dir, progress=lambda *a: None)
        self.server.ranges.clear()
        models.download_models(self.data_dir)
        self.assertEqual(self.server.ranges, [])


if __name__ == "__main__":
    unittest.main()
