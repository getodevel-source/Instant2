"""Smoke del panel web de producción: instancia `_WebPanel`, espera el canal,
ejercita ops reales por el bridge y captura la ventana.

Uso: python web_panel_smoke.py [--out RUTA.png]
"""
import argparse
import copy
import json
import logging
import os
import sys
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "src")))

from PySide6.QtCore import QPoint, QTimer, Qt  # noqa: E402
from PySide6.QtQuick import QQuickWindow  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from instant2_app import gui  # noqa: E402

assert callable(QQuickWindow.grabWindow)

SMOKE_CONFIG = {
    "mic_hint": "USB Microphone", "mic_index": 5, "key": "f9",
    "lang": "es", "autostart": False, "llm_url": "", "threads": 4,
    "sound": False, "overlay_style": "orbital", "max_seg": 20,
    "active_context": "General",
    "context_profiles": {"General": [{
        "term": "Instant2", "aliases": ["in stand"], "sonido": True,
    }]},
}
PAGES = ("home", "audio", "settings", "vocab", "read")


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=os.path.join(
        HERE, "bakeoff", "out", "web_panel.png"))
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--page", choices=(*PAGES, "llm-setup", "all"), default="home")
    parser.add_argument("--mic-state", choices=("available", "empty", "lost"),
                        default="available")
    parser.add_argument("--model-state", choices=("ready", "missing"),
                        default="ready")
    parser.add_argument("--daemon-state", choices=("stopped", "active"),
                        default="stopped")
    parser.add_argument("--width", type=int, default=1100)
    parser.add_argument("--height", type=int, default=600)
    args = parser.parse_args()
    mic_rows = ([(5, "USB Microphone", 1, 48000)]
                if args.mic_state != "empty" else [])

    # Estado del usuario a resguardo: nada de este smoke escribe el config
    # real ni toca el autostart del sistema.
    with patch("instant2_app.gui.config.save",
               side_effect=lambda cfg: "smoke-config.json") as save_cfg, \
            patch("instant2_app.gui.config.load", side_effect=lambda: copy.deepcopy(SMOKE_CONFIG)), \
            patch("instant2_app.gui.models.check",
                  return_value={"parakeet": args.model_state == "ready",
                                "vad": args.model_state == "ready"}), \
            patch("instant2_app.gui.audio.input_choices",
                  return_value=mic_rows), \
            patch("instant2_app.gui.audio.preferred_input_index",
                  side_effect=lambda default, rows: default if default in
                  [row[0] for row in rows] else (rows[0][0] if rows else None)), \
            patch("instant2_app.gui.daemon_is_running",
                  return_value=args.daemon_state == "active"), \
            patch.object(gui.PanelLogic, "_silent_update_check"), \
            patch("instant2_app.gui.autostart.is_enabled", return_value=False), \
            patch("instant2_app.gui.autostart.enable") as autostart_enable, \
            patch("instant2_app.gui.autostart.disable") as autostart_disable:
        run(args, save_cfg, autostart_enable, autostart_disable)
    return 0


def run(args, save_cfg, autostart_enable, autostart_disable):
    panel = gui._WebPanel(page="home", autostart_override=False)
    app = panel.application
    events = []

    def op(payload):
        events.append(payload)
        panel._handle_call(json.dumps(payload))

    def capture_frame(page):
        image = panel.root.grabWindow()
        target = args.out
        if args.page == "all":
            root, ext = os.path.splitext(args.out)
            target = f"{root}.{page}{ext}"
        if not image.save(target):
            raise AssertionError(f"No se pudo guardar la captura: {target}")
        print(json.dumps({
            "event": "captured", "file": target, "page": page or "home",
            "size": [image.width(), image.height()],
            "title": panel.view.property("title"),
            "loadProgress": panel.view.property("loadProgress"),
            "ops": events,
            "settings_status": panel.logic.settings_status,
            "status_title": panel.logic.state_payload()["status"]["title"],
            "mic": panel.logic.state_payload()["mic"],
            "vocab_rows": panel.logic.state_payload()["vocab"]["rows"][:3],
            "config_saved": save_cfg.call_count,
            "autostart_calls": [autostart_enable.call_count,
                                autostart_disable.call_count],
        }, ensure_ascii=False), flush=True)
        if args.page == "all" and page != PAGES[-1]:
            next_page = PAGES[PAGES.index(page) + 1]
            QTimer.singleShot(150, lambda: capture_page(next_page))
        else:
            finish()

    def capture_page(page):
        op({"op": "navigate", "page": page})
        QTimer.singleShot(500, lambda: capture_frame(page))

    # Secuencia: perfil desechable para editar sin tocar el vocabulario real.
    if args.page != "llm-setup":
        QTimer.singleShot(2500, lambda: op({"op": "navigate", "page": "settings"}))
        QTimer.singleShot(3000, lambda: op({"op": "context_add", "name": "Smoke"}))
        QTimer.singleShot(3200, lambda: op({"op": "vocab_add"}))
        QTimer.singleShot(3400, lambda: op({"op": "vocab_set", "row": 0, "col": "term",
                                            "value": "OpenAI"}))
        QTimer.singleShot(3600, lambda: op({"op": "vocab_toggle_sound", "row": 0}))
        QTimer.singleShot(3900, lambda: op({"op": "context_remove"}))
        QTimer.singleShot(4200, lambda: op({"op": "set_autostart", "value": False}))
        QTimer.singleShot(4800, lambda: op({"op": "key_captured", "code": 120, "name": "f9"}))
        QTimer.singleShot(5200, lambda: op({"op": "save_config"}))

    if args.mic_state == "lost":
        def lose_selected_mic():
            logic = panel.logic
            logic.catalog.rows = ((8, "External Microphone", 1, 48000),)
            logic.catalog.devices = {"External Microphone": (8, "External Microphone")}
            logic.mic_labels = ["External Microphone", gui.MIC_UNAVAILABLE_LABEL]
            logic.mic_selected = gui.MIC_UNAVAILABLE_LABEL
            logic.push_state()
        QTimer.singleShot(1100, lose_selected_mic)

    def capture_requested_page():
        if args.page == "llm-setup":
            if args.width != 1100 or args.height != 700:
                raise AssertionError("llm-setup requiere una captura de 1100x700")
            QTest.mouseClick(
                panel.root, Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier, QPoint(1015, 305))
            QTimer.singleShot(450, lambda: capture_frame("llm-setup"))
        else:
            capture_page(PAGES[0] if args.page == "all" else args.page)

    def finish():
        panel.logic.close()
        app.quit()

    QTimer.singleShot(int(args.seconds * 1000), capture_requested_page)

    if args.width == 1100 and panel.root.property("width") != 1100:
        raise AssertionError("panel WebEngine: el ancho predeterminado no coincide")
    if args.height == 600 and panel.root.property("height") != 600:
        raise AssertionError("panel WebEngine: el alto predeterminado no coincide")
    if args.width != 1100:
        panel.root.setProperty("width", args.width)
    if args.height != 600:
        panel.root.setProperty("height", args.height)
    panel.show()
    app.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())
