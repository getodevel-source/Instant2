"""Piezas puras del overlay web (QtWebEngine) y red de seguridad de la pagina.

La construccion real del renderer se prueba con el smoke manual
(`bench/web_overlay_smoke.py`): levantar Chromium en CI/headless es fragil y
aqui solo se garantizan los contratos que no dependen del motor.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from instant2_app.branding import PALETTE  # noqa: E402
from instant2_app.overlay_web import (  # noqa: E402
    WINDOW_SIZES, compose_page, frame_payload)

PAGE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "src", "instant2_app", "web", "overlay.html")


class FramePayloadTests(unittest.TestCase):
    def test_full_frame_round_trips(self):
        payload = json.loads(frame_payload(
            "listening", "hola", "F9", "orbital",
            level=0.51234, pitch=0.4, bands=[0.1] * 9))
        self.assertEqual(payload["mode"], "listening")
        self.assertEqual(payload["message"], "hola")
        self.assertEqual(payload["keyLabel"], "F9")
        self.assertEqual(payload["style"], "orbital")
        self.assertEqual(payload["level"], 0.5123)
        self.assertEqual(len(payload["bands"]), 9)

    def test_partial_frame_carries_state_only(self):
        payload = json.loads(frame_payload("idle", "", "F9", "classic"))
        self.assertEqual(set(payload), {"mode", "message", "keyLabel", "style"})



class ComposePageTests(unittest.TestCase):
    def test_qwebchannel_is_injected_once(self):
        out = compose_page("<html><!--QWEBCHANNEL--></html>", "window.qt=1;")
        self.assertIn("<script>", out)
        self.assertIn("window.qt=1;", out)
        self.assertNotIn("<!--QWEBCHANNEL-->", out)

    def test_shipped_page_keeps_its_contract(self):
        with open(PAGE_PATH, encoding="utf-8") as handle:
            source = handle.read()
        for expected in ("<!--QWEBCHANNEL-->", "window.pushFrame", "bridge.frame.connect",
                         "bridge.event", "classic", "orbital", "state.bands",
                         "TRANSCRIBIENDO", "ESCUCHANDO", "toString(16)"):
            self.assertIn(expected, source)
        # El "Copiado" es copy del renderer classic, como en el QML.
        self.assertIn("Copiado", source)
        self.assertIn("DICTADO MEJORADO", source)


    def test_roundrect_falls_back_without_native_support(self):
        with open(PAGE_PATH, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("typeof C.roundRect", source)
        self.assertIn("arcTo", source)

    def test_draw_loop_never_dies_on_a_bad_frame(self):
        with open(PAGE_PATH, encoding="utf-8") as handle:
            source = handle.read()
        frame = source[source.index("function frame(now)"):]
        frame = frame[:frame.index("function hasPending")]
        self.assertIn("try {", frame)
        self.assertIn("catch", frame)
        self.assertIn("scheduleNextFrame();", frame)
        self.assertIn("requestAnimationFrame(frame)", source)

    def test_reduced_motion_freezes_audio_signal_and_renders_without_a_loop(self):
        with open(PAGE_PATH, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("prefers-reduced-motion: reduce", source)
        self.assertIn("let reducedMotion", source)
        self.assertIn("}, reducedMotion ? 40 : 33);", source)
        self.assertIn("const breathe = 0.5;", source)
        self.assertNotIn("Math.sin(timeS * 4.2", source)
        self.assertNotIn("Math.sin(timeS * 3.2", source)
        self.assertIn("if (!reducedMotion && exiting", source)
        self.assertIn("state.level = 0;", source)
        self.assertIn("state.bands.fill(0.18);", source)
        self.assertIn("if (reducedMotion && state.mode !== 'idle')", source)
        self.assertIn("if (reducedMotion && state.mode !== 'idle')\n      scheduleNextFrame();", source)
        frame = source[source.index("function frame(now)"):]
        frame = frame[:frame.index("function hasPending")]
        self.assertIn("if (reducedMotion) {\n      running = false;\n      return;\n    }", frame)
        self.assertLess(frame.index("if (reducedMotion) {"), frame.index("scheduleNextFrame();"))





class WindowSizesTests(unittest.TestCase):
    def test_both_styles_have_room_for_wide_feedback_pills(self):
        self.assertGreaterEqual(WINDOW_SIZES["classic"][0], 424 + 24)
        self.assertGreaterEqual(WINDOW_SIZES["orbital"][0], 440 + 24)
        self.assertEqual(WINDOW_SIZES["orbital"][1], 140)

    def test_palette_tokens_used_by_the_page_exist(self):
        with open(PAGE_PATH, encoding="utf-8") as handle:
            source = handle.read()
        for key in ("accent", "working", "green", "amber", "red", "text",
                    "muted", "glass", "glass_alt", "line"):
            self.assertIn(PALETTE[key], source)


if __name__ == "__main__":
    unittest.main()
