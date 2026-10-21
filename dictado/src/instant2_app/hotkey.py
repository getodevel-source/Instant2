"""Hotkey hold-to-talk por SO.
Windows: polling GetAsyncKeyState. Linux Wayland: portal GlobalShortcuts.
Linux X11/macOS: pynput (listener press/release). Sin backend -> error claro.
"""
import logging
import threading
import time

log = logging.getLogger("instant2")

WINDOWS_KEYS = {"f9": 0x78, "f10": 0x79, "f20": 0x83, "scroll": 0x91, "pause": 0x13}
POSIX_KEYS = ("f9", "f10", "f11", "f12")

_WINDOWS_NAME_VKS = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "shift": 0x10, "control": 0x11, "ctrl": 0x11, "alt": 0x12,
    "pause": 0x13, "caps_lock": 0x14, "escape": 0x1B, "esc": 0x1B,
    "space": 0x20, "page_up": 0x21, "prior": 0x21, "page_down": 0x22,
    "next": 0x22, "end": 0x23, "home": 0x24, "left": 0x25,
    "up": 0x26, "right": 0x27, "down": 0x28, "print_screen": 0x2C,
    "insert": 0x2D, "delete": 0x2E, "win": 0x5B, "num_lock": 0x90,
    "scroll_lock": 0x91, "shift_l": 0xA0, "shift_r": 0xA1,
    "control_l": 0xA2, "control_r": 0xA3, "alt_l": 0xA4, "alt_r": 0xA5,
    "semicolon": 0xBA, "equal": 0xBB, "comma": 0xBC, "minus": 0xBD,
    "period": 0xBE, "slash": 0xBF, "grave": 0xC0, "bracketleft": 0xDB,
    "backslash": 0xDC, "bracketright": 0xDD, "apostrophe": 0xDE,
}
_WINDOWS_SHIFTED_VKS = {
    "!": 0x31, "@": 0x32, "#": 0x33, "$": 0x34, "%": 0x35,
    "^": 0x36, "&": 0x37, "*": 0x38, "(": 0x39, ")": 0x30,
    "_": 0xBD, "+": 0xBB, ":": 0xBA, '"': 0xDE, "<": 0xBC,
    ">": 0xBE, "?": 0xBF, "~": 0xC0, "{": 0xDB, "}": 0xDD,
    "|": 0xDC,
}
_VK_LABELS = {
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x10: "Shift",
    0x11: "Ctrl", 0x12: "Alt", 0x13: "Pause", 0x14: "Caps Lock",
    0x1B: "Esc", 0x20: "Space", 0x21: "Page Up", 0x22: "Page Down",
    0x23: "End", 0x24: "Home", 0x25: "←", 0x26: "↑", 0x27: "→",
    0x28: "↓", 0x2C: "Print Screen", 0x2D: "Insert", 0x2E: "Delete",
    0x5B: "Win", 0x5C: "Win", 0x90: "Num Lock", 0x91: "Scroll Lock",
    0xA0: "Shift", 0xA1: "Shift", 0xA2: "Ctrl", 0xA3: "Ctrl",
    0xA4: "Alt", 0xA5: "Alt", 0xBA: ";", 0xBB: "=", 0xBC: ",",
    0xBD: "-", 0xBE: ".", 0xBF: "/", 0xC0: "`", 0xDB: "[",
    0xDC: "\\", 0xDD: "]", 0xDE: "'",
}
_POSIX_SPECIAL_KEYS = {
    "backspace": "backspace", "tab": "tab", "enter": "enter",
    "return": "enter", "esc": "esc", "escape": "esc", "space": "space",
    "delete": "delete", "insert": "insert", "home": "home", "end": "end",
    "page_up": "page_up", "page_down": "page_down", "left": "left",
    "right": "right", "up": "up", "down": "down", "caps_lock": "caps_lock",
    "num_lock": "num_lock", "scroll_lock": "scroll_lock", "pause": "pause",
    "print_screen": "print_screen",
}


def _windows_vk(key_name):
    key = _key_name(key_name)
    if key.startswith("vk:"):
        try:
            vk = int(key[3:])
        except ValueError:
            return None
        return vk if 8 <= vk <= 0xFE else None
    if key in WINDOWS_KEYS:
        return WINDOWS_KEYS[key]
    if key in _WINDOWS_NAME_VKS:
        return _WINDOWS_NAME_VKS[key]
    if key in _WINDOWS_SHIFTED_VKS:
        return _WINDOWS_SHIFTED_VKS[key]
    if len(key) == 1 and key.isascii():
        char = key.upper()
        if "A" <= char <= "Z" or "0" <= char <= "9":
            return ord(char)
    if key.startswith("f") and key[1:].isdigit():
        number = int(key[1:])
        if 1 <= number <= 24:
            return 0x70 + number - 1
    return None


def is_valid_key(key_name):
    import sys

    key = _key_name(key_name)
    if sys.platform == "win32":
        return _windows_vk(key) is not None
    return (
        key in POSIX_KEYS
        or key in _POSIX_SPECIAL_KEYS
        or (len(key) == 1 and key.isprintable())
    )


def key_label(key_name):
    key = _key_name(key_name)
    vk = _windows_vk(key)
    if vk is not None:
        if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
            return chr(vk)
        if 0x60 <= vk <= 0x69:
            return f"Num {vk - 0x60}"
        if 0x70 <= vk <= 0x87:
            return f"F{vk - 0x70 + 1}"
        return _VK_LABELS.get(vk, f"VK {vk:02X}")
    if key in _POSIX_SPECIAL_KEYS:
        return {
            "space": "Space", "enter": "Enter", "return": "Enter",
            "esc": "Esc", "escape": "Esc", "page_up": "Page Up",
            "page_down": "Page Down", "caps_lock": "Caps Lock",
            "num_lock": "Num Lock", "scroll_lock": "Scroll Lock",
        }.get(key, key.replace("_", " ").title())
    return key.upper()


def _pynput_key_name(key_name):
    key = _key_name(key_name)
    return _POSIX_SPECIAL_KEYS.get(key, key)


def available_keys():
    """Teclas comunes; captura acepta otras teclas individuales admitidas."""
    import sys
    if sys.platform == "win32":
        return tuple(WINDOWS_KEYS)
    return POSIX_KEYS


def _key_name(raw):
    return "" if raw is None else str(raw).strip().lower()


def normalize_key(raw, default="f9"):
    """Normaliza el nombre; una entrada vacía conserva la tecla actual."""
    key = _key_name(raw)
    return key if key else (default or "f9").lower()


def same_binding(left, right, windows=None):
    """Return whether two configured names resolve to the same physical key."""
    import sys
    windows = sys.platform == "win32" if windows is None else bool(windows)
    left, right = _key_name(left), _key_name(right)
    if windows:
        left_vk, right_vk = _windows_vk(left), _windows_vk(right)
        return left_vk is not None and left_vk == right_vk
    aliases = {"return": "enter", "esc": "escape"}
    return aliases.get(left, left) == aliases.get(right, right)


# Scan codes extendidos win (tras prefijo \x00/\xe0) -> nombre. Solo captura.
_SCAN_WIN = {59: "f1", 60: "f2", 61: "f3", 62: "f4", 63: "f5", 64: "f6",
             65: "f7", 66: "f8", 67: "f9", 68: "f10", 133: "f11", 134: "f12"}
# Secuencias xterm posix -> nombre. Solo captura.
_SEQ_POSIX = {"\x1b[20~": "f9", "\x1b[21~": "f10",
              "\x1b[23~": "f11", "\x1b[24~": "f12"}


def _read_keypress():
    """Un keypress crudo -> nombre normalizado ("" = Enter) o None si no se pudo.
    Solo captura para el setup; el dictado sigue con GetAsyncKeyState/pynput."""
    import sys
    try:
        if sys.stdin is None or not sys.stdin.isatty():
            return None
        if sys.platform == "win32":
            import msvcrt
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                return _SCAN_WIN.get(ord(msvcrt.getwch()))
            if ch in ("\r", "\n"):
                return ""
            if ch == " ":
                return "space"
            return ch.lower() if len(ch) == 1 else None
        import select
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                seq = ch
                while len(seq) < 6 and select.select([sys.stdin], [], [], 0.15)[0]:
                    seq += sys.stdin.read(1)
                    if seq in _SEQ_POSIX:
                        break
                return _SEQ_POSIX.get(seq, seq.strip().lower() or None)
            if ch in ("\r", "\n"):
                return ""
            if ch == " ":
                return "space"
            return ch.lower() if ch else None
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:
        return None


def capture_key(
    instruction="Presiona la tecla para dictar... (Enter = tecla actual)",
    default="f9",
):
    """Captura una tecla individual admitida por el backend del sistema."""
    keys = available_keys()
    dflt = (default or "f9").lower()
    print(f"  {instruction}")
    while True:
        pressed = _read_keypress()
        if pressed is None:
            try:
                typed = input(
                    f"  tecla [{dflt}] ({', '.join(keys)} o una tecla individual): ")
            except (EOFError, KeyboardInterrupt):
                print()
                raise KeyboardInterrupt
            key = normalize_key(typed, dflt)
        elif pressed == "":
            key = dflt
        else:
            key = pressed
            print(f"  detectada: {key}")
        if is_valid_key(key):
            return key
        print(f"  '{key}' no es una tecla admitida. Intenta otra.")


class WindowsPolling:
    def __init__(self, key_name):
        import ctypes
        vk = _windows_vk(key_name)
        if vk is None:
            raise ValueError(f"tecla {key_name!r} no soportada en Windows")
        self.vk = vk
        self._user32 = ctypes.windll.user32
        self._stop = threading.Event()
        self._t = None

    def is_down(self):
        return bool(self._user32.GetAsyncKeyState(self.vk) & 0x8000)

    def start(self, on_press, on_release, interval=0.01):
        if self._t is not None and self._t.is_alive():
            log.warning("hotkey polling ya activo; ignoro segundo start.")
            return
        self._stop.clear()

        def _loop():
            was = False
            while not self._stop.is_set():
                try:
                    down = bool(self.is_down())
                except Exception:
                    log.exception("hotkey polling fail (is_down)")
                    time.sleep(interval)
                    continue
                try:
                    if down and not was:
                        on_press()
                    elif not down and was:
                        on_release()
                except Exception:
                    log.exception("hotkey callback fail")
                was = down
                time.sleep(interval)

        self._t = threading.Thread(target=_loop, daemon=True)
        self._t.start()

    def stop(self):
        self._stop.set()
        t, self._t = self._t, None
        if t is not None:
            t.join(timeout=1.0)


class PynputHotkey:
    def __init__(self, key_name, suppress=False):
        key_name = _key_name(key_name)
        if not is_valid_key(key_name) or key_name.startswith("vk:"):
            raise ValueError(f"tecla {key_name!r} no soportada fuera de Windows")
        try:
            from pynput import keyboard as _pk
        except ImportError:
            raise RuntimeError("falta pynput: pip install 'instant2' en linux/mac lo incluye; "
                               "si falla, instala pynput manual.") from None
        self._pk = _pk
        self._suppress = bool(suppress)
        if len(key_name) == 1 and key_name.isprintable():
            self._target = _pk.KeyCode.from_char(key_name)
        else:
            self._target = getattr(_pk.Key, _pynput_key_name(key_name))
        self._listener = None
        self._down = False

    def _norm(self, key):
        try:
            target_char = getattr(self._target, "char", None)
            char = getattr(key, "char", None)
            if target_char is not None and char is not None:
                return char.casefold() == target_char.casefold()
            return key == self._target
        except Exception:
            return False

    def start(self, on_press, on_release, interval=0.01):
        del interval
        if self._listener is not None:
            try:
                if getattr(self._listener, "is_alive", lambda: False)():
                    log.warning("hotkey pynput ya activo; ignoro segundo start.")
                    return
            except Exception:
                pass
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        self._down = False

        def _p(key):
            try:
                if not self._down and self._norm(key):
                    self._down = True
                    on_press()
            except Exception:
                log.exception("hotkey press fail")

        def _r(key):
            try:
                if self._down and self._norm(key):
                    self._down = False
                    on_release()
            except Exception:
                log.exception("hotkey release fail")

        self._listener = self._pk.Listener(
            on_press=_p, on_release=_r, suppress=self._suppress)
        self._listener.start()

    def stop(self):
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass




def create(key_name):
    import os
    import sys
    if sys.platform == "win32":
        return WindowsPolling(key_name)
    if (sys.platform.startswith("linux")
            and (os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
                 == "wayland"
                 or bool(os.environ.get("WAYLAND_DISPLAY")))):
        import importlib
        portal = importlib.import_module("instant2_app.wayland_hotkey")
        return portal.PortalHotkey(key_name)
    return PynputHotkey(key_name)


class WindowsPressHotkey:
    """Consume one global Windows key press before reading the selection."""

    def __init__(self, key_name):
        key_name = _key_name(key_name)
        if not is_valid_key(key_name):
            raise ValueError(f"tecla {key_name!r} no soportada en Windows")
        self.key_name = key_name
        self.vk = _windows_vk(key_name)
        self._handle = None
        self._thread = None
        self._thread_id = None
        self._ready = threading.Event()
        self._startup_error = None
        self._on_press = None
        self._down = False
        self._error_callback = None
        self._ready_callback = None

    def set_error_callback(self, callback):
        self._error_callback = callback

    def set_ready_callback(self, callback):
        self._ready_callback = callback

    def _report_error(self, message):
        if self._error_callback is not None:
            try:
                self._error_callback(message)
            except Exception:
                log.debug("no pude informar el error del atajo de lectura", exc_info=True)

    def _run_hook(self):
        import ctypes
        from ctypes import wintypes

        from instant2_app.gui_lifecycle import winfunctype

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()

        class Point(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

        class Message(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                        ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                        ("time", wintypes.DWORD), ("pt", Point),
                        ("lPrivate", wintypes.DWORD)]

        class KeyboardEvent(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        callback_type = winfunctype(
            ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_void_p)

        def callback(code, message, data):
            if code >= 0:
                event = ctypes.cast(data, ctypes.POINTER(KeyboardEvent)).contents
                # Ignore keyboard events injected by Instant2 itself (Ctrl+C
                # for copying the current selection), or a letter shortcut
                # could recursively trigger while the selection is copied.
                if event.vkCode == self.vk and not (event.flags & 0x10):
                    if message in (0x0100, 0x0104):  # WM_KEYDOWN / WM_SYSKEYDOWN
                        if not self._down:
                            self._down = True
                            try:
                                self._on_press()
                            except Exception:
                                log.exception("atajo de lectura: falló on_press")
                        return 1
                    if message in (0x0101, 0x0105):  # WM_KEYUP / WM_SYSKEYUP
                        self._down = False
                        return 1
            return user32.CallNextHookEx(self._handle, code, message, data)

        self._callback = callback_type(callback)  # keep the native callback alive
        try:
            user32.SetWindowsHookExW.argtypes = [
                ctypes.c_int, callback_type, ctypes.c_void_p, wintypes.DWORD]
            user32.SetWindowsHookExW.restype = ctypes.c_void_p
            user32.CallNextHookEx.argtypes = [
                ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
            user32.CallNextHookEx.restype = ctypes.c_ssize_t
            user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
            user32.UnhookWindowsHookEx.restype = wintypes.BOOL
            kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
            kernel32.GetModuleHandleW.restype = ctypes.c_void_p
            user32.GetMessageW.argtypes = [
                ctypes.POINTER(Message), wintypes.HWND, wintypes.UINT, wintypes.UINT]
            user32.GetMessageW.restype = wintypes.BOOL
            user32.PeekMessageW.argtypes = [
                ctypes.POINTER(Message), wintypes.HWND, wintypes.UINT,
                wintypes.UINT, wintypes.UINT]
            user32.PeekMessageW.restype = wintypes.BOOL
            user32.TranslateMessage.argtypes = [ctypes.POINTER(Message)]
            user32.DispatchMessageW.argtypes = [ctypes.POINTER(Message)]
            user32.PostThreadMessageW.argtypes = [
                wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user32.PostThreadMessageW.restype = wintypes.BOOL

            self._handle = user32.SetWindowsHookExW(
                13, self._callback, kernel32.GetModuleHandleW(None), 0)
            if not self._handle:
                raise OSError("Windows no permitió registrar el atajo global")
            # Force creation of the message queue before start() returns, so
            # stop() can reliably post WM_QUIT even on an immediate shutdown.
            queued_message = Message()
            user32.PeekMessageW(ctypes.byref(queued_message), None, 0, 0, 0)
        except Exception as exc:
            self._startup_error = exc
            self._ready.set()
            self._report_error(str(exc))
            return

        self._ready.set()
        if self._ready_callback is not None:
            try:
                self._ready_callback(f"Atajo de lectura activo: {key_label(self.key_name)}")
            except Exception:
                log.debug("no pude informar que el atajo de lectura quedó activo",
                          exc_info=True)
        try:
            message = Message()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:
            log.exception("bucle del atajo de lectura falló")
            self._report_error(str(exc))
        finally:
            handle, self._handle = self._handle, None
            if handle:
                user32.UnhookWindowsHookEx(handle)

    def start(self, on_press, on_release=None, interval=0.01):
        del on_release, interval
        if self._thread is not None and self._thread.is_alive():
            return
        self._ready.clear()
        self._startup_error = None
        self._on_press = on_press
        self._down = False
        self._thread = threading.Thread(
            target=self._run_hook, name="instant2-read-hotkey", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("Windows no confirmó el inicio del atajo de lectura")
        if self._startup_error is not None:
            raise RuntimeError("no se pudo registrar el atajo de lectura") from self._startup_error

    def stop(self):
        thread = self._thread
        if thread is None:
            return
        try:
            import ctypes
            if self._thread_id is not None:
                ctypes.windll.user32.PostThreadMessageW(
                    self._thread_id, 0x0012, 0, 0)  # WM_QUIT
            thread.join(timeout=1.0)
            if not thread.is_alive():
                self._thread = None
                self._thread_id = None
        except Exception:
            log.debug("no pude retirar el atajo de lectura", exc_info=True)


def create_press(key_name):
    """Create a global press-only hotkey that consumes its trigger key."""
    import os
    import sys
    if sys.platform == "win32":
        return WindowsPressHotkey(key_name)
    if (sys.platform.startswith("linux")
            and (os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
                 == "wayland"
                 or bool(os.environ.get("WAYLAND_DISPLAY")))):
        import importlib
        portal = importlib.import_module("instant2_app.wayland_hotkey")
        return portal.PortalHotkey(key_name)
    # pynput suppression is backend-wide on several POSIX systems; do not
    # enable it here because it could swallow unrelated typing. Wayland uses
    # the compositor portal, which owns the global key event.
    return PynputHotkey(key_name, suppress=False)
