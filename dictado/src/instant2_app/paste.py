"""Pegado por SO. Windows: clipboard + Ctrl+V (probado, respeta ñ/á).
Linux: xclip/xsel + xdotool en X11; wl-clipboard + wtype en Wayland.
macOS: pbcopy + pynput Cmd+V.

El clipboard previo se guarda y restaura (el dictado no debe comerse lo
que el usuario tenia copiado) cuando las herramientas del escritorio lo
permiten. `paste("")` es no-op.
"""
import logging
import time

log = logging.getLogger("instant2")

# Espera de pegado: el Ctrl+V necesita que el clipboard ya este escrito.
# Poll corto con tope (hasta ~200 ms) en vez de sleep fijo ciego.
_PASTE_WAIT_S = 0.2
_WINDOWS_KEY_SETTLE_S = 0.04
_WINDOWS_PASTE_SETTLE_S = 0.2


def _wait_paste_ready(check=None):
    """Espera robusta del pegado: poll con tope, no sleep fijo ciego.

    `check`: vuelve a leer el clipboard y devuelve True cuando ya tiene
    el texto (solo donde se puede leer). Devuelve False si vence el plazo.
    Sin `check`, pausa minima de 20 ms para ceder el turno al SO.
    Nunca supera `_PASTE_WAIT_S`.
    """
    if check is None:
        time.sleep(0.02)
        return True
    deadline = time.monotonic() + _PASTE_WAIT_S
    while time.monotonic() < deadline:
        try:
            if check():
                return True
        except Exception:
            # El clipboard de Windows puede estar ocupado momentaneamente;
            # seguir esperando permite recuperarse dentro del plazo.
            pass
        time.sleep(0.02)
    return False


def paste(text):
    import sys
    if not text:
        return
    if sys.platform == "win32":
        _paste_windows(text)
        return
    if sys.platform == "darwin":
        _paste_macos(text)
        return
    _paste_linux(text)


def _paste_windows(text):
    import pyperclip
    import keyboard
    try:
        prev = pyperclip.paste()
    except Exception:
        prev = None
    pyperclip.copy(text)
    if not _wait_paste_ready(lambda: pyperclip.paste() == text):
        raise RuntimeError(
            "No pude confirmar que el dictado estuviera en el portapapeles. "
            "Probá Ctrl+V manualmente.")
    sent = False
    try:
        # keyboard.press_and_release() emite Ctrl/V down/up casi sin pausa.
        # Algunos campos de navegador no procesan bien esa ráfaga sintética;
        # dejar que cada transición llegue al sistema mejora la entrega.
        keyboard.press("ctrl")
        try:
            time.sleep(_WINDOWS_KEY_SETTLE_S)
            keyboard.press("v")
            try:
                time.sleep(_WINDOWS_KEY_SETTLE_S)
            finally:
                keyboard.release("v")
        finally:
            keyboard.release("ctrl")
        sent = True
    finally:
        if sent and prev is not None:
            # El navegador procesa WM_PASTE después de recibir Ctrl+V. Darle
            # margen antes de restaurar evita que lea el clipboard anterior.
            time.sleep(_WINDOWS_PASTE_SETTLE_S)
            try:
                pyperclip.copy(prev)
            except Exception:
                pass


def _paste_linux(text):
    import os
    import shutil
    import subprocess
    # Wayland/XWayland usa wl-clipboard y wtype. Sin colapsar vars con `or`:
    # en XWayland XDG_SESSION_TYPE puede ser "x11" pero WAYLAND_DISPLAY sigue
    # seteado y xdotool no puede inyectar teclas en superficies Wayland.
    session_type = (os.environ.get("XDG_SESSION_TYPE") or "").strip().lower()
    wayland_display = (os.environ.get("WAYLAND_DISPLAY") or "").strip()
    is_wayland = (session_type == "wayland" or "wayland" in session_type
                  or bool(wayland_display))
    if is_wayland:
        wl_copy = shutil.which("wl-copy")
        wl_paste = shutil.which("wl-paste")
        wtype = shutil.which("wtype")
        if wl_copy is None or wl_paste is None:
            raise RuntimeError(
                "Wayland necesita wl-clipboard para preservar el portapapeles "
                "(instalá wl-clipboard; en Arch/Omarchy: "
                "sudo pacman -S wl-clipboard). No se modificó el portapapeles.")
        if wtype is None:
            raise RuntimeError(
                "Wayland necesita wtype para pegar con Ctrl+V "
                "(en Arch/Omarchy: sudo pacman -S wtype). "
                "No se modificó el portapapeles.")
        _paste_wayland(text, wl_copy, wl_paste, wtype, subprocess)
        return
    tool = shutil.which("xclip") or shutil.which("xsel")
    if tool is None:
        raise RuntimeError("falta xclip o xsel: sudo apt install xclip xdotool")
    xdotool = shutil.which("xdotool")
    if xdotool is None:
        raise RuntimeError(
            "falta xdotool: sudo apt install xdotool "
            "(solo X11; en Wayland no pega teclas; no se piso el clipboard)")
    try:
        prev = subprocess.run(
            [tool, "-selection", "clipboard", "-o"] if tool.endswith("xclip")
            else [tool, "--clipboard", "--output"],
            capture_output=True, timeout=10).stdout
    except Exception:
        prev = None
    if tool.endswith("xclip"):
        subprocess.run([tool, "-selection", "clipboard"], input=text.encode("utf-8"),
                       check=True, timeout=10)
    else:
        subprocess.run([tool, "--clipboard", "--input"], input=text.encode("utf-8"),
                       check=True, timeout=10)
    _wait_paste_ready()
    try:
        subprocess.run([xdotool, "key", "ctrl+v"], check=True, timeout=10)
    finally:
        if prev is not None:
            _wait_paste_ready()
            try:
                if tool.endswith("xclip"):
                    subprocess.run([tool, "-selection", "clipboard"],
                                   input=prev, check=True, timeout=10)
                else:
                    subprocess.run([tool, "--clipboard", "--input"],
                                   input=prev, check=True, timeout=10)
            except Exception:
                pass


def _paste_wayland(text, wl_copy, wl_paste, wtype, subprocess):
    """Usa el portapapeles Wayland y su teclado virtual, sin leer /dev/input."""
    try:
        previous = subprocess.run(
            [wl_paste, "--no-newline", "--type", "text/plain"],
            capture_output=True, timeout=10)
        prev = previous.stdout if previous.returncode == 0 else None
    except Exception:
        prev = None

    subprocess.run([wl_copy, "--type", "text/plain"],
                   input=text.encode("utf-8"), check=True, timeout=10)
    _wait_paste_ready()
    pasted = False
    try:
        subprocess.run([wtype, "-M", "ctrl", "v", "-m", "ctrl"],
                       check=True, timeout=10)
        pasted = True
    except Exception as exc:
        # Dejar el dictado disponible para un Ctrl+V manual da una salida
        # recuperable si el compositor rechaza el teclado virtual.
        raise RuntimeError(
            "No pude enviar Ctrl+V en Wayland; el texto quedó en el "
            "portapapeles para pegarlo manualmente.") from exc
    finally:
        if pasted and prev is not None:
            _wait_paste_ready()
            try:
                subprocess.run([wl_copy, "--type", "text/plain"],
                               input=prev, check=True, timeout=10)
            except Exception:
                log.warning("no pude restaurar el portapapeles previo en Wayland",
                            exc_info=True)


def _paste_macos(text):
    import subprocess
    try:
        prev = subprocess.run(["pbpaste"], capture_output=True,
                              timeout=10).stdout
    except Exception:
        prev = None
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True, timeout=10)
    _wait_paste_ready()
    try:
        from pynput.keyboard import Controller, Key
    except ImportError:
        raise RuntimeError("falta pynput para pegar en macOS.") from None
    kb = Controller()
    try:
        with kb.pressed(Key.cmd):
            kb.press("v")
            kb.release("v")
    finally:
        if prev is not None:
            _wait_paste_ready()
            try:
                subprocess.run(["pbcopy"], input=prev,
                               check=True, timeout=10)
            except Exception:
                pass
