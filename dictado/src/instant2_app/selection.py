"""Read the current text selection through the system clipboard, in memory only."""
import os
import shutil
import subprocess
import sys
import time


class SelectionUnavailable(RuntimeError):
    """No readable text selection was copied by the foreground application."""


class SelectionAccessError(RuntimeError):
    """The desktop did not allow access to the text clipboard or keyboard."""


def _is_wayland():
    session = (os.environ.get("XDG_SESSION_TYPE") or "").strip().lower()
    return (session == "wayland" or "wayland" in session
            or bool(os.environ.get("WAYLAND_DISPLAY")))


def _read_clipboard():
    if sys.platform == "win32":
        import pyperclip
        return pyperclip.paste()
    if sys.platform == "darwin":
        result = subprocess.run(["pbpaste"], capture_output=True, timeout=3)
        return result.stdout.decode("utf-8", errors="replace")
    if _is_wayland():
        result = subprocess.run(
            ["wl-paste", "--no-newline", "--type", "text/plain"],
            capture_output=True, timeout=3)
        if result.returncode:
            return ""
        return result.stdout.decode("utf-8", errors="replace")
    xclip, xsel = shutil.which("xclip"), shutil.which("xsel")
    if xclip:
        result = subprocess.run(
            [xclip, "-selection", "clipboard", "-o"],
            capture_output=True, timeout=3)
    elif xsel:
        result = subprocess.run(
            [xsel, "--clipboard", "--output"], capture_output=True, timeout=3)
    else:
        raise RuntimeError("clipboard utility unavailable")
    return result.stdout.decode("utf-8", errors="replace")


def _write_clipboard(text):
    if sys.platform == "win32":
        import pyperclip
        pyperclip.copy(text)
        return
    if sys.platform == "darwin":
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True,
                       timeout=3)
        return
    if _is_wayland():
        subprocess.run(["wl-copy", "--type", "text/plain"],
                       input=text.encode("utf-8"), check=True, timeout=3)
        return
    xclip, xsel = shutil.which("xclip"), shutil.which("xsel")
    if xclip:
        subprocess.run([xclip, "-selection", "clipboard"],
                       input=text.encode("utf-8"), check=True, timeout=3)
    elif xsel:
        subprocess.run([xsel, "--clipboard", "--input"],
                       input=text.encode("utf-8"), check=True, timeout=3)
    else:
        raise RuntimeError("clipboard utility unavailable")


def _send_copy():
    if sys.platform == "win32":
        import keyboard
        keyboard.press("ctrl")
        try:
            time.sleep(0.025)
            keyboard.press("c")
            try:
                time.sleep(0.04)
            finally:
                keyboard.release("c")
        finally:
            keyboard.release("ctrl")
        return
    if sys.platform == "darwin":
        from pynput.keyboard import Controller, Key
        controller = Controller()
        with controller.pressed(Key.cmd):
            controller.press("c")
            controller.release("c")
        return
    if _is_wayland():
        wtype = shutil.which("wtype")
        if not wtype:
            raise RuntimeError("wtype unavailable")
        subprocess.run([wtype, "-M", "ctrl", "c", "-m", "ctrl"],
                       check=True, timeout=3)
        return
    from pynput.keyboard import Controller, Key
    controller = Controller()
    with controller.pressed(Key.ctrl):
        controller.press("c")
        controller.release("c")


def copy_selected_text(timeout=0.9):
    """Copy the foreground selection, restore prior plain text, return in RAM.

    The F10 shortcut is consumed before this function runs, so F10 itself does
    not move focus or open the foreground app's menu. The selection is copied
    with Ctrl+C, read without logging, and the previous text clipboard is
    restored if it has not changed independently in the meantime.
    """
    try:
        previous = _read_clipboard()
    except Exception:
        raise SelectionAccessError("clipboard unavailable") from None
    windows = sys.platform == "win32"
    sequence_before = None
    if windows:
        import ctypes
        sequence_before = ctypes.windll.user32.GetClipboardSequenceNumber()

    try:
        _send_copy()
    except Exception:
        raise SelectionAccessError("copy shortcut unavailable") from None
    deadline = time.monotonic() + max(0.1, min(float(timeout), 3.0))
    copied = ""
    copied_sequence = None
    while time.monotonic() < deadline:
        try:
            candidate = _read_clipboard()
            if windows:
                sequence = ctypes.windll.user32.GetClipboardSequenceNumber()
                if sequence != sequence_before:
                    copied, copied_sequence = candidate, sequence
                    break
            elif candidate != previous:
                copied = candidate
                break
        except Exception:
            pass
        time.sleep(0.025)

    try:
        if copied and copied.strip():
            return copied
        raise SelectionUnavailable("No readable text selection")
    finally:
        try:
            if windows:
                current_sequence = ctypes.windll.user32.GetClipboardSequenceNumber()
                if copied_sequence is not None and current_sequence == copied_sequence:
                    _write_clipboard(previous)
            else:
                if copied and _read_clipboard() == copied:
                    _write_clipboard(previous)
        except Exception:
            # The voice feature should still work if the desktop clipboard
            # manager does not allow restoring its previous text value.
            pass
