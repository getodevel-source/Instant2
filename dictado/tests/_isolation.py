"""Aislamiento del estado real del usuario para la suite de tests.

La suite corre en la máquina de quien la escribe. Si un test escribe la
configuración del usuario (`%APPDATA%\\instant2\\config.json`) o mueve el
directorio de modelos del producto anterior, deja la máquina rota: la config
vuelve a los defaults y los modelos que no se pueden volver a bajar quedan
en otro lado.

Importar este módulo redirige `APPDATA`, `LOCALAPPDATA` y el override de la
configuración a un directorio temporal, y fija `INSTANT2_DATA` para que los
datos tampoco salgan de ahí. Como el aislamiento se aplica al importar, cada
test alcanza con importar el módulo:

    import _isolation  # noqa: F401  (aisla el estado real del usuario)

El directorio queda en `%TEMP%\\instant2-tests-...` y se puede borrar sin
consecuencias.
"""
import os
import site
import sys
import tempfile

HOME_ENV = "INSTANT2_TEST_HOME"
# Variables que apuntan al estado del usuario en cada sistema operativo.
USER_ENV = ("APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME")
# El HOME tambien es estado del usuario: en macOS el directorio de datos por
# defecto es "~/Library/Application Support" y en Linux "~/.local/share". Sin
# redirigirlo, un test que escriba ahi toca la maquina real.
# En Windows la app no usa el HOME para los datos (usa %APPDATA% y
# %LOCALAPPDATA%), y tocarlo rompe la expansion del perfil.
HOME_DIRS = ("HOME",) if os.name != "nt" else ()

# Las variables reales ANTES de redirigirlas. Hay herramientas instaladas en el
# perfil del usuario (Inno Setup, entre otras) que se buscan con la ruta real:
# si el test las busca con %LOCALAPPDATA% ya redirigido, no las encuentra y se
# saltea sin avisar, dejando la cobertura del instalador sin ejercitar.
ORIGINAL_ENV = dict(os.environ)

# El user-site de Python vive en el perfil del usuario. Si se redirige el
# HOME, esa ruta deja de resolver y el interprete puede dejar de encontrar
# numpy: se guarda la ruta real y se la deja en sys.path.
_USER_SITE = ""
if hasattr(site, "getusersitepackages"):
    try:
        _USER_SITE = site.getusersitepackages()
    except Exception:  # pragma: no cover - depende del interprete
        _USER_SITE = ""


def isolate_user_state():
    """Redirige el estado del usuario a un directorio temporal (idempotente)."""
    root = os.environ.get(HOME_ENV)
    if not root:
        root = tempfile.mkdtemp(prefix="instant2-tests-")
        os.environ[HOME_ENV] = root
    appdata = os.path.join(root, "appdata")
    localdata = os.path.join(root, "localdata")
    models = os.path.join(root, "models")
    for path in (appdata, localdata, models):
        os.makedirs(path, exist_ok=True)
    os.environ["APPDATA"] = appdata
    os.environ["LOCALAPPDATA"] = localdata
    os.environ["XDG_CONFIG_HOME"] = appdata
    os.environ["XDG_DATA_HOME"] = localdata
    # El HOME tambien se aisla: en macOS y Linux el directorio de datos por
    # defecto vive ahi y no en %APPDATA% ni en %LOCALAPPDATA%.
    for name in HOME_DIRS:
        os.environ[name] = root
    # El override de la configuración y el de los datos: los dos caminos que
    # la app usa para escribir en el disco del usuario.
    os.environ["INSTANT2_CONFIG_DIR"] = os.path.join(appdata, "instant2")
    os.environ["INSTANT2_DATA"] = models
    # Con el HOME redirigido, el user-site de Python deja de resolver: se
    # conserva la ruta real para que el interprete siga viendo sus paquetes.
    if _USER_SITE and os.path.isdir(_USER_SITE) and _USER_SITE not in sys.path:
        sys.path.append(_USER_SITE)
    return root


isolate_user_state()
