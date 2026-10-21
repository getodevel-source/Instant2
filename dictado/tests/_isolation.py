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
import tempfile

HOME_ENV = "INSTANT2_TEST_HOME"
# Variables que apuntan al estado del usuario en cada sistema operativo.
USER_ENV = ("APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME")

# Las variables reales ANTES de redirigirlas. Hay herramientas instaladas en el
# perfil del usuario (Inno Setup, entre otras) que se buscan con la ruta real:
# si el test las busca con %LOCALAPPDATA% ya redirigido, no las encuentra y se
# saltea sin avisar, dejando la cobertura del instalador sin ejercitar.
ORIGINAL_ENV = dict(os.environ)


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
    # El override de la configuración y el de los datos: los dos caminos que
    # la app usa para escribir en el disco del usuario.
    os.environ["INSTANT2_CONFIG_DIR"] = os.path.join(appdata, "instant2")
    os.environ["INSTANT2_DATA"] = models
    return root


isolate_user_state()
