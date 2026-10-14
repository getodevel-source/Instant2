"""Private, text-free status and retry handoff between panel and daemon."""
import json
import os
import tempfile
import time

from instant2_app.paths import config_dir, ensure_private_dir, restrict_file

STATUS_NAME = "llm-runtime-status.json"
RETRY_NAME = "llm-runtime-retry"
_STATUSES = {
    "disabled", "needs_setup", "configured", "starting", "ready",
    "sleeping", "recovering", "error", "stopped",
}


def _path(name):
    return os.path.join(config_dir(), name)


def write_status(state, pid=None):
    """Atomically publish the service state without config, prompts or text."""
    state = state if isinstance(state, dict) else {}
    status = state.get("status")
    payload = {
        "status": status if isinstance(status, str) and status in _STATUSES else "error",
        "detail": str(state.get("detail") or "")[:320],
        "device": str(state.get("device") or "")[:100],
        "owned": bool(state.get("owned")),
        "pid": int(pid or os.getpid()),
        "updated_at": time.time(),
    }
    directory = ensure_private_dir(config_dir())
    fd, temporary = tempfile.mkstemp(prefix=".llm-status-", suffix=".tmp",
                                     dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        restrict_file(temporary)
        os.replace(temporary, _path(STATUS_NAME))
        restrict_file(_path(STATUS_NAME))
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return payload


def read_status(max_age_seconds=30.0):
    """Return a recent daemon state; stale status is never shown as ready."""
    try:
        with open(_path(STATUS_NAME), encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return None
        status = payload.get("status")
        updated_at = float(payload.get("updated_at", 0))
        if not isinstance(status, str) or status not in _STATUSES:
            return None
        if time.time() - updated_at > max_age_seconds:
            return {
                "status": "stopped", "detail": "El estado del servicio quedó desactualizado.",
                "device": "", "owned": False,
            }
        return {key: payload.get(key) for key in ("status", "detail", "device", "owned")}
    except (OSError, ValueError, TypeError):
        return None


def request_retry():
    """Ask the resident daemon to retry its local LLM startup/recovery."""
    directory = ensure_private_dir(config_dir())
    path = os.path.join(directory, RETRY_NAME)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return True
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write("retry")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    restrict_file(path)
    return True


def consume_retry_request():
    """Consume a retry request once; its file contains no user data."""
    path = _path(RETRY_NAME)
    try:
        os.unlink(path)
        return True
    except FileNotFoundError:
        return False
