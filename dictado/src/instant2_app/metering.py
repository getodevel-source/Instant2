"""Reusable nine-band audio metering with one cached FFT layout per stream."""
import numpy as np


class SpectrumMeter:
    def __init__(self, sample_rate, decay):
        self.sample_rate = sample_rate
        self.decay = decay
        self.maximum = np.full(9, 1e-3, dtype=np.float64)
        self._size = 0
        self._window = None
        self._masks = ()

    def bands(self, samples):
        samples = np.asarray(samples).reshape(-1)
        if not samples.size:
            return np.zeros(9, dtype=np.float64)
        if samples.size != self._size:
            self._size = samples.size
            self._window = np.hanning(samples.size)
            frequencies = np.fft.rfftfreq(samples.size, 1.0 / self.sample_rate)
            edges = np.logspace(np.log10(80.0), np.log10(7500.0), 10)
            self._masks = tuple(
                np.flatnonzero((frequencies >= edges[index]) & (frequencies < edges[index + 1]))
                for index in range(9))
        spectrum = np.abs(np.fft.rfft(samples * self._window))
        values = np.array([float(np.mean(spectrum[indices])) if indices.size else 0.0
                           for indices in self._masks])
        self.maximum = np.maximum(self.maximum * self.decay, values)
        return np.clip(values / np.maximum(self.maximum, 1e-9), 0.0, 1.0)
