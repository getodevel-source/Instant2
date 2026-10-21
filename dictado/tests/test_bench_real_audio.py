"""Tests for private real-voice corpus validation and aggregate metrics."""
import csv
import os
import sys
import tempfile
import unittest
import wave
from unittest.mock import Mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bench"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolation  # noqa: F401  (aisla el estado real del usuario)
import bench_real_audio as bench


class RealAudioBenchTests(unittest.TestCase):
    def test_edit_counts_cover_substitutions_deletions_insertions_and_accents(self):
        self.assertEqual(bench._edit_counts("El árbol", "el arbol")["errors"], 0)
        self.assertEqual(bench._edit_counts("uno", "dos")["substitutions"], 1)
        self.assertEqual(bench._edit_counts("uno dos", "uno")["deletions"], 1)
        self.assertEqual(bench._edit_counts("uno", "uno dos")["insertions"], 1)

    def test_manifest_requires_explicit_consent_and_relative_wav_exists(self):
        with tempfile.TemporaryDirectory() as temp:
            audio = os.path.join(temp, "clip.wav")
            with wave.open(audio, "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(bench.SAMPLE_RATE)
                output.writeframes(np.zeros(160, dtype="<i2").tobytes())
            manifest = os.path.join(temp, "manifest.csv")
            fields = ["path", "reference", "condition", "microphone",
                      "speaker_id", "consent"]
            with open(manifest, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow({
                    "path": "clip.wav", "reference": "Hola mundo",
                    "condition": "silencio", "microphone": "integrado",
                    "speaker_id": "persona-01", "consent": "no",
                })
            with self.assertRaisesRegex(ValueError, "consentimiento afirmativo"):
                bench.read_manifest(manifest)

            with open(manifest, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow({
                    "path": "clip.wav", "reference": "Hola mundo",
                    "condition": "silencio", "microphone": "integrado",
                    "speaker_id": "persona-01", "consent": "sí",
                })
            rows = bench.read_manifest(manifest)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["path"], audio)

    def test_summary_aggregates_without_exposing_speaker_ids_or_text(self):
        with tempfile.TemporaryDirectory() as temp:
            audio = os.path.join(temp, "clip.wav")
            with wave.open(audio, "wb") as output:
                output.setnchannels(2)
                output.setsampwidth(2)
                output.setframerate(bench.SAMPLE_RATE)
                stereo = np.zeros((160, 2), dtype="<i2")
                output.writeframes(stereo.tobytes())
            engine = Mock()
            engine.last_conf = 0.9
            engine.transcribe.return_value = "Hola mundo"
            record = {
                "path": audio, "reference": "Hola mundo", "line": 2,
                "speaker_id": "private-speaker", "condition": "interior",
                "microphone": "integrado",
            }
            result = bench.evaluate([record, record], engine)
            overall = result["overall"]
            self.assertEqual(overall["clips"], 2)
            self.assertEqual(overall["speakers"], 1)
            self.assertEqual(overall["raw"]["wer"], 0.0)
            self.assertEqual(overall["bias_regressions"], 0)
            serialized = str(result)
            self.assertNotIn("private-speaker", serialized)
            self.assertNotIn("Hola mundo", serialized)
            self.assertNotIn(audio, serialized)


if __name__ == "__main__":
    unittest.main()
