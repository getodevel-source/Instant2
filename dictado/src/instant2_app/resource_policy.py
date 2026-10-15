"""Shared residency policy for the ASR, local LLM and Kokoro models."""
import os

MODE_IDLE_SECONDS = {
    # Keep the models resident while the background dictation daemon is active.
    "speed": 0,
    # Keep the current five-minute behavior as the balanced default.
    "balanced": 5 * 60,
    # Release ASR, TTS and LLM weights quickly on memory-constrained systems.
    "memory": 90,
}


def resource_mode(config):
    mode = (config or {}).get("resource_mode", "balanced")
    return mode if mode in MODE_IDLE_SECONDS else "balanced"


def idle_unload_seconds(config):
    """Seconds before an idle inference model is released; zero means resident."""
    return MODE_IDLE_SECONDS[resource_mode(config)]


def inference_threads(value=4):
    """Apply the existing CPU budget to each local voice inference session."""
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = 4
    return max(1, min(8, os.cpu_count() or 4, value))
