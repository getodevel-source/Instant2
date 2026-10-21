"""Arranque con el sistema: enable/disable/idempotencia, sin tocar el SO real."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolation  # noqa: F401  (aisla el estado real del usuario)

from instant2_app import autostart as a


def _check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(1)


def _redir(base):
    """Redirige HOME/APPDATA/XDG a un tmp; devuelve dict para restaurar."""
    saved = {}
    for k in ("APPDATA", "HOME", "USERPROFILE", "XDG_CONFIG_HOME",
              "INSTANT2_AUTOSTART_PLATFORM"):
        saved[k] = os.environ.get(k)
    os.environ["APPDATA"] = base
    os.environ["HOME"] = base
    os.environ["USERPROFILE"] = base
    os.environ.pop("XDG_CONFIG_HOME", None)
    return saved


def _restore(saved):
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _cycle(platform, target_fn, alien_fn):
    import unittest.mock as _mock
    os.environ["INSTANT2_AUTOSTART_PLATFORM"] = platform
    # launchctl no existe en runners Linux/Windows ni funciona en el sandbox
    # de macOS: el ciclo de mac se prueba a nivel plist, sin tocar el SO.
    with _mock.patch.object(a, "_mac_launchctl", return_value=(0, "")):
        target = target_fn()
        _check("%s arranca apagado" % platform, a.is_enabled() is False)
        _check("%s describe una linea" % platform,
               isinstance(a.describe(), str) and len(a.describe().splitlines()) == 1)
        msg1 = a.enable()
        _check("%s enable reporta" % platform, isinstance(msg1, str) and msg1)
        _check("%s enable->on" % platform, a.is_enabled() is True)
        msg2 = a.enable()
        _check("%s enable idempotente" % platform, isinstance(msg2, str) and msg2)
        _check("%s enable x2 sigue on" % platform, a.is_enabled() is True)
        _check("%s disable idempotente-1" % platform, isinstance(a.disable(), str))
        _check("%s disable->off" % platform, a.is_enabled() is False)
        _check("%s disable idempotente-2" % platform, isinstance(a.disable(), str))
        # No borra archivo ajeno con mismo nombre pero contenido distinto.
        os.makedirs(os.path.dirname(target), exist_ok=True)
        alien_fn(target)
        _check("%s no borra ajeno" % platform, os.path.isfile(target))
        _check("%s ajeno no es enabled" % platform, a.is_enabled() is False)
        os.remove(target)


def _plat_paths():
    from instant2_app.autostart import (
        _desktop_file, _link_path, _plist_path)
    return _desktop_file, _link_path, _plist_path


with tempfile.TemporaryDirectory() as d:
    saved = _redir(d)
    try:
        desktop_fn, link_fn, plist_fn = _plat_paths()

        # Linux primero (sin poderes admin, puro archivo de texto).
        _cycle("linux", desktop_fn,
               lambda p: open(p, "w", encoding="utf-8").write("ajeno\n"))

        # macOS (plist stdlib).
        def _alien_plist(p):
            import plistlib
            with open(p, "wb") as f:
                plistlib.dump({"Label": "otro.programa"}, f)
        _cycle("darwin", plist_fn, _alien_plist)

        # Windows con plataforma forzada. El camino de Windows usa PowerShell
        # para crear el acceso; aca se fuerza su ausencia (FileNotFoundError)
        # para que el test ejercite el stub y no dependa de si el runner tiene
        # PowerShell instalado: en macOS el runner si lo tiene y el COM de
        # Windows falla, que es como se descubrio este fallo.
        def _alien_lnk(p):
            with open(p, "w", encoding="utf-8") as f:
                f.write("acceso ajeno\n")

        from unittest.mock import patch
        with patch.object(a, "_powershell",
                          side_effect=FileNotFoundError("powershell")):
            _cycle("win32", link_fn, _alien_lnk)

        frozen_exe = os.path.join(d, "Instant2.exe")
        with patch.object(a.sys, "frozen", True, create=True), \
                patch.object(a.sys, "executable", frozen_exe):
            _check("binario arranca la instancia de bandeja",
                   a._resolve_win_launch() == (frozen_exe, "run", d))

        frozen_posix = os.path.join(d, "instant2-linux")
        with patch.object(a.sys, "frozen", True, create=True), \
                patch.object(a.sys, "executable", frozen_posix):
            _check("portable POSIX usa su binario para autostart",
                   a._resolve_posix_argv() == [frozen_posix, "run"])

        # Windows: acceso ajeno que solo contenga "instant2" como argumento de otra app no se toma por nuestro
        alien_lnk_strict = os.path.join(d, "Instant2 Dictado.lnk")
        with patch.object(a, "_read_win_target", return_value=("C:\\OtherApp\\other.exe", "--instant2-flag")):
            _check("match estricto no adopta app ajena", a._is_ours_win(alien_lnk_strict) is False)

        os.environ["HOME"] = os.path.join(d, "no-existe-definitivamente")
        os.environ["APPDATA"] = os.path.join(d, "no-existe-definitivamente")
        try:
            _check("is_enabled no crashea", a.is_enabled() is False)
        finally:
            os.environ["HOME"] = d
            os.environ["APPDATA"] = d
    finally:
        _restore(saved)

print("OK: autostart verde.")
