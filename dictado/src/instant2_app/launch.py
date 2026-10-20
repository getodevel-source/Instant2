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
        _drop_stale_webengine_path(env)
    return env


def _parent_extraction_root():
    """El árbol extraído del proceso actual (el `_MEIPASS` del onefile)."""
    root = getattr(sys, "_MEIPASS", None)
    if root:
        return os.path.abspath(root)
    return os.path.dirname(os.path.abspath(sys.executable))


def _drop_stale_webengine_path(env):
    """Saca del entorno del hijo el helper de QtWebEngine del padre.

    `prepare_webengine_env` apunta el helper al árbol extraído del proceso
    que corre, y ese árbol se borra cuando el padre sale. El hijo no puede
    usar esa ruta: tiene que buscar la suya (o quedarse sin ella).
    """
    path = env.get("QTWEBENGINEPROCESS_PATH")
    if not path:
        return
    root = os.path.normcase(_parent_extraction_root())
    target = os.path.normcase(os.path.abspath(path))
    if target == root or target.startswith(root + os.sep):
        env.pop("QTWEBENGINEPROCESS_PATH", None)


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
