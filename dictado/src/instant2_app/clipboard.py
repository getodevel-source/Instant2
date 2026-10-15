"""Ownership checks shared by dictation delivery and selected-text reading.

Only plain text is restored, matching the existing clipboard contract. A newer
clipboard value always takes precedence over restoring our saved text.
"""
import sys


def sequence_number():
    """Windows detects a new copy even when its text happens to be identical."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        value = ctypes.windll.user32.GetClipboardSequenceNumber()
        return value or None
    except (AttributeError, OSError):
        return None


def restore_text(previous, expected, read, write, expected_sequence=None):
    """Restore only while the clipboard still contains this operation's text.

    A busy clipboard or an unavailable ownership check is a reason to leave it
    alone. Restoration must never turn a successful paste/read into a failure.
    """
    if previous is None:
        return False
    try:
        if expected_sequence is not None and sequence_number() != expected_sequence:
            return False
        if read() != expected:
            return False
        # Reading can trigger delayed clipboard rendering or take time while
        # another app owns it. Check again after the read, before writing.
        if expected_sequence is not None and sequence_number() != expected_sequence:
            return False
        write(previous)
        return True
    except Exception:
        return False
