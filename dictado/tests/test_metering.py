"""Cached visual metering preserves the existing nine-band calculation."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from instant2_app.metering import SpectrumMeter


class MeteringTests(unittest.TestCase):
    def test_cached_layout_matches_uncached_calculation_and_does_not_change_audio(self):
        for rate, decay in ((16000, .995), (24000, .98)):
            meter = SpectrumMeter(rate, decay)
            maximum = np.full(9, 1e-3)
            for size in (960, 960, 2400, 2400, 127):
                samples = np.sin(np.arange(size) * 2 * np.pi * 700 / rate).astype(np.float32)
                original = samples.copy()
                spectrum = np.abs(np.fft.rfft(samples * np.hanning(size)))
                frequencies = np.fft.rfftfreq(size, 1 / rate)
                edges = np.logspace(np.log10(80), np.log10(7500), 10)
                values = []
                for index in range(9):
                    mask = (frequencies >= edges[index]) & (frequencies < edges[index + 1])
                    values.append(float(np.mean(spectrum[mask])) if np.any(mask) else 0)
                maximum = np.maximum(maximum * decay, values)
                expected = np.clip(values / np.maximum(maximum, 1e-9), 0, 1)
                cached = meter._window
                old_size = meter._size
                np.testing.assert_allclose(meter.bands(samples), expected)
                np.testing.assert_array_equal(samples, original)
                if old_size == size:
                    self.assertIs(meter._window, cached)

    def test_empty_input_and_silence_stay_finite_and_quiet(self):
        meter = SpectrumMeter(16000, .995)
        for samples in ([], np.zeros(960), np.zeros(2400)):
            np.testing.assert_array_equal(meter.bands(samples), np.zeros(9))


if __name__ == "__main__":
    unittest.main()
