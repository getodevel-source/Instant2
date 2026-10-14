"""Regresion minima: solo comportamiento consumidor-visible."""
import os
import sys
from unittest.mock import patch

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from instant2_app import audio, config, llm


def _check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(1)


# Overlay progression is ordered by session/phase, with errors preempting completion.
from instant2_app.overlay import OverlayTransitions


_states = OverlayTransitions()
_check("overlay start to listening to processing",
       _states.accept(1, "starting") < _states.accept(1, "listening")
       < _states.accept(1, "processing"))
_check("late listening cannot replace processing",
       _states.accept(1, "listening") is None)
_check("success appears after processing", _states.accept(1, "success") is not None)
_check("error replaces success and stays terminal",
       _states.accept(1, "error") is not None
       and _states.accept(1, "success") is None)
_check("new press starts a new overlay session",
       _states.accept(2, "starting") is not None)

# The Tk fallback (Linux/macOS) computes its animation per frame; the Qt Quick
# overlay animates in QML. If this helper goes missing, _animate raises
# NameError inside the Tk callback and the overlay silently stops animating.
from instant2_app.overlay import animation_frame, _TK_BAR_MAX, _TK_SPINNER_SEGMENTS

_frame_bars, _frame_spin = animation_frame("recording", 0)
_check("Tk animation frame matches the five drawn bars",
       len(_frame_bars) == 5
       and all(0 < height <= _TK_BAR_MAX for height in _frame_bars))
_check("Tk spinner phase stays inside the eight drawn segments",
       0 <= _frame_spin < _TK_SPINNER_SEGMENTS)
_check("Tk animation actually moves between frames",
       len({tuple(animation_frame("recording", tick)[0]) for tick in range(24)}) > 4)
_check("Tk spinner covers every segment",
       sorted(animation_frame("processing", tick)[1] for tick in range(8))
       == list(range(_TK_SPINNER_SEGMENTS)))

# hotkey: claves win esperadas.
from instant2_app import hotkey
_check("keys win", set(hotkey.WINDOWS_KEYS) == {"f9", "f10", "f20", "scroll", "pause"})
_check("keys posix", tuple(hotkey.POSIX_KEYS) == ("f9", "f10", "f11", "f12"))

# hotkey: normalizaciÃ³n para captura (vacÃ­o = default, case-insensitive).
_check("normalize enter", hotkey.normalize_key("", "f9") == "f9")
_check("normalize none", hotkey.normalize_key(None, "f9") == "f9")
_check("normalize named enter", hotkey.normalize_key("enter", "f9") == "enter")
_check("normalize case", hotkey.normalize_key("F10", "f9") == "f10")
_check("normalize strip", hotkey.normalize_key("  f9  ", "f9") == "f9")
with patch("sys.platform", "win32"):
    _check("arbitrary VK accepted", hotkey.is_valid_key("vk:65"))
    _check("letter maps to VK", hotkey._windows_vk("a") == 0x41)
    _check("custom key label", hotkey.key_label("vk:65") == "A")
    _check("mouse button rejected", not hotkey.is_valid_key("vk:1"))
with patch("sys.platform", "linux"):
    _check("POSIX character accepted", hotkey.is_valid_key("x"))
    _check("Windows VK rejected on POSIX", not hotkey.is_valid_key("vk:65"))

# hotkey: captura asigna tecla valida, Enter = default, repite si invalida.
_orig_read = hotkey._read_keypress
try:
    hotkey._read_keypress = lambda: "f10"
    _check("capture directa", hotkey.capture_key("Presiona una tecla", "f9") == "f10")
    hotkey._read_keypress = lambda: "x"
    _check("capture arbitrary character", hotkey.capture_key("Presiona una tecla", "f9") == "x")
    hotkey._read_keypress = lambda: "space"
    _check("capture spacebar", hotkey.capture_key("Presiona una tecla", "f9") == "space")
    hotkey._read_keypress = lambda: ""
    _check("capture enter default", hotkey.capture_key("Presiona una tecla", "f9") == "f9")
    seq = iter(["invalida", "f9"])
    hotkey._read_keypress = lambda: next(seq)
    _check("capture reintenta", hotkey.capture_key("Presiona una tecla", "f9") == "f9")
finally:
    hotkey._read_keypress = _orig_read

# Windows WASAPI runs at the device's native rate; permit shared-mode
# conversion when Instant2 requests the recognizer's 16 kHz stream.
from io import StringIO

with patch("sys.stdin", StringIO("f10\n")):
    _check("captura de tecla sin TTY",
           hotkey.capture_key("Presiona una tecla", "f9") == "f10")

class _FakeSettings:
    def __init__(self, auto_convert):
        self.auto_convert = auto_convert


class _FakeSounddevice:
    def __init__(self, hostapi):
        self.hostapi = hostapi

    def query_devices(self, device, kind):
        return {"hostapi": 0, "max_input_channels": 2}

    def query_hostapis(self, index):
        return {"name": self.hostapi}

    def WasapiSettings(self, *, auto_convert):
        return _FakeSettings(auto_convert)


def _stream_options(hostapi):
    fake_sd = _FakeSounddevice(hostapi)
    with patch("instant2_app.audio.sys.platform", "win32"):
        with patch.dict(sys.modules, {"sounddevice": fake_sd}):
            return audio.input_stream_options(26)


wasapi_options = _stream_options("Windows WASAPI")
_check("WASAPI 16 kHz auto-convert",
       wasapi_options["extra_settings"].auto_convert is True)
_check("MME does not get WASAPI settings", _stream_options("MME") == {})

stereo = np.array([[0.0, 0.2], [0.0, -0.2]], dtype=np.float32)
_check("stereo elige canal con seÃ±al",
       np.array_equal(audio.to_mono(stereo), stereo[:, 1]))
_check("mono conserva el canal",
       np.array_equal(audio.to_mono(stereo[:, 1:2]), stereo[:, 1]))

fake_sd = _FakeSounddevice("MME")
with patch.dict(sys.modules, {"sounddevice": fake_sd}):
    _check("captura estÃ©reo cuando el mic ofrece dos canales",
           audio.input_channels(26) == 2)

class _FakeMicDevices:
    def __init__(self):
        self.devices = [
            {"name": "HyperX", "max_input_channels": 1, "default_samplerate": 48000},
            {"name": "DGM20", "max_input_channels": 1, "default_samplerate": 48000},
        ]

    def query_devices(self, device=None, kind=None):
        if device is None:
            return self.devices
        return self.devices[device]


with patch.dict(sys.modules, {"sounddevice": _FakeMicDevices()}):
    _check("mic name survives shifted device index",
           audio.resolve_mic("DGM20", 0) == 1)

class _FakePortAudioChoices:
    hostapis = ("MME", "Windows DirectSound", "Windows WASAPI", "Windows WDM-KS")
    devices = [
        {"name": "Microsoft Sound Mapper - Input", "hostapi": 0, "max_input_channels": 2},
        {"name": "Microphone (DGM20 USB Microphon", "hostapi": 0, "max_input_channels": 1},
        {"name": "Microphone (DGM20 USB Microphone)", "hostapi": 1, "max_input_channels": 1},
        {"name": "Microphone (DGM20 USB Microphone)", "hostapi": 2, "max_input_channels": 1},
        {"name": "Stereo Mix (Realtek HD Audio)", "hostapi": 3, "max_input_channels": 2},
        {"name": "Microphone (Twin USB Microphone)", "hostapi": 2, "max_input_channels": 1},
        {"name": "Microphone (Twin USB Microphone)", "hostapi": 2, "max_input_channels": 1},
        {"name": "Microphone (Twin USB Microphone)", "hostapi": 0, "max_input_channels": 1},
        {"name": "Microphone (Twin USB Microphone)", "hostapi": 0, "max_input_channels": 1},
        {"name": "Line In (Realtek HD Audio)", "hostapi": 3, "max_input_channels": 2},
        {"name": "Microphone (DGM20 USB Microphone)", "hostapi": 3, "max_input_channels": 1},
    ]

    def query_devices(self, device=None, kind=None):
        return self.devices if device is None else self.devices[device]

    def query_hostapis(self, index):
        return {"name": self.hostapis[index]}


with patch("sys.platform", "win32"), patch.dict(
        sys.modules, {"sounddevice": _FakePortAudioChoices()}):
    choices = audio.input_choices()
    _check("mic aliases collapse to preferred backend",
           [entry[0] for entry in choices] == [3, 5, 6, 9])
    _check("saved WDM-KS index resolves to preferred WASAPI mic",
           audio.resolve_mic("Microphone (DGM20 USB Microphone)", 10) == 3)
    _check("legacy mic index maps to visible alias",
           audio.preferred_input_index(1, choices) == 3)

class _ChangedDeviceList:
    hostapis = ("Windows WASAPI",)
    devices = [
        {"name": "Microphone (Different USB Microphone)",
         "hostapi": 0, "max_input_channels": 1},
    ]

    def query_devices(self, device=None, kind=None):
        return self.devices if device is None else self.devices[device]

    def query_hostapis(self, index):
        return {"name": self.hostapis[index]}


with patch("sys.platform", "win32"), patch.dict(
        sys.modules, {"sounddevice": _ChangedDeviceList()}):
    _check("missing saved mic does not select a reused device index",
           audio.resolve_mic("Microphone (DGM20 USB Microphone)", 0,
                             strict_hint=True) is None)


class _FakeStartStream:
    def __init__(self, device, kwargs, attempts, fail_devices):
        self.device = device
        self.kwargs = kwargs
        self.attempts = attempts
        self.fail_devices = fail_devices

    def start(self):
        self.attempts.append((self.device, self.kwargs.get("callback")))
        if self.device in self.fail_devices:
            raise RuntimeError("Windows callback start failed")

    def close(self):
        pass

    def stop(self):
        pass


class _FakeAudioOpen(_FakePortAudioChoices):
    def __init__(self):
        self.attempts = []
        self.fail_devices = {2, 3}

    @staticmethod
    def WasapiSettings(auto_convert):
        return _FakeSettings(auto_convert)

    def InputStream(self, **kwargs):
        return _FakeStartStream(
            kwargs["device"], kwargs, self.attempts, self.fail_devices)



class _FakeMeterStream(_FakeStartStream):
    def start(self):
        super().start()
        if self.kwargs["device"] not in self.fail_devices:
            self.kwargs["callback"](
                np.array([[0.25, -0.1]], dtype=np.float32), 1, None, None)


class _FakeMeterOpen(_FakeAudioOpen):
    def InputStream(self, **kwargs):
        return _FakeMeterStream(
            kwargs["device"], kwargs, self.attempts, self.fail_devices)


meter_sd = _FakeMeterOpen()
meter_levels = []
with patch("sys.platform", "win32"), patch.dict(
        sys.modules, {"sounddevice": meter_sd}):
    meter_peak = audio.peak_meter(3, seconds=0.05, on_level=meter_levels.append)
    _check("GUI meter measures callback audio after same-mic fallback",
           meter_peak == 0.25 and meter_levels == [0.25] and
           [device for device, _callback in meter_sd.attempts] == [3, 2, 1])

class _FakeDuplicateMicOpen(_FakeAudioOpen):
    # DirectSound and WDM expose the two identical names in reverse physical order.
    devices = [
        {"name": "Microphone (Twin USB Microphone)", "hostapi": 0,
         "max_input_channels": 1, "physical_id": "A"},
        {"name": "Microphone (Twin USB Microphone)", "hostapi": 0,
         "max_input_channels": 1, "physical_id": "B"},
        {"name": "Microphone (Twin USB Microphone)", "hostapi": 1,
         "max_input_channels": 1, "physical_id": "B"},
        {"name": "Microphone (Twin USB Microphone)", "hostapi": 1,
         "max_input_channels": 1, "physical_id": "A"},
        {"name": "Microphone (Twin USB Microphone)", "hostapi": 2,
         "max_input_channels": 1, "physical_id": "A"},
        {"name": "Microphone (Twin USB Microphone)", "hostapi": 2,
         "max_input_channels": 1, "physical_id": "B"},
        {"name": "Microphone (Twin USB Microphone)", "hostapi": 3,
         "max_input_channels": 1, "physical_id": "B"},
        {"name": "Microphone (Twin USB Microphone)", "hostapi": 3,
         "max_input_channels": 1, "physical_id": "A"},
    ]

    def __init__(self):
        super().__init__()
        self.fail_devices = {5}


duplicate_sd = _FakeDuplicateMicOpen()
duplicate_callback = lambda *_args: None
with patch("sys.platform", "win32"), patch.dict(
        sys.modules, {"sounddevice": duplicate_sd}):
    try:
        audio.open_input_stream(5, callback=duplicate_callback)
        duplicate_mic_failed = False
    except RuntimeError as error:
        duplicate_mic_failed = str(error) == "Windows callback start failed"
    _check("reordered same-name host aliases are never guessed",
           duplicate_mic_failed and duplicate_sd.attempts == [(5, duplicate_callback)])
    _check("stale index among same-name duplicate mics is unavailable",
           audio.resolve_mic("Twin USB Microphone", 99, strict_hint=True) is None)
class _FakeAmbiguousMicOpen(_FakeAudioOpen):
    devices = [
        {"name": "Microphone (Twin USB Microphone)", "hostapi": 2, "max_input_channels": 1},
        {"name": "Microphone (Twin USB Microphone)", "hostapi": 2, "max_input_channels": 1},
        {"name": "Microphone (Twin USB Microphone)", "hostapi": 0, "max_input_channels": 1},
    ]

    def __init__(self):
        super().__init__()
        self.fail_devices = {1}


ambiguous_sd = _FakeAmbiguousMicOpen()
ambiguous_callback = lambda *_args: None
with patch("sys.platform", "win32"), patch.dict(
        sys.modules, {"sounddevice": ambiguous_sd}):
    try:
        audio.open_input_stream(1, callback=ambiguous_callback)
        failed_as_unavailable = False
    except RuntimeError as error:
        failed_as_unavailable = str(error) == "Windows callback start failed"
    _check("ambiguous alias counts do not switch to another same-name mic",
           failed_as_unavailable and
           ambiguous_sd.attempts == [(1, ambiguous_callback)])


open_sd = _FakeAudioOpen()
callback = lambda *_args: None
with patch("sys.platform", "win32"), patch.dict(sys.modules, {"sounddevice": open_sd}):
    stream, selected = audio.open_input_stream(3, callback=callback)
    _check("callback start fails over only to same-mic backend alias",
           selected == 1 and open_sd.attempts == [
               (3, callback), (2, callback), (1, callback)])
    stream.close()

open_sd.attempts.clear()
with patch("sys.platform", "win32"), patch.dict(sys.modules, {"sounddevice": open_sd}):
    _check("mic probe uses callback start and same-mic fallback",
           audio.probe(3, seconds=0) and
           [device for device, _callback in open_sd.attempts] == [3, 2, 1] and
           all(callback_ is not None for _device, callback_ in open_sd.attempts))
# setup: no-interactive configuration flags include context profiles.
from instant2_app.setup import _parse_args
o = _parse_args(["--yes", "--no-probe"])
_check("setup yes noprobe", o.yes and o.no_probe)
o = _parse_args(["--yes", "--mic", "3", "--key", "f9", "--threads", "4", "--no-sound"])
_check("setup flags", o.mic == 3 and o.key == "f9" and o.threads == 4 and o.no_sound)
o = _parse_args([
    "--context-profile", "Trabajo",
    "--context-term", "Instant2=instante|in stand",
    "--context-remove-term", "Parakeet",
    "--context-delete-profile",
])
_check("setup context management flags",
       o.context_profile == "Trabajo"
       and o.context_term == ["Instant2=instante|in stand"]
       and o.context_remove_term == ["Parakeet"]
       and o.context_delete_profile)

# Setup can remove exact terms from a selected profile without changing others.
from instant2_app import setup as setup_module
_setup_cfg = {
    **config.DEFAULTS,
    "active_context": "Trabajo",
    "context_profiles": {
        "General": [],
        "Trabajo": [{"term": "Instant2", "aliases": ["instante"]},
                    {"term": "Parakeet", "aliases": ["para kit"]}],
    },
}
_saved_setup = {}
with patch("instant2_app.setup.config.load", return_value=_setup_cfg), \
        patch("instant2_app.setup.config.save",
              side_effect=lambda value: (_saved_setup.update(value) or "config.json")), \
        patch("instant2_app.deps.check", return_value={
            "sherpa_onnx": {"ok": True}, "sounddevice": {"ok": True}}), \
        patch("instant2_app.deps.report", return_value=""), \
        patch("instant2_app.models.check",
              return_value={"parakeet": True, "vad": True}), \
        patch("instant2_app.setup._real_inputs", return_value=[]), \
        patch("instant2_app.autostart.is_enabled", return_value=False), \
        patch("instant2_app.autostart.describe", return_value="disabled"):
    setup_module.cmd_setup([
        "--yes", "--no-probe", "--context-profile", "Trabajo",
        "--context-remove-term", "Instant2",
    ])
_check("setup removes a vocabulary term from only the selected profile",
       _saved_setup["active_context"] == "Trabajo"
       and _saved_setup["context_profiles"]["General"] == []
       and [(item["term"], item["aliases"]) for item
            in _saved_setup["context_profiles"]["Trabajo"]]
       == [("Parakeet", ["para kit"])])

# La descarga de modelos (progreso con bytes, reanudación, espejo, disco y
# verificación) se cubre en test_models_download.py contra un servidor local:
# esta regresión ya no baja los 670 MB reales de Hugging Face.

# El glosario activo: solo variantes exactas, sin sustituciones difusas.
_work_context = {
    "active_context": "Trabajo",
    "context_profiles": {
        "General": [],
        "Trabajo": [
            {"term": "Instant2", "aliases": ["instante", "in stand"]},
            {"term": "Parakeet", "aliases": ["para kit"]},
        ],
    },
}
with patch("urllib.request.urlopen", side_effect=OSError("server unavailable")):
    _check("LLM failure keeps explicit local glossary correction",
           llm.maybe_polish(
               "instante",
               {**_work_context, "llm_url": "http://127.0.0.1:8080"})
           == "Instant2")

# Bias: la vecina no cruza frontera de oracion (.?!).
from instant2_app import bias as _bias
_check("bias no cruza punto entre alias y vecina",
       _bias.correct_biased("lei un comic. Hace push y sigo.", conf=0.5)
       == "lei un comic. Hace push y sigo.")
_check("bias rescata en la misma oracion",
       _bias.correct_biased("hice un comic del workflow", conf=0.5)
       == "hice un commit del workflow")

# Bias: "quemet" es SOLO de commit (antes duplicado en Qwen).
_check("bias quemet es commit",
       _bias.correct_biased("subi el quemet al repo", conf=0.5)
       == "subi el commit al repo")

# Openers: "por" suelto no abre; compuestos si.
_check("opener por suelto no abre",
       llm.restore_openers("por favor, pasame eso?") == "por favor, pasame eso?")
_check("opener por que compuesto abre",
       llm.restore_openers("por que no viniste?") == "¿por que no viniste?")
_check("opener porque abre",
       llm.restore_openers("porque no viniste?") == "¿porque no viniste?")

# Paste: guard no-op, Wayland no pisa, clipboard se restaura.
import sys as _sys
from unittest.mock import MagicMock as _MagicMock
from instant2_app import paste as _paste
_paste.paste("")
_check("paste vacio no-op", True)
_fake_pyper = _MagicMock()
_fake_clipboard = {"value": "previo"}
_fake_pyper.paste.side_effect = lambda: _fake_clipboard["value"]
_fake_pyper.copy.side_effect = lambda value: _fake_clipboard.update(value=value)
_fake_kb = _MagicMock()
with patch.dict(_sys.modules, {"pyperclip": _fake_pyper, "keyboard": _fake_kb}):
    with patch.object(_sys, "platform", "win32"):
        _paste.paste("hola")
_check("paste restaura clipboard previo",
       _fake_pyper.copy.call_args_list[-1][0][0] == "previo")
_check("paste envia ctrl+v con pulsaciones espaciadas",
       [args[0][0] for args in _fake_kb.press.call_args_list] == ["ctrl", "v"]
       and [args[0][0] for args in _fake_kb.release.call_args_list]
       == ["v", "ctrl"])
import shutil as _shutil
import subprocess as _subp
with patch.object(_sys, "platform", "linux"), \
        patch.dict("os.environ", {"XDG_SESSION_TYPE": "wayland"}, clear=False), \
        patch.object(_shutil, "which", return_value=None):
    try:
        _paste.paste("texto wayland")
        _check("paste wayland sin tools avisa sin pisar", False)
    except RuntimeError as _e:
        _check("paste wayland sin tools avisa sin pisar",
               "Wayland" in str(_e) or "wayland" in str(_e).lower())
with patch.object(_sys, "platform", "linux"), \
        patch.dict("os.environ", {"XDG_SESSION_TYPE": "wayland"}, clear=False), \
        patch.object(_shutil, "which",
                     side_effect=lambda n: {
                         "wl-copy": "/usr/bin/wl-copy",
                         "wl-paste": "/usr/bin/wl-paste",
                         "wtype": "/usr/bin/wtype",
                     }.get(n)), \
        patch.object(_subp, "run") as _run:
    _paste.paste("texto wayland")
    _check("paste wayland preserva clipboard y usa wtype",
           _run.call_count == 3 and "wtype" in str(_run.call_args_list[2][0]))
# Paste: XWayland (x11 con WAYLAND_DISPLAY) tambien va por ruta segura.
with patch.object(_sys, "platform", "linux"), \
        patch.dict("os.environ", {"XDG_SESSION_TYPE": "x11",
                                  "WAYLAND_DISPLAY": "wayland-0"}, clear=False), \
        patch.object(_shutil, "which",
                     side_effect=lambda n: {
                         "wl-copy": "/usr/bin/wl-copy",
                         "wl-paste": "/usr/bin/wl-paste",
                         "wtype": "/usr/bin/wtype",
                     }.get(n)), \
        patch.object(_subp, "run") as _run:
    _paste.paste("texto xwayland")
    _check("paste xwayland elige la ruta Wayland completa",
           _run.call_count == 3 and "wtype" in str(_run.call_args_list[2][0]))
with patch.object(_sys, "platform", "linux"), \
        patch.dict("os.environ", {"XDG_SESSION_TYPE": "",
                                  "WAYLAND_DISPLAY": "wayland-0"}, clear=False), \
        patch.object(_shutil, "which",
                     side_effect=lambda n: {
                         "wl-copy": "/usr/bin/wl-copy",
                         "wl-paste": "/usr/bin/wl-paste",
                         "wtype": "/usr/bin/wtype",
                     }.get(n)), \
        patch.object(_subp, "run") as _run:
    _paste.paste("texto wayland-display")
    _check("paste solo WAYLAND_DISPLAY completa pegado y preservacion",
           _run.call_count == 3 and "wtype" in str(_run.call_args_list[2][0]))

# Bias: conf invalida sigue a _strong_spot (Regla C siempre, como docstring)
# y alinea con llm.maybe_polish (invalida = take seguro 1.0).
_check("bias conf invalida aplica regla C",
       _bias.correct_biased("El Will fallo en la manana por el driver.",
                            conf="mal")
       == "El build fallo en la manana por el driver.")
_check("bias conf None aplica regla C",
       _bias.correct_biased("El Will fallo en la manana por el driver.",
                            conf=None)
       == "El build fallo en la manana por el driver.")
_check("bias conf invalida no abre gate debil sin vecina",
       _bias.correct_biased("lei un comic. Hace push y sigo.", conf="mal")
       == "lei un comic. Hace push y sigo.")

# GUI: clamp de CPU avisa (no silencioso) y _op_ready purga toasts viejos.
import os as _os
from instant2_app import gui as _gui
def _bare_logic():
    logic = _gui.PanelLogic.__new__(_gui.PanelLogic)
    logic.cfg = {}
    logic._toast_callbacks = {}
    logic._closed = False
    logic._toasts = []
    logic.emit = lambda kind, payload: logic._toasts.append((kind, payload))
    return logic
with patch.object(_gui.PanelLogic, "push_state", lambda self: None), \
        patch.object(_gui.PanelLogic, "_update_settings_status", lambda self: None), \
        patch.object(_os, "cpu_count", return_value=4):
    _logic = _bare_logic()
    _seen = []
    _logic.toast = lambda title, message="", level="info", **kw: _seen.append(title)
    _logic.handle({"op": "set_advanced", "key": "threads", "value": "64"})
    _check("threads clamp avisa", "Hilos de CPU ajustados" in _seen)
    _check("threads clamp respeta CPU", _logic.cfg["threads"] <= 4)
    _seen.clear()
    _logic.handle({"op": "set_advanced", "key": "threads", "value": "2"})
    _check("threads valido no avisa", _seen == [])
    _logic2 = _bare_logic()
    _logic2._toast_callbacks = {"t-viejo": [lambda: None]}
    _logic2.handle({"op": "ready"})
    _check("ready purga toast callbacks", _logic2._toast_callbacks == {})

print("OK: regresion minima verde.")
