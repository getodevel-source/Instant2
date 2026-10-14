"""Lifecycle manager for a user-configured local llama.cpp server.

Instant2 never downloads a runtime or model. The persistent dictation daemon
starts the selected files on loopback, optionally sleeps the model between
requests, and stops only the child process it owns.
"""
import os
import re
import subprocess
import sys
import threading
import time
import math
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from instant2_app import llm_resources
from instant2_app.llm import check_url


_DEVICE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_-]*\d+)\s*:", re.MULTILINE)
_DEVICE_MEMORY_RE = re.compile(
    r"\((\d+)\s*MiB\s*,\s*(\d+)\s*MiB\s+free\)", re.IGNORECASE)
_CPU_RAM_RESERVE_BYTES = 1024 ** 3
_CPU_CACHE_MIN_BYTES = 256 * 1024 ** 2
_CPU_CACHE_MODEL_RATIO = 0.15
_GPU_MIN_FREE_MIB = 1536
_GPU_MODEL_HEADROOM_RATIO = 0.20
_GPU_RUNTIME_HEADROOM_MIB = 512


class RuntimeConfigError(ValueError):
    """A safe, user-facing local runtime configuration error."""


def cpu_memory_required_bytes(model_size_bytes):
    """Estimate GGUF + context/cache RAM and leave 1 GiB for the desktop."""
    model_size_bytes = max(0, int(model_size_bytes))
    cache_bytes = max(_CPU_CACHE_MIN_BYTES,
                      int(model_size_bytes * _CPU_CACHE_MODEL_RATIO))
    return model_size_bytes + cache_bytes + _CPU_RAM_RESERVE_BYTES


def cpu_memory_error(model_size_bytes, available_bytes):
    """Return an actionable message when CPU loading is unlikely to fit.

    Unknown RAM is not treated as zero: on platforms where the OS cannot report
    available memory, the server health check remains the final decision.
    """
    if available_bytes is None:
        return ""
    try:
        available_bytes = int(available_bytes)
    except (TypeError, ValueError):
        return ""
    required = cpu_memory_required_bytes(model_size_bytes)
    if available_bytes >= required:
        return ""
    available_gib = available_bytes / (1024 ** 3)
    required_gib = required / (1024 ** 3)
    return ("No hay suficiente RAM libre para cargar este modelo en CPU "
            f"({available_gib:.1f} GB libres; se estiman {required_gib:.1f} GB "
            "incluyendo el contexto y un margen para el escritorio). "
            "Liberá memoria o elegí un GGUF más pequeño.")


def gpu_memory_required_mib(model_size_bytes):
    """Estimate free VRAM needed for a full model load plus runtime overhead.

    This is a conservative device-selection hint, not a promise that a backend
    can load the model. The llama.cpp health check remains authoritative.
    """
    model_mib = max(0, int(model_size_bytes)) / (1024 ** 2)
    estimated = math.ceil(model_mib * (1 + _GPU_MODEL_HEADROOM_RATIO)
                          + _GPU_RUNTIME_HEADROOM_MIB)
    return max(_GPU_MIN_FREE_MIB, estimated)


def local_runtime_target(url):
    """Return host and port for a loopback llama.cpp URL or raise safely."""
    try:
        parsed = urlsplit(check_url(url))
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except (TypeError, ValueError) as exc:
        raise RuntimeConfigError("La dirección del servidor local no es válida.") from exc
    if parsed.scheme != "http" or not _loopback(host):
        raise RuntimeConfigError(
            "El servidor administrado debe usar http:// en una dirección local (127.0.0.1 o localhost).")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeConfigError(
            "Quitá credenciales y parámetros de la dirección local administrada.")
    if parsed.path not in ("", "/", "/v1"):
        raise RuntimeConfigError(
            "El servidor administrado necesita una dirección raíz, por ejemplo http://127.0.0.1:8080/v1.")
    return ("::1" if ":" in host else "127.0.0.1"), port


def is_local_endpoint(url):
    """Whether a configured OpenAI-compatible endpoint is a local HTTP service."""
    try:
        parsed = urlsplit(check_url(url))
    except (TypeError, ValueError):
        return False
    return parsed.scheme == "http" and _loopback(parsed.hostname or "")


def is_remote_endpoint(url):
    """Whether a valid configured endpoint is remote rather than loopback."""
    try:
        parsed = urlsplit(check_url(url))
    except (TypeError, ValueError):
        return False
    return bool(parsed.scheme and parsed.hostname) and not (
        parsed.scheme == "http" and _loopback(parsed.hostname))


def _loopback(host):
    import ipaddress

    if host.lower().rstrip(".") == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def health_url(url):
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, "/health", "", ""))


def parse_devices(output, min_free_mib=_GPU_MIN_FREE_MIB):
    """Extract usable llama.cpp device IDs, preferring the most free VRAM."""
    devices = []
    for match in _DEVICE_RE.finditer(output or ""):
        line_end = (output or "").find("\n", match.start())
        line = (output or "")[match.start():line_end if line_end >= 0 else None]
        memory = _DEVICE_MEMORY_RE.search(line)
        free_mib = int(memory.group(2)) if memory else None
        if free_mib is not None and free_mib < min_free_mib:
            continue
        devices.append((match.group(1), free_mib))
    # Unknown memory is still useful on backends that do not report VRAM.
    return [device for device, _free in sorted(
        devices, key=lambda item: item[1] if item[1] is not None else -1,
        reverse=True)]


class LocalLLMRuntime:
    """Starts/reuses one local server and owns only its own child process."""

    def __init__(self, popen=None, opener=None, clock=None, sleep=None,
                 available_memory=None):
        self._popen = popen or subprocess.Popen
        self._opener = opener or urlopen
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._available_memory = available_memory or llm_resources.available_memory_bytes
        self._lock = threading.RLock()
        self._process = None
        self._owned_url = ""
        self._owned_signature = None
        self._target_url = ""
        self._starting = False
        self._cancel = threading.Event()
        self._generation = 0
        self._sleep_support = {}
        self._last_state = {
            "status": "disabled", "detail": "La administración local está apagada.",
            "device": "", "owned": False,
        }

    @property
    def process(self):
        with self._lock:
            return self._process

    def state(self):
        with self._lock:
            process = self._process
            state = dict(self._last_state)
        if process is not None and process.poll() is not None:
            with self._lock:
                if self._process is process:
                    self._process = None
                    self._owned_url = ""
                    self._owned_signature = None
                    state = {
                        "status": "error",
                        "detail": "El servidor local se cerró inesperadamente. Podés reintentar.",
                        "device": state.get("device", ""), "owned": False,
                    }
                    self._last_state = state
        return state

    def set_disabled(self):
        self.stop()
        result = {"status": "disabled", "detail": "La administración local está apagada.",
                  "device": "", "owned": False}
        with self._lock:
            self._last_state = result
        return dict(result)

    def _healthy(self, url, timeout=0.8):
        try:
            request = Request(health_url(url), headers={"Accept": "application/json"})
            with self._opener(request, timeout=timeout) as response:
                return 200 <= response.status < 300
        except (HTTPError, URLError, OSError, TimeoutError, ValueError):
            return False

    def _is_sleeping(self, url, timeout=0.8):
        """Read llama.cpp's cached sleep flag without waking the model."""
        try:
            parsed = urlsplit(check_url(url))
            props = urlunsplit((parsed.scheme, parsed.netloc, "/props", "", ""))
            request = Request(props, headers={"Accept": "application/json"})
            with self._opener(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8", "replace"))
            return bool(data.get("is_sleeping")) if isinstance(data, dict) else False
        except (HTTPError, URLError, OSError, TimeoutError, ValueError,
                TypeError, AttributeError):
            # Older llama.cpp versions and other OpenAI-compatible servers do
            # not expose /props. They remain healthy and keep their old policy.
            return False

    def _supports_idle_sleep(self, executable):
        key = os.path.normcase(os.path.realpath(executable))
        with self._lock:
            if key in self._sleep_support:
                return self._sleep_support[key]
        supported = False
        try:
            result = subprocess.run(
                [executable, "--help"], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", timeout=8, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            supported = "--sleep-idle-seconds" in (result.stdout or "")
        except (OSError, subprocess.SubprocessError):
            pass
        with self._lock:
            self._sleep_support[key] = supported
        return supported

    def _devices(self, executable, min_free_mib=_GPU_MIN_FREE_MIB):
        try:
            result = subprocess.run(
                [executable, "--list-devices"], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                timeout=8, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode == 0:
                return parse_devices(result.stdout, min_free_mib=min_free_mib)
        except (OSError, subprocess.SubprocessError):
            pass
        return []

    @staticmethod
    def _validate(config):
        executable = os.path.expandvars(os.path.expanduser(
            str(config.get("llm_server_path") or "").strip()))
        model = os.path.expandvars(os.path.expanduser(
            str(config.get("llm_model_path") or "").strip()))
        if not executable or not os.path.isfile(executable):
            raise RuntimeConfigError("Elegí el ejecutable llama-server que ya instalaste.")
        if not model or not os.path.isfile(model):
            raise RuntimeConfigError("Elegí el archivo GGUF del modelo que ya descargaste.")
        host, port = local_runtime_target(config.get("llm_url", ""))
        return executable, model, host, port

    @staticmethod
    def _signature(executable, model, config):
        return (os.path.normcase(os.path.realpath(executable)),
                os.path.normcase(os.path.realpath(model)),
                str(config.get("llm_model") or "local").strip(),
                str(config.get("llm_acceleration", "auto")))

    @staticmethod
    def _args(executable, model, host, port, config, device=None,
              sleep_supported=False):
        args = [executable, "--model", model, "--host", host, "--port", str(port),
                "--no-webui", "--log-disable", "--ctx-size", "4096",
                "--parallel", "1",
                "--threads", str(max(1, min(8, (os.cpu_count() or 4) // 2)))]
        # The chat client needs a stable API model ID even when the UI leaves
        # the optional display name blank.
        alias = str(config.get("llm_model") or "local").strip()
        args.extend(("--alias", alias))
        if device:
            args.extend(("--device", device, "--n-gpu-layers", "99"))
        else:
            args.extend(("--device", "none", "--n-gpu-layers", "0"))
        try:
            sleep_seconds = max(0, int(config.get("llm_idle_sleep_seconds", 900)))
        except (TypeError, ValueError):
            sleep_seconds = 900
        if sleep_supported and sleep_seconds > 0:
            args.extend(("--sleep-idle-seconds", str(sleep_seconds)))
        return args

    def _spawn_and_wait(self, args, url, generation, signature, device, timeout):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            process = self._popen(
                args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags,
                start_new_session=(sys.platform != "win32"))
        except (OSError, ValueError):
            return False, None
        with self._lock:
            if generation != self._generation or self._cancel.is_set():
                stale = True
            else:
                stale = False
                self._process = process
                self._owned_url = url
                self._owned_signature = signature
                self._last_state = {
                    "status": "starting", "detail": "Iniciando el modelo local…",
                    "device": device or "CPU", "owned": True,
                }
        if stale:
            self._terminate(process)
            return False, None
        deadline = self._clock() + timeout
        while self._clock() < deadline and not self._cancel.is_set():
            if process.poll() is not None:
                with self._lock:
                    if self._process is process:
                        self._process = None
                        self._owned_url = ""
                        self._owned_signature = None
                return False, process
            if self._healthy(url):
                return True, process
            self._sleep(0.25)
        if self._cancel.is_set() or process.poll() is not None:
            return False, process
        self._terminate(process)
        with self._lock:
            if self._process is process:
                self._process = None
                self._owned_url = ""
                self._owned_signature = None
        return False, process

    def start(self, config, timeout=90.0):
        """Ensure local endpoint is ready; reuse foreign endpoint without owning it."""
        url = str(config.get("llm_url", "")).strip()
        with self._lock:
            if self._starting:
                return dict(self._last_state)
        if not url:
            self.stop()
            result = {
                "status": "needs_setup",
                "detail": ("Configurá un servicio local y elegí el runtime y el modelo "
                           "en Ajustes. Instant2 lo iniciará automáticamente."),
                "device": "", "owned": False,
            }
            with self._lock:
                self._last_state = result
            return dict(result)
        try:
            # Reuse an already healthy loopback server without requiring the
            # paths for a process Instant2 would start itself.
            local_runtime_target(url)
        except RuntimeConfigError as exc:
            self.stop()
            result = {"status": "needs_setup", "detail": str(exc),
                      "device": "", "owned": False}
            with self._lock:
                self._last_state = result
            return dict(result)
        with self._lock:
            process = self._process
            same_owned = (process is not None and process.poll() is None
                          and self._owned_url == url)
            owned_signature = self._owned_signature
        validated = None
        restarting_owned = False
        if same_owned:
            try:
                validated = self._validate(config)
            except RuntimeConfigError as exc:
                with self._lock:
                    if self._process is process:
                        self._process = None
                        self._owned_url = ""
                        self._owned_signature = None
                self._terminate(process)
                result = {"status": "needs_setup", "detail": str(exc),
                          "device": "", "owned": False}
                with self._lock:
                    self._last_state = result
                return dict(result)
            desired_signature = self._signature(validated[0], validated[1], config)
            if desired_signature == owned_signature and self._healthy(url):
                sleeping = self._is_sleeping(url)
                result = {
                    "status": "sleeping" if sleeping else "ready",
                    "detail": ("LLM local en reposo; se reactivará al dictar."
                               if sleeping else "LLM local listo."),
                    "device": self._last_state.get("device", ""), "owned": True,
                }
                with self._lock:
                    self._last_state = result
                return dict(result)
            with self._lock:
                if self._process is process:
                    self._process = None
                    self._owned_url = ""
                    self._owned_signature = None
            self._terminate(process)
            restarting_owned = True
        if not restarting_owned and self._healthy(url):
            with self._lock:
                previous = self._process
                self._process = None
                self._owned_url = ""
                self._owned_signature = None
                self._target_url = url
            if previous is not None:
                self._terminate(previous)
            sleeping = self._is_sleeping(url)
            result = {
                "status": "sleeping" if sleeping else "ready",
                "detail": ("LLM local en reposo; se reactivará al dictar."
                           if sleeping else
                           "Servicio local listo (ya estaba iniciado)."),
                "device": "Servidor existente", "owned": False,
            }
            with self._lock:
                self._last_state = result
            return dict(result)
        if validated is None:
            try:
                validated = self._validate(config)
            except RuntimeConfigError as exc:
                result = {"status": "needs_setup", "detail": str(exc),
                          "device": "", "owned": False}
                with self._lock:
                    self._last_state = result
                return dict(result)
        executable, model, host, port = validated
        signature = self._signature(executable, model, config)
        with self._lock:
            if self._starting:
                return dict(self._last_state)
            self._starting = True
            self._generation += 1
            generation = self._generation
            self._cancel = threading.Event()
            previous = self._process
            if previous is not None:
                self._process = None
                self._owned_url = ""
                self._owned_signature = None
            self._target_url = url
            self._last_state = {"status": "starting", "detail": "Comprobando el servicio local…",
                                "device": "", "owned": False}
        try:
            if previous is not None:
                self._terminate(previous)
            try:
                model_size = os.path.getsize(model)
            except OSError:
                model_size = None
            min_free_mib = (gpu_memory_required_mib(model_size)
                            if model_size is not None else _GPU_MIN_FREE_MIB)
            sleep_supported = self._supports_idle_sleep(executable)
            devices = (self._devices(executable, min_free_mib=min_free_mib)
                       if config.get("llm_acceleration", "auto") == "auto" else [])
            attempts = devices[:1] + [None]
            for index, device in enumerate(attempts):
                if self._cancel.is_set() or generation != self._generation:
                    break
                if device is None:
                    if model_size is None:
                        detail = "No encuentro el archivo del modelo local para iniciar CPU."
                    else:
                        try:
                            available = self._available_memory()
                        except (AttributeError, OSError, TypeError, ValueError):
                            available = None
                        detail = cpu_memory_error(model_size, available)
                    if detail:
                        if devices:
                            detail = "No se pudo iniciar con GPU. " + detail
                        result = {"status": "error", "detail": detail,
                                  "device": "", "owned": False}
                        with self._lock:
                            self._last_state = result
                        return dict(result)
                args = self._args(executable, model, host, port, config, device,
                                  sleep_supported=sleep_supported)
                succeeded, process = self._spawn_and_wait(
                    args, url, generation, signature, device, timeout)
                if succeeded:
                    name = device or "CPU"
                    result = {"status": "ready", "detail": f"LLM local listo · {name}.",
                              "device": name, "owned": True}
                    with self._lock:
                        self._last_state = result
                    return dict(result)
                if self._cancel.is_set() or generation != self._generation:
                    break
                if index == 0 and device is not None:
                    continue  # One bounded CPU fallback after accelerated startup.

            result = {"status": "error",
                      "detail": "No se pudo iniciar el servidor local. Revisá el ejecutable, el modelo y la memoria; podés reintentar.",
                      "device": "", "owned": False}
            with self._lock:
                self._last_state = result
            return dict(result)
        finally:
            with self._lock:
                self._starting = False

    @staticmethod
    def _terminate(process, wait=2.0):
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=wait)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=wait)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def stop(self):
        """Stop only our Popen child. An already-running endpoint is untouched."""
        with self._lock:
            self._generation += 1
            self._cancel.set()
            process = self._process
            self._process = None
            self._owned_url = ""
            self._owned_signature = None
            self._target_url = ""
            self._last_state = {
                "status": "disabled", "detail": "La administración local está apagada.",
                "device": "", "owned": False,
            }
        self._terminate(process)
        return process is not None

    def check(self):
        """Lightweight health check; does not start or stop foreign services."""
        state = self.state()
        if state["status"] not in ("ready", "sleeping"):
            return state
        with self._lock:
            owned = self._process is not None
            url = self._owned_url or self._target_url
        if url and not self._healthy(url):
            with self._lock:
                process = self._process
                if process is None or process.poll() is None:
                    self._last_state = {
                        "status": "recovering",
                        "detail": ("El servidor local dejó de responder; se comprobará de nuevo."
                                   if owned else
                                   "El servidor local dejó de responder; podés reiniciarlo o permitir que Instant2 lo inicie."),
                        "device": state.get("device", ""), "owned": owned,
                    }
            return self.state()
        if url:
            sleeping = self._is_sleeping(url)
            result = {
                "status": "sleeping" if sleeping else "ready",
                "detail": ("LLM local en reposo; se reactivará al dictar."
                           if sleeping else "LLM local listo."),
                "device": state.get("device", ""), "owned": owned,
            }
            with self._lock:
                self._last_state = result
            return result
        return state

    def request_timeout(self, config=None):
        """Allow a sleeping managed model to reload before falling back locally."""
        with self._lock:
            url = self._owned_url or self._target_url
            sleeping = self._last_state.get("status") == "sleeping"
        if url and (sleeping or self._is_sleeping(url)):
            return 60.0
        return 8.0
