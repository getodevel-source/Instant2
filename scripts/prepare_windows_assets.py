r"""Stage the Windows app-side assets that the installer ships.

The Windows installer carries the app and the llama.cpp runtime; the voice
components (ASR, TTS and the optional local LLM) are downloaded by the app
itself, so they are not staged here and the installer stays small. This
script prepares what the installer *does* carry: the llama.cpp runtime in
`{app}\llm-runtime`, the third-party notices, the helper that stops the
bundled runtime and the license texts.

Model weights are never checked into this repository. The llama.cpp archive
is downloaded from its pinned release and verified by size and SHA-256.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
import zipfile
import subprocess


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dictado" / "src"))

from instant2_app import models  # noqa: E402


LLAMA_TAG = "b11573"
LLAMA_COMMIT = "0f701c88298606b41a13268a027e97c877ff63ca"
LLAMA_ZIP_NAME = "llama-b11573-bin-win-vulkan-x64.zip"
LLAMA_ZIP_SIZE = 32_112_251
LLAMA_ZIP_SHA256 = "a7ed859c22ff1f53e0a8d81d8cddf533932031f8b93f06d9e7a2ed64bcceebd6"
LLAMA_ZIP_URL = (
    "https://github.com/ggml-org/llama.cpp/releases/download/"
    f"{LLAMA_TAG}/{LLAMA_ZIP_NAME}"
)


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


def prepare(app_dir: Path, cache_dir: Path) -> dict:
    app_dir.mkdir(parents=True, exist_ok=True)
    runtime = _stage_runtime(app_dir, cache_dir)
    server = app_dir / "llm-runtime" / "llama-server.exe"
    if not server.is_file() or server.stat().st_size == 0:
        raise RuntimeError("llama-server no quedó preparado")

    shutil.copyfile(ROOT / "THIRD-PARTY-NOTICES.txt",
                    app_dir / "THIRD-PARTY-NOTICES.txt")
    shutil.copyfile(ROOT / "scripts" / "stop-bundled-runtime.ps1",
                    app_dir / "stop-bundled-runtime.ps1")
    license_dir = app_dir / "licenses"
    license_dir.mkdir(parents=True, exist_ok=True)
    for license_file in (ROOT / "docs" / "licenses").glob("*.txt"):
        shutil.copyfile(license_file, license_dir / license_file.name)
    return {"runtime": runtime}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, required=True,
                        help="directorio PyInstaller que instala Instant2")
    parser.add_argument("--cache-dir", type=Path,
                        default=(Path(tempfile.gettempdir())
                                / "instant2-llama-runtime-cache"))
    args = parser.parse_args(argv)
    result = prepare(args.app_dir.resolve(), args.cache_dir.resolve())
    print("Recursos de Windows listos: "
          f"{result['runtime']['files']} archivos del runtime llama.cpp.")
    print(f"llama.cpp {result['runtime']['tag']} listo con CPU y Vulkan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
