"""Actualizaciones desde GitHub Releases, solo stdlib (sin deps nuevas).

Ciclo: check (API) -> download (asset + sidecar .sha256) -> verify -> aplicar.
El asset depende de cómo esté instalada la app: una instalación con
desinstalador (Windows) se actualiza con `Instant2-Update.exe` (la app sola,
~204 MB) y, si el release no lo trae, con `Instant2-Setup.exe` (el instalador
completo con los modelos de voz, ~1,1 GB); el exe portable se cambia con
`instant2-update.bat` (frena todo, respalda y reemplaza); en Linux/macOS el
binario se reemplaza en caliente (POSIX permite unlink+rename con el proceso
vivo, que sigue usando el inodo viejo).
La actualización POSIX conserva el binario previo en `<destino>.bak` y lo
restaura solo si el nuevo no pasa el health-check (existe+ejecutable).
"""
import hashlib
import json
import ntpath
import os
import re
import shutil
import sys
import tempfile
import urllib.request

from instant2_app.identity import GITHUB_REPOSITORY

REPO = GITHUB_REPOSITORY
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
TIMEOUT = 20
CHUNK = 65536

# Plataforma local -> asset portable del release.
ASSETS = {"win32": "Instant2.exe", "linux": "instant2-linux", "darwin": "instant2-macos"}
# Asset de la instalación con desinstalador (Windows, Inno Setup). Es el
# instalador completo: app + runtime + los modelos de voz, en un solo archivo.
INSTALLER_ASSET = "Instant2-Setup.exe"
# Instalador solo app, sin modelos. Es el que usa el actualizador in-app: los
# pesos ya viven en %LOCALAPPDATA%\instant2\models y la actualización no los
# toca, así que bajar 204 MB alcanza (en vez de 1,1 GB).
UPDATE_ASSET = "Instant2-Update.exe"
UNINSTALLER = "unins000.exe"
_INSTALLER_PART = re.compile(r"^Instant2-Setup-(\d+)\.bin$")

# Tag válido: semver estricto X.Y.Z (tras `normalize`). Un tag raro no dispara
# descarga: `check()` lo rechaza en voz alta en vez de comparar a ciegas.
_STRICT_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def current_version():
    from instant2_app import __version__
    return __version__


def normalize(tag):
    """Normalize a SemVer tag, including Instant2's collision-safe prefix."""
    value = tag.strip()
    if value[:10].casefold() == "instant2-v":
        return value[10:]
    return value.lstrip("vV")


def _parts(version):
    return tuple(int(piece) for piece in re.findall(r"\d+", version))


def is_newer(latest, current):
    """True si latest supera a current comparando números (no strings).

    Ante cualquier fallo de parseo devuelve False: un updater que no entiende
    una versión no descarga nada (mejor quedarse que traer lo incorrecto).
    """
    try:
        return _parts(normalize(latest)) > _parts(normalize(current))
    except Exception:
        return False


def fetch_json(url):
    """GET JSON con UA propia (la API de GitHub la exige) y timeout."""
    request = urllib.request.Request(url, headers={"User-Agent": "Instant2-updater"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def latest_release():
    """Lee el último release: tag, versión, notas y URLs por asset."""
    payload = fetch_json(API_LATEST)
    assets = {item.get("name", ""): item.get("browser_download_url", "")
              for item in payload.get("assets", [])}
    sizes = {item.get("name", ""): max(0, int(item.get("size") or 0))
             for item in payload.get("assets", [])}
    return {"tag": payload.get("tag_name", ""),
            "version": normalize(payload.get("tag_name", "")),
            "notes": payload.get("body", "") or "",
            "assets": assets, "asset_sizes": sizes}


def _installer_files(assets, sizes=None):
    """Return the main setup and all Inno Setup disk volumes in disk order."""
    sizes = sizes or {}
    names = [name for name in assets if name == INSTALLER_ASSET]
    parts = []
    for name in assets:
        match = _INSTALLER_PART.fullmatch(name)
        if match:
            parts.append((int(match.group(1)), name))
    part_numbers = [number for number, _name in sorted(parts)]
    if part_numbers and part_numbers != list(range(1, len(part_numbers) + 1)):
        raise ValueError(
            "el release tiene partes incompletas del instalador: "
            f"números encontrados {part_numbers}")
    names.extend(name for _number, name in sorted(parts))
    return [{"name": name, "url": assets[name],
             "size": max(0, int(sizes.get(name) or 0))}
            for name in names if assets.get(name)]


def install_mode():
    """Cómo está instalada la app: 'installed' | 'portable' | 'source'."""
    if not getattr(sys, "frozen", False):
        return "source"
    if sys.platform != "win32":
        return "portable"
    directory = os.path.dirname(sys.executable)
    if os.path.isfile(os.path.join(directory, UNINSTALLER)):
        return "installed"
    return "portable"


def platform_asset(mode=None):
    """Asset del release que corresponde a este SO y modo de instalación."""
    mode = install_mode() if mode is None else mode
    if sys.platform == "win32":
        return INSTALLER_ASSET if mode == "installed" else ASSETS["win32"]
    try:
        return ASSETS[sys.platform]
    except KeyError:
        raise LookupError(f"sin asset para sys.platform={sys.platform!r}")


def apply_hint():
    """Cómo se aplica la descarga en este sistema (texto de CLI)."""
    if install_mode() == "installed":
        return ("Corré el instalador descargado (Instant2-Update.exe): "
                "actualiza en el lugar, sin desinstalar ni pedir admin. "
                "Los modelos de voz que ya tenés no se vuelven a bajar.")
    if sys.platform == "win32":
        return ("Aplicá con: instant2-update.bat <archivo> <sha256> "
                "(frena todo, respalda el exe anterior y lo cambia).")
    return ("Aplicá con: instant2 update --download DIR --apply "
            "(reemplaza el binario en caliente; el próximo arranque ya es el nuevo).")


def _sha256_of(path):
    """SHA256 de un archivo en disco (por stream, sin cargarlo entero)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _posix_health_ok(path):
    """Health-check mínimo post-replace: existe, no vacío y ejecutable."""
    try:
        return (os.path.isfile(path)
                and os.path.getsize(path) > 0
                and os.access(path, os.X_OK))
    except OSError:
        return False


def apply_binary_update(downloaded, target=None, allow_source=False,
                        expected_sha256=None):
    """Reemplaza el binario en uso (Linux/macOS). Devuelve la ruta destino.

    POSIX permite renombrar sobre un ejecutable en ejecución: el proceso vivo
    sigue con el inodo viejo y el próximo arranque ya usa el nuevo. En Windows
    eso no existe: ahí van instalador (modo instalado) o el .bat (portable).
    En instalación source no hay binario que reemplazar: aunque se pase
    `allow_source=True`, se exige `target` explícito fuera del prefijo del
    intérprete (nunca se sobrescribe `sys.executable`).

    `expected_sha256` es obligatorio (el SHA del sidecar `.sha256` verificado
    al descargar): justo antes del `os.replace` final se vuelve a hasear lo
    stageado y se compara. Esto cierra el TOCTOU entre la descarga verificada
    y el reemplazo (si el archivo cambió en el medio, no se instala nada).
    Sin expected no hay apply.

    Rollback: el binario previo se copia a `<target>.bak` antes de reemplazar;
    tras el reemplazo se verifica que el nuevo existe y es ejecutable, y si
    falla se restaura el .bak automáticamente. El .bak se deja en disco a
    propósito como vuelta atrás manual (el .bat de Windows lo borra en éxito).
    """
    if sys.platform == "win32":
        raise RuntimeError(
            "en Windows no se puede reemplazar el binario en uso; "
            "se actualiza con el instalador o instant2-update.bat")
    if install_mode() == "source":
        prefix = os.path.abspath(sys.prefix or sys.executable)
        candidate = os.path.abspath(target) if target else os.path.abspath(sys.executable)
        if target is None or candidate == os.path.abspath(sys.executable) \
                or candidate == prefix or candidate.startswith(prefix + os.sep):
            raise RuntimeError(
                "update --apply no corre en instalación source (solo frozen/portable); "
                "bajá con --download y actualizá el checkout a mano "
                "(o pasá un destino explícito fuera del intérprete).")
    if not expected_sha256 or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        raise RuntimeError(
            "sin SHA256 esperado no se aplica la actualización: pasá "
            "expected_sha256 con el hash del sidecar .sha256 verificado.")
    target = target or sys.executable
    directory = os.path.dirname(os.path.abspath(target))
    fd, staged = tempfile.mkstemp(prefix=".instant2-update-", suffix=".part",
                                  dir=directory)
    applied = False
    backup = target + ".bak"
    had_target = os.path.isfile(target)
    try:
        with os.fdopen(fd, "wb") as destination, open(downloaded, "rb") as source:
            shutil.copyfileobj(source, destination, length=1024 * 1024)
            destination.flush()
            os.fsync(destination.fileno())
        os.chmod(staged, 0o755)
        if _sha256_of(staged) != expected_sha256.lower():
            raise ValueError(
                "SHA256 cambió entre la descarga y el reemplazo "
                "(posible TOCTOU); no se instala nada.")
        if had_target:
            # Sin respaldo no hay reemplazo: si no se puede copiar el previo,
            # se aborta antes de tocar el binario en uso.
            shutil.copyfile(target, backup)
        os.replace(staged, target)
        if not _posix_health_ok(target):
            if had_target and os.path.isfile(backup):
                try:
                    os.replace(backup, target)
                except OSError:
                    pass
                raise RuntimeError(
                    "el binario nuevo no pasó el health-check "
                    "(existe+ejecutable); se restauró la versión anterior.")
            raise RuntimeError(
                "el binario nuevo no pasó el health-check (existe+ejecutable).")
        applied = True
    finally:
        if not applied:
            try:
                os.remove(staged)
            except OSError:
                pass
    try:
        os.remove(downloaded)
    except FileNotFoundError:
        pass
    return target


def check():
    """Compara la versión instalada con el último release."""
    current = current_version()
    release = latest_release()
    if not release["version"]:
        raise RuntimeError(
            "GitHub no devolvió versión (¿rate-limit o sin releases?). "
            "No se informa 'al día' sin haber consultado.")
    if not _STRICT_SEMVER.fullmatch(release["version"]):
        raise RuntimeError(
            f"el tag {release['tag']!r} no es semver X.Y.Z "
            f"(vino {release['version']!r}): sin versión clara no se actualiza.")
    name = platform_asset()
    if name == INSTALLER_ASSET and UPDATE_ASSET in release["assets"]:
        # Instalación existente: los modelos de voz ya están en el directorio
        # de datos y el instalador solo app no los toca. Se baja la cuarta
        # parte en vez del instalador completo.
        name = UPDATE_ASSET
    url = release["assets"].get(name, "")
    asset_files = []
    if name == INSTALLER_ASSET:
        asset_files = _installer_files(release["assets"],
                                      release.get("asset_sizes"))
    elif url:
        asset_files = [{"name": name, "url": url,
                        "size": max(0, int(release.get("asset_sizes", {}).get(name) or 0))}]
    if not is_newer(release["version"], current):
        # latest<=current: no hay update aunque el asset exista. Se devuelve
        # la info igual (sin exigir asset) para no romper el "estás al día".
        return {"update": False,
                "current": current, "latest": release["version"],
                "notes": release["notes"], "asset": name, "asset_url": url,
                "asset_files": asset_files}
    if not url:
        available = ", ".join(sorted(release["assets"])) or "ninguno"
        raise LookupError(
            f"el release {release['tag']} no trae {name} (hay: {available})")
    if name == INSTALLER_ASSET and not asset_files:
        raise LookupError(
            f"el release {release['tag']} no trae un paquete de instalador válido")
    return {"update": True,
            "current": current, "latest": release["version"],
            "notes": release["notes"], "asset": name, "asset_url": url,
            "asset_files": asset_files}


def fetch_expected_sha256(asset_url):
    """Baja el sidecar '<asset>.sha256' ('<hash>  <nombre]'). 404 = error.

    Sin sidecar no hay instalación: instalar sin verificar sería peor que
    no actualizar.
    """
    sidecar = asset_url + ".sha256"
    request = urllib.request.Request(sidecar, headers={"User-Agent": "Instant2-updater"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            text = response.read().decode("utf-8", "replace")
    except Exception as exc:
        raise RuntimeError(f"sin sidecar SHA256 ({sidecar}): {exc}")
    token = text.strip().split()
    if not token or not re.fullmatch(r"[0-9a-fA-F]{64}", token[0]):
        raise ValueError(f"sidecar SHA256 ilegible: {text[:80]!r}")
    return token[0].lower()


def download(url, dest, progress=None, expected_sha256=None):
    """Descarga por stream con hash al vuelo; si no coincide, borra y falla.

    Cualquier error de red a mitad de stream también borra el parcial: nunca
    queda un archivo a medias haciéndose pasar por descarga. Se escribe en
    `<dest>.part` y solo aparece en su ruta final tras verificar el hash.
    """
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    part = dest + ".part"
    digest = hashlib.sha256()
    request = urllib.request.Request(url, headers={"User-Agent": "Instant2-updater"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            header = response.getheader("Content-Length") if hasattr(
                response, "getheader") else None
            total = int(header) if header and header.isdigit() else None
            done = 0
            with open(part, "wb") as handle:
                while True:
                    block = response.read(CHUNK)
                    if not block:
                        break
                    handle.write(block)
                    digest.update(block)
                    done += len(block)
                    if progress is not None:
                        progress(done, total)
    except Exception:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    actual = digest.hexdigest()
    if expected_sha256 and actual != expected_sha256.lower():
        try:
            os.remove(part)
        except OSError:
            pass
        raise ValueError(
            f"SHA256 no coincide (esperado {expected_sha256[:12]}…, "
            f"bajado {actual[:12]}…); parcial eliminado.")
    os.replace(part, dest)
    return dest


def download_release_package(info, download_dir, progress=None):
    """Download and verify every file needed to run the selected release asset.

    A spanning Inno installer needs its .exe and all .bin volumes together.
    The release API lists every file; each one is checked against its own
    SHA-256 sidecar before this function returns the main setup path.
    """
    files = info.get("asset_files") or [{
        "name": info["asset"], "url": info["asset_url"], "size": 0,
    }]
    if not files or files[0].get("name") != info.get("asset"):
        raise ValueError("el paquete de actualización no empieza con su archivo principal")
    for item in files:
        name = item.get("name") or ""
        url = item.get("url") or ""
        if (not url or not name or os.path.basename(name) != name
                or ntpath.basename(name) != name):
            raise ValueError("el release contiene una ruta de asset inválida")
    os.makedirs(download_dir, exist_ok=True)
    known_sizes = [max(0, int(item.get("size") or 0)) for item in files]
    overall_total = sum(known_sizes) if all(known_sizes) else None
    aggregate_done = 0
    main_path = ""
    main_sha256 = ""
    for index, item in enumerate(files):
        url = item.get("url") or ""
        name = item.get("name") or ""
        expected = fetch_expected_sha256(url)
        target = os.path.join(download_dir, name)
        part_base = aggregate_done
        known = known_sizes[index]

        def report(done, part_total):
            if progress is not None:
                total = overall_total
                if total is None and part_total:
                    total = part_base + part_total
                progress(part_base + done, total)

        download(url, target, expected_sha256=expected, progress=report)
        if index == 0:
            main_path, main_sha256 = target, expected
        aggregate_done += known
    return main_path, main_sha256


def clean_notes(notes, max_lines=8, max_chars=600):
    """Notas del release a texto plano corto (sin prefijos markdown)."""
    lines = []
    for raw in (notes or "").splitlines():
        line = re.sub(r"^#+\s*", "", raw).strip()
        if line:
            lines.append(line)
    text = "\n".join(lines[:max_lines])
    return text[:max_chars]


def cmd_update(download_dir=None, apply=False, allow_source_apply=False):
    """CLI `instant2 update [--download DIR] [--apply]`: informa, baja y aplica."""
    try:
        info = check()
    except Exception as exc:
        print(f"No se pudo consultar versiones: {exc}")
        return 2
    print(f"instalada: {info['current'] or '?'} / última: {info['latest'] or '?'}")
    if not info["update"]:
        print("Estás al día.")
        return 0
    notes = clean_notes(info["notes"])
    print(f"Hay versión nueva: {info['latest']} ({info['asset']})")
    for line in notes.splitlines():
        print("  " + line)
    if install_mode() == "source" and apply and not allow_source_apply:
        print("update --apply bloqueado en instalación source: usá --download "
              "y actualizá el checkout a mano (o pasá --allow-source-apply).")
        return 2
    if not download_dir:
        print("Para bajarla: instant2 update --download DIR  "
              "(verifica SHA256 solo).")
        print(apply_hint())
        return 0
    def progress(done, total):
        if total:
            print(f"\r  {done / total:.0%} ({done // 1024} KB)", end="")
        else:
            print(f"\r  {done // 1024} KB", end="")

    try:
        dest, expected = download_release_package(
            info, download_dir, progress=progress)
    except Exception as exc:
        print(f"\nDescarga fallida: {exc}")
        return 2
    print(f"\nDescargado y verificado: {dest}")
    if len(info.get("asset_files") or []) > 1:
        print("Dejé juntas todas las partes necesarias del instalador.")
    print(f"SHA256 del archivo principal: {expected}")
    if not apply:
        print(apply_hint())
        return 0
    try:
        target = apply_binary_update(dest, allow_source=allow_source_apply,
                                     expected_sha256=expected)
    except Exception as exc:
        print(f"No se pudo aplicar: {exc}")
        return 2
    print(f"Aplicado en {target}.")
    print("Reiniciá el dictado (instant2-stop.sh y de nuevo) para usar la versión nueva.")
    return 0
