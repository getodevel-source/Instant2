"""Build isolated subprocess environments for the source and frozen app."""
import os
import sys


def app_command(args=()):
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, "-m", "instant2_app", *args]


def app_environment():
    """Give independent one-file children their own extraction directory."""
    env = os.environ.copy()
    if getattr(sys, "frozen", False):
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def prepare_webengine_env():
    """Hace que QtWebEngine encuentre su proceso helper en binarios congelados.

    Qt lo busca en `QLibraryInfo::LibraryExecutablesPath`; dentro del árbol
    extraído de un binario las rutas cambian, y en macOS además espera
    `.app/Helpers/QtWebEngineProcess`. La variable `QTWEBENGINEPROCESS_PATH`
    (documentada por Qt) fija el ejecutable exacto. Best-effort e idempotente:
    si ya está fijada o no hay helper, no toca nada.
    """
    if not getattr(sys, "frozen", False) or os.environ.get("QTWEBENGINEPROCESS_PATH"):
        return None
    root = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(sys.executable))
    candidates = (
        os.path.join(root, "PySide6", "QtWebEngineProcess.app", "Contents", "MacOS",
                     "QtWebEngineProcess"),
        os.path.join(root, "PySide6", "QtWebEngineProcess"),
        os.path.join(root, "PySide6", "libexec", "QtWebEngineProcess"),
        os.path.join(root, "libexec", "QtWebEngineProcess"),
    )
    for candidate in candidates:
        if os.path.isfile(candidate):
            os.environ["QTWEBENGINEPROCESS_PATH"] = candidate
            return candidate
    return None
