"""Windows-only helpers for keeping a single Instant2 GUI window."""
import ctypes
import os
import time
from ctypes import wintypes


_MUTEX_NAME = "Local\\Instant2.GUI"
_ERROR_ALREADY_EXISTS = 183
_SW_RESTORE = 9
_WM_CLOSE = 0x0010
_GUI_ACTION_MESSAGE = 0x8000 + 0x4A1
_ACTION_START_DAEMON = 1
_ACTION_SETUP = 2
_ACTION_DIAGNOSTICS = 3


def winfunctype(*argtypes):
    """El tipo de callback de Windows: `ctypes.WINFUNCTYPE`.

    Vive en `ctypes`, no en `ctypes.wintypes`: el nombre
    `ctypes.wintypes.WINFUNCTYPE` no existe (ni en 3.12 ni en 3.14) y la
    app instalada 0.1.0 crasheaba al arrancar el panel por tomarlo de alla
    ("module 'ctypes.wintypes' has no attribute 'WINFUNCTYPE'"). Se resuelve
    aca, en un solo lugar, para que no vuelva a pasar y para que un
    interprete que no lo traiga falle con un mensaje claro.
    """
    factory = getattr(ctypes, "WINFUNCTYPE", None)
    if factory is None:
        raise AttributeError(
            "ctypes.WINFUNCTYPE no existe: este interprete de Python no "
            "trae el tipo de callback de Windows")
    return factory(*argtypes)


def _user32():
    dll = ctypes.WinDLL("user32", use_last_error=True)
    dll.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
    dll.FindWindowW.restype = wintypes.HWND
    dll.EnumWindows.argtypes = (winfunctype(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM)
    dll.EnumWindows.restype = wintypes.BOOL
    dll.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    dll.GetClassNameW.restype = ctypes.c_int
    dll.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    dll.GetWindowTextW.restype = ctypes.c_int
    dll.IsWindow.argtypes = (wintypes.HWND,)
    dll.IsWindow.restype = wintypes.BOOL
    dll.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    dll.SetForegroundWindow.argtypes = (wintypes.HWND,)
    dll.SetForegroundWindow.restype = wintypes.BOOL
    dll.PostMessageW.argtypes = (
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    dll.PostMessageW.restype = wintypes.BOOL
    dll.GetWindowThreadProcessId.argtypes = (
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    dll.GetWindowThreadProcessId.restype = wintypes.DWORD
    return dll


def _is_our_window(user32, hwnd):
    """True si el hwnd sigue siendo una ventana nuestra de Instant2.

    No basta el titulo ("Instant2" lo puede tener cualquier app): se exige la
    clase de ventana Qt registrada por nuestro panel. Sin user32 real (mocks
    de tests) se acepta el hwnd tal cual.
    """
    get_class = getattr(user32, "GetClassNameW", None)
    if get_class is None:
        return True
    try:
        buf = ctypes.create_unicode_buffer(256)
        if not get_class(hwnd, buf, 256):
            return False
    except Exception:
        return True
    return buf.value.startswith("Qt")


def _find_our_gui(user32):
    """Primer hwnd nuestro con titulo Instant2, o None.

    El título puede no ser exacto (p. ej. "Instant2 — Ajustes"): se enumeran
    las ventanas top-level buscando "instant2" en el título + clase Qt*.
    `FindWindowW(None, "Instant2")` queda como atajo rápido antes de enumerar.
    """
    hwnd = user32.FindWindowW(None, "Instant2")
    if hwnd and _is_our_window(user32, hwnd):
        return hwnd
    enum = getattr(user32, "EnumWindows", None)
    get_text = getattr(user32, "GetWindowTextW", None)
    if enum is None or get_text is None:
        return hwnd if hwnd and _is_our_window(user32, hwnd) else None
    enum_is_real = getattr(enum, "argtypes", None) is not None
    if enum_is_real:
        try:
            callback_type = winfunctype(
                wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        except AttributeError:
            enum_is_real = False
    found = []

    def _visit(candidate, _param):
        try:
            buf = ctypes.create_unicode_buffer(256)
            if not get_text(candidate, buf, 256):
                return True
            if "instant2" not in buf.value.casefold():
                return True
            if _is_our_window(user32, candidate):
                found.append(candidate)
                return False
        except Exception:
            return True
        return True

    try:
        if enum_is_real:
            enum(callback_type(_visit), 0)
        else:
            # Fake de tests: EnumWindows llama al callback por ventana.
            enum(_visit, 0)
    except Exception:
        return hwnd if hwnd and _is_our_window(user32, hwnd) else None
    return found[0] if found else None


def focus_existing_gui(request_daemon_start=False, page=None):
    """Restore the GUI and optionally request a page or daemon start."""
    if os.name != "nt":
        return False
    user32 = _user32()
    hwnd = _find_our_gui(user32)
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, _SW_RESTORE)
    if not user32.SetForegroundWindow(hwnd):
        return False
    action = None
    if request_daemon_start:
        action = _ACTION_START_DAEMON
    elif page == "setup":
        action = _ACTION_SETUP
    elif page == "diagnostics":
        action = _ACTION_DIAGNOSTICS
    if action is not None:
        user32.PostMessageW(hwnd, _GUI_ACTION_MESSAGE, action, 0)
    return True


def terminate_existing_gui(graceful_ms=1500):
    """Cierra (o termina) el panel si está abierto. True si había uno.

    Un WM_CLOSE solo no alcanza para tareas que necesitan la carpeta libre
    (desinstalador, actualización): el panel puede quedar vivo esperando
    workers. Primero se intenta el cierre limpio y, si sigue ahí, se termina
    el proceso dueño de la ventana, previa verificación de que sigue siendo
    una ventana nuestra (clase Qt + mismo hwnd): nunca se mata por título.
    """
    if os.name != "nt":
        return False
    user32 = _user32()
    hwnd = _find_our_gui(user32)
    if not hwnd:
        return False
    close_existing_gui()
    deadline = time.monotonic() + max(0, graceful_ms) / 1000
    while time.monotonic() < deadline:
        if not _find_our_gui(user32):
            return True
        time.sleep(0.1)
    hwnd = _find_our_gui(user32)
    if not hwnd:
        return True
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value or pid.value == os.getpid():
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    _PROCESS_TERMINATE = 0x0001
    if not _is_our_window(user32, hwnd):
        return False
    handle = kernel32.OpenProcess(_PROCESS_TERMINATE, False, pid.value)
    if not handle:
        return False
    try:
        return bool(kernel32.TerminateProcess(handle, 0))
    finally:
        kernel32.CloseHandle(handle)


def close_existing_gui():
    """Ask the existing Qt window to close through its normal close handler."""
    if os.name != "nt":
        return False
    user32 = _user32()
    hwnd = _find_our_gui(user32)
    if not hwnd:
        return False
    return bool(user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0))


def acquire_gui_mutex(request_daemon_start=False, page=None):
    """Return the owning mutex handle, or None when another GUI owns it."""
    if os.name != "nt":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (
        wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        focus_existing_gui(
            request_daemon_start=request_daemon_start, page=page)
        return None
    return handle


def release_gui_mutex(handle):
    if os.name == "nt" and handle not in (None, True):
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle(handle)
