"""Ajustes y micrófonos del panel: catálogo (puro) y `PanelLogic` sin Qt.

El catálogo de dispositivos conserva sus dos regresiones de siempre; el resto
verifica el comportamiento de consumidor que antes vivía en los widgets:
sucio/guardado/reinicio, precedencia de autostart, caminos de error del
micrófono, captura de tecla y vocabulario.
"""
import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from instant2_app import gui, hotkey

CFG = {"mic_hint": "", "mic_index": None, "key": "f9",
       "autostart": False}


class DeviceCatalogTests(unittest.TestCase):
    def test_unique_device_survives_index_shift_and_ambiguous_duplicate_does_not(self):
        catalog = gui.DeviceCatalog({"mic_hint": "USB Mic", "mic_index": 5})
        choices = [(5, "USB Mic", 1, 48000), (8, "USB Mic", 1, 48000)]
        sd = SimpleNamespace(default=SimpleNamespace(device=(5, -1)))
        with patch("instant2_app.gui.audio.input_choices", return_value=choices), \
                patch("instant2_app.gui.audio.preferred_input_index", side_effect=lambda default, _rows: default), \
                patch.dict(sys.modules, {"sounddevice": sd}):
            labels, selected, missing = catalog.refresh()
        self.assertEqual(selected, "USB Mic [5] (predeterminado)")
        self.assertFalse(missing)
        catalog.remember(catalog.devices["USB Mic [8]"])
        shifted = [(12, "USB Mic", 1, 48000), (18, "USB Mic", 1, 48000)]
        sd.default.device = (12, -1)
        with patch("instant2_app.gui.audio.input_choices", return_value=shifted), \
                patch("instant2_app.gui.audio.preferred_input_index", side_effect=lambda default, _rows: default), \
                patch.dict(sys.modules, {"sounddevice": sd}):
            labels, selected, missing = catalog.refresh(catalog.pending_device, preserve=True)
        self.assertIsNone(selected)
        self.assertTrue(missing)
        self.assertEqual(set(labels), {"USB Mic [12] (predeterminado)", "USB Mic [18]"})

    def test_refresh_preserves_unique_name_when_only_index_changes(self):
        catalog = gui.DeviceCatalog({"mic_hint": "USB Mic", "mic_index": 5})
        sd = SimpleNamespace(default=SimpleNamespace(device=(5, -1)))
        with patch("instant2_app.gui.audio.input_choices", return_value=[(5, "USB Mic", 1, 48000)]), \
                patch("instant2_app.gui.audio.preferred_input_index", side_effect=lambda default, _rows: default), \
                patch.dict(sys.modules, {"sounddevice": sd}):
            catalog.refresh()
        catalog.remember((5, "USB Mic"))
        sd.default.device = (12, -1)
        with patch("instant2_app.gui.audio.input_choices", return_value=[(12, "USB Mic", 1, 48000)]), \
                patch("instant2_app.gui.audio.preferred_input_index", side_effect=lambda default, _rows: default), \
                patch.dict(sys.modules, {"sounddevice": sd}):
            _labels, selected, missing = catalog.refresh(catalog.pending_device, preserve=True)
        self.assertIsNotNone(selected)
        self.assertFalse(missing)
        self.assertEqual(catalog.devices[selected], (12, "USB Mic"))


class SyncTasks:
    def __init__(self):
        self.pending = 0
        self.on_idle = None
        self.on_error = None

    def submit(self, fn, on_result=None, on_error=None, on_progress=None):
        self.pending += 1
        try:
            value = fn(lambda payload: on_progress and on_progress(payload))
        except Exception as exc:  # noqa: BLE001  (igual que el runner real)
            if on_error is not None:
                on_error(exc)
            value = None
        else:
            if on_result is not None:
                on_result(value)
        finally:
            self.pending -= 1
        return id(fn)

    def drain(self):
        return 0

    def close(self):
        self.pending = 0


def make_logic(autostart_override=False, **kwargs):
    emitted = []
    with patch("instant2_app.gui.config.load", return_value=dict(CFG)), \
            patch("instant2_app.gui.models.check", return_value={"parakeet": True, "vad": True}), \
            patch("instant2_app.gui.resolve_data_dir", return_value=os.path.join(os.getcwd(), "models")), \
            patch("instant2_app.gui.daemon_is_running", return_value=False), \
            patch("instant2_app.gui.audio.input_choices", return_value=[]), \
            patch("instant2_app.gui.autostart.is_enabled", return_value=False), \
            patch.dict(sys.modules, {"sounddevice": SimpleNamespace(
                default=SimpleNamespace(device=(-1, -1)))}):
        logic = gui.PanelLogic(
            emit=lambda kind, payload: emitted.append((kind, payload)),
            tasks=SyncTasks(), schedule=lambda ms, fn: None,
            autostart_override=autostart_override, **kwargs)
    return logic, emitted


MIC = [(5, "USB Mic", 1, 48000)]


def with_mics():
    return patch("instant2_app.gui.audio.input_choices", return_value=MIC), \
        patch("instant2_app.gui.audio.preferred_input_index",
              side_effect=lambda default, _rows: default), \
        patch.dict(sys.modules, {"sounddevice": SimpleNamespace(
            default=SimpleNamespace(device=(5, -1)))})


class LogicSettingsTests(unittest.TestCase):
    def test_unconfigured_llm_is_reported_without_a_false_start_state(self):
        logic, emitted = make_logic()
        logic.sync_llm_runtime()
        states = [payload["llm_runtime"]["status"] for kind, payload in emitted
                  if kind == "state" and "llm_runtime" in payload]
        self.assertNotIn("starting", states)
        current = logic.state_payload()["llm_runtime"]
        self.assertEqual(current["status"], "needs_setup")
        self.assertIn("dictado local sigue disponible", current["detail"])

    def test_selected_hotkey_is_published_without_repeating_the_hint(self):
        logic, _emitted = make_logic()
        logic._set_key("enter")
        state = logic.state_payload()
        self.assertEqual(state["key_label"], "Enter")
        self.assertIn("campo", state["status"]["detail"])

    def test_selected_device_after_refresh(self):
        logic, _emitted = make_logic()
        choices, preferred, sounddevice = with_mics()
        with choices, preferred, sounddevice:
            logic.refresh_microphones(initial=True)
        self.assertEqual(logic._selected_device(), (5, "USB Mic"))
        self.assertIn("USB Mic", logic.state_payload()["mic"]["selected"])

    def test_key_change_marks_dirty_and_save_reports_restart(self):
        logic, emitted = make_logic()
        self.assertEqual(logic.settings_status, "Ajustes guardados.")
        logic._set_key("f12")
        self.assertEqual(logic.settings_status, "Hay cambios sin guardar.")
        with patch("instant2_app.gui.config.save", return_value="config.json"), \
                patch("instant2_app.gui.autostart.is_enabled", return_value=False):
            logic.save_config(show_message=True)
        self.assertEqual(logic.settings_status, "Ajustes guardados.")
        self.assertFalse(logic._restart_needed)

        # Con el daemon corriendo, cambiar la tecla pide reinicio al guardar.
        logic._last_daemon_running = True
        logic._settings_daemon_changed(True)
        logic._set_key("f11")
        with patch("instant2_app.gui.config.save", return_value="config.json"), \
                patch("instant2_app.gui.autostart.is_enabled", return_value=False):
            logic.save_config(show_message=True)
        self.assertEqual(logic.settings_status,
                         "Guardado; reiniciá Instant2 para aplicar micrófono, "
                         "atajos, LLM y rendimiento.")
        saved = [payload for kind, payload in emitted if kind == "toast"
                 and payload["title"] == "Ajustes guardados"]
        self.assertTrue(saved and "reiniciá Instant2" in saved[-1]["message"])


    def test_advanced_voice_options_are_dirty_persisted_and_request_restart(self):
        logic, _emitted = make_logic()
        logic.handle({"op": "set_advanced", "key": "overlay_style", "value": "orbital"})
        logic.handle({"op": "set_advanced", "key": "threads", "value": "2"})
        logic.handle({"op": "set_advanced", "key": "max_seg", "value": "12"})
        logic.handle({"op": "set_advanced", "key": "sound", "value": True})
        logic.handle({"op": "set_advanced", "key": "llm_url",
                      "value": "http://127.0.0.1:8080"})
        logic.handle({"op": "set_advanced", "key": "llm_model",
                      "value": "qwen3.5:2b-q4_K_M"})
        logic.handle({"op": "set_advanced", "key": "llm_normal_enabled",
                      "value": True})
        logic.handle({"op": "set_advanced", "key": "resource_mode",
                      "value": "memory"})
        self.assertEqual(logic.settings_status, "Hay cambios sin guardar.")

        logic._last_daemon_running = True
        with patch("instant2_app.gui.config.save", return_value="config.json"), \
                patch("instant2_app.gui.autostart.is_enabled", return_value=False):
            logic.save_config(show_message=False)

        self.assertEqual(logic.cfg["overlay_style"], "orbital")
        self.assertEqual(logic.cfg["threads"], 2)
        self.assertEqual(logic.cfg["max_seg"], 12)
        self.assertTrue(logic.cfg["sound"])
        self.assertEqual(logic.cfg["llm_url"], "http://127.0.0.1:8080")
        self.assertEqual(logic.cfg["llm_model"], "qwen3.5:2b-q4_K_M")
        self.assertTrue(logic.cfg["llm_normal_enabled"])
        self.assertEqual(logic.cfg["resource_mode"], "memory")
        self.assertTrue(logic._restart_needed)

    def test_advanced_settings_reject_invalid_values(self):
        logic, emitted = make_logic()
        logic.handle({"op": "set_advanced", "key": "llm_url", "value": "ftp://host"})
        logic.handle({"op": "set_advanced", "key": "llm_url",
                      "value": "http://[not-a-valid-ipv6-address"})
        logic.handle({"op": "set_advanced", "key": "max_seg", "value": "120"})
        logic.handle({"op": "set_advanced", "key": "resource_mode",
                      "value": "ultra-memory"})
        self.assertEqual(logic.cfg.get("llm_url", ""), "")
        self.assertEqual(logic.cfg.get("max_seg", 20.0), 20.0)
        warnings = [payload for kind, payload in emitted if kind == "toast"]
        self.assertEqual(len(warnings), 4)

    def test_managed_local_llm_settings_are_visible_saved_and_not_assumed_ready(self):
        logic, _emitted = make_logic()
        logic.handle({"op": "set_advanced", "key": "llm_server_path",
                      "value": r"C:\tools\llama-server.exe"})
        logic.handle({"op": "set_advanced", "key": "llm_model_path",
                      "value": r"C:\models\qwen.gguf"})
        logic.handle({"op": "set_advanced", "key": "llm_acceleration",
                      "value": "cpu"})
        state = logic.state_payload()
        self.assertEqual(state["advanced"]["llm_url"], "http://127.0.0.1:8080/v1")
        self.assertEqual(state["advanced"]["llm_acceleration"], "cpu")
        self.assertNotEqual(state["llm_runtime"]["status"], "ready")

        with patch("instant2_app.gui.config.save", return_value="config.json"), \
                patch("instant2_app.gui.autostart.is_enabled", return_value=False):
            logic.save_config(show_message=False)
        self.assertEqual(logic.cfg["llm_server_path"], r"C:\tools\llama-server.exe")
        self.assertEqual(logic.cfg["llm_model_path"], r"C:\models\qwen.gguf")
        self.assertEqual(logic.cfg["llm_acceleration"], "cpu")
        self.assertTrue(logic.cfg["llm_managed_enabled"])
        self.assertEqual(logic.state_payload()["llm_runtime"]["status"], "stopped")

    def test_selecting_local_runtime_paths_sets_loopback_endpoint_automatically(self):
        logic, _emitted = make_logic()
        logic.cfg["llm_url"] = ""
        logic.handle({"op": "set_advanced", "key": "llm_server_path",
                      "value": r"C:\tools\llama-server.exe"})
        self.assertEqual(logic.cfg["llm_url"], "http://127.0.0.1:8080/v1")

    def test_llm_status_is_read_from_the_resident_daemon(self):
        logic, _emitted = make_logic()
        logic.cfg.update({
            "llm_managed_enabled": True,
            "llm_url": "http://127.0.0.1:8080/v1",
            "llm_server_path": r"C:\\tools\\llama-server.exe",
            "llm_model_path": r"C:\\models\\qwen.gguf",
        })
        logic._last_daemon_running = True
        sleeping = {"status": "sleeping", "detail": "LLM local en reposo.",
                    "device": "CPU", "owned": True}
        with patch("instant2_app.llm_runtime_status.read_status",
                   return_value=sleeping):
            logic.sync_llm_runtime()
        self.assertEqual(logic.state_payload()["llm_runtime"], sleeping)

    def test_gui_retries_by_signaling_daemon_instead_of_owning_llm(self):
        logic, _emitted = make_logic()
        logic.cfg.update({
            "llm_managed_enabled": True,
            "llm_url": "http://127.0.0.1:8080/v1",
            "llm_server_path": r"C:\\tools\\llama-server.exe",
            "llm_model_path": r"C:\\models\\qwen.gguf",
        })
        logic._last_daemon_running = True
        with patch("instant2_app.llm_runtime_status.request_retry") as retry, \
                patch.object(logic, "save_config", return_value=True):
            logic._op_llm_runtime_retry({})
        retry.assert_called_once_with()
        self.assertEqual(logic.state_payload()["llm_runtime"]["status"], "starting")

    def test_local_external_and_remote_endpoints_are_reported_without_starting_processes(self):
        logic, _emitted = make_logic()
        logic.cfg.update({
            "llm_managed_enabled": False,
            "llm_url": "http://127.0.0.1:11434/v1",
        })
        logic.sync_llm_runtime()
        state = logic.state_payload()["llm_runtime"]
        self.assertEqual(state["status"], "configured")
        self.assertIn("local", state["detail"])
        logic.cfg["llm_url"] = "https://llm.example.test/v1"
        logic.sync_llm_runtime()
        state = logic.state_payload()["llm_runtime"]
        self.assertEqual(state["status"], "configured")
        self.assertIn("remoto", state["detail"])

    def test_local_runtime_file_picker_routes_only_supported_paths(self):
        panel = gui._WebPanel.__new__(gui._WebPanel)
        opened = []
        selected = []
        panel.root = SimpleNamespace(openFileDialog=opened.append)
        panel.logic = SimpleNamespace(handle=selected.append)
        panel._handle_call('{"op":"choose_runtime_file","kind":"llm_model_path"}')
        self.assertEqual(opened, ["llm_model_path"])
        panel._handle_runtime_file_selected("llm_model_path", r"C:\models\qwen.gguf")
        self.assertEqual(selected, [{"op": "set_advanced", "key": "llm_model_path",
                                     "value": r"C:\models\qwen.gguf"}])
        panel._handle_runtime_file_selected("other", "ignored")
        self.assertEqual(len(selected), 1)

    def test_autostart_override_wins_and_save_applies_it(self):
        logic, _emitted = make_logic(autostart_override=True)
        self.assertTrue(logic.state_payload()["autostart"])
        with patch("instant2_app.gui.config.save", return_value="config.json"), \
                patch("instant2_app.gui.autostart.is_enabled", return_value=False), \
                patch("instant2_app.gui.autostart.enable") as enable, \
                patch("instant2_app.gui.autostart.disable") as disable:
            logic.save_config(show_message=False)
        enable.assert_called_once()
        disable.assert_not_called()

    def test_mic_missing_after_shift_reports_unavailable(self):
        logic, _emitted = make_logic()
        # Dos entradas con el mismo nombre: la identidad es ambigua a propósito.
        with patch("instant2_app.gui.audio.input_choices",
                   return_value=[(5, "USB Mic", 1, 48000), (8, "USB Mic", 1, 48000)]), \
                patch("instant2_app.gui.audio.preferred_input_index",
                      side_effect=lambda default, _rows: default), \
                patch.dict(sys.modules, {"sounddevice": SimpleNamespace(
                    default=SimpleNamespace(device=(5, -1)))}):
            logic.refresh_microphones(initial=True)
        logic.select_mic("USB Mic [5] (predeterminado)")
        with patch("instant2_app.gui.audio.input_choices",
                   return_value=[(12, "USB Mic", 1, 48000), (18, "USB Mic", 1, 48000)]), \
                patch("instant2_app.gui.audio.preferred_input_index",
                      side_effect=lambda default, _rows: default), \
                patch.dict(sys.modules, {"sounddevice": SimpleNamespace(
                    default=SimpleNamespace(device=(12, -1)))}):
            logic.refresh_microphones(initial=False)
        self.assertIsNone(logic._selected_device())
        self.assertIn("El micrófono seleccionado no está disponible", logic.mic_labels)
        self.assertFalse(logic.state_payload()["mic"]["available"])
        self.assertTrue(logic.state_payload()["mic"]["has_devices"])
        self.assertIn("no está disponible", logic.meter["text"])

    def test_empty_device_list_reports_no_microphones(self):
        logic, _emitted = make_logic()
        with patch("instant2_app.gui.audio.input_choices", side_effect=OSError("sin ALSA")):
            logic.refresh_microphones(initial=True)
        self.assertTrue(logic._microphones_loaded)
        self.assertTrue(logic.meter["text"].startswith("No se detectaron micrófonos."))

    def test_test_microphone_requires_selection_and_reports_signal(self):
        logic, emitted = make_logic()
        logic.test_microphone()
        titles = [payload["title"] for kind, payload in emitted if kind == "toast"]
        self.assertIn("Sin micrófono", titles)

        choices, preferred, sounddevice = with_mics()
        with choices, preferred, sounddevice:
            logic.refresh_microphones(initial=True)

        def peak_meter(_device, seconds=3.0, on_level=None):
            self.assertEqual(seconds, 3.0)
            if on_level is not None:
                on_level(0.5)
            return 0.5

        with patch("instant2_app.gui.audio.peak_meter", side_effect=peak_meter):
            logic.test_microphone()
        self.assertEqual(logic.meter["text"], "Señal detectada (0.500).")
        self.assertEqual(logic.meter["percent"], 100)

        with patch("instant2_app.gui.audio.peak_meter", return_value=0.001):
            logic.test_microphone()
        self.assertEqual(logic.meter["text"],
                         "No detecté señal; revisá micrófono/volumen.")

    def test_key_capture_accepts_windows_vk_and_rejects_unknown_names(self):
        logic, emitted = make_logic()
        logic.key_captured(13, "enter")
        results = [payload for kind, payload in emitted if kind == "key_result"]
        self.assertTrue(results and results[-1]["ok"])
        self.assertEqual(logic.key_value, hotkey.key_label("vk:13"))

        with patch.object(os, "name", "posix"):
            logic.key_captured(0, "zz")
        results = [payload for kind, payload in emitted if kind == "key_result"]
        self.assertFalse(results[-1]["ok"])
        self.assertIn("prob", results[-1]["message"])


    def test_vocab_flow_round_trips_through_the_editor_format(self):
        logic, _emitted = make_logic()
        self.assertEqual(logic.state_payload()["vocab"]["rows"], [])
        logic.vocab_add()
        logic.vocab_set(0, "term", "OpenAI")
        logic.vocab_set(0, "heard", "open ai | o pen ai")
        logic.vocab_toggle_sound(0)
        self.assertEqual(logic._vocab_rows(), [{
            "term": "OpenAI", "aliases": ["open ai", "o pen ai"], "sonido": True}])
        rows = logic.state_payload()["vocab"]["rows"]
        self.assertEqual(rows[0]["term"], "OpenAI")
        self.assertTrue(rows[0]["sound"])
        logic.vocab_delete(0)
        self.assertEqual(logic._vocab_rows(), [])

    def test_context_add_duplicate_and_remove(self):
        logic, emitted = make_logic()
        logic.context_add("General")
        titles = [payload["title"] for kind, payload in emitted if kind == "toast"]
        self.assertIn("Instant2", titles)
        messages = [payload["message"] for kind, payload in emitted if kind == "toast"]
        self.assertIn("Ya existe ese perfil.", messages)

        logic.context_add("Trabajo")
        state = logic.state_payload()["vocab"]
        self.assertIn("Trabajo", state["profiles"])
        self.assertEqual(state["active"], "Trabajo")
        self.assertTrue(state["removable"])

        logic.context_remove()
        state = logic.state_payload()["vocab"]
        self.assertNotIn("Trabajo", state["profiles"])
        self.assertEqual(state["active"], "General")
        self.assertFalse(state["removable"])

    def test_toast_without_actions_registers_no_callback(self):
        logic, _emitted = make_logic()
        logic.toast("Hola", "mundo")
        self.assertEqual(logic._toast_callbacks, {})

    def test_toast_dismiss_drops_callbacks(self):
        logic, emitted = make_logic()
        logic.toast("Listo", "aplicar", actions=(("Aplicar", lambda: None),))
        toast_id = next(payload["id"] for kind, payload in emitted if kind == "toast")
        self.assertIn(toast_id, logic._toast_callbacks)
        logic.handle({"op": "toast_dismiss", "id": toast_id})
        self.assertNotIn(toast_id, logic._toast_callbacks)
        # Doble dismiss o id desconocido: no-ops silenciosos.
        logic.handle({"op": "toast_dismiss", "id": toast_id})
        logic.handle({"op": "toast_dismiss", "id": "t-inexistente"})
        self.assertEqual(logic._toast_callbacks, {})

    def test_toast_action_still_runs_then_clears(self):
        logic, emitted = make_logic()
        seen = []
        logic.toast("Listo", "aplicar", actions=(("Aplicar", lambda: seen.append(1)),))
        toast_id = next(payload["id"] for kind, payload in emitted if kind == "toast")
        logic.handle({"op": "toast_action", "id": toast_id, "action": "0"})
        self.assertEqual(seen, [1])
        self.assertNotIn(toast_id, logic._toast_callbacks)

    def test_invalid_advanced_config_falls_back_and_warns_once(self):
        logic, emitted = make_logic()
        logic.cfg["overlay_style"] = "neon"
        logic.cfg["threads"] = 3
        logic.push_state()
        state = logic.state_payload()["advanced"]
        self.assertEqual(state["overlay_style"], "orbital")
        self.assertIn(state["threads"], (1, 2, 4, 6, 8))
        titles = [payload["title"] for kind, payload in emitted if kind == "toast"]
        self.assertIn("Indicador de voz no válido", titles)
        self.assertIn("Hilos de CPU no válidos", titles)
        warned = len([payload for kind, payload in emitted if kind == "toast"])
        logic.push_state()
        self.assertEqual(len([payload for kind, payload in emitted if kind == "toast"]),
                         warned)

    def test_set_advanced_clamps_threads_and_rejects_bad_style(self):
        logic, emitted = make_logic()
        logic.handle({"op": "set_advanced", "key": "threads", "value": "3"})
        self.assertIn(logic.cfg["threads"], (1, 2, 4, 6, 8))
        logic.handle({"op": "set_advanced", "key": "overlay_style", "value": "neon"})
        self.assertEqual(logic.cfg.get("overlay_style", "orbital"), "orbital")
        titles = [payload["title"] for kind, payload in emitted if kind == "toast"]
        self.assertIn("Indicador de voz no válido", titles)

    def test_key_cancel_clears_capture_flag(self):
        logic, _emitted = make_logic()
        logic.begin_key_capture()
        self.assertTrue(logic._key_capturing)
        logic.handle({"op": "key_cancel"})
        self.assertFalse(logic._key_capturing)

    def test_mic_test_failure_resets_meter(self):
        logic, _emitted = make_logic()
        choices, preferred, sounddevice = with_mics()
        with choices, preferred, sounddevice:
            logic.refresh_microphones(initial=True)
        logic.meter = {"percent": 87, "text": "Señal 0.500"}
        with patch("instant2_app.gui.audio.peak_meter", return_value=0.001):
            logic.test_microphone()
        self.assertEqual(logic.meter["percent"], 0)
        self.assertIn("No detecté señal", logic.meter["text"])

    def test_action_toasts_last_long_enough_to_click(self):
        logic, emitted = make_logic()
        logic.toast("A", "sin acciones")
        logic.toast("B", "con acciones", actions=(("Hacer", lambda: None),))
        plain = next(payload for kind, payload in emitted
                     if kind == "toast" and payload["title"] == "A")
        actioned = next(payload for kind, payload in emitted
                        if kind == "toast" and payload["title"] == "B")
        self.assertEqual(plain["ms"], 4500)
        self.assertGreaterEqual(actioned["ms"], 15000)

    def test_set_advanced_accepts_vad_blank_and_update_mode(self):
        logic, emitted = make_logic()
        logic.handle({"op": "set_advanced", "key": "vad_model", "value": "ten"})
        logic.handle({"op": "set_advanced", "key": "blank_penalty", "value": "0.5"})
        logic.handle({"op": "set_advanced", "key": "update_mode", "value": "off"})
        self.assertEqual(logic.cfg["vad_model"], "ten")
        self.assertEqual(logic.cfg["blank_penalty"], 0.5)
        self.assertEqual(logic.cfg["update_mode"], "off")
        state = logic.state_payload()["advanced"]
        self.assertEqual(state["vad_model"], "ten")
        self.assertEqual(state["blank_penalty"], 0.5)
        self.assertEqual(state["update_mode"], "off")
        self.assertIn("llm_resources", state)
        self.assertTrue(state["llm_normal_enabled"])
        logic.handle({"op": "set_advanced", "key": "llm_model",
                      "value": "llama3.2:3b-instruct-q4_K_M"})
        logic.handle({"op": "set_advanced", "key": "llm_normal_enabled",
                      "value": False})
        self.assertEqual(logic.cfg["llm_model"], "llama3.2:3b-instruct-q4_K_M")
        self.assertFalse(logic.cfg["llm_normal_enabled"])
        logic.handle({"op": "set_advanced", "key": "vad_model", "value": "xxx"})
        logic.handle({"op": "set_advanced", "key": "blank_penalty", "value": "9"})
        logic.handle({"op": "set_advanced", "key": "update_mode", "value": "xxx"})
        self.assertEqual(logic.cfg["vad_model"], "ten")
        self.assertEqual(logic.cfg["blank_penalty"], 0.5)
        self.assertEqual(logic.cfg["update_mode"], "off")
        titles = [payload["title"] for kind, payload in emitted if kind == "toast"]
        self.assertIn("Detector de voz no válido", titles)
        self.assertIn("Penalidad no válida", titles)
        self.assertIn("Modo de actualización no válido", titles)
    def test_set_advanced_rejects_unsafe_model_names(self):
        logic, emitted = make_logic()
        logic.handle({"op": "set_advanced", "key": "llm_model",
                      "value": "model\nsecret"})
        self.assertEqual(logic.cfg.get("llm_model", ""), "")
        titles = [payload["title"] for kind, payload in emitted if kind == "toast"]
        self.assertIn("Modelo no válido", titles)

    def test_set_advanced_llm_token_never_echoed(self):
        logic, _emitted = make_logic()
        logic.handle({"op": "set_advanced", "key": "llm_token", "value": "secreto-123"})
        self.assertEqual(logic.cfg["llm_token"], "secreto-123")
        state = logic.state_payload()["advanced"]
        self.assertTrue(state["llm_token_set"])
        self.assertNotIn("secreto-123", repr(state))

    def test_set_advanced_llm_url_blocks_plain_http(self):
        logic, emitted = make_logic()
        logic.handle({"op": "set_advanced", "key": "llm_url",
                      "value": "http://192.168.1.10:8080"})
        self.assertEqual(logic.cfg.get("llm_url", ""), "")
        titles = [payload["title"] for kind, payload in emitted if kind == "toast"]
        self.assertIn("Dirección no válida", titles)
        logic.handle({"op": "set_advanced", "key": "llm_url",
                      "value": "http://127.0.0.1:8080"})
        self.assertEqual(logic.cfg["llm_url"], "http://127.0.0.1:8080")

    def test_set_advanced_llm_token_marks_dirty(self):
        logic, _emitted = make_logic()
        logic.save_config(show_message=False)
        self.assertFalse(logic._dirty())
        logic.handle({"op": "set_advanced", "key": "llm_token", "value": "tok-1"})
        self.assertTrue(logic._dirty())
        logic, _emitted = make_logic()
        logic.model_ready = False
        state = logic.state_payload()
        self.assertTrue(state["status"]["disabled_reason"])
        self.assertIn("modelo", state["status"]["disabled_reason"].casefold())
        pages = {item["id"]: item["page"] for item in state["checklist"]}
        self.assertEqual(pages, {"models": "home", "mic": "audio", "key": "settings"})

    def test_home_without_recognizer_keeps_other_features_available(self):
        logic, emitted = make_logic()
        logic.model_ready = False
        logic.page = "home"
        logic.handle({"op": "ready"})
        self.assertEqual(logic.page, "home")
        self.assertNotIn(("navigate", {"page": "settings"}), emitted)
        titles = [payload["title"] for kind, payload in emitted if kind == "toast"]
        self.assertIn("Dictado pendiente", titles)

    def test_friendly_errors_wrap_raw_detail(self):
        from instant2_app.gui import _friendly_error
        cause, action = _friendly_error(OSError("sin red"))
        self.assertTrue(cause and action)
        logic, emitted = make_logic()
        with patch("instant2_app.gui.subprocess.Popen",
                   side_effect=OSError("sin red")):
            logic._daemon_state_ready = True
            logic._microphones_loaded = True
            logic.model_ready = True
            logic._start_daemon(save_settings=False)
        toast = next(payload for kind, payload in emitted if kind == "toast")
        self.assertIn("Detalle:", toast["message"])
        self.assertNotEqual(toast["message"].strip(), "sin red")

    def test_cancelled_model_download_is_recoverable(self):
        logic, _emitted = make_logic()
        logic.model_ready = False
        with patch("instant2_app.gui.models.download_models",
                   side_effect=gui.models.DownloadCancelled("cancelada")):
            logic.download_models()
        self.assertFalse(logic.state_payload()["model"]["downloading"])
        self.assertFalse(logic.state_payload()["model"]["canceling"])
        self.assertIn("progreso parcial se conserva", logic.model_status)
        self.assertEqual(logic.state_payload()["model"]["button"],
                         "Reintentar descarga")

    def test_model_download_cancel_sets_event_and_announces_state(self):
        logic, _emitted = make_logic()
        logic._model_download_pending = True
        logic._model_cancel_event = threading.Event()
        logic.cancel_model_download()
        self.assertTrue(logic._model_cancel_event.is_set())
        self.assertTrue(logic.state_payload()["model"]["canceling"])
        self.assertEqual(logic.model_status, "Cancelando…")


class SetupConsoleTests(unittest.TestCase):
    def test_unknown_flag_exits_uncleanly(self):
        import io
        from contextlib import redirect_stdout
        from instant2_app import setup as setup_module
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            with self.assertRaises(SystemExit) as caught:
                setup_module.cmd_setup(["--typo-flag"])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("flag desconocido", buffer.getvalue())

    def test_incomplete_setup_names_what_is_missing(self):
        import io
        from contextlib import redirect_stdout
        from unittest.mock import patch as _patch
        from instant2_app import setup as setup_module
        buffer = io.StringIO()
        with redirect_stdout(buffer), \
                _patch("instant2_app.setup.config.load", return_value=dict(
                    setup_module.config.DEFAULTS)), \
                _patch("instant2_app.setup.config.save",
                       return_value="config.json"), \
                _patch("instant2_app.deps.check", return_value={
                    "sherpa_onnx": {"ok": True}, "sounddevice": {"ok": True}}), \
                _patch("instant2_app.deps.report", return_value=""), \
                _patch("instant2_app.models.check",
                       return_value={"parakeet": False, "vad": False}), \
                _patch("instant2_app.models.download_models",
                       side_effect=OSError("sin red")), \
                _patch("instant2_app.setup._real_inputs", return_value=[]), \
                _patch("instant2_app.autostart.is_enabled", return_value=False), \
                _patch("instant2_app.autostart.describe", return_value="off"):
            rc = setup_module.cmd_setup(["--yes", "--no-probe"])
        out = buffer.getvalue()
        self.assertEqual(rc, 2)
        self.assertNotIn("\nListo.", out)
        self.assertIn("Configuración incompleta:", out)
        self.assertIn("instant2 setup", out)


class UpdateConsentTests(unittest.TestCase):
    def _logic(self, cfg):
        from types import SimpleNamespace as _NS
        from unittest.mock import patch as _patch
        from instant2_app import gui as _gui
        emitted = []
        with _patch("instant2_app.gui.config.load", return_value=dict(cfg)), \
                _patch("instant2_app.gui.models.check",
                       return_value={"parakeet": True, "vad": True}), \
                _patch("instant2_app.gui.resolve_data_dir",
                       return_value="models"), \
                _patch("instant2_app.gui.daemon_is_running", return_value=False), \
                _patch("instant2_app.gui.audio.input_choices", return_value=[]), \
                _patch("instant2_app.gui.autostart.is_enabled",
                       return_value=False), \
                _patch.dict(sys.modules, {"sounddevice": SimpleNamespace(
                    default=_NS(device=(-1, -1)))}):
            logic = _gui.PanelLogic(
                emit=lambda kind, payload: emitted.append((kind, payload)),
                tasks=SyncTasks(), schedule=lambda ms, fn: None)
            logic._update_source_mode = False
        return logic, emitted

    def _release(self):
        return {"update": True, "current": "0.1.0", "latest": "0.2.0",
                "notes": "", "asset": "Instant2.exe",
                "asset_url": "https://x/Instant2.exe"}

    def test_off_mode_skips_silent_check_entirely(self):
        from unittest.mock import patch as _patch
        logic, _emitted = self._logic({"mic_hint": "", "mic_index": None,
                                       "key": "f9", "autostart": False,
                                       "update_mode": "off"})
        with _patch("instant2_app.update.check") as check:
            logic._silent_update_check()
        check.assert_not_called()

    def test_env_kill_switch_skips_silent_check(self):
        from unittest.mock import patch as _patch
        logic, _emitted = self._logic({"mic_hint": "", "mic_index": None,
                                       "key": "f9", "autostart": False,
                                       "update_mode": "auto"})
        with _patch.dict(os.environ, {"INSTANT2_NO_UPDATE": "1"}), \
                _patch("instant2_app.update.check") as check:
            logic._silent_update_check()
        check.assert_not_called()

    def test_notify_asks_before_downloading(self):
        from unittest.mock import patch as _patch
        logic, emitted = self._logic({"mic_hint": "", "mic_index": None,
                                      "key": "f9", "autostart": False,
                                      "update_mode": "notify"})
        with _patch("instant2_app.update.check", return_value=self._release()), \
                _patch("instant2_app.gui.config.save",
                       return_value="config.json"), \
                _patch("instant2_app.update.download") as download:
            logic._silent_update_check()
        download.assert_not_called()
        self.assertIn("Descargar v0.2.0", logic.update_button)
        consent = [payload for kind, payload in emitted if kind == "toast"
                   and payload["title"] == "Hay versión nueva: 0.2.0"]
        self.assertTrue(consent)
        self.assertIn("Descargar", [a["label"] for a in consent[-1]["actions"]])
        with _patch("instant2_app.update.fetch_expected_sha256",
                     return_value="ab" * 32), \
                _patch("instant2_app.update.download") as download2:
            logic.handle({"op": "download_update"})
        download2.assert_called_once()

    def test_install_rehashes_before_any_popen(self):
        import hashlib as _hl
        import tempfile as _tf
        from unittest.mock import patch as _patch
        from instant2_app import gui as _gui
        logic, emitted = self._logic({"mic_hint": "", "mic_index": None,
                                      "key": "f9", "autostart": False,
                                      "update_mode": "auto",
                                      "update_last_check": 0})
        with _tf.NamedTemporaryFile(delete=False) as handle:
            handle.write(b"binario-nuevo")
            target = handle.name
        try:
            digest = _hl.sha256(b"binario-nuevo").hexdigest()
            logic._pending_update = self._release()
            with _patch("instant2_app.update.install_mode",
                        return_value="portable"), \
                    _patch("instant2_app.gui.os.path.isfile",
                           return_value=False), \
                    _patch("instant2_app.gui.subprocess.Popen") as popen:
                logic._install_ready(target, "00" * 32)
            popen.assert_not_called()
            failed = [payload for kind, payload in emitted
                      if kind == "toast"
                      and payload["title"] == "Actualización sin aplicar"]
            self.assertTrue(failed)
            self.assertIn("Detalle:", failed[-1]["message"])
            with _patch.object(_gui.sys, "frozen", True, create=True), \
                    _patch.object(_gui.sys, "platform", "linux"), \
                    _patch("instant2_app.update.install_mode",
                           return_value="portable"), \
                    _patch("instant2_app.update.apply_binary_update",
                           side_effect=TypeError("viejo")) as apply_update, \
                    _patch.object(logic, "stop_daemon_flow"):
                logic._update_install_pending = False
                logic._install_in_place(target, digest)
            self.assertEqual(apply_update.call_count, 2)
            self.assertEqual(apply_update.call_args_list[-1].args, (target,))
        finally:
            os.unlink(target)
