"""El panel tiene que arrancar con Python 3.14 (y con 3.12).

La app instalada 0.1.0 caia al abrir el panel con
`AttributeError: module 'ctypes.wintypes' has no attribute 'WINFUNCTYPE'`:
el tipo de callback de Windows se tomaba de `ctypes.wintypes`, donde ese
nombre no existe (ni en 3.12 ni en 3.14). Vive en `ctypes`.

Estas pruebas comprueban dos cosas:
- que el tipo de callback se resuelve en este interprete (la llamada exacta
  que reventaba al abrir el panel); en Linux y macOS esa asercion no tiene
  sentido (`ctypes.WINFUNCTYPE` no existe ahi) y se salta, pero la guarda
  que no se use `ctypes.wintypes` se corre en los tres sistemas.
- que ningun modulo de la app vuelve a tomarlo de `ctypes.wintypes`.
"""
import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from instant2_app import gui_lifecycle

PACKAGE = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "src", "instant2_app"))


def _uses_wintypes_winfunctype(source):
    """True si el codigo usa `wintypes.WINFUNCTYPE` (o `getattr` de eso).

    Se mira el arbol, no el texto: los comentarios que explican el crash
    mencionan el nombre y no son un uso.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute)
                and node.attr == "WINFUNCTYPE"
                and isinstance(node.value, ast.Name)
                and node.value.id == "wintypes"):
            return True
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) == 2
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "wintypes"
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "WINFUNCTYPE"):
            return True
    return False


class WindowsCallbackTests(unittest.TestCase):
    """El tipo de callback de Windows se resuelve desde `ctypes`."""

    @unittest.skipUnless(os.name == "nt", "ctypes.WINFUNCTYPE es de Windows")
    def test_the_callback_type_comes_from_ctypes(self):
        import ctypes

        self.assertTrue(hasattr(ctypes, "WINFUNCTYPE"))
        callback = gui_lifecycle.winfunctype(ctypes.c_int, ctypes.c_void_p)
        self.assertTrue(callback)

    def test_no_module_takes_the_callback_from_wintypes(self):
        """`ctypes.wintypes.WINFUNCTYPE` no existe: no puede volver a usarse."""
        offenders = []
        for name in sorted(os.listdir(PACKAGE)):
            if not name.endswith(".py"):
                continue
            path = os.path.join(PACKAGE, name)
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            if _uses_wintypes_winfunctype(source):
                offenders.append(name)
        self.assertEqual(offenders, [])

    @unittest.skipUnless(os.name == "nt", "user32 es de Windows")
    def test_user32_resolves_the_callback_type_on_this_interpreter(self):
        """La llamada que reventaba al abrir el panel en 3.14.

        `_user32()` es lo que se ejecuta al buscar la ventana del panel:
        con el tipo de callback taken de `ctypes.wintypes` fallaba aqui y el
        panel caia en "interfaz grafica no disponible".
        """
        user32 = gui_lifecycle._user32()
        self.assertIsNotNone(user32.EnumWindows.argtypes)
        self.assertTrue(user32.EnumWindows.argtypes[0])

    def test_a_missing_callback_type_fails_with_a_clear_message(self):
        """Un interprete sin el tipo de callback dice por que falla."""
        import ctypes

        original = getattr(ctypes, "WINFUNCTYPE", None)
        try:
            if hasattr(ctypes, "WINFUNCTYPE"):
                del ctypes.WINFUNCTYPE
            with self.assertRaises(AttributeError) as caught:
                gui_lifecycle.winfunctype(ctypes.c_int)
            self.assertIn("WINFUNCTYPE", str(caught.exception))
        finally:
            if original is not None:
                ctypes.WINFUNCTYPE = original


if __name__ == "__main__":
    unittest.main()
