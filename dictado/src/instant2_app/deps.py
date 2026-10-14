"""Chequeo + autoinstalacion asistida de dependencias (solo stdlib).

Responde a: "la instalacion puede detectar y descargar dependencias
faltantes junto con la app?" Si: `instant2 setup --check-deps` muestra la
tabla y `--fix-deps` instala lo que el SO permite sin intervencion.

Matriz auto / no-auto:
- Windows: Python>=3.12, pip y paquetes por pip (auto=si). Sin
  dependencias del sistema (entrada "sistema": ok=True con nota).
- Linux X11: portaudio, xclip/xsel y xdotool por apt o pacman
  (auto=si solo con sudo sin password o root; nunca pide password).
- Linux Wayland: dbus-fast por pip; wl-clipboard y wtype por apt/pacman
  (auto=si solo con sudo sin password o root).
- macOS: portaudio por brew (auto=si solo si brew existe).
- Sin red no se instala nada: queda [FALTA] con hint, sin traceback.
"""
import ctypes.util
import importlib.util
import logging
import os
import shutil
import socket
import subprocess
import sys

log = logging.getLogger("instant2")

MIN_PYTHON = (3, 12)

_PIP = {
    "numpy": "numpy",
    "sounddevice": "sounddevice",
    "sherpa_onnx": "sherpa-onnx",
    "keyboard": "keyboard",
    "pynput": "pynput",
    "dbus_fast": "dbus-fast",
    "pyperclip": "pyperclip",
    "pystray": "pystray",
    "PIL": "Pillow",
    "PySide6": "PySide6",
}

_APT = {
    "portaudio": "libportaudio2",
    "xclip/xsel": "xclip",
    "xdotool": "xdotool",
    "wl-clipboard": "wl-clipboard",
    "wtype": "wtype",
    "xdg-desktop-portal": "xdg-desktop-portal",
    "xdg-desktop-portal-hyprland": "xdg-desktop-portal-hyprland",
}

_PACMAN = {
    "portaudio": "portaudio",
    "xclip/xsel": "xclip",
    "xdotool": "xdotool",
    "wl-clipboard": "wl-clipboard",
    "wtype": "wtype",
    "xdg-desktop-portal": "xdg-desktop-portal",
    "xdg-desktop-portal-hyprland": "xdg-desktop-portal-hyprland",
}


def _entry(ok, hint, auto=False):
    return {"ok": bool(ok), "hint": hint, "auto": bool(auto)}


def _importable(mod):
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def _run(cmd, timeout):
    return subprocess.run(cmd, stdout=subprocess.DEVNULL,
                          stderr=subprocess.STDOUT, timeout=timeout)


def _can_apt():
    """True si apt-get puede correr sin pedir password (root o sudo -n)."""
    try:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            return True
        r = _run(["sudo", "-n", "true"], timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def _arch_based():
    """Detect Arch and Arch-derived systems without assuming their name."""
    try:
        values = {}
        with open("/etc/os-release", encoding="utf-8") as handle:
            for line in handle:
                key, separator, value = line.partition("=")
                if separator and key in ("ID", "ID_LIKE"):
                    values[key] = value.strip().strip('"').lower()
        family = " ".join(values.values()).split()
        return bool({"arch", "omarchy"}.intersection(family)) and \
            shutil.which("pacman") is not None
    except OSError:
        return False


def _can_pacman():
    if shutil.which("pacman") is None:
        return False
    try:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            return True
        return shutil.which("sudo") is not None and \
            _run(["sudo", "-n", "true"], timeout=10).returncode == 0
    except Exception:
        return False


def _portal_component_present(name):
    """Portal executables live under libexec/lib, not always in PATH."""
    if shutil.which(name) is not None:
        return True
    paths = {
        "xdg-desktop-portal": (
            "/usr/lib/xdg-desktop-portal",
            "/usr/libexec/xdg-desktop-portal",
        ),
        "xdg-desktop-portal-hyprland": (
            "/usr/lib/xdg-desktop-portal-hyprland",
            "/usr/libexec/xdg-desktop-portal-hyprland",
            "/usr/share/xdg-desktop-portal/portals/hyprland.portal",
        ),
    }
    return any(os.path.isfile(path) for path in paths.get(name, ()))


def _lib_present(name):
    """Busca libreria .so por ctypes, ldconfig o dpkg (solo Linux)."""
    try:
        if ctypes.util.find_library(name):
            return True
    except Exception:
        pass
    try:
        r = subprocess.run(["ldconfig", "-p"], capture_output=True,
                           text=True, timeout=10)
        if r.returncode == 0 and name in (r.stdout or ""):
            return True
    except FileNotFoundError:
        pass
    except Exception:
        pass
    # Fallback sin ldconfig: el .so suele estar en las rutas del linker.
    for directory in ("/lib", "/usr/lib", "/lib64", "/usr/lib64",
                      "/usr/local/lib"):
        try:
            for entry in os.listdir(directory):
                if entry.startswith("lib%s" % name) and ".so" in entry:
                    return True
        except OSError:
            continue
    return False


def _dpkg_ok(pkg):
    try:
        r = _run(["dpkg", "-s", pkg], timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def _has_net():
    """Hay red para descargar? Via proxy se asume que si (pip lo usa)."""
    env = {k.lower(): v for k, v in os.environ.items()}
    if env.get("https_proxy") or env.get("http_proxy"):
        return True
    try:
        socket.create_connection(("pypi.org", 443), timeout=4).close()
        return True
    except Exception:
        return False


def check():
    """Devuelve dict por dependencia: {nombre: {ok, hint, auto}}."""
    out = {}
    plat = sys.platform

    ver = sys.version_info
    out["python"] = _entry(
        ver >= MIN_PYTHON,
        f"Python {sys.version.split()[0]} (>=3.12 OK)."
        if ver >= MIN_PYTHON else
        f"Python {sys.version.split()[0]}: necesitas 3.12+ desde python.org.",
        auto=False)

    out["pip"] = _entry(
        _importable("pip"),
        "pip disponible." if _importable("pip")
        else "python -m ensurepip (o reinstala Python con pip).",
        auto=True)  # via ensurepip (stdlib, sin red)

    pkgs = ["numpy", "sounddevice", "sherpa_onnx", "pyperclip",
            "PySide6", "PIL"]
    wayland = (plat.startswith("linux") and (
        bool(os.environ.get("WAYLAND_DISPLAY"))
        or os.environ.get("XDG_SESSION_TYPE", "").strip().lower() == "wayland"))
    if plat == "win32":
        pkgs.append("keyboard")
    elif wayland:
        pkgs.append("dbus_fast")
    else:
        pkgs.append("pynput")
    if plat == "win32":
        pkgs.append("pystray")
    for mod in pkgs:
        ok = _importable(mod)
        if ok:
            out[mod] = _entry(True, "instalado (importable).", auto=True)
        else:
            extra = ""
            if mod == "keyboard":
                extra = " Si la tecla no anda en apps elevadas, corre como admin."
            if mod == "pynput":
                extra = " En Linux necesita X11."
            if mod == "PySide6" and plat.startswith("linux"):
                extra = " Si el panel no abre, falta libxcb-cursor0 (apt)."
            out[mod] = _entry(False, f"pip install {_PIP[mod]}.{extra}",
                              auto=True)

    if plat == "win32":
        out["sistema"] = _entry(
            True, "Windows no necesita dependencias del sistema.", auto=False)
        return out

    if plat == "darwin":
        try:
            r = _run(["brew", "--prefix", "portaudio"], timeout=15)
            ok = r.returncode == 0
        except Exception:
            ok = False
        brew = shutil.which("brew") is not None
        out["portaudio"] = _entry(
            ok, "portaudio presente (brew)." if ok
            else "brew install portaudio.", auto=brew)
        out["sistema"] = _entry(
            True, "macOS usa pbcopy nativo (sin xclip/xdotool).", auto=False)
        return out

    # Linux.
    arch = _arch_based()
    can_system_install = _can_pacman() if arch else _can_apt()
    ok_pa = _lib_present("portaudio") or _dpkg_ok("libportaudio2")
    out["portaudio"] = _entry(
        ok_pa, "libportaudio2 presente." if ok_pa
        else ("pacman -S portaudio." if arch
              else "sudo apt install libportaudio2."), auto=can_system_install)
    if wayland:
        out["xclip/xsel"] = _entry(
            True, "no se necesita en Wayland; se usa wl-clipboard.", auto=False)
        out["xdotool"] = _entry(
            True, "no se necesita en Wayland; se usa wtype.", auto=False)
        portal_ok = _portal_component_present("xdg-desktop-portal")
        out["xdg-desktop-portal"] = _entry(
            portal_ok, "portal de escritorio presente." if portal_ok else
            ("pacman -S xdg-desktop-portal." if arch else
             "instala y activa xdg-desktop-portal para registrar el atajo."),
            auto=can_system_install)
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        hyprland = ("hyprland" in desktop
                    or bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")))
        if hyprland:
            portal_backend_ok = _portal_component_present(
                "xdg-desktop-portal-hyprland")
            out["xdg-desktop-portal-hyprland"] = _entry(
                portal_backend_ok,
                "backend del portal Hyprland presente." if portal_backend_ok else
                ("pacman -S xdg-desktop-portal-hyprland." if arch else
                 "instala xdg-desktop-portal-hyprland y reinicia el portal."),
                auto=can_system_install)
        ok_wl = (shutil.which("wl-copy") is not None
                 and shutil.which("wl-paste") is not None)
        out["wl-clipboard"] = _entry(
            ok_wl, "wl-clipboard presente." if ok_wl else
            ("pacman -S wl-clipboard." if arch
             else "instala wl-clipboard para gestionar el portapapeles Wayland."),
            auto=can_system_install)
        ok_wtype = shutil.which("wtype") is not None
        out["wtype"] = _entry(
            ok_wtype, "wtype presente (pegado Wayland)." if ok_wtype else
            ("pacman -S wtype." if arch else "sudo apt install wtype."),
            auto=can_system_install)
    else:
        ok_cb = shutil.which("xclip") is not None or shutil.which("xsel") is not None
        out["xclip/xsel"] = _entry(
            ok_cb, "portapapeles presente (xclip/xsel)." if ok_cb
            else ("pacman -S xclip." if arch else "sudo apt install xclip."),
            auto=can_system_install)
        ok_xd = shutil.which("xdotool") is not None
        out["xdotool"] = _entry(
            ok_xd, "xdotool presente (X11)." if ok_xd
            else ("pacman -S xdotool." if arch
                  else "sudo apt install xdotool."), auto=can_system_install)
    return out


def report(results=None):
    """Texto ES simple con la tabla [OK]/[FALTA]."""
    if results is None:
        results = check()
    title = {"win32": "Windows", "darwin": "macOS"}.get(sys.platform, "Linux")
    lines = [f"Dependencias ({title}):"]
    for name, e in results.items():
        mark = "OK" if e["ok"] else "FALTA"
        auto = " (auto)" if (not e["ok"] and e["auto"]) else ""
        lines.append(f"  [{mark}] {name}: {e['hint']}{auto}")
    return "\n".join(lines)


def _install_one(name):
    if name == "pip":
        subprocess.run([sys.executable, "-m", "ensurepip", "--default-pip"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                       timeout=120, check=True)
        return
    if name in _PIP:
        print(f"  instalando {name} con pip...")
        subprocess.run([sys.executable, "-m", "pip", "install", _PIP[name]],
                       stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                       timeout=300, check=True)
        return
    if sys.platform.startswith("linux"):
        if name in _APT:
            if _arch_based():
                pkg = _PACMAN.get(name)
                if not pkg:
                    raise RuntimeError(f"no hay paquete Arch configurado para {name}")
                cmd = ["pacman", "-S", "--needed", "--noconfirm", pkg]
                if not (hasattr(os, "geteuid") and os.geteuid() == 0):
                    cmd = ["sudo", "-n"] + cmd  # nunca pide password
                print(f"  instalando {pkg} con pacman...")
            else:
                pkg = _APT[name]
                cmd = ["apt-get", "install", "-y", pkg]
                if not (hasattr(os, "geteuid") and os.geteuid() == 0):
                    cmd = ["sudo", "-n"] + cmd  # nunca pide password
                print(f"  instalando {pkg} con apt...")
            subprocess.run(cmd, stdout=subprocess.DEVNULL,
                           stderr=subprocess.STDOUT, timeout=300, check=True)
            return
    if sys.platform == "darwin" and name == "portaudio":
        print("  instalando portaudio con brew...")
        subprocess.run(["brew", "install", "portaudio"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                       timeout=300, check=True)
        return
    raise RuntimeError(f"{name} no es autoinstalable")


def ensure(auto=False):
    """Si auto=True, instala lo auto-instalable y devuelve check() fresco.

    Sin red no instala nada (hint "sin red", sin traceback). Con auto=False
    solo chequea, sin tocar nada.
    """
    results = check()
    if not auto:
        return results
    if not results["python"]["ok"]:
        for name, entry in results.items():
            if name != "python" and not entry["ok"] and entry["auto"]:
                entry["hint"] += " (no se instala: Instant2 requiere Python 3.12+)."
        return results
    pending = [n for n, e in results.items() if not e["ok"] and e["auto"]]
    if not pending:
        return results
    if not _has_net():
        for n in pending:
            results[n]["hint"] += " (sin red: no se pudo autoinstalar)."
        return results
    for n in pending:
        try:
            _install_one(n)
        except Exception as e:
            log.info("no se pudo autoinstalar %s: %s", n, e)
    return check()
