"""Unit checks for the experimental Arch/Hyprland Wayland route."""
import os
import sys
import unittest
from unittest.mock import mock_open, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from instant2_app import deps, hotkey
from instant2_app.wayland_hotkey import PortalHotkey, _trigger_for


class WaylandHotkeyTests(unittest.TestCase):
    def test_trigger_maps_common_keys_to_portal_keysyms(self):
        self.assertEqual(_trigger_for("f9"), "F9")
        self.assertEqual(_trigger_for("page_down"), "Page_Down")
        self.assertEqual(_trigger_for("a"), "a")
        with self.assertRaises(ValueError):
            _trigger_for("not a key")

    def test_factory_uses_portal_when_wayland_session_is_detected(self):
        with patch.object(sys, "platform", "linux"), \
                patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland",
                                        "WAYLAND_DISPLAY": "wayland-0"},
                           clear=False):
            backend = hotkey.create("f9")
        self.assertIsInstance(backend, PortalHotkey)
        self.assertEqual(backend.key_name, "f9")
        self.assertEqual(backend.trigger, "F9")

    def test_portal_dispatches_press_and_release_for_the_configured_key(self):
        backend = PortalHotkey("f9")
        events = []
        backend._MessageType = type("MessageType", (), {"SIGNAL": object()})
        signal = backend._MessageType.SIGNAL
        backend._session_path = "/session/test"
        backend._pressed = False
        backend._on_press = lambda: events.append("press")
        backend._on_release = lambda: events.append("release")

        def send(member):
            message = type("Message", (), {
                "message_type": signal, "interface": "org.freedesktop.portal.GlobalShortcuts",
                "path": "/session/test", "body": [0, backend.shortcut_id], "member": member,
            })()
            backend._on_portal_signal(message)

        send("Activated")
        send("Deactivated")
        self.assertEqual(events, ["press", "release"])

    def test_custom_key_gets_a_binding_id(self):
        default = PortalHotkey("f9")
        custom = PortalHotkey("f12")
        self.assertEqual(default.shortcut_id, "instant2-push-to-talk")
        self.assertEqual(custom.shortcut_id, "instant2-push-to-talk-f12")


class ArchDependencyTests(unittest.TestCase):
    def test_detects_omarchy_and_arch_os_release(self):
        with patch("builtins.open", mock_open(
                read_data='ID=omarchy\nID_LIKE="arch"\n')), \
                patch.object(deps.shutil, "which", return_value="/usr/bin/pacman"):
            self.assertTrue(deps._arch_based())

    def test_system_dependency_install_uses_pacman_without_interactive_password(self):
        with patch.object(sys, "platform", "linux"), \
                patch.object(deps, "_arch_based", return_value=True), \
                patch.object(deps.os, "geteuid", return_value=1000, create=True), \
                patch.object(deps.subprocess, "run") as run:
            deps._install_one("wtype")
        command = run.call_args.args[0]
        self.assertEqual(command, ["sudo", "-n", "pacman", "-S",
                                   "--needed", "--noconfirm", "wtype"])

    def test_wayland_diagnostics_name_the_arch_packages(self):
        imports = lambda name: name != "dbus_fast"
        which = lambda name: "/usr/bin/pacman" if name == "pacman" else None
        with patch.object(sys, "platform", "linux"), \
                patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland",
                                        "XDG_CURRENT_DESKTOP": "Hyprland",
                                        "WAYLAND_DISPLAY": "wayland-0"},
                           clear=False), \
                patch.object(deps, "_importable", side_effect=imports), \
                patch.object(deps, "_arch_based", return_value=True), \
                patch.object(deps, "_can_pacman", return_value=True), \
                patch.object(deps, "_lib_present", return_value=True), \
                patch.object(deps, "_dpkg_ok", return_value=False), \
                patch.object(deps.shutil, "which", side_effect=which):
            result = deps.check()
        self.assertFalse(result["dbus_fast"]["ok"])
        self.assertIn("pacman -S xdg-desktop-portal",
                      result["xdg-desktop-portal"]["hint"])
        self.assertIn("pacman -S xdg-desktop-portal-hyprland",
                      result["xdg-desktop-portal-hyprland"]["hint"])
        self.assertIn("pacman -S wl-clipboard", result["wl-clipboard"]["hint"])
        self.assertIn("pacman -S wtype", result["wtype"]["hint"])
        self.assertTrue(result["wl-clipboard"]["auto"])


if __name__ == "__main__":
    unittest.main()
