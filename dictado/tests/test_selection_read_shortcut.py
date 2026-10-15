"""F10 keeps its start/stop contract and publishes real audio to the overlay."""
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from instant2_app.daemon import Daemon
from instant2_app import selection


class SelectionReadShortcutTests(unittest.TestCase):
    def setUp(self):
        self.daemon = Daemon.__new__(Daemon)
        self.daemon.lock = threading.Lock()
        self.daemon.rec = {"grabando": False, "busy": False}
        self.daemon._last_read_press = 0
        self.daemon._tts_busy = False
        self.daemon._tts_cancel_event = None
        self.daemon._tts_session = 0
        self.daemon._overlay_sid = 0
        self.daemon._tts_reader = Mock()
        self.daemon.overlay = Mock()

    def test_second_f10_stops_same_session_without_starting_another_worker(self):
        with patch("instant2_app.daemon.threading.Thread") as worker, \
                patch("instant2_app.daemon.time.monotonic", side_effect=[1, 1.5]):
            self.daemon.on_read_press()
            cancel = self.daemon._tts_cancel_event
            self.assertFalse(cancel.is_set())
            self.daemon.overlay.reading_start.assert_called_once_with(
                1, "Copiando el texto seleccionado…")
            self.daemon.on_read_press()
            self.assertTrue(cancel.is_set())
            self.daemon.overlay.reading_stopping.assert_called_once_with(1)
            self.assertEqual(worker.call_count, 1)

    def test_f10_during_dictation_does_not_take_over_overlay(self):
        for recording, processing in ((True, False), (False, True)):
            with self.subTest(recording=recording, processing=processing):
                self.daemon._last_read_press = 0
                self.daemon.rec = {"grabando": recording, "busy": processing}
                with patch("instant2_app.daemon.threading.Thread") as worker:
                    self.daemon.on_read_press()
                    worker.assert_not_called()
                self.assertFalse(self.daemon._tts_busy)
                self.daemon.overlay.reading_start.assert_not_called()

    def test_reading_uses_saved_preferences_and_feeds_actual_audio_signal(self):
        self.daemon._tts_session = 3
        self.daemon._tts_busy = True
        event = threading.Event()

        def play(text, **options):
            self.assertEqual(text, "Texto de prueba local.")
            self.assertEqual((options["voice"], options["speed"]), ("ef_dora", 1.2))
            options["level_callback"](.6)
            options["bands_callback"]([.3] * 9)

        self.daemon._tts_reader.play.side_effect = play
        with patch("instant2_app.selection.copy_selected_text", return_value="Texto de prueba local."), \
                patch("instant2_app.tts.load_preferences", return_value={"voice": "ef_dora", "speed": 1.2}):
            self.daemon._read_selection_job(3, event)
        self.daemon.overlay.reading.assert_called_once_with(3)
        self.daemon.overlay.set_level.assert_called_once_with(3, .6)
        self.daemon.overlay.set_bands.assert_called_once_with(3, [.3] * 9)
        self.daemon.overlay.reading_success_for.assert_called_once_with(3)
        self.assertFalse(self.daemon._tts_busy)

    def test_missing_selection_reports_notice_and_releases_busy_state(self):
        self.daemon._tts_session = 1
        self.daemon._tts_busy = True
        with patch("instant2_app.selection.copy_selected_text", side_effect=selection.SelectionUnavailable()):
            self.daemon._read_selection_job(1, threading.Event())
        self.daemon.overlay.reading_notice_for.assert_called_once()
        self.daemon._tts_reader.play.assert_not_called()
        self.daemon.overlay.reading_error_for.assert_not_called()
        self.assertFalse(self.daemon._tts_busy)


if __name__ == "__main__":
    unittest.main()
