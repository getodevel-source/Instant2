"""Stage the verified Windows ASR, LLM, and Kokoro assets for Inno Setup.

The installer payload is generated during a release build; model weights are
never checked into this repository. Existing verified per-user model files
are reused for a local build, while CI downloads the same pinned assets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
import zipfile
import subprocess


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dictado" / "src"))

from instant2_app import models, tts  # noqa: E402
from instant2_app.paths import (  # noqa: E402
    PARAKEET_SUBDIR, VAD_SUBDIR, model_paths,
)


LLM_SUBDIR = "llm"
LLM_MODEL_NAME = "Qwen_Qwen3.5-4B-Q4_K_M.gguf"
LLM_MODEL_EXPECTED = (
    3_013_027_808,
    "13c16f426047e2de38cd075bdade4a7bcbc8c774384876f677740cda65f8a983",
)
LLM_MODEL_URL = (
    "https://huggingface.co/bartowski/Qwen_Qwen3.5-4B-GGUF/resolve/"
    "ba06320255db2dbec194dad738d066be90dabf29/"
    "Qwen_Qwen3.5-4B-Q4_K_M.gguf"
)

LLAMA_TAG = "b11573"
LLAMA_COMMIT = "0f701c88298606b41a13268a027e97c877ff63ca"
LLAMA_ZIP_NAME = "llama-b11573-bin-win-vulkan-x64.zip"
LLAMA_ZIP_SIZE = 32_112_251
LLAMA_ZIP_SHA256 = "a7ed859c22ff1f53e0a8d81d8cddf533932031f8b93f06d9e7a2ed64bcceebd6"
LLAMA_ZIP_URL = (
    "https://github.com/ggml-org/llama.cpp/releases/download/"
    f"{LLAMA_TAG}/{LLAMA_ZIP_NAME}"
)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _copy_verified(source: Path, destination: Path, expected) -> bool:
    ok, _reason = models.verify_file(str(source), expected)
    if not ok:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != destination.resolve():
        shutil.copyfile(source, destination)
    ok, reason = models.verify_file(str(destination), expected)
    if not ok:
        raise models.ModelIntegrityError(
            f"la copia de {source.name} no verifica: {reason}")
    return True


def _copy_source_models(source_data: Path, payload: Path) -> None:
    """Reuse only model files whose pinned size and SHA-256 already match."""
    source_paths = model_paths(str(source_data))
    target_paths = model_paths(str(payload))
    for key, name in (("encoder", "encoder.int8.onnx"),
                      ("decoder", "decoder.int8.onnx"),
                      ("joiner", "joiner.int8.onnx"),
                      ("tokens", "tokens.txt")):
        _copy_verified(Path(source_paths[key]), Path(target_paths[key]),
                       models.PARAKEET_SHA256[name])
    _copy_verified(Path(source_paths["vad"]), Path(target_paths["vad"]),
                   models.VAD_SHA256)

    source_tts = tts.asset_paths(str(source_data))
    target_tts = tts.asset_paths(str(payload))
    _copy_verified(Path(source_tts["model"]), Path(target_tts["model"]),
                   tts.MODEL_FILE)

    if tts._voice_archive_valid(source_tts["voices"]):
        _copy_verified(Path(source_tts["voices"]), Path(target_tts["voices"]),
                       (os.path.getsize(source_tts["voices"]), _hash(Path(source_tts["voices"]))))
    else:
        source_raw = Path(source_tts["voice_source"])
        if models.verify_file(str(source_raw), tts.DORA_FILE)[0]:
            _copy_verified(source_raw, Path(target_tts["voice_source"]), tts.DORA_FILE)
        else:
            legacy = Path(source_tts["directory"]) / "voices-v1.0.bin"
            if legacy.is_file():
                _convert_legacy_dora(legacy, Path(target_tts["voices"]))

    source_llm = source_data / LLM_SUBDIR / LLM_MODEL_NAME
    target_llm = payload / LLM_SUBDIR / LLM_MODEL_NAME
    _copy_verified(source_llm, target_llm, LLM_MODEL_EXPECTED)


def _convert_legacy_dora(legacy: Path, destination: Path) -> None:
    """Extract Dora from an older all-voice cache without modifying the source."""
    import numpy as np

    destination.parent.mkdir(parents=True, exist_ok=True)
    with np.load(legacy, allow_pickle=False) as archive:
        if "ef_dora" not in archive.files:
            return
        voice = archive["ef_dora"]
    if (voice.shape != tts.DORA_STYLE_SHAPE
            or voice.dtype != np.float32
            or hashlib.sha256(voice.tobytes()).hexdigest() != tts.DORA_FILE[1]):
        return
    np.savez_compressed(destination, ef_dora=voice)
    if not tts._voice_archive_valid(str(destination)):
        destination.unlink(missing_ok=True)
        raise models.ModelIntegrityError("la voz Dora antigua no verifica")


def _fetch_if_missing(url: str, destination: Path, expected) -> None:
    ok, _reason = models.verify_file(str(destination), expected)
    if ok:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    models._fetch(url, str(destination), expected, lambda *_args: None)


def _stage_runtime(app_dir: Path, cache_dir: Path) -> dict:
    cache_dir.mkdir(parents=True, exist_ok=True)
    archive = cache_dir / LLAMA_ZIP_NAME
    _fetch_if_missing(LLAMA_ZIP_URL, archive,
                      (LLAMA_ZIP_SIZE, LLAMA_ZIP_SHA256))

    runtime_dir = app_dir / "llm-runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    written = set()
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            if entry.is_dir():
                continue
            name = PurePosixPath(entry.filename.replace("\\", "/")).name
            upper = name.upper()
            if not (name.lower().endswith(".dll")
                    or name.lower() == "llama-server.exe"
                    or upper.startswith("LICENSE")
                    or upper.startswith("NOTICE")):
                continue
            destination = runtime_dir / name
            with bundle.open(entry) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target, length=1 << 20)
            written.add(name)
    if "llama-server.exe" not in written:
        raise RuntimeError("el paquete oficial no contiene llama-server.exe")
    server = runtime_dir / "llama-server.exe"
    result = subprocess.run(
        [str(server), "--version"], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", timeout=30, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    version_text = result.stdout or ""
    if result.returncode != 0 or LLAMA_COMMIT[:9] not in version_text:
        raise RuntimeError(
            "el runtime llama.cpp incluido no pasó --version: "
            f"exit={result.returncode}, salida={version_text[:300]!r}")
    notice = runtime_dir / "llama-runtime-source.json"
    notice.write_text(json.dumps({
        "repository": "ggml-org/llama.cpp",
        "tag": LLAMA_TAG,
        "commit": LLAMA_COMMIT,
        "asset": LLAMA_ZIP_NAME,
        "asset_sha256": LLAMA_ZIP_SHA256,
        "license": "MIT",
    }, indent=2) + "\n", encoding="utf-8")
    return {"tag": LLAMA_TAG, "commit": LLAMA_COMMIT,
            "zip_sha256": LLAMA_ZIP_SHA256, "files": len(written)}


def prepare(app_dir: Path, payload_dir: Path, source_data: Path,
            cache_dir: Path) -> dict:
    app_dir.mkdir(parents=True, exist_ok=True)
    payload_dir.mkdir(parents=True, exist_ok=True)
    if any(payload_dir.iterdir()):
        raise RuntimeError(
            f"el directorio de modelos debe estar vacío: {payload_dir}")
    _copy_source_models(source_data, payload_dir)

    # Missing files are downloaded by their existing integrity-checked paths.
    models.download_models(str(payload_dir))
    tts.ensure_model(str(payload_dir))
    _fetch_if_missing(
        LLM_MODEL_URL, payload_dir / LLM_SUBDIR / LLM_MODEL_NAME,
        LLM_MODEL_EXPECTED)

    status = models.check_integrity(str(payload_dir))
    invalid = {name: value for name, value in status.items() if value is not True}
    tts_paths = tts.asset_paths(str(payload_dir))
    if invalid:
        raise models.ModelIntegrityError(f"modelos ASR inválidos: {invalid}")
    ok, reason = models.verify_file(tts_paths["model"], tts.MODEL_FILE)
    if not ok or not tts._voice_archive_valid(tts_paths["voices"]):
        raise models.ModelIntegrityError(f"Kokoro/Dora no verifica: {reason}")
    qwen_path = payload_dir / LLM_SUBDIR / LLM_MODEL_NAME
    ok, reason = models.verify_file(str(qwen_path), LLM_MODEL_EXPECTED)
    if not ok:
        raise models.ModelIntegrityError(f"Qwen no verifica: {reason}")

    expected_paths = {
        "parakeet-v3-int8/encoder.int8.onnx",
        "parakeet-v3-int8/decoder.int8.onnx",
        "parakeet-v3-int8/joiner.int8.onnx",
        "parakeet-v3-int8/tokens.txt",
        "silero-vad/silero_vad.onnx",
        f"{LLM_SUBDIR}/{LLM_MODEL_NAME}",
        "kokoro-onnx/kokoro-v1.0.onnx",
        "kokoro-onnx/voices-dora-v1.0.npz",
    }
    actual_paths = {path.relative_to(payload_dir).as_posix()
                    for path in payload_dir.rglob("*") if path.is_file()}
    if actual_paths != expected_paths:
        raise RuntimeError(
            "el bundle contiene archivos inesperados o le faltan archivos: "
            f"faltan={sorted(expected_paths - actual_paths)}, "
            f"sobran={sorted(actual_paths - expected_paths)}")

    runtime = _stage_runtime(app_dir, cache_dir)
    server = app_dir / "llm-runtime" / "llama-server.exe"
    if not server.is_file() or server.stat().st_size == 0:
        raise RuntimeError("llama-server no quedó preparado")

    files = []
    for path in sorted(payload_dir.rglob("*")):
        if path.is_file():
            files.append({"path": path.relative_to(payload_dir).as_posix(),
                          "size": path.stat().st_size,
                          "sha256": _hash(path)})
    total = sum(item["size"] for item in files)
    manifest = {
        "schema": 1,
        "bundle": "Instant2 Windows offline model bundle",
        "llm": {"model": LLM_MODEL_NAME,
                "model_sha256": LLM_MODEL_EXPECTED[1],
                "model_source": LLM_MODEL_URL,
                "runtime": runtime},
        "kokoro_voice": "ef_dora",
        "files": files,
        "total_bytes": total,
    }
    (app_dir / "offline-model-bundle.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    shutil.copyfile(ROOT / "THIRD-PARTY-NOTICES.txt",
                    app_dir / "THIRD-PARTY-NOTICES.txt")
    shutil.copyfile(ROOT / "scripts" / "stop-bundled-runtime.ps1",
                    app_dir / "stop-bundled-runtime.ps1")
    license_dir = app_dir / "licenses"
    license_dir.mkdir(parents=True, exist_ok=True)
    for license_file in (ROOT / "docs" / "licenses").glob("*.txt"):
        shutil.copyfile(license_file, license_dir / license_file.name)
    return {"files": len(files), "bytes": total, "runtime": runtime}


def main(argv=None) -> int:
    from instant2_app.paths import resolve_data_dir

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, required=True,
                        help="directorio PyInstaller que instala Instant2")
    parser.add_argument("--payload-dir", type=Path, required=True,
                        help="directorio separado de modelos para Inno Setup")
    parser.add_argument("--source-data-dir", type=Path,
                        default=Path(resolve_data_dir()),
                        help="reutiliza solo archivos locales verificados")
    parser.add_argument("--cache-dir", type=Path,
                        default=Path(tempfile.gettempdir()) / "instant2-offline-cache")
    args = parser.parse_args(argv)
    result = prepare(args.app_dir.resolve(), args.payload_dir.resolve(),
                     args.source_data_dir.resolve(), args.cache_dir.resolve())
    print(f"Bundle verificado: {result['files']} archivos, "
          f"{result['bytes'] / (1024 ** 3):.2f} GiB de modelos/voz.")
    print(f"llama.cpp {result['runtime']['tag']} listo con CPU y Vulkan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
