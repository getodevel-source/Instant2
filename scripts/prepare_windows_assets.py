r"""Stage the Windows installer payload.

The installer is a single file and carries two things: the app (the
PyInstaller directory the build already produced) and, for the full build,
the voice models. The voice models are what make a fresh install dictate and
read aloud offline from the first minute; they are staged into a separate
directory that Inno Setup copies to `%LOCALAPPDATA%\instant2\models`.

The optional local LLM (~3 GB) is never staged: the app downloads it on
demand from Settings. That is what keeps the installer at ~1.1 GB instead of
4 GB, and the in-app updater at ~204 MB (the app-only build, without
`--models-dir`).

Model weights are never checked into this repository. Locally, files already
verified in the user's model directory are reused; CI downloads the same
pinned assets, and every file is verified by size and SHA-256 before it is
staged.
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


LLAMA_TAG = "b11573"
LLAMA_COMMIT = "0f701c88298606b41a13268a027e97c877ff63ca"
LLAMA_ZIP_NAME = "llama-b11573-bin-win-vulkan-x64.zip"
LLAMA_ZIP_SIZE = 32_112_251
LLAMA_ZIP_SHA256 = "a7ed859c22ff1f53e0a8d81d8cddf533932031f8b93f06d9e7a2ed64bcceebd6"
LLAMA_ZIP_URL = (
    "https://github.com/ggml-org/llama.cpp/releases/download/"
    f"{LLAMA_TAG}/{LLAMA_ZIP_NAME}"
)

# Exact file set of the voice payload. There is deliberately no `llm/` entry:
# bundling the 3 GB GGUF is what made the old installer 4 GB.
VOICE_MODEL_FILES = (
    f"{PARAKEET_SUBDIR}/encoder.int8.onnx",
    f"{PARAKEET_SUBDIR}/decoder.int8.onnx",
    f"{PARAKEET_SUBDIR}/joiner.int8.onnx",
    f"{PARAKEET_SUBDIR}/tokens.txt",
    f"{VAD_SUBDIR}/silero_vad.onnx",
    "kokoro-onnx/kokoro-v1.0.onnx",
    "kokoro-onnx/voices-dora-v1.0.npz",
)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _copy_verified(source: Path, destination: Path, expected) -> bool:
    """Copy a model file only when the source matches its pinned hash."""
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
                       (os.path.getsize(source_tts["voices"]),
                        _hash(Path(source_tts["voices"]))))
    else:
        source_raw = Path(source_tts["voice_source"])
        if models.verify_file(str(source_raw), tts.DORA_FILE)[0]:
            _copy_verified(source_raw, Path(target_tts["voice_source"]),
                           tts.DORA_FILE)


def _stage_models(payload_dir: Path, source_data: Path) -> dict:
    payload_dir.mkdir(parents=True, exist_ok=True)
    if any(payload_dir.iterdir()):
        raise RuntimeError(
            f"el directorio de modelos debe estar vacío: {payload_dir}")
    _copy_source_models(source_data, payload_dir)

    # Missing files are downloaded through their integrity-checked paths.
    models.download_models(str(payload_dir))
    tts.ensure_model(str(payload_dir))

    invalid = {name: value
               for name, value in models.check_integrity(str(payload_dir)).items()
               if value is not True}
    tts_paths = tts.asset_paths(str(payload_dir))
    ok, reason = models.verify_file(tts_paths["model"], tts.MODEL_FILE)
    if invalid or not ok or not tts._voice_archive_valid(tts_paths["voices"]):
        raise models.ModelIntegrityError(
            f"los modelos de voz no verifican: ASR={invalid}, Kokoro={reason}")

    expected = set(VOICE_MODEL_FILES)
    actual = {path.relative_to(payload_dir).as_posix()
              for path in payload_dir.rglob("*") if path.is_file()}
    if actual != expected:
        raise RuntimeError(
            "el payload de voz contiene archivos inesperados o le faltan: "
            f"faltan={sorted(expected - actual)}, "
            f"sobran={sorted(actual - expected)}")
    return {"files": len(actual),
            "bytes": sum(path.stat().st_size
                         for path in payload_dir.rglob("*")
                         if path.is_file())}


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


def prepare(app_dir: Path, cache_dir: Path, models_dir: Path = None,
            source_data_dir: Path = None) -> dict:
    app_dir.mkdir(parents=True, exist_ok=True)
    result = {"runtime": _stage_runtime(app_dir, cache_dir)}
    if models_dir is not None:
        voice = _stage_models(models_dir, source_data_dir)
        result["models"] = voice
        files = [{"path": path.relative_to(models_dir).as_posix(),
                  "size": path.stat().st_size, "sha256": _hash(path)}
                 for path in sorted(models_dir.rglob("*")) if path.is_file()]
        (app_dir / "voice-model-bundle.json").write_text(
            json.dumps({
                "schema": 1,
                "bundle": "Instant2 Windows voice model bundle",
                "llm_bundled": False,
                "files": files,
                "total_bytes": sum(item["size"] for item in files),
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")

    shutil.copyfile(ROOT / "THIRD-PARTY-NOTICES.txt",
                    app_dir / "THIRD-PARTY-NOTICES.txt")
    shutil.copyfile(ROOT / "scripts" / "stop-bundled-runtime.ps1",
                    app_dir / "stop-bundled-runtime.ps1")
    license_dir = app_dir / "licenses"
    license_dir.mkdir(parents=True, exist_ok=True)
    for license_file in (ROOT / "docs" / "licenses").glob("*.txt"):
        shutil.copyfile(license_file, license_dir / license_file.name)
    return result


def main(argv=None) -> int:
    from instant2_app.paths import resolve_data_dir

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, required=True,
                        help="directorio PyInstaller que instala Instant2")
    parser.add_argument("--models-dir", type=Path,
                        help="stage the voice models here (full installer); "
                             "omit it for the app-only update installer")
    parser.add_argument("--source-data-dir", type=Path,
                        help="reuse only verified local model files "
                             "(default: the user's model directory)")
    parser.add_argument("--cache-dir", type=Path,
                        default=(Path(tempfile.gettempdir())
                                 / "instant2-windows-cache"))
    args = parser.parse_args(argv)
    source = (args.source_data_dir.resolve() if args.source_data_dir
              else Path(resolve_data_dir()).resolve())
    result = prepare(args.app_dir.resolve(), args.cache_dir.resolve(),
                     args.models_dir.resolve() if args.models_dir else None,
                     source)
    print("Recursos de Windows listos: "
          f"{result['runtime']['files']} archivos del runtime llama.cpp.")
    if "models" in result:
        print(f"Payload de voz verificado: {result['models']['files']} archivos, "
              f"{result['models']['bytes'] / (1024 ** 2):.0f} MB (sin LLM).")
    else:
        print("Instalador solo app: sin modelos (los que ya estan en el "
              "directorio de datos no se tocan).")
    print(f"llama.cpp {result['runtime']['tag']} listo con CPU y Vulkan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
