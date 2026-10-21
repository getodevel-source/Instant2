"""A permissive model verifier cannot authorize provable changes to data."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolation  # noqa: F401  (aisla el estado real del usuario)
from instant2_app import llm


class CorrectionFactTests(unittest.TestCase):
    def test_changed_numbers_negations_and_restrictions_are_rejected_without_http(self):
        cases = [
            ("Mandá 38000 pesos", "Mandá 42500 pesos"),
            ("Mandá treinta y ocho mil pesos", "Mandá cuarenta y dos mil quinientos pesos"),
            ("Esperá veinte minutos", "Esperá doce minutos"),
            ("Esperá diez días", "Esperá diez semanas"),
            ("El ajuste es menos tres puntos", "El ajuste es tres puntos"),
            ("Guardá cien registros", "Guardá 100 registros"),
            ("No envíes el mensaje", "Envía el mensaje"),
            ("Mostrá solo las diferencias", "Mostrá las diferencias"),
            ("No lo borres ni lo publiques", "No lo borres y lo publiques"),
            ("Generan ochenta y cuatro puestos", "Generan ochenta y cuatro por ciento de los puestos"),
        ]
        for original, candidate in cases:
            with self.subTest(original=original), patch("urllib.request.urlopen") as network:
                self.assertFalse(llm.candidate_preserves_meaning(original, candidate, "http://127.0.0.1:8080"))
                network.assert_not_called()

    def test_normal_punctuation_and_lexical_repairs_remain_eligible(self):
        for original,candidate in [
            ("hola juan", "Hola, Juan."),
            ("hice un comic del repositorio", "hice un commit del repositorio"),
            ("el equipo derrotar", "el equipo a derrotar"),
        ]:
            with self.subTest(original=original):
                self.assertTrue(llm.preserves_facts(original,candidate))

    def test_number_words_survive_orthographic_cleanup(self):
        self.assertTrue(llm.preserves_facts(
            "hoy son veinte y un asistentes", "Hoy son veinte y un asistentes."))


if __name__ == "__main__":
    unittest.main()
