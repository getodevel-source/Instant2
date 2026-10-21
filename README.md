<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="brand/png/lockup-horizontal-on-dark-1200.png">
    <source media="(prefers-color-scheme: light)" srcset="brand/png/lockup-horizontal-on-light-1200.png">
    <img src="brand/png/lockup-horizontal-on-light-1200.png" alt="Instant2" width="360">
  </picture>
</p>

**Dictado por voz en español con revisión opcional del texto.** Mantené F9, hablá y soltá: Instant2 escribe en el campo activo. El audio se procesa en este equipo.

[⬇️ Descargar](https://github.com/getodevel-source/Instant2/releases/latest) · [📖 Guía](dictado/README.md) · [📝 Cambios](CHANGELOG.md)

<p align="center">
  <a href="docs/screenshots/instant2-home.png"><img src="docs/screenshots/instant2-home.png" alt="Pantalla principal de Instant2" width="100%"></a>
</p>

## Por qué Instant2

| | |
|---|---|
| 🔒 **Privacidad** | El audio se reconoce en tu equipo. Si conectás un servicio remoto para revisar el texto, solo recibe el texto reconocido. |
| ⚡ **Listo para usar** | El instalador de Windows trae la app y el runtime de llama.cpp; la app descarga los componentes de voz en el primer uso, sola. |
| 🎯 **Dictado flexible** | Correcciones de términos, teclas configurables y revisión opcional del texto. |
| 🔊 **Lectura en voz alta** | Escribí o seleccioná texto para escucharlo en español. |

## Empezar

1. Descargá la versión de tu sistema desde [Releases](https://github.com/getodevel-source/Instant2/releases/latest).
2. Abrí Instant2, elegí el micrófono y configurá la tecla de dictado.
3. Enfocá un campo de texto, mantené la tecla mientras hablás y soltala al terminar.
4. Para escuchar texto, abrí **Leer texto** o seleccioná texto y presioná la tecla de lectura configurada en **Ajustes**.

En Windows, el instalador de una sola descarga (`Instant2-Setup.exe`, un solo archivo) ya trae los componentes de voz: al terminar de instalar podés dictar y leer en voz alta sin internet. El instalador incluye la app, el runtime de llama.cpp y Parakeet, Silero y Kokoro con Dora (unos 951 MB); el instalador completo pesa cerca de 1,1 GB. El modelo de lenguaje local (3 GB) no viene con la app y se descarga a pedido.

En el resto de las plataformas (portable de Windows, Linux y macOS), Instant2 descarga los componentes de voz durante el primer uso: necesitás internet y cerca de 1 GB libre. La descarga la hace la app, con su barra de progreso y su verificación por hash.

Actualizar desde la app no vuelve a bajar los modelos: el release publica además `Instant2-Update.exe` (unos 204 MB), que reemplaza la app y el runtime y deja intactos los pesos que ya están en `%LOCALAPPDATA%\instant2\models`.

En esas plataformas, la primera vez el panel arranca solo la descarga del reconocimiento (unos 670 MB) cuando detecta que faltan los modelos. Si la cancelás o se corta la red, el botón «Reintentar descarga» queda disponible; la app no vuelve a insistirte sola.

El modelo de lenguaje local (3 GB) es una descarga aparte y explícita: son demasiados gigabytes para bajarlos sin que los pidas. En **Ajustes → Revisión del dictado y privacidad → Archivos del servicio local** tenés «Descargar modelo (3 GB)», con su progreso y su botón de cancelar.

Si venís de Instant (el producto anterior), no se vuelve a descargar nada: Instant2 reutiliza los modelos que ya tenías y los mueve del directorio anterior al suyo. Ojo con esto: si seguís usando Instant, sus modelos quedan en el directorio de Instant2 y la app anterior deja de encontrarlos. Copiá los que te interesen antes de abrir Instant2 por primera vez.

En una instalación nueva, el instalador deja seleccionados **Iniciar Instant2 con Windows** y **Abrir Instant2 y activar el dictado en segundo plano**. Puedes desmarcar cualquiera de esas opciones. Al abrirse, Instant2 inicia el servicio si detecta un micrófono disponible; cerrar el panel deja el servicio en la bandeja.

| Sistema | Descarga |
|---|---|
| Windows | `Instant2-Setup.exe` (instalador) o `Instant2.exe` (portable) |
| Linux (X11; Wayland/Hyprland experimental) | `instant2-linux` |
| macOS | `instant2-macos` |

Los assets incluyen un archivo `.sha256` para verificar la descarga. Antes de dictar, aceptá el permiso del micrófono. En macOS autorizá también **Accesibilidad** para permitir el pegado. Los binarios actuales no tienen firma de sistema operativo; Windows y macOS pueden mostrar un aviso la primera vez.

## Pantallas

<table>
  <tr>
    <th>Micrófono</th>
    <th>Ajustes</th>
  </tr>
  <tr>
    <td><a href="docs/screenshots/instant2-audio.png"><img src="docs/screenshots/instant2-audio.png" alt="Selección y prueba del micrófono" width="100%"></a></td>
    <td><a href="docs/screenshots/instant2-settings.png"><img src="docs/screenshots/instant2-settings.png" alt="Preferencias de Instant2" width="100%"></a></td>
  </tr>
  <tr>
    <th>Vocabulario</th>
    <th>Lectura en voz alta</th>
  </tr>
  <tr>
    <td><a href="docs/screenshots/instant2-vocabulary.png"><img src="docs/screenshots/instant2-vocabulary.png" alt="Edición del vocabulario personalizado" width="100%"></a></td>
    <td><a href="docs/screenshots/instant2-reading.png"><img src="docs/screenshots/instant2-reading.png" alt="Lectura de texto en voz alta" width="100%"></a></td>
  </tr>
  <tr>
    <th>Dictado activo</th>
    <th>Aviso</th>
  </tr>
  <tr>
    <td><a href="docs/screenshots/instant2-overlay_listening.png"><img src="docs/screenshots/instant2-overlay_listening.png" alt="Indicador de dictado activo" width="100%"></a></td>
    <td><a href="docs/screenshots/instant2-overlay_notice.png"><img src="docs/screenshots/instant2-overlay_notice.png" alt="Aviso del indicador de voz" width="100%"></a></td>
  </tr>
  <tr>
    <th colspan="2">Estado listo</th>
  </tr>
  <tr>
    <td colspan="2" align="center"><a href="docs/screenshots/instant2-overlay_final.png"><img src="docs/screenshots/instant2-overlay_final.png" alt="Indicador de voz listo" width="50%"></a></td>
  </tr>
</table>

## Privacidad y actualizaciones

El audio no se envía a la nube. La revisión opcional puede usar un servicio local o remoto; si elegís uno remoto, recibe texto, nunca audio. Instant2 no guarda audio ni transcripciones en sus registros por defecto.

En **Ajustes → Actualizaciones**, podés recibir un aviso antes de descargar, permitir la descarga automática en segundo plano o apagar las consultas. Cada descarga se verifica antes de instalarse. Instant2 te pide confirmar la instalación y el reinicio para que puedas terminar lo que estás haciendo.

## Más información

[Instalación y uso detallados](dictado/README.md) · [Precisión y motores](docs/precision.md) · [Entorno de desarrollo](dictado/README.md#desarrollo) · [Diseño y marca](design.md) · [Historial de versiones](CHANGELOG.md) · [Licencia MIT](LICENSE)
