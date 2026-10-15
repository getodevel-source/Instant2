"""Reading state matches playback and stopping discards buffered sound."""
import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from instant2_app.tts import KokoroReader, SpeechCancelled


class ReadingCancellationTests(unittest.TestCase):
    def test_playing_is_announced_before_first_audio_and_cancel_aborts_stream(self):
        event = threading.Event()
        progress = Mock()
        stream = Mock()
        stream.__enter__ = Mock(return_value=stream)
        stream.__exit__ = Mock(return_value=False)
        def write(_samples):
            self.assertTrue(any(str(call.args[2]).startswith("Leyendo") for call in progress.call_args_list))
            event.set()
        stream.write.side_effect = write
        reader = KokoroReader("unused")
        reader._engine = SimpleNamespace(create=lambda *args, **kwargs: (np.ones(4800,dtype=np.float32),24000))
        reader._g2p = lambda text: (text,None)
        audio = SimpleNamespace(OutputStream=Mock(return_value=stream))
        with patch.dict(sys.modules, {"sounddevice": audio}):
            with self.assertRaises(SpeechCancelled):
                reader.play("Texto para comprobar la detención.", cancel_event=event, progress=progress)
        self.assertEqual(stream.write.call_count,1)
        stream.abort.assert_called_once_with()
        self.assertFalse(any(t.name=="instant2-kokoro-prefetch" and t.is_alive() for t in threading.enumerate()))


if __name__ == "__main__":
    unittest.main()
