"""The paste transaction must not overwrite a clipboard changed by its owner."""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from instant2_app import paste
from instant2_app import clipboard as ownership


class WindowsClipboardOwnershipTests(unittest.TestCase):
    def run_paste(self, *, changed=False, fail_key=False):
        clipboard = {"value": "antes"}
        copy = Mock(side_effect=lambda value: clipboard.update(value=value))
        pyperclip = SimpleNamespace(paste=lambda: clipboard["value"], copy=copy)
        keyboard = Mock()
        if fail_key:
            keyboard.press.side_effect = lambda key: (_ for _ in ()).throw(OSError()) if key == "v" else None

        def sleep(seconds):
            if changed and seconds == paste._WINDOWS_PASTE_SETTLE_S:
                clipboard["value"] = "copia nueva del usuario"

        with patch.dict(sys.modules, {"pyperclip": pyperclip, "keyboard": keyboard}), \
                patch.object(paste.time, "sleep", side_effect=sleep):
            if fail_key:
                with self.assertRaises(OSError):
                    paste._paste_windows("dictado")
            else:
                paste._paste_windows("dictado")
        return clipboard["value"], keyboard, copy

    def test_user_copy_during_delivery_is_preserved(self):
        value, _, _ = self.run_paste(changed=True)
        self.assertEqual(value, "copia nueva del usuario")

    def test_previous_text_is_restored_when_clipboard_is_still_ours(self):
        value, keyboard, _ = self.run_paste()
        self.assertEqual(value, "antes")
        self.assertEqual([call.args[0] for call in keyboard.release.call_args_list], ["v", "ctrl"])

    def test_failure_keeps_dictation_available_for_manual_paste(self):
        value, keyboard, _ = self.run_paste(fail_key=True)
        self.assertEqual(value, "dictado")
        keyboard.release.assert_called_with("ctrl")


class ClipboardGuardTests(unittest.TestCase):
    def test_copy_of_identical_text_is_still_a_new_owner(self):
        write = Mock()
        with patch.object(ownership, "sequence_number", return_value=13):
            self.assertFalse(ownership.restore_text("old", "same", lambda: "same", write, 12))
        write.assert_not_called()

    def test_copy_while_reading_prevents_restore(self):
        write = Mock()
        with patch.object(ownership, "sequence_number", side_effect=[12, 13]):
            self.assertFalse(ownership.restore_text("old", "ours", lambda: "ours", write, 12))
        write.assert_not_called()

    def test_unreadable_clipboard_is_left_alone(self):
        write = Mock()
        self.assertFalse(ownership.restore_text("old", "ours", Mock(side_effect=OSError()), write))
        write.assert_not_called()


class PosixClipboardOwnershipTests(unittest.TestCase):
    def test_changed_clipboard_survives_all_existing_backends(self):
        import subprocess
        for backend in ("x11", "wayland", "macos"):
            for changed in (False, True):
                with self.subTest(backend=backend, changed=changed):
                    value = {"text": b"before"}
                    def run(command, **kwargs):
                        if "input" in kwargs:
                            value["text"] = kwargs["input"]
                        elif any("xdotool" in arg or "wtype" in arg for arg in command):
                            if changed:
                                value["text"] = b"new user copy"
                        return SimpleNamespace(returncode=0, stdout=value["text"])
                    keyboard = Mock()
                    keyboard.Controller.return_value.pressed.return_value.__enter__ = Mock()
                    keyboard.Controller.return_value.pressed.return_value.__exit__ = Mock()
                    keyboard.Controller.return_value.release.side_effect = lambda key: value.update(text=b"new user copy") if changed else None
                    with patch.object(subprocess, "run", side_effect=run), \
                            patch.object(paste.time, "sleep"), \
                            patch.dict(sys.modules, {"pynput.keyboard": keyboard}), \
                            patch("shutil.which", side_effect=lambda name: "/usr/bin/" + name), \
                            patch.dict(os.environ, {"XDG_SESSION_TYPE": "x11", "WAYLAND_DISPLAY": ""}):
                        if backend == "macos":
                            paste._paste_macos("dictation")
                        elif backend == "wayland":
                            paste._paste_wayland("dictation", "wl-copy", "wl-paste", "wtype", subprocess)
                        else:
                            paste._paste_linux("dictation")
                    self.assertEqual(value["text"], b"new user copy" if changed else b"before")


if __name__ == "__main__":
    unittest.main()
