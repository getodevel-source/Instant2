"""Lightweight host-resource detection for local LLM recommendations.

This module reports hardware only. It never downloads a model or assumes that
a detected GPU is usable by the selected inference server; the runtime still
has to confirm its accelerator and can fall back to CPU.
"""
import os
import platform
import re
import sys


def _windows_memory():
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    state = MEMORYSTATUSEX()
    state.dwLength = ctypes.sizeof(state)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
        return None, None
    return int(state.ullTotalPhys), int(state.ullAvailPhys)


def _posix_memory():
    if sys.platform.startswith("linux"):
        try:
            values = {}
            with open("/proc/meminfo", encoding="ascii") as handle:
                for line in handle:
                    match = re.match(r"^(MemTotal|MemAvailable):\s+(\d+)\s+kB", line)
                    if match:
                        values[match.group(1)] = int(match.group(2)) * 1024
            return values.get("MemTotal"), values.get("MemAvailable")
        except OSError:
            pass
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        total = os.sysconf("SC_PHYS_PAGES") * page_size
        available = os.sysconf("SC_AVPHYS_PAGES") * page_size
        return total, available
    except (AttributeError, OSError, ValueError):
        return None, None


def available_memory_bytes():
    """Return current OS-reported available RAM, or None when unavailable."""
    try:
        if sys.platform == "win32":
            return _windows_memory()[1]
        return _posix_memory()[1]
    except (AttributeError, OSError, ValueError):
        return None


def _windows_gpus():
    try:
        import winreg
    except ImportError:
        return []
    root = (winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Class"
            r"\{4d36e968-e325-11ce-bfc1-08002be10318}")
    found = []
    try:
        with winreg.OpenKey(*root) as key:
            index = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(key, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(key, subkey_name) as adapter:
                        name = winreg.QueryValueEx(adapter, "DriverDesc")[0]
                        try:
                            memory = winreg.QueryValueEx(
                                adapter, "HardwareInformation.qwMemorySize")[0]
                        except OSError:
                            memory = 0
                        if isinstance(memory, bytes):
                            memory = int.from_bytes(memory[:8], "little")
                        if not isinstance(name, str) or not name.strip():
                            continue
                        try:
                            memory = max(0, int(memory))
                        except (TypeError, ValueError):
                            memory = 0
                        found.append({"name": name.strip(), "vram_bytes": memory})
                except OSError:
                    continue
    except OSError:
        return []
    return found


def _linux_gpus():
    cards = "/sys/class/drm"
    found = []
    try:
        entries = os.listdir(cards)
    except OSError:
        return found
    for entry in entries:
        if not re.fullmatch(r"card\d+", entry):
            continue
        device = os.path.join(cards, entry, "device")
        name = ""
        for filename in ("product_name", "product", "uevent"):
            try:
                with open(os.path.join(device, filename), encoding="utf-8") as handle:
                    value = handle.read().strip()
            except OSError:
                continue
            if filename == "uevent":
                match = re.search(r"^DRIVER=(.+)$", value, flags=re.MULTILINE)
                name = match.group(1) if match else ""
            else:
                name = value
            if name:
                break
        try:
            with open(os.path.join(device, "mem_info_vram_total"), encoding="ascii") as handle:
                vram = int(handle.read().strip())
        except (OSError, ValueError):
            vram = 0
        if name or vram:
            found.append({"name": name or "GPU detectada", "vram_bytes": vram})
    return found


def _cpu_name():
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except (ImportError, OSError):
            return os.environ.get("PROCESSOR_IDENTIFIER", "CPU")
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if line.lower().startswith(("model name", "hardware")):
                        return line.split(":", 1)[-1].strip()
        except OSError:
            pass
    return platform.processor() or "CPU"


def _recommendation(total_bytes, available_bytes, gpus):
    total_gb = (total_bytes or 0) / (1024 ** 3)
    available_gb = (available_bytes or 0) / (1024 ** 3)
    gpu = max((item.get("vram_bytes", 0) for item in gpus), default=0)
    gpu_gb = gpu / (1024 ** 3)
    if gpu_gb >= 8 and available_gb >= 3:
        return {
            "profile": "gpu",
            "model_hint": "Empezar comparando modelos de 2B y 4B en Q4; esta GPU tiene margen para probar ambos.",
            "runtime_hint": "Usar aceleración GPU automática si Ollama o llama.cpp confirma que el controlador es compatible.",
        }
    if gpu_gb >= 3 and available_gb >= 2:
        return {
            "profile": "gpu_light",
            "model_hint": "Empezar con un modelo de 2B en Q4 y confirmar cuánta VRAM deja libre el runtime.",
            "runtime_hint": "Probar GPU con fallback a CPU; no cargar dos modelos a la vez.",
        }
    if total_gb >= 8 and available_gb >= 2.5:
        return {
            "profile": "cpu",
            "model_hint": "Empezar con un modelo de 0.8B a 2B en Q4 para mantener bajo el uso de RAM.",
            "runtime_hint": "Usar CPU y pocos hilos al inicio; medir antes de aumentar el tamaño.",
        }
    return {
        "profile": "constrained",
        "model_hint": "La memoria libre detectada es baja; probar 0.8B Q4 o dejar el LLM apagado.",
        "runtime_hint": "Evitar cargar modelos en segundo plano y verificar la memoria disponible antes de iniciar.",
    }


def detect_resources():
    """Return a privacy-safe hardware summary and conservative model hint."""
    if sys.platform == "win32":
        total, available = _windows_memory()
        gpus = _windows_gpus()
    else:
        total, available = _posix_memory()
        gpus = _linux_gpus() if sys.platform.startswith("linux") else []
    result = {
        "cpu": _cpu_name(),
        "logical_cpus": max(1, os.cpu_count() or 1),
        "ram_total_bytes": total or 0,
        "ram_available_bytes": available or 0,
        "gpus": gpus,
    }
    result.update(_recommendation(total, available, gpus))
    return result


def _gb(value):
    return f"{value / (1024 ** 3):.1f} GB" if value else "sin dato"


def format_summary(resources):
    """Short Spanish settings summary without machine identifiers or paths."""
    cpu = resources.get("cpu") or "CPU"
    ram = _gb(resources.get("ram_total_bytes", 0))
    free = _gb(resources.get("ram_available_bytes", 0))
    gpus = resources.get("gpus") or []
    if gpus:
        largest = max(gpus, key=lambda item: item.get("vram_bytes", 0))
        gpu = largest.get("name") or "GPU detectada"
        vram = _gb(largest.get("vram_bytes", 0))
        gpu_text = f"{gpu} ({vram} VRAM)"
    else:
        gpu_text = "sin GPU dedicada detectada"
    hint = resources.get("model_hint", "Elegir un modelo pequeño y medir.")
    return (f"Equipo: {cpu} · RAM {ram} total, {free} disponible · {gpu_text}. "
            f"{hint} El motor local decide si puede usar GPU o necesita CPU.")
