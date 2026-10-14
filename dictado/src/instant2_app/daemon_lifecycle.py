"""Single-instance ownership for the background daemon.

Windows: named mutex. POSIX: pidfile + flock (el lock lo libera el kernel
si el proceso muere, asi no quedan candados rancios).

Dueño del pidfile: lo escribe el lock POSIX (`_acquire_posix`, mismo archivo
que `daemon.pid_path`), y `daemon.clear_pid()` lo borra al salir solo si
sigue siendo el PID propio; si el PID es de otro proceso, se deja intacto.
"""
import ctypes
import logging
import os
from ctypes import wintypes

log = logging.getLogger("instant2")

_PIDFILE_NAME = "instant2.pid"
_lock_file = None


def _daemon_pidfile():
    from instant2_app.paths import config_dir
    directory = config_dir()
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, _PIDFILE_NAME)


def _acquire_posix():
    """Toma el lock del pidfile; None si otro proceso lo tiene."""
    global _lock_file
    if _lock_file is not None:
        return _lock_file
    try:
        path = _daemon_pidfile()
    except Exception:
        log.exception("no pude resolver el pidfile; arranco sin lock")
        return True
    try:
        handle = open(path, "a+b")
    except OSError:
        log.exception("no pude abrir %s; arranco sin lock", path)
        return True
    try:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except ImportError:
        pass
    except OSError:
        handle.close()
        return None
    try:
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()).encode("ascii"))
        handle.flush()
        os.fsync(handle.fileno())
    except OSError:
        log.exception("no pude escribir el pid en %s", path)
    _lock_file = handle
    return handle


_MUTEX_NAME = "Local\\Instant2.Daemon"
_ERROR_ALREADY_EXISTS = 183


def acquire_daemon_mutex():
    """Return the daemon lock handle, or None if another instance owns it.

    El dueño es `__main__.main()`: lo libera en su `finally`. `Daemon.run()`
    no lo toca (ver `test_daemon_mutex_released_after_failed_start`).
    """
    if os.name != "nt":
        return _acquire_posix()
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
        return None
    return handle


def release_daemon_mutex(handle):
    global _lock_file
    if os.name == "nt":
        if handle not in (None, True):
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle(handle)
        return
    if handle is True or handle is None:
        return
    try:
        handle.close()
    except OSError:
        pass
    finally:
        _lock_file = None
    from instant2_app.daemon import clear_pid
    clear_pid()
