"""El passthrough de sampling del LLM solo existe para el benchmark.

`polish_candidate` acepta overrides de sampling que usa el benchmark local;
la produccion los omite y se queda con el cuerpo determinista (temperatura 0).
"""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolation  # noqa: F401  (aisla el estado real del usuario)
from instant2_app import llm


class _FakeResponse:
    """Respuesta minima de un servidor compatible con OpenAI."""

    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _capture_body(**kwargs):
    """Ejecuta `polish_candidate` y devuelve el cuerpo JSON que se envio."""
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _FakeResponse(
            {"choices": [{"message": {"content": "texto corregido"}}]})

    with patch("urllib.request.urlopen", fake_urlopen):
        result = llm.polish_candidate(
            "hola mundo", "http://127.0.0.1:8080", **kwargs)
    return captured, result


class LLMSamplingPassthroughTests(unittest.TestCase):
    def test_benchmark_overrides_reach_the_request_body(self):
        captured, result = _capture_body(sampling_params={
            "temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.05,
            "presence_penalty": 1.5, "repetition_penalty": 1.1})
        body = captured["body"]
        self.assertEqual(result, "texto corregido")
        self.assertEqual(body["temperature"], 0.7)
        self.assertEqual(body["top_p"], 0.8)
        self.assertEqual(body["top_k"], 20)
        self.assertEqual(body["min_p"], 0.05)
        self.assertEqual(body["presence_penalty"], 1.5)
        self.assertEqual(body["repetition_penalty"], 1.1)

    def test_production_call_sends_only_the_deterministic_body(self):
        captured, _result = _capture_body()
        body = captured["body"]
        self.assertEqual(body["temperature"], 0.0)
        for name in ("top_p", "top_k", "min_p", "presence_penalty",
                     "repetition_penalty"):
            with self.subTest(parameter=name):
                self.assertNotIn(name, body)
        self.assertEqual(body["chat_template_kwargs"],
                         {"enable_thinking": False})
        self.assertEqual(body["messages"][0]["role"], "system")
        self.assertEqual(body["messages"][1], {"role": "user",
                                                "content": "hola mundo"})

    def test_unknown_sampling_keys_are_not_forwarded(self):
        captured, _result = _capture_body(sampling_params={
            "min_p": 0.05, "repetition_penalty": 1.1, "miro": "no"})
        body = captured["body"]
        self.assertEqual(body["min_p"], 0.05)
        self.assertEqual(body["repetition_penalty"], 1.1)
        self.assertNotIn("miro", body)


if __name__ == "__main__":
    unittest.main()
