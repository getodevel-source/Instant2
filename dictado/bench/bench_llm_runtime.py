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
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

CANDIDATES = {
    "qwen3.5-2b-q4": ("Qwen_Qwen3.5-2B-Q4_K_M.gguf", "qwen3.5:2b-q4_K_M"),
    "qwen3.5-2b-q8": ("Qwen_Qwen3.5-2B-Q8_0.gguf", "qwen3.5:2b-q8_0"),
    "qwen3.5-4b-q4": ("Qwen_Qwen3.5-4B-Q4_K_M.gguf", "qwen3.5:4b-q4_K_M"),
    "qwen3.5-4b-q8": ("Qwen_Qwen3.5-4B-Q8_0.gguf", "qwen3.5:4b-q8_0"),
    "llama3.2-3b-q4": ("Llama-3.2-3B-Instruct-Q4_K_M.gguf",
                        "llama3.2:3b-instruct-q4_K_M"),
}
BENCH_SCRIPT = os.path.join(os.path.dirname(__file__), "bench_llm.py")


def build_command(server, model_path, alias, port, backend, device, threads,
                  context):
    command = [server, "-m", model_path, "--alias", alias,
               "--host", "127.0.0.1", "--port", str(port),
               "--ctx-size", str(context), "--threads", str(threads),
               "--no-webui"]
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


def run_candidate(candidate, model_dir, server, report_dir, port=8080,
                  backend="gpu", device="Vulkan0", threads=6, context=4096,
                  timeout=120):
    filename, alias = CANDIDATES[candidate]
    model_path = os.path.join(model_dir, filename)
    if not os.path.isfile(model_path):
        return {"candidate": candidate, "error": "No se encontró el archivo de modelo."}
    os.makedirs(report_dir, exist_ok=True)
    stem = f"{candidate}-{backend}"
    report_path = os.path.join(report_dir, stem + ".json")
    log_path = os.path.join(report_dir, stem + ".server.log")
    command = build_command(server, model_path, alias, port, backend, device,
                            threads, context)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    server_log = open(log_path, "wb")
    process = None
    try:
        started = time.perf_counter()
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=server_log,
            stderr=subprocess.STDOUT, creationflags=creationflags)
        server_start_seconds = _wait_for_server(process, port, timeout)
        completed = subprocess.run(
            [sys.executable, BENCH_SCRIPT, "--url", f"http://127.0.0.1:{port}",
             "--model", alias, "--timeout", "60",
             "--report", report_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
            check=False,
        )
        result = {
            "candidate": candidate,
            "model": alias,
            "backend": backend,
            "device": device if backend == "gpu" else "CPU",
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
        return result
    except Exception as exc:
        return {
            "candidate": candidate,
            "model": alias,
            "backend": backend,
            "error": type(exc).__name__,
            "message": str(exc),
            "server_log_path": log_path,
        }
    finally:
        if process is not None:
            _stop_server(process)
        server_log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True, help="Path to llama-server executable")
    parser.add_argument("--model-dir", required=True, help="Directory with candidate GGUFs")
    parser.add_argument("--report-dir", required=True, help="Local directory for result JSON/logs")
    parser.add_argument("--backend", choices=("gpu", "cpu"), default="gpu")
    parser.add_argument("--device", default="Vulkan0", help="llama.cpp device, e.g. Vulkan0")
    parser.add_argument("--threads", type=int, default=min(8, os.cpu_count() or 4))
    parser.add_argument("--context", type=int, default=4096)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--candidate", action="append", choices=tuple(CANDIDATES),
                        help="Repeat to select candidates; default runs all candidates")
    args = parser.parse_args()
    candidates = args.candidate or list(CANDIDATES)
    os.makedirs(args.report_dir, exist_ok=True)
    summary = {
        "backend": args.backend,
        "device": args.device if args.backend == "gpu" else "CPU",
        "threads": args.threads,
        "context": args.context,
        "candidates": [],
    }
    for candidate in candidates:
        print(f"\n[{candidate}] iniciando servidor ({args.backend})...", flush=True)
        result = run_candidate(
            candidate, args.model_dir, args.server, args.report_dir,
            port=args.port, backend=args.backend, device=args.device,
            threads=args.threads, context=args.context, timeout=args.timeout)
        summary["candidates"].append(result)
        if "error" in result:
            print(f"  error: {result['error']} · {result.get('message', '')}", flush=True)
            continue
        print(f"  modelo listo en {result['server_start_seconds']:.2f}s", flush=True)
        print(result["benchmark_output"], flush=True)
        if result["benchmark_error"]:
            print(result["benchmark_error"], flush=True)
    summary_path = os.path.join(args.report_dir, f"summary-{args.backend}.json")
    with open(summary_path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    print(f"\nResumen guardado en: {summary_path}")


if __name__ == "__main__":
    main()
