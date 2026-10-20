"""Run the synthetic dictation correction corpus against local GGUF variants.

Example for a Vulkan-capable GPU:
    python bench/bench_llm_runtime.py --server C:/llama/llama-server.exe \
        --model-dir C:/models --backend gpu --device Vulkan0 \
        --report-dir C:/Temp/instant2-llm-results

The server and models stay outside the installer. The script starts one model
at a time, measures server startup separately from warm requests, and stops the
server after each candidate.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

CANDIDATES = {
    "qwen3.5-4b-q4": ("Qwen_Qwen3.5-4B-Q4_K_M.gguf", "qwen3.5:4b-q4_K_M"),
    "bonsai2-9b-q2": ("Ternary-Bonsai-2-9B-Q2_0.gguf", "bonsai2-9b-q2-0"),
}
DEFAULT_PORT = 8081
DEFAULT_CORPUS = os.path.join(
    os.path.dirname(__file__), "llm_cases_expanded_2026-10-16.json")
BENCH_SCRIPT = os.path.join(os.path.dirname(__file__), "bench_llm.py")
SAMPLING = {
    "matched-deterministic": {
        "temperature": 0.0, "top_p": 1.0, "top_k": 0, "min_p": 0.0,
        "presence_penalty": 0.0, "repetition_penalty": 1.0,
    },
    "bonsai-nonthinking": {
        "temperature": 0.7, "top_p": 0.80, "top_k": 20, "min_p": 0.0,
        "presence_penalty": 1.5, "repetition_penalty": 1.0,
    },
}


def build_command(server, model_path, alias, port, backend, device, threads,
                  context, sampling_profile="matched-deterministic"):
    sampling = SAMPLING[sampling_profile]
    command = [server, "-m", model_path, "--alias", alias,
               "--host", "127.0.0.1", "--port", str(port),
               "--ctx-size", str(context), "--threads", str(threads),
               "--no-webui", "--temp", str(sampling["temperature"]),
               "--top-p", str(sampling["top_p"]),
               "--top-k", str(sampling["top_k"]),
               "--min-p", str(sampling["min_p"]),
               "--presence-penalty", str(sampling["presence_penalty"]),
               "--repeat-penalty", str(sampling["repetition_penalty"]),
               "--repeat-last-n", "64"]
    if backend == "cpu":
        command.extend(["--device", "none", "--n-gpu-layers", "0"])
    else:
        command.extend(["--device", device, "--n-gpu-layers", "99"])
    return command


def _wait_for_server(process, port, timeout):
    start = time.perf_counter()
    deadline = start + timeout
    url = f"http://127.0.0.1:{port}/health"
    while time.perf_counter() < deadline:
        code = process.poll()
        if code is not None:
            raise RuntimeError(f"llama-server terminó durante el arranque (código {code})")
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                body = json.loads(response.read().decode("utf-8", "replace"))
            if body.get("status") == "ok":
                return time.perf_counter() - start
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    raise TimeoutError("llama-server no estuvo listo dentro del tiempo límite")


def _stop_server(process):
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)

def _process_memory(pid):
    """Return Windows process memory counters, or None where unavailable."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x0400 | 0x0010, False, pid)
    if not handle:
        return {"error": ctypes.get_last_error()}
    try:
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(
                handle, ctypes.byref(counters), counters.cb):
            return {"error": ctypes.get_last_error()}
        return {
            "measurement": "Windows GetProcessMemoryInfo after benchmark; process-lifetime peaks",
            "working_set_bytes": counters.WorkingSetSize,
            "peak_working_set_bytes": counters.PeakWorkingSetSize,
            "private_bytes": counters.PagefileUsage,
            "peak_private_bytes": counters.PeakPagefileUsage,
        }
    finally:
        kernel32.CloseHandle(handle)


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def run_candidate(candidate, model_dir, server, report_dir, port=DEFAULT_PORT,
                  backend="gpu", device="Vulkan0", threads=6, context=4096,
                  timeout=120, sampling_profile="matched-deterministic",
                  corpus=DEFAULT_CORPUS, case_ids=None, run_label=None):
    filename, alias = CANDIDATES[candidate]
    model_path = os.path.join(model_dir, filename)
    if not os.path.isfile(model_path):
        return {"candidate": candidate, "error": "No se encontró el archivo de modelo."}
    os.makedirs(report_dir, exist_ok=True)
    stem = f"{candidate}-{sampling_profile}-{run_label or 'run1'}-{backend}"
    report_path = os.path.join(report_dir, stem + ".json")
    log_path = os.path.join(report_dir, stem + ".server.log")
    command = build_command(server, model_path, alias, port, backend, device,
                            threads, context, sampling_profile)
    bench_command = [
        sys.executable, BENCH_SCRIPT, "--url", f"http://127.0.0.1:{port}",
        "--model", alias, "--timeout", "60", "--corpus", corpus,
        "--sampling-profile", sampling_profile, "--report", report_path]
    if case_ids:
        bench_command.extend(["--case-ids", ",".join(case_ids)])
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    server_version = subprocess.run(
        [server, "--version"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=15, check=False)
    server_log = open(log_path, "wb")
    process = None
    result = None
    try:
        started = time.perf_counter()
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=server_log,
            stderr=subprocess.STDOUT, creationflags=creationflags)
        server_start_seconds = _wait_for_server(process, port, timeout)
        completed = subprocess.run(
            bench_command, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            check=False)
        result = {
            "candidate": candidate,
            "model": alias,
            "backend": backend,
            "device": device if backend == "gpu" else "CPU",
            "port": port,
            "sampling_profile": sampling_profile,
            "sampling_params": SAMPLING[sampling_profile],
            "run_label": run_label or "run1",
            "server_build": server_version.stdout.strip() or server_version.stderr.strip(),
            "server_version_exit_code": server_version.returncode,
            "server_binary_sha256": _sha256_file(server),
            "model_file": model_path,
            "model_sha256": _sha256_file(model_path),
            "server_command": command,
            "benchmark_command": bench_command,
            "server_start_seconds": server_start_seconds,
            "total_run_seconds": time.perf_counter() - started,
            "benchmark_exit_code": completed.returncode,
            "benchmark_output": completed.stdout.strip(),
            "benchmark_error": completed.stderr.strip()[:1000],
            "report_path": report_path,
            "server_log_path": log_path,
        }
        try:
            with open(report_path, encoding="utf-8") as handle:
                result["metrics"] = json.load(handle)
        except (OSError, ValueError):
            pass
    except Exception as exc:
        result = {
            "candidate": candidate,
            "model": alias,
            "backend": backend,
            "sampling_profile": sampling_profile,
            "run_label": run_label or "run1",
            "error": type(exc).__name__,
            "message": str(exc),
            "server_command": command,
            "benchmark_command": bench_command,
            "server_log_path": log_path,
        }
    finally:
        if process is not None:
            try:
                server_memory = _process_memory(process.pid)
            except Exception as exc:
                server_memory = {"error": type(exc).__name__ + ": " + str(exc)}
            if isinstance(result, dict):
                result["server_memory"] = server_memory
                try:
                    with open(report_path, encoding="utf-8") as handle:
                        benchmark_report = json.load(handle)
                    benchmark_report["runtime"] = {
                        "run_label": run_label or "run1",
                        "port": port,
                        "server_build": server_version.stdout.strip()
                        or server_version.stderr.strip(),
                        "server_binary_sha256": _sha256_file(server),
                        "model_file": model_path,
                        "model_sha256": _sha256_file(model_path),
                        "server_command": command,
                        "benchmark_command": bench_command,
                        "server_memory": server_memory,
                        "server_start_seconds": result.get("server_start_seconds"),
                    }
                    with open(report_path, "w", encoding="utf-8") as handle:
                        json.dump(benchmark_report, handle, ensure_ascii=False, indent=2)
                except (OSError, ValueError):
                    pass
            _stop_server(process)
        server_log.close()
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True, help="Path to llama-server executable")
    parser.add_argument("--model-dir", required=True, help="Directory with candidate GGUFs")
    parser.add_argument("--report-dir", required=True, help="Local directory for result JSON/logs")
    parser.add_argument("--backend", choices=("gpu", "cpu"), default="gpu")
    parser.add_argument("--device", default="Vulkan0", help="llama.cpp device, e.g. Vulkan0")
    parser.add_argument("--threads", type=int, default=min(8, os.cpu_count() or 4))
    parser.add_argument("--context", type=int, default=4096)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--corpus", default=DEFAULT_CORPUS)
    parser.add_argument("--case-ids", help="Comma-separated subset for sensitivity runs")
    parser.add_argument("--run-label", default="run1",
                        help="Unique report/log suffix, e.g. official-rep2")
    parser.add_argument(
        "--sampling-profile", action="append", choices=tuple(SAMPLING),
        help="Repeatable matched profile; default is matched-deterministic")
    parser.add_argument("--candidate", action="append", choices=tuple(CANDIDATES),
                        help="Repeat to select candidates; default runs both")
    args = parser.parse_args()
    candidates = args.candidate or list(CANDIDATES)
    profiles = args.sampling_profile or ["matched-deterministic"]
    case_ids = ([value.strip() for value in args.case_ids.split(",") if value.strip()]
                if args.case_ids else None)
    os.makedirs(args.report_dir, exist_ok=True)
    summary = {
        "run_label": args.run_label,
        "runtime_invocation": [
            sys.executable, os.path.abspath(__file__), *sys.argv[1:]],
        "backend": args.backend,
        "device": args.device if args.backend == "gpu" else "CPU",
        "threads": args.threads,
        "context": args.context,
        "port": args.port,
        "corpus_path": os.path.abspath(args.corpus),
        "corpus_sha256": _sha256_file(args.corpus),
        "sampling_profiles": {profile: SAMPLING[profile] for profile in profiles},
        "candidates": [],
    }
    for profile in profiles:
        for candidate in candidates:
            print(f"\n[{candidate} · {profile} · {args.run_label}] starting server "
                  f"({args.backend})...", flush=True)
            result = run_candidate(
                candidate, args.model_dir, args.server, args.report_dir,
                port=args.port, backend=args.backend, device=args.device,
                threads=args.threads, context=args.context, timeout=args.timeout,
                sampling_profile=profile, corpus=args.corpus,
                case_ids=case_ids, run_label=args.run_label)
            summary["candidates"].append(result)
            if "error" in result:
                print(f"  error: {result['error']} · {result.get('message', '')}",
                      flush=True)
                continue
            print(f"  server ready in {result['server_start_seconds']:.2f}s",
                  flush=True)
            print(result["benchmark_output"], flush=True)
            if result["benchmark_error"]:
                print(result["benchmark_error"], flush=True)
    profile_key = "-".join(profiles)
    summary_path = os.path.join(
        args.report_dir, f"summary-{profile_key}-{args.run_label}.json")
    with open(summary_path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    print(f"\nSummary saved to: {summary_path}")

if __name__ == "__main__":
    main()
