"""Contrato del panel web: página, hosts QML y composición con qwebchannel.

Los hosts de QML ya se rompieron una vez por olvidar `webChannel: channel`
(el canal queda sin transporte y la página lo avisa con un título). Acá queda
anclado ese contrato, además de la paleta y las piezas puras de gui.py.
"""
import html
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolation  # noqa: F401  (aisla el estado real del usuario)

from instant2_app.branding import PALETTE
from instant2_app.gui import compose_panel_page

PACKAGE = os.path.join(os.path.dirname(__file__), "..", "src", "instant2_app")
GUI_SOURCE = os.path.join(PACKAGE, "gui.py")
PANEL_PAGE = os.path.join(PACKAGE, "web", "panel.html")
OVERLAY_PAGE = os.path.join(PACKAGE, "web", "overlay.html")
HOSTS = {
    "panel_web_host.qml": os.path.join(PACKAGE, "qml", "panel_web_host.qml"),
    "overlay_web_host.qml": os.path.join(PACKAGE, "qml", "overlay_web_host.qml"),
}
ALLOWED_LITERALS = {"#ffffff", "#000000"}


class PanelPageContractTests(unittest.TestCase):
    def _source(self):
        with open(PANEL_PAGE, encoding="utf-8") as handle:
            return handle.read()

    def test_page_keeps_its_bridge_contract(self):
        source = self._source()
        for expected in ("<!--QWEBCHANNEL-->", "window.__panelReady",
                         "bridge.call", 'on("state", onState)',
                         'on("toast", onToast)', 'on("key_result", onKeyResult)',
                         'on("key_capture", onKeyCapture)',
                         'op:"ready"', 'op:"save_config"', 'op:"toggle_daemon"',
                         'op:"key_captured"', 'op:"toast_dismiss"',
                         'op:"diagnostics"'):
            self.assertIn(expected, source)

    def test_toasts_are_capped_to_bound_dom_nodes(self):
        """onToast descarta viejos (max 3): sin cota el DOM crece (G6)."""
        source = self._source()
        self.assertIn("MAX_TOASTS", source)
        self.assertRegex(source, r"while\s*\(toasts\.children\.length\s*>\s*MAX_TOASTS\)")

    def test_page_keeps_the_user_facing_copy(self):
        source = self._source()
        for expected in ("Tu voz, en texto.", "Iniciar dictado", "Detener dictado",
                         "ESTADO DEL DICTADO",
                         "Guardar cambios", "A&ntilde;adir perfil", "Eliminar perfil",
                         "A&ntilde;adir t&eacute;rmino", "Descargar reconocimiento", "Cambiar tecla",
                         "Leer texto en voz alta", "El texto no se env&iacute;a ni se guarda.",
                         "Iniciar con el sistema", "Diagnóstico",
                         "Buscar actualizaciones", "La prueba mide el nivel; no guarda audio.",
                         "Dictado mejorado"):
            self.assertIn(expected, source)

    def test_page_hides_bundled_model_and_runtime_names(self):
        source = self._source()
        visible = re.sub(r"<script\b[^>]*>.*?</script>", " ", source,
                         flags=re.IGNORECASE | re.DOTALL)
        visible = html.unescape(re.sub(r"<[^>]+>", " ", visible)).casefold()
        for name in ("parakeet", "kokoro", "qwen", "silero", "ten (",
                     "ollama", "llama.cpp", "llama-server", "dora ·",
                     "modelo local", "modelos", "el modelo"):
            self.assertNotIn(name, visible)

    def test_automatic_updates_download_in_background_and_confirm_install(self):
        source = self._source()
        self.assertIn("Descargar autom&aacute;ticamente", source)
        self.assertIn("La descarga autom&aacute;tica no instala la actualizaci&oacute;n", source)
        self.assertIn("siempre te pide confirmaci&oacute;n", source)

    def test_reader_offers_only_the_spanish_dora_voice(self):
        source = self._source()
        self.assertIn('<option value="ef_dora">', source)
        self.assertIn('const readVoices = ["ef_dora"]', source)
        self.assertNotIn('value="em_alex"', source)
        self.assertNotIn('value="em_santa"', source)

    def test_llm_retry_control_matches_setup_and_remote_states(self):
        source = self._source()
        self.assertIn('["starting", "recovering", "needs_setup", "disabled"]', source)
        self.assertIn('["ready", "sleeping"]', source)
        self.assertIn("externalLocal", source)
        self.assertIn("remoteConfigured", source)
        self.assertIn("Completá la configuración", source)
        self.assertIn("Servicio remoto", source)

    def test_the_local_model_download_is_an_explicit_action(self):
        source = self._source()
        for expected in ('id="llmModelStatus"', 'id="llmModelDownloadBtn"',
                         'id="llmModelCancelBtn"', 'id="llmModelProgWrap"',
                         'id="llmModelProgFill"', 'op:"download_llm_model"',
                         'op:"cancel_llm_model"', "Descargar modelo (3 GB)",
                         "la descarga es a pedido"):
            self.assertIn(expected, source)

    def test_redesign_uses_single_section_views_and_keeps_advanced_settings(self):
        source = self._source()
        for expected in ("class=\"side-nav\"", "aria-current=\"page\"",
                         'id="sec-home"', 'id="sec-audio"', 'id="sec-settings"',
                         'id="sec-vocab"', 'id="sec-read"', 'id="readText"',
                         'id="readPlay"', 'id="readStop"', 'id="modelSetup"',
                         "section.hidden = !visible", "page-enter", "updateProgressWrap",
                         "overlayStyleSelect", "threadsSelect", "maxSegmentInput",
                         "soundCheck", "llmUrlInput", "op:\"set_advanced\"",
                         "llmDestination", "llmModelInput",
                         "llmNormalCheck", "llmResources", "llmHomeStatus",
                          "La instalaci&oacute;n completa configura esto por vos", "llmServerPathInput", "llmModelPathInput",
                         "llmAccelerationSelect", "llmRuntimeStatus", "llmRuntimeRetry",
                         "keyBtn",
                         "vadModelSelect", "blankPenaltyInput", "updateModeSelect",
                         "runBtnReason", 'id="checklist"', "renderChecklist"):
            self.assertIn(expected, source)
        self.assertIn('aria-describedby="statusDetail runBtnReason"', source)
        self.assertIn('aria-describedby="blankPenaltyHelp blankPenaltyError"',
                      source)
        self.assertNotIn('id="llmManagedCheck"', source)


    def test_motion_theme_uses_real_state_and_accessible_transitions(self):
        source = self._source()
        self.assertIn('id="instant2-motion-system"', source)
        self.assertIn("@keyframes page-arrive", source)
        self.assertIn("@keyframes details-arrive", source)
        self.assertIn("prefers-reduced-motion: reduce", source)
        self.assertIn('homeStatusLine.dataset.state = state', source)
        self.assertIn('transform: scaleX(var(--progress,0))', source)
        self.assertNotRegex(source, r"@keyframes[^}]*\b(?:width|height|left|top)\s*:")
        self.assertNotIn("box-shadow 210ms", source)
        self.assertNotIn("box-shadow 200ms", source)

    def test_controls_have_explicit_accessible_status_and_error_text(self):
        source = self._source()
        for expected in ('role="status" aria-live="polite"',
                         'aria-describedby="maxSegmentHelp maxSegmentError"',
                         'aria-describedby="llmUrlHelp llmUrlError"',
                         'aria-valuenow', 'prefers-reduced-motion: reduce',
                         'prefers-contrast: more', 'forced-colors: active',
                         'id="uiScaleSelect"', 'document.documentElement.style.zoom',
                         'id="uiScaleStatus"', 'min-height: 44px'):
            self.assertIn(expected, source)

    def test_javascript_element_references_exist_and_ids_are_unique(self):
        source = self._source()
        ids = re.findall(r'\bid="([^"]+)"', source)
        self.assertEqual(len(ids), len(set(ids)), "panel.html: hay id duplicados")
        referenced = set(re.findall(r'\$\("([^"]+)"\)', source))
        self.assertEqual(referenced - set(ids), set(),
                         "panel.html: JavaScript apunta a ids inexistentes")

    def test_no_color_escapes_the_palette(self):
        found = {value.lower() for value in re.findall(r"#[0-9a-fA-F]{6}", self._source())}
        allowed = {value.lower() for value in PALETTE.values()} | ALLOWED_LITERALS
        self.assertEqual(found - allowed, set(), "panel.html: colores fuera de la paleta")

    def test_panel_theme_colors_are_declared_in_branding(self):
        source = compose_panel_page(self._source(), "")
        for key in ("panel_background", "panel_surface", "panel_text",
                    "panel_accent", "panel_accent_button", "panel_hero_start"):
            self.assertIn(PALETTE[key], source)

    def test_qwebchannel_is_injected_once(self):
        out = compose_panel_page("<html><!--QWEBCHANNEL--></html>", "window.qt=1;")
        self.assertIn("<script>", out)
        self.assertIn("window.qt=1;", out)
        self.assertNotIn("<!--QWEBCHANNEL-->", out)

    def test_local_asset_base_is_resolved_for_the_generated_page(self):
        out = compose_panel_page("<head><!--PANEL_ASSETS--></head>", "")
        self.assertNotIn("<!--PANEL_ASSETS-->", out)
        self.assertIn('<base href="file:///', out)
        self.assertIn('/web/">', out)

    def test_manrope_and_its_license_are_bundled_for_offline_use(self):
        folder = os.path.dirname(PANEL_PAGE)
        with open(os.path.join(folder, "Manrope.ttf"), "rb") as handle:
            self.assertEqual(handle.read(4), b"\x00\x01\x00\x00")
        with open(os.path.join(folder, "Manrope-OFL.txt"), encoding="utf-8") as handle:
            self.assertIn("SIL OPEN FONT LICENSE", handle.read())

    def test_normalize_key_matches_hotkey_contract(self):
        source = self._source()
        block = source[source.index("function normalizeKey"):]
        block = block[:block.index("function onKeyResult")]
        for expected in ('PageUp:"page_up"', 'PageDown:"page_down"',
                         'CapsLock:"caps_lock"'):
            self.assertIn(expected, block,
                          f"panel.html: normalizeKey debe emitir {expected}")
        self.assertNotIn('"pageup"', block)
        self.assertNotIn('"pagedown"', block)
        with open(os.path.join(PACKAGE, "hotkey.py"), encoding="utf-8") as handle:
            hotkey_source = handle.read()
        for key in ("page_up", "page_down", "caps_lock"):
            self.assertIn(f'"{key}": "{key}"', hotkey_source,
                         f"hotkey.py: falta el alias POSIX {key}")


class HostContractTests(unittest.TestCase):
    def test_panel_bridge_emits_qt_signal_for_web_messages(self):
        with open(GUI_SOURCE, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("self.received.emit(payload)", source)

    def test_hosts_bind_the_channel_and_register_the_bridge(self):
        for name, path in HOSTS.items():
            with self.subTest(host=name), open(path, encoding="utf-8") as handle:
                source = handle.read()
            self.assertIn("webChannel: channel", source,
                          f"{name}: el WebEngineView sin webChannel no inyecta "
                          "qt.webChannelTransport")
            self.assertIn("registerBridge", source, f"{name}: falta registerBridge")
            self.assertIn('objectName: "view"', source, f"{name}: falta el view")
            if name == "panel_web_host.qml":
                self.assertIn("width: 1100", source)
                self.assertIn("height: 600", source)
                self.assertIn("minimumWidth: 760", source)
                self.assertIn("minimumHeight: 480", source)
                self.assertIn("import QtQuick.Dialogs", source)
                self.assertIn("FileDialog", source)
                self.assertIn("function openFileDialog(kind)", source)
                self.assertIn("bridgeObject.fileSelected", source)

    def test_pages_ship_with_the_package(self):
        self.assertTrue(os.path.isfile(PANEL_PAGE))
        self.assertTrue(os.path.isfile(OVERLAY_PAGE))


if __name__ == "__main__":
    unittest.main()
