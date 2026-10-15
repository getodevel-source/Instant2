"""Worker results wake their owner and do not outlive a closed panel."""
import os
import sys
import threading
import unittest
from unittest.mock import Mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from instant2_app.tasks import TaskRunner


class TaskDeliveryTests(unittest.TestCase):
    def test_progress_result_and_done_notify_without_applying_ui_on_worker(self):
        tasks = TaskRunner()
        done = threading.Event()
        seen = []
        def notify():
            if tasks._queue.qsize() == 3:
                done.set()
        tasks.notify = notify
        def work(emit):
            emit(25)
            return "ready"
        tasks.submit(work, lambda result: seen.append(result), on_progress=lambda value: seen.append(value))
        self.assertTrue(done.wait(2))
        self.assertEqual(seen, [])
        self.assertEqual(tasks.drain(), 2)
        self.assertEqual(seen, [25, "ready"])
        self.assertEqual(tasks.pending, 0)

    def test_close_drops_pending_data_and_ignores_late_worker_completion(self):
        tasks = TaskRunner()
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        seen = Mock()
        def work(emit):
            emit(bytearray(1024))
            started.set()
            release.wait(2)
            finished.set()
            return bytearray(1024)
        tasks.submit(work, seen)
        self.assertTrue(started.wait(2))
        tasks.close()
        self.assertEqual(tasks._queue.qsize(), 0)
        release.set()
        self.assertTrue(finished.wait(2))
        self.assertEqual(tasks.drain(), 0)
        self.assertEqual(tasks.pending, 0)
        seen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
