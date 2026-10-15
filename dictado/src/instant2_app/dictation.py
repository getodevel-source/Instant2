"""Text preparation for the existing dictation flow, independent of its UI."""
import time

from instant2_app import llm


def prepare_text(recognized, cfg, conf=1.0, *, initial_ready=None,
                 runtime=None, runtime_config=None):
    """Share one correction budget between optional startup and both requests.

    Disabled, unconfigured and external services cannot depend on the managed
    local server's startup event. Exhausting the budget uses the existing local
    correction fallback; a sleeping managed model keeps its longer wake budget.
    """
    started = time.monotonic()
    enabled = bool(llm.resolve_url(cfg)) and cfg.get("llm_normal_enabled", True)
    managed = enabled and runtime is not None and runtime_config is not None
    budget = runtime.request_timeout(runtime_config) if managed else 8.0
    deadline = started + budget
    if managed and initial_ready is not None and not initial_ready.is_set():
        initial_ready.wait(timeout=max(0.0, min(15.0, deadline - time.monotonic())))
    remaining = deadline - time.monotonic()
    if enabled and remaining <= 0:
        return llm.correct_locally(recognized, cfg)
    return llm.maybe_polish(recognized, cfg, conf=conf,
                            timeout=max(.001, remaining))
